# diagnose_sandbox.py
# Tsclp_v2.0 — РУЧНАЯ диагностика песочницы.
#
# ВНИМАНИЕ: этот скрипт ПОДКЛЮЧАЕТСЯ к песочнице T-Invest API.
# Он НЕ отправляет торговых заявок, но выполняет read-only запросы:
#  - портфель (positions: quantity и quantityLots)
#  - свечи (is_complete последней свечи)
#  - стоп-заявки (если есть)
#
# Запускать ТОЛЬКО вручную, после локальных тестов.
# По умолчанию отключён: требует --yes.

import argparse
import asyncio
import logging
from datetime import timedelta

from t_tech.invest import CandleInterval, InstrumentIdType
from t_tech.invest.sandbox.async_client import AsyncSandboxClient
from t_tech.invest.utils import now, quotation_to_decimal

import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="Подтвердить подключение")
    args = parser.parse_args()

    if not args.yes:
        print("Диагностика отключена. Запустите: python diagnose_sandbox.py --yes")
        print("Скрипт выполняет ТОЛЬКО чтение, но подключается к песочнице.")
        return

    config.validate()

    async with AsyncSandboxClient(config.TINKOFF_API_TOKEN) as client:
        account_id = config.TINKOFF_ACCOUNT_ID
        if not account_id:
            accounts = await client.users.get_accounts()
            if not accounts.accounts:
                print("Нет песочных счетов. Создайте через основной робот.")
                return
            account_id = accounts.accounts[0].id
            print(f"Используется первый счёт: {account_id}")

        print("=" * 60)
        print("1. ПОРТФЕЛЬ (единицы quantity vs quantityLots)")
        print("=" * 60)
        portfolio = await client.sandbox.get_sandbox_portfolio(account_id=account_id)
        for pos in portfolio.positions:
            qty = float(quotation_to_decimal(pos.quantity))
            lots_attr = getattr(pos, "quantity_lots", None)
            lots = float(quotation_to_decimal(lots_attr)) if lots_attr is not None else None
            print(f"  figi={pos.figi[:12]}... quantity={qty} quantityLots={lots}")

        print()
        print("=" * 60)
        print("2. СВЕЧИ (is_complete последней свечи)")
        print("=" * 60)
        for ticker in config.TRADING_TICKERS:
            try:
                resp = await client.instruments.get_instrument_by(
                    id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
                    id=ticker,
                    class_code="TQBR",
                )
            except Exception as e:
                print(f"  {ticker}: не удалось найти инструмент ({e})")
                continue
            figi = resp.instrument.figi
            candles = []
            async for c in client.get_all_candles(
                figi=figi,
                from_=now() - timedelta(days=1),
                interval=CandleInterval.CANDLE_INTERVAL_5_MIN,
            ):
                candles.append(c)
            if candles:
                last = candles[-1]
                is_complete = getattr(last, "is_complete", None)
                print(f"  {ticker}: last candle time={last.time} is_complete={is_complete}")
            else:
                print(f"  {ticker}: нет свечей")

        print()
        print("=" * 60)
        print("3. СТОП-ЗАЯВКИ")
        print("=" * 60)
        try:
            stops = await client.sandbox.get_sandbox_stop_orders(account_id=account_id)
            if not stops.stop_orders:
                print("  (нет стоп-заявок)")
            else:
                for o in stops.stop_orders:
                    print(
                        f"  id={o.stop_order_id} figi={o.figi[:12]}... "
                        f"type={o.stop_order_type} status={getattr(o, 'status', None)}"
                    )
        except Exception as e:
            print(f"  ОШИБКА: {e}")


if __name__ == "__main__":
    asyncio.run(main())