# check_readonly.py
# Проверка боевого Read-Only токена: находит SBER по тикеру.
# Только чтение. Заявок не отправляет.

import asyncio
import config
from t_tech.invest import AsyncClient, InstrumentIdType


async def main():
    async with AsyncClient(config.TINKOFF_READONLY_TOKEN) as client:
        try:
            r = await client.instruments.get_instrument_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
                id="SBER",
                class_code="TQBR",
            )
            print("OK: FIGI =", r.instrument.figi, "| name =", r.instrument.name)
        except Exception as e:
            print("ERROR:", e)


if __name__ == "__main__":
    asyncio.run(main())