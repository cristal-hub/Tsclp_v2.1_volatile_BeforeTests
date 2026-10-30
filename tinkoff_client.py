# tinkoff_client.py
# Tsclp_v2.1_volatile — клиент T-Invest API (ТОЛЬКО ПЕСОЧНИЦА).
#
# ИСПРАВЛЕНИЕ: добавлен get_sandbox_order_state() для фактической цены исполнения.
# Парсинг OrderState: lots_executed, executed_order_price.

import asyncio
import logging
import uuid
from datetime import timedelta
from typing import Optional

from t_tech.invest import (
    CandleInterval,
    InstrumentIdType,
    OrderDirection,
    OrderType,
    StopOrderDirection,
    StopOrderExpirationType,
    StopOrderType,
)
from t_tech.invest.sandbox.async_client import AsyncSandboxClient
from t_tech.invest.utils import now, quotation_to_decimal, decimal_to_quotation

import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class SandboxRequiredError(RuntimeError):
    pass


class TinkoffClient:
    def __init__(self, token: str, use_sandbox: bool = True):
        if not use_sandbox:
            raise SandboxRequiredError("Tsclp_v2.1_volatile работает ТОЛЬКО в песочнице.")
        self.token = token
        self._sandbox_client = None
        self.client = None
        self.account_id: Optional[str] = None
        self.instrument_info_cache = {}
        self.uncertain_state = False

    async def __aenter__(self):
        last_exc = None
        for attempt in range(config.API_RETRY_COUNT):
            try:
                self._sandbox_client = AsyncSandboxClient(self.token)
                self.client = await self._sandbox_client.__aenter__()
                logging.info("Подключение к T-Invest SANDBOX API успешно.")
                await self._ensure_account()
                return self
            except Exception as e:
                last_exc = e
                logging.error(f"Ошибка подключения (попытка {attempt + 1}): {e}")
                if attempt < config.API_RETRY_COUNT - 1:
                    await asyncio.sleep(config.API_RETRY_DELAY_SECONDS)
        raise RuntimeError(f"Не удалось подключиться: {last_exc}")

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._sandbox_client:
            try:
                await self._sandbox_client.__aexit__(exc_type, exc_val, exc_tb)
            except Exception as e:
                logging.warning(f"Ошибка закрытия: {e}")

    async def _ensure_account(self):
        if config.TINKOFF_ACCOUNT_ID:
            self.account_id = config.TINKOFF_ACCOUNT_ID
            return
        accounts = await self.client.users.get_accounts()
        if accounts.accounts:
            self.account_id = accounts.accounts[0].id
        else:
            resp = await self.client.sandbox.open_sandbox_account()
            self.account_id = resp.account_id

    async def get_all_tradable_shares(self):
        try:
            instruments = await self.client.instruments.shares()
            return [
                s for s in instruments.instruments
                if (s.exchange or "").upper() == "MOEX"
                and (s.currency or "").lower() == "rub"
                and s.buy_available_flag and s.sell_available_flag
            ]
        except Exception as e:
            logging.error(f"Ошибка списка акций: {e}")
            return []

    async def get_historical_candles(self, figi, days, interval=CandleInterval.CANDLE_INTERVAL_5_MIN):
        try:
            await asyncio.sleep(config.API_CALL_DELAY_SECONDS)
            candles = []
            async for candle in self.client.get_all_candles(
                figi=figi, from_=now() - timedelta(days=days), interval=interval,
            ):
                candles.append(candle)
            return candles
        except Exception as e:
            logging.error(f"Ошибка свечей {figi}: {e}")
            return []

    async def get_last_prices(self, figi_list):
        try:
            await asyncio.sleep(config.API_CALL_DELAY_SECONDS)
            response = await self.client.market_data.get_last_prices(figi=figi_list)
            return {
                p.figi: float(quotation_to_decimal(p.price))
                for p in response.last_prices if p.price is not None
            }
        except Exception as e:
            logging.error(f"Ошибка цен: {e}")
            return {}

    async def get_instrument_info(self, figi):
        if figi in self.instrument_info_cache:
            return self.instrument_info_cache[figi]
        try:
            response = await self.client.instruments.get_instrument_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=figi,
            )
            instrument = response.instrument
            inc = quotation_to_decimal(instrument.min_price_increment)
            info = {
                'lot': int(instrument.lot or 1),
                'min_price_increment': float(inc),
                'ticker': instrument.ticker,
                'name': instrument.name,
            }
            self.instrument_info_cache[figi] = info
            return info
        except Exception as e:
            logging.error(f"Ошибка info {figi}: {e}")
            return None

    async def get_account_balance(self):
        try:
            portfolio = await self.client.sandbox.get_sandbox_portfolio(account_id=self.account_id)
            return float(quotation_to_decimal(portfolio.total_amount_portfolio))
        except Exception as e:
            logging.error(f"Ошибка баланса: {e}")
            return 0.0

    async def get_positions(self):
        try:
            portfolio = await self.client.sandbox.get_sandbox_portfolio(account_id=self.account_id)
            result = {}
            for pos in portfolio.positions:
                qty = float(quotation_to_decimal(pos.quantity))
                if qty != 0:
                    result[pos.figi] = qty
            return result
        except Exception as e:
            logging.error(f"Ошибка позиций: {e}")
            self.uncertain_state = True
            return {}

    async def post_market_order(self, figi, quantity, direction):
        order_id = str(uuid.uuid4())
        try:
            response = await self.client.sandbox.post_sandbox_order(
                account_id=self.account_id, figi=figi, quantity=quantity,
                direction=direction, order_type=OrderType.ORDER_TYPE_MARKET,
                order_id=order_id,
            )
            return response
        except Exception as e:
            logging.error(f"Ошибка заявки {figi}: {e}")
            self.uncertain_state = True
            return None

    async def get_sandbox_order_state(self, order_id: str):
        """Получает статус заявки по order_id. Для фактической цены исполнения."""
        try:
            return await self.client.sandbox.get_sandbox_order_state(
                account_id=self.account_id, order_id=order_id,
            )
        except Exception as e:
            logging.error(f"Ошибка статуса заявки {order_id}: {e}")
            return None

    def is_uncertain(self) -> bool:
        return self.uncertain_state