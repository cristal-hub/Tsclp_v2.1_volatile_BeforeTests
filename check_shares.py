import asyncio
import config
from t_tech.invest import AsyncClient, InstrumentStatus

async def main():
    async with AsyncClient(config.TINKOFF_READONLY_TOKEN) as client:
        # 1. Проверяем, какие аргументы вообще принимает метод shares()
        import inspect
        sig = inspect.signature(client.instruments.shares)
        print("Сигнатура shares():", sig)
        print("---")

        # 2. Пробуем ALL без площадки (как было)
        try:
            r1 = await client.instruments.shares(
                instrument_status=InstrumentStatus.INSTRUMENT_STATUS_ALL
            )
            print(f"ALL (без площадки): {len(r1.instruments)} акций")
            for s in r1.instruments[:10]:
                print(f"  - {s.ticker} | exchange={s.exchange}")
        except Exception as e:
            print(f"ALL: ОШИБКА {e}")

        # 3. Пробуем ALL + строковое значение "INSTRUMENT_EXCHANGE_DEALER"
        try:
            r2 = await client.instruments.shares(
                instrument_status=InstrumentStatus.INSTRUMENT_STATUS_ALL,
                instrument_exchange="INSTRUMENT_EXCHANGE_DEALER"
            )
            print(f"\nALL + DEALER (строка): {len(r2.instruments)} акций")
            for s in r2.instruments[:10]:
                print(f"  - {s.ticker} | exchange={s.exchange}")
        except Exception as e:
            print(f"\nALL + DEALER (строка): ОШИБКА {e}")

        # 4. Пробуем ALL + числовое значение 1 (DEALER = 1)
        try:
            r3 = await client.instruments.shares(
                instrument_status=InstrumentStatus.INSTRUMENT_STATUS_ALL,
                instrument_exchange=1
            )
            print(f"\nALL + DEALER (число 1): {len(r3.instruments)} акций")
            for s in r3.instruments[:10]:
                print(f"  - {s.ticker} | exchange={s.exchange}")
        except Exception as e:
            print(f"\nALL + DEALER (число 1): ОШИБКА {e}")

if __name__ == "__main__":
    asyncio.run(main())