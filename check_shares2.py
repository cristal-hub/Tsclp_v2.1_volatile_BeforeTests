import asyncio
import config
from t_tech.invest import AsyncClient, InstrumentStatus

async def main():
    async with AsyncClient(config.TINKOFF_READONLY_TOKEN) as client:
        r = await client.instruments.shares(
            instrument_status=InstrumentStatus.INSTRUMENT_STATUS_ALL
        )
        print(f"Всего акций: {len(r.instruments)}")
        print("--- Первые 10 с полями ---")
        for s in r.instruments[:10]:
            print(f"ticker={s.ticker}")
            print(f"  exchange={s.exchange}")
            print(f"  real_exchange={getattr(s, 'real_exchange', 'НЕТ ПОЛЯ')}")
            print(f"  class_code={getattr(s, 'class_code', 'НЕТ ПОЛЯ')}")
            print(f"  currency={s.currency}")
            print(f"  buy_available={s.buy_available_flag} sell_available={s.sell_available_flag}")
            print("---")

        # Считаем, сколько акций с real_exchange == MOEX
        moex_count = 0
        for s in r.instruments:
            re = getattr(s, 'real_exchange', None)
            if re is not None and 'MOEX' in str(re).upper():
                moex_count += 1
        print(f"\nАкций с real_exchange == MOEX: {moex_count}")

if __name__ == "__main__":
    asyncio.run(main())