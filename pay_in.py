# pay_in.py
# Tsclp_v2.0 — РУЧНОЕ пополнение песочницы.
#
# ВНИМАНИЕ: этот скрипт ПОПОЛНЯЕТ счёт песочницы на 100 000 ₽.
# Он НЕ запускается автоматически и требует явного подтверждения.
# Работает ТОЛЬКО в песочнице. Реальный контур не реализован.

import asyncio
from decimal import Decimal
import logging

from t_tech.invest.sandbox.async_client import AsyncSandboxClient
from t_tech.invest.utils import decimal_to_quotation

import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


async def main():
    config.validate()

    if not config.USE_SANDBOX:
        print("❌ Пополнение разрешено только в песочнице. USE_SANDBOX=False.")
        return

    if not config.TINKOFF_ACCOUNT_ID:
        print("❌ TINKOFF_ACCOUNT_ID не задан в .env.")
        return

    print("=" * 60)
    print("⚠️  ВНИМАНИЕ: пополнение счёта ПЕСОЧНИЦЫ на 100 000 ₽")
    print(f"   Счёт: {config.TINKOFF_ACCOUNT_ID[:12]}...")
    print("=" * 60)
    answer = input("Введите YES (заглавными) для подтверждения: ").strip()
    if answer != "YES":
        print("Отменено.")
        return

    async with AsyncSandboxClient(config.TINKOFF_API_TOKEN) as client:
        amount = decimal_to_quotation(Decimal("100000"))
        response = await client.sandbox.sandbox_pay_in(
            account_id=config.TINKOFF_ACCOUNT_ID,
            amount=amount,
        )
        print(f"✅ Счёт пополнен. Баланс: {response.balance}")


if __name__ == "__main__":
    asyncio.run(main())