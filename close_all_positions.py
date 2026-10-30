# close_all_positions.py
# Tsclp_v2.1_volatile — РУЧНОЕ закрытие ВСЕХ позиций в песочнице.
# Универсальный: находит все акции в портфеле и продаёт их рыночной заявкой.
# Валютные позиции (RUB, USD, EUR) пропускаются.

import asyncio
import logging

from t_tech.invest import OrderDirection, OrderType
from t_tech.invest.sandbox.async_client import AsyncSandboxClient
from t_tech.invest.utils import quotation_to_decimal

import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


async def main():
    config.validate()

    async with AsyncSandboxClient(config.TINKOFF_API_TOKEN) as client:
        account_id = config.TINKOFF_ACCOUNT_ID
        if not account_id:
            accounts = await client.users.get_accounts()
            if not accounts.accounts:
                print("❌ Нет песочных счетов.")
                return
            account_id = accounts.accounts[0].id

        print(f"📊 Счёт: {account_id[:12]}...")
        portfolio = await client.sandbox.get_sandbox_portfolio(account_id=account_id)

        positions = []
        for pos in portfolio.positions:
            qty = float(quotation_to_decimal(pos.quantity))
            if qty <= 0:
                continue
            figi = pos.figi
            # Пропускаем валютные позиции
            if figi.startswith("RUB") or figi.startswith("USD") or figi.startswith("EUR"):
                print(f"⏭️  Пропуск валютной позиции {figi}({qty})")
                continue
            positions.append((figi, qty))

        if not positions:
            print("✅ Нет позиций для закрытия.")
            return

        print(f"\n🔍 Найдено {len(positions)} позиций для закрытия:")
        for figi, qty in positions:
            print(f"   • {figi}: {qty}")

        print("\n⚠️  ВНИМАНИЕ: будут отправлены РЫНОЧНЫЕ заявки на продажу.")
        answer = input("Введите YES (заглавными) для подтверждения: ").strip()
        if answer != "YES":
            print("Отменено.")
            return

        print()
        for figi, qty in positions:
            try:
                # Округляем вниз (нельзя продать дробное количество лотов)
                lots = int(qty)
                if lots <= 0:
                    print(f"⚠️  {figi}: qty={qty}, пропуск (0 лотов)")
                    continue

                print(f"📤 Продажа {lots} лотов {figi}...")
                response = await client.sandbox.post_sandbox_order(
                    account_id=account_id,
                    figi=figi,
                    quantity=lots,
                    direction=OrderDirection.ORDER_DIRECTION_SELL,
                    order_type=OrderType.ORDER_TYPE_MARKET,
                )
                status = response.execution_report_status
                executed = getattr(response, 'lots_executed', '?')
                print(f"   ✅ Заявка отправлена: id={response.order_id}")
                print(f"   Статус: {status}, исполнено: {executed}")
            except Exception as e:
                print(f"   ❌ Ошибка для {figi}: {e}")

        print("\n✅ Готово. Проверьте портфель.")


if __name__ == "__main__":
    asyncio.run(main())