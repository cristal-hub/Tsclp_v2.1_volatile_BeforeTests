# screener.py
# Tsclp_v2.1_volatile — скрининг TOP-10 волатильных акций MOEX.
#
# ИСПРАВЛЕНИЕ: при ошибке сохраняется последний успешный список.
# Кеш: если возраст < VOLATILITY_REFRESH_HOURS, возврат без запросов.

import asyncio
import logging
from datetime import timedelta
from typing import Optional

import pandas as pd
import pandas_ta_classic as ta
from t_tech.invest import AsyncClient, CandleInterval, InstrumentStatus
from t_tech.invest.utils import now

import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

REAL_EXCHANGE_MOEX = 1


class Screener:
    def __init__(self):
        self.cached_instruments = None
        self.cached_at = None
        self.last_success_at = None

    def _cache_valid(self) -> bool:
        if not self.cached_instruments or not self.cached_at:
            return False
        age_hours = (now() - self.cached_at).total_seconds() / 3600
        return age_hours < config.VOLATILITY_REFRESH_HOURS

    async def select_top_volatile(self, force: bool = False) -> Optional[list]:
        if not force and self._cache_valid():
            logging.info(f"[screener] Кэш ({len(self.cached_instruments)} инстр.)")
            return self.cached_instruments

        for attempt in range(1, 3):
            try:
                result = await self._do_scan()
                if result:
                    self.cached_instruments = result
                    self.cached_at = now()
                    self.last_success_at = now()
                    logging.info(f"[screener] Скрининг: {len(result)} инструментов.")
                    return result
                logging.warning(f"[screener] Попытка {attempt}: пусто.")
            except Exception as e:
                logging.error(f"[screener] Попытка {attempt} ошибка: {e}")
            if attempt < 2:
                await asyncio.sleep(config.API_RETRY_DELAY_SECONDS)

        # Если есть последний успешный список — возвращаем его, не блокируя торговлю.
        if self.cached_instruments:
            logging.error("[screener] Скрининг не удался. Используется последний успешный список.")
            return self.cached_instruments

        logging.error("[screener] Скрининг не удался. Кеша нет.")
        return None

    async def _do_scan(self) -> Optional[list]:
        async with AsyncClient(config.TINKOFF_READONLY_TOKEN) as client:
            instruments = await client.instruments.shares(
                instrument_status=InstrumentStatus.INSTRUMENT_STATUS_ALL
            )
            shares = [
                s for s in instruments.instruments
                if getattr(s, 'real_exchange', 0) == REAL_EXCHANGE_MOEX
                and (s.currency or "").lower() == "rub"
                and s.buy_available_flag and s.sell_available_flag
            ]
            logging.info(f"[screener] Акций MOEX: {len(shares)}")

            if not shares:
                return None

            volatility_data = []
            for share in shares:
                try:
                    await asyncio.sleep(config.API_CALL_DELAY_SECONDS)
                    candles = []
                    async for c in client.get_all_candles(
                        figi=share.figi,
                        from_=now() - timedelta(days=config.VOLATILITY_PERIOD_DAYS),
                        interval=CandleInterval.CANDLE_INTERVAL_DAY,
                    ):
                        candles.append(c)
                    if len(candles) < config.VOLATILITY_PERIOD_DAYS - 1:
                        continue

                    df = pd.DataFrame([{
                        'high': float(c.high.units + c.high.nano / 1e9),
                        'low': float(c.low.units + c.low.nano / 1e9),
                        'close': float(c.close.units + c.close.nano / 1e9),
                        'volume': int(c.volume),
                    } for c in candles])

                    atr_series = ta.atr(
                        high=df['high'], low=df['low'], close=df['close'],
                        length=config.ATR_PERIOD,
                    )
                    if atr_series is None or atr_series.dropna().empty:
                        continue
                    atr = float(atr_series.dropna().iloc[-1])
                    last_close = float(df['close'].iloc[-1])
                    if last_close <= 0 or atr <= 0:
                        continue
                    avg_turnover = float(df['volume'].tail(20).mean()) * float(df['close'].tail(20).mean())
                    if avg_turnover < config.MIN_DAILY_TURNOVER_RUB:
                        continue
                    volatility_data.append({
                        'figi': share.figi, 'ticker': share.ticker, 'name': share.name,
                        'volatility': (atr / last_close) * 100,
                        'avg_turnover_rub': avg_turnover,
                    })
                except Exception as e:
                    logging.warning(f"[screener] {share.ticker}: {e}")
                    continue

            if not volatility_data:
                return None
            volatility_data.sort(key=lambda x: x['volatility'], reverse=True)
            return volatility_data[:config.TOP_VOLATILE_INSTRUMENTS_COUNT]