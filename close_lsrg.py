# close_lsrg.py
# РУЧНОЕ закрытие позиции LSRG в песочнице. Одноразовый скрипт.
import asyncio
import config
from t_tech.invest import AsyncClient, OrderDirection, OrderType
from t_tech.invest.sandbox.async_client import AsyncSandboxClient
from t_tech.invest.utils import quotation_to_decimal

LSRG_FIGI = "BBG004S68C39"  # FIGI LSRG из вашего лога
LOTS_TO_SELL = 27

async def main():
    config.validate()
    # Используем песочный клиент и токен
    async with AsyncSandboxClient(config.TINKOFF_API_TOKEN) as client:
        account_id = config.TINKOFF_ACCOUNT_ID
        if not account_id:
            accounts = await client.users.get_accounts()
            account_id = accounts.accounts[0].id
        
        print(f"Продажа {LOTS_TO_SELL} лотов LSRG на счёте {account_id[:12]}...")
        response = await client.sandbox.post_sandbox_order(
            account_id=account_id,
            figi=LSRG_FIGI,
            quantity=LOTS_TO_SELL,
            direction=OrderDirection.ORDER_DIRECTION_SELL,
            order_type=OrderType.ORDER_TYPE_MARKET,
        )
        print(f"Заявка отправлена: id={response.order_id}")
        print(f"Статус: {response.execution_report_status}")
        print(f"Исполнено лотов: {response.lots_executed}")

if __name__ == "__main__":
    asyncio.run(main())