# close_all_positions_v2.py
# Закрывает ВСЕ позиции, включая шорт (отрицательные) и лонг (положительные).
# Для шорта: покупает. Для лонга: продаёт.

import asyncio
from t_tech.invest import OrderDirection, OrderType
from t_tech.invest.sandbox.async_client import AsyncSandboxClient
from t_tech.invest.utils import quotation_to_decimal
import config

async def main():
    config.validate()
    async with AsyncSandboxClient(config.TINKOFF_API_TOKEN) as client:
        account_id = config.TINKOFF_ACCOUNT_ID
        if not account_id:
            accounts = await client.users.get_accounts()
            account_id = accounts.accounts[0].id

        portfolio = await client.sandbox.get_sandbox_portfolio(account_id=account_id)
        to_close = []
        for pos in portfolio.positions:
            qty = float(quotation_to_decimal(pos.quantity))
            if qty == 0:
                continue
            figi = pos.figi
            if figi.startswith("RUB") or figi.startswith("USD") or figi.startswith("EUR"):
                print(f"⏭️ Пропуск валюты {figi}({qty})")
                continue
            to_close.append((figi, qty))

        if not to_close:
            print("✅ Нет позиций для закрытия.")
            return

        print(f"Найдено {len(to_close)} позиций:")
        for figi, qty in to_close:
            side = "КУПИТЬ (закрыть шорт)" if qty < 0 else "ПРОДАТЬ (закрыть лонг)"
            print(f"  {figi}: {qty} → {side}")

        answer = input("\nВведите YES для подтверждения: ").strip()
        if answer != "YES":
            print("Отменено.")
            return

        for figi, qty in to_close:
            lots = int(abs(qty))
            if lots == 0:
                continue
            direction = (
                OrderDirection.ORDER_DIRECTION_BUY if qty < 0
                else OrderDirection.ORDER_DIRECTION_SELL
            )
            print(f"\n📤 {figi}: {direction.name} {lots} лотов...")
            try:
                resp = await client.sandbox.post_sandbox_order(
                    account_id=account_id, figi=figi, quantity=lots,
                    direction=direction, order_type=OrderType.ORDER_TYPE_MARKET,
                )
                print(f"  ✅ id={resp.order_id}, статус={resp.execution_report_status}")
            except Exception as e:
                print(f"  ❌ Ошибка: {e}")

        print("\n✅ Готово. Проверьте портфель.")

if __name__ == "__main__":
    asyncio.run(main())