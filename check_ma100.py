# check_ma100.py
# Диагностика: показывает последнюю цену и MA100 для SBER/GAZP.
# Только чтение, заявок не отправляет.

import asyncio
from datetime import timedelta

import pandas as pd
import pandas_ta_classic as ta
from t_tech.invest import CandleInterval, InstrumentIdType
from t_tech.invest.sandbox.async_client import AsyncSandboxClient
from t_tech.invest.utils import now, quotation_to_decimal

import config


async def main():
    config.validate()
    async with AsyncSandboxClient(config.TINKOFF_API_TOKEN) as client:
        for ticker in config.TRADING_TICKERS:
            resp = await client.instruments.get_instrument_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
                id=ticker,
                class_code="TQBR",
            )
            figi = resp.instrument.figi
            closes = []
            async for c in client.get_all_candles(
                figi=figi,
                from_=now() - timedelta(days=2),
                interval=CandleInterval.CANDLE_INTERVAL_5_MIN,
            ):
                closes.append(float(quotation_to_decimal(c.close)))
            if len(closes) < 100:
                print(f"{ticker}: мало свечей ({len(closes)}), MA100 не считается")
                continue
            df = pd.DataFrame({"close": closes})
            ma100 = ta.sma(df["close"], length=100).iloc[-1]
            last = closes[-1]
            position = "ВЫШЕ" if last > ma100 else "НИЖЕ"
            print(f"{ticker}: last={last:.2f}, MA100={ma100:.2f}, {position} MA100")


if __name__ == "__main__":
    asyncio.run(main())