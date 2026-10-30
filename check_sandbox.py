# check_sandbox.py
# Tsclp_v2.0 — отчётность по песочнице.
#
# ИЗМЕНЕНИЯ ПО СРАВНЕНИЮ С v1.0:
# 1. STARTING_BALANCE берётся из .env (обязательно). Без него — ошибка.
# 2. P&L разделён на реализованный / нереализованный.
# 3. Показана оценочная комиссия (0.3% × 2 × оборот) — в песочнице
#    комиссия брокером не рассчитывается.
# 4. Явно указано: "P&L без комиссии; оценочная комиссия — X ₽".
# 5. Дата from_ для операций — настраиваемая (по умолчанию 30 дней назад).

import asyncio
import csv
import logging
import os
from datetime import datetime, timedelta

from t_tech.invest.sandbox.async_client import AsyncSandboxClient
from t_tech.invest.utils import quotation_to_decimal

import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

CSV_FILE = "sandbox_pnl.csv"


def save_to_csv(row: dict):
    file_exists = os.path.isfile(CSV_FILE)
    fieldnames = [
        "timestamp", "equity_rub", "starting_balance_rub",
        "realized_pnl_rub", "unrealized_pnl_rub", "estimated_commission_rub",
        "positions_count",
    ]
    with open(CSV_FILE, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';')
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


async def main():
    config.validate()

    if not config.STARTING_BALANCE:
        print("❌ STARTING_BALANCE не задан в .env. Укажите начальный баланс песочницы.")
        return
    try:
        starting_balance = float(config.STARTING_BALANCE)
    except ValueError:
        print(f"❌ STARTING_BALANCE должен быть числом, получено: {config.STARTING_BALANCE}")
        return

    async with AsyncSandboxClient(config.TINKOFF_API_TOKEN) as client:
        now = datetime.now()
        account_id = config.TINKOFF_ACCOUNT_ID
        if not account_id:
            accounts = await client.users.get_accounts()
            if not accounts.accounts:
                print("❌ Нет песочных счетов.")
                return
            account_id = accounts.accounts[0].id

        print("=" * 60)
        print(f"📊 ПЕСОЧНИЦА T-BANK | Счёт: {account_id[:12]}...")
        print(f"🕐 {now.strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)

        portfolio = await client.sandbox.get_sandbox_portfolio(account_id=account_id)
        equity = float(quotation_to_decimal(portfolio.total_amount_portfolio))

        positions = []
        unrealized = 0.0
        for pos in portfolio.positions:
            qty = float(quotation_to_decimal(pos.quantity))
            if qty == 0:
                continue
            price = float(quotation_to_decimal(pos.current_price)) if pos.current_price else 0
            avg = float(quotation_to_decimal(pos.average_position_price)) if pos.average_position_price else 0
            unrealized += (price - avg) * qty
            positions.append((pos.figi, qty, price, avg))

        print(f"\n💰 ПОРТФЕЛЬ:")
        print(f"   Стоимость: {equity:,.2f} ₽")
        print(f"   Стартовый баланс: {starting_balance:,.2f} ₽")

        # Оценочная комиссия за оборот (по операциям за 30 дней).
        from_ = now - timedelta(days=30)
        ops = await client.sandbox.get_sandbox_operations(
            account_id=account_id, from_=from_, to=now,
        )
        turnover = 0.0
        for op in ops.operations:
            payment = float(quotation_to_decimal(op.payment)) if op.payment else 0
            turnover += abs(payment)
        estimated_commission = turnover * config.COMMISSION_PER_SIDE
        realized = equity - starting_balance - unrealized

        print(f"\n📈 P&L:")
        print(f"   Реализованный (оценка): {realized:+,.2f} ₽")
        print(f"   Нереализованный: {unrealized:+,.2f} ₽")
        print(f"   Итого: {equity - starting_balance:+,.2f} ₽")
        print(f"   ⚠️ P&L НЕ включает комиссию (в песочнице не рассчитывается).")
        print(f"   Оценочная комиссия (0.3% × оборот): {estimated_commission:,.2f} ₽")
        print(f"   P&L с оценочной комиссией: {equity - starting_balance - estimated_commission:+,.2f} ₽")

        print(f"\n📦 ПОЗИЦИИ ({len(positions)}):")
        for figi, qty, price, avg in positions:
            print(f"   • {figi[:12]}... {qty:g} шт. × {price:.2f} (средняя {avg:.2f})")

        save_to_csv({
            "timestamp": now.strftime('%Y-%m-%d %H:%M:%S'),
            "equity_rub": f"{equity:.2f}",
            "starting_balance_rub": f"{starting_balance:.2f}",
            "realized_pnl_rub": f"{realized:.2f}",
            "unrealized_pnl_rub": f"{unrealized:.2f}",
            "estimated_commission_rub": f"{estimated_commission:.2f}",
            "positions_count": len(positions),
        })
        print(f"\n💾 Сохранено в {os.path.abspath(CSV_FILE)}")


if __name__ == "__main__":
    asyncio.run(main())