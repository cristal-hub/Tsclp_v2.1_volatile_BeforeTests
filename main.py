# main.py
# Tsclp_v2.1_volatile — основной цикл (ТОЛЬКО ПЕСОЧНИЦА).
#
# Telegram — ОПЦИОНАЛЬНО (сторож переподключает при VPN).
# ДОБАВЛЕНО: slippage при входе и выходе.

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

import pandas as pd
from t_tech.invest import (
    CandleInterval,
    OrderDirection,
    OrderExecutionReportStatus,
)

import config
import logger as research_logger
from risk_manager import RiskManager
from strategy import StrategyManager
from tinkoff_client import TinkoffClient
from execution import ExecutionMonitor, PositionState
from screener import Screener

if config.ENABLE_TELEGRAM:
    from telegram_bot import TelegramBot
else:
    TelegramBot = None

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.ext._utils.networkloop").setLevel(logging.WARNING)

TRADING_FLAG = "trading.flag"
top_instruments = []
telegram_bot = None
main_client = None
reconcile_done = False
monitor_ref = None


def is_trading_enabled() -> bool:
    return os.path.exists(TRADING_FLAG)


def start_trading():
    try:
        with open(TRADING_FLAG, "w", encoding="utf-8") as f:
            f.write(datetime.now(timezone.utc).isoformat())
        logging.info("✅ Торговля ВКЛЮЧЕНА (создан trading.flag)")
        research_logger.log_event("TRADING_START", {})
    except Exception as e:
        logging.error(f"Не удалось создать {TRADING_FLAG}: {e}")


def stop_trading():
    try:
        if os.path.exists(TRADING_FLAG):
            os.remove(TRADING_FLAG)
        logging.info("🛑 Торговля ВЫКЛЮЧЕНА (удалён trading.flag)")
        research_logger.log_event("TRADING_STOP", {})
    except Exception as e:
        logging.error(f"Не удалось удалить {TRADING_FLAG}: {e}")


def get_status():
    instruments = ", ".join(i['ticker'] for i in top_instruments) or "—"
    monitored = ", ".join(
        p.ticker for p in (monitor_ref.positions.values() if monitor_ref else [])
        if p.state in (PositionState.OPEN, PositionState.CLOSING)
    ) or "—"
    uncertain = "ДА" if (main_client and main_client.is_uncertain()) else "нет"
    trading = "АКТИВНА" if is_trading_enabled() else "СТОП"
    tg_status = "работает" if telegram_bot else "НЕДОСТУПЕН"
    return (
        f"🤖 Статус\n"
        f"Торговля: {trading}\n"
        f"Telegram: {tg_status}\n"
        f"Режим: SANDBOX\n"
        f"Сверка: {'OK' if reconcile_done else 'НЕ ЗАВЕРШЕНА'}\n"
        f"Неопределённость: {uncertain}\n"
        f"Для новых входов ({len(top_instruments)}): {instruments}\n"
        f"Под мониторингом ({monitor_ref.active_count() if monitor_ref else 0}): {monitored}"
    )


async def notify(text: str):
    logging.info(f"[notify] {text}")
    if telegram_bot:
        try:
            await telegram_bot.send_message(text)
        except Exception as e:
            logging.warning(f"Telegram send error: {e}")


async def _telegram_watchdog():
    global telegram_bot
    if not telegram_bot:
        return
    failures = 0
    while True:
        await asyncio.sleep(60)
        if not telegram_bot:
            continue
        try:
            await asyncio.wait_for(telegram_bot.application.bot.get_me(), timeout=15.0)
            if failures > 0:
                logging.info(f"[tg-watchdog] Telegram восстановлен после {failures} неудач.")
            failures = 0
        except Exception as e:
            failures += 1
            logging.warning(f"[tg-watchdog] Telegram недоступен ({failures}/3): {e}")
            if failures >= 3:
                logging.info("[tg-watchdog] Пробую перезапустить polling...")
                try:
                    try:
                        await asyncio.wait_for(telegram_bot.application.updater.stop(), timeout=15.0)
                    except Exception as stop_err:
                        logging.warning(f"[tg-watchdog] updater.stop() не удался: {stop_err}")
                    await asyncio.wait_for(
                        telegram_bot.application.updater.start_polling(), timeout=15.0
                    )
                    logging.info("[tg-watchdog] Polling перезапущен.")
                    failures = 0
                except Exception as restart_err:
                    logging.error(f"[tg-watchdog] Перезапуск не удался: {restart_err}")


async def reconcile(client: TinkoffClient, monitor: ExecutionMonitor):
    global reconcile_done
    positions = await client.get_positions()
    if not positions:
        logging.info("[reconcile] Позиций нет.")
        reconcile_done = True
        research_logger.log_event("RECONCILE_OK", {"positions": 0})
        return

    unknown = []
    for figi, qty in positions.items():
        if figi.startswith("RUB") or figi.startswith("USD") or figi.startswith("EUR"):
            logging.info(f"[reconcile] Пропуск валютной позиции {figi}({qty})")
            continue
        pos = monitor.get_position(figi)
        if pos is None or pos.state == PositionState.CLOSED:
            unknown.append((figi, qty))

    if unknown:
        client.uncertain_state = True
        msg = "⚠️ Позиции без локальной записи: " + ", ".join(f"{f}({q})" for f, q in unknown)
        logging.error(msg)
        research_logger.log_event("RECONCILE_UNKNOWN", {"positions": unknown})
        await notify(msg)
    else:
        logging.info("[reconcile] Все позиции учтены.")
        reconcile_done = True
        research_logger.log_event("RECONCILE_OK", {"positions": len(positions)})


async def close_position(client: TinkoffClient, pos, price: float):
    direction = (
        OrderDirection.ORDER_DIRECTION_SELL if pos.direction == "BUY"
        else OrderDirection.ORDER_DIRECTION_BUY
    )
    order = await client.post_market_order(pos.figi, pos.quantity, direction)
    if order is None:
        pos.state = PositionState.UNCERTAIN
        client.uncertain_state = True
        logging.error(f"[close] {pos.ticker}: заявка не отправлена — UNCERTAIN")
        research_logger.log_event("CLOSE_FAILED", {"ticker": pos.ticker, "figi": pos.figi})
        return
    pos.close_order_id = getattr(order, 'order_id', None)
    pos.close_requested_at = datetime.now(timezone.utc).isoformat()
    logging.info(f"🔒 [CLOSE] {pos.ticker} {pos.close_reason} @ {price:.4f} → CLOSING (id={pos.close_order_id})")
    research_logger.log_event("CLOSE_SENT", {
        "ticker": pos.ticker, "figi": pos.figi, "reason": pos.close_reason,
        "order_id": pos.close_order_id, "price_before_order": price,
    })


async def process_closing(client: TinkoffClient, monitor: ExecutionMonitor):
    for pos in list(monitor.positions.values()):
        if pos.state != PositionState.CLOSING:
            continue
        await monitor.resolve_closing(pos)
        if pos.state == PositionState.CLOSED:
            exit_price = pos.execution_price or 0.0
            executed_lots = pos.executed_lots or pos.quantity
            shares = executed_lots * pos.lot_size

            # Slippage
            entry_slippage = (pos.entry_price - pos.price_before_order) * shares
            exit_slippage = (exit_price - pos.close_price_before_order) * shares
            # Для BUY: положительное = неблагоприятное
            total_slippage = entry_slippage + exit_slippage

            gross_pnl = (exit_price - pos.entry_price) * shares
            turnover = (exit_price + pos.entry_price) * shares
            commission = abs(turnover * config.COMMISSION_PER_SIDE)
            net_pnl = gross_pnl - commission

            research_logger.log_trade({
                "trade_id": pos.trade_id or uuid.uuid4().hex[:12],
                "timestamp_utc": pos.close_requested_at or "",
                "ticker": pos.ticker, "figi": pos.figi, "lot_size": pos.lot_size,
                "side": pos.direction,
                "signal_reason": pos.signal_reason,
                "signal_time": pos.signal_time,
                "price_before_order": pos.price_before_order,
                "order_send_time": pos.order_send_time,
                "order_id": pos.close_order_id or "",
                "execution_status": "FILL",
                "requested_lots": pos.quantity,
                "executed_lots": executed_lots,
                "execution_price": f"{pos.entry_price:.4f}",
                "exit_reason": pos.close_reason or "",
                "exit_price": f"{exit_price:.4f}",
                "exit_time": datetime.now(timezone.utc).isoformat(),
                "commission_rub": f"{commission:.2f}",
                "slippage_rub": f"{total_slippage:+.2f}",
                "gross_pnl_rub": f"{gross_pnl:.2f}",
                "net_pnl_rub": f"{net_pnl:.2f}",
                "error_note": "",
            })

            logging.info(
                f"✅ [CLOSED] {pos.ticker} {pos.close_reason} "
                f"entry={pos.entry_price:.4f} exit={exit_price:.4f} "
                f"lots={executed_lots} gross={gross_pnl:+.2f}₽ "
                f"commission=-{commission:.2f}₽ slippage={total_slippage:+.2f}₽ "
                f"net={net_pnl:+.2f}₽"
            )
            await notify(
                f"🔒 <b>{pos.ticker}</b> закрыто по {pos.close_reason}\n"
                f"Вход: {pos.entry_price:.4f} → Выход: {exit_price:.4f}\n"
                f"Лотов: {executed_lots} (акций: {shares})\n"
                f"Gross: {gross_pnl:+.2f} ₽\n"
                f"Комиссия: -{commission:.2f} ₽\n"
                f"Slippage: {total_slippage:+.2f} ₽\n"
                f"Net: <b>{net_pnl:+.2f} ₽</b>"
            )


async def trading_cycle(client: TinkoffClient, rm: RiskManager, sm: StrategyManager, monitor: ExecutionMonitor):
    figi_to_monitor = list(monitor.positions.keys()) or [i['figi'] for i in top_instruments]
    prices = await client.get_last_prices(figi_to_monitor)
    if not prices:
        logging.warning("Нет цен — пропуск итерации")
        return

    to_close = await monitor.check_all(prices)
    for pos in to_close:
        await close_position(client, pos, prices[pos.figi])

    await process_closing(client, monitor)

    if not is_trading_enabled():
        logging.info("[cycle] Торговля выключена (нет trading.flag).")
        return
    if client.is_uncertain():
        logging.warning("[cycle] uncertain_state=True — новые входы запрещены.")
        return
    if not reconcile_done:
        logging.warning("[cycle] reconcile не завершён.")
        return

    positions = await client.get_positions()

    for inst in top_instruments:
        figi = inst['figi']
        price = prices.get(figi)
        if not price:
            logging.info(f"{inst['ticker']}: нет цены, пропуск")
            continue
        if positions.get(figi, 0) != 0 or monitor.has_active_position(figi):
            logging.info(f"{inst['ticker']}: позиция уже открыта, пропуск")
            continue

        candles = await client.get_historical_candles(
            figi, days=config.CANDLES_LOOKBACK_DAYS,
            interval=CandleInterval.CANDLE_INTERVAL_5_MIN,
        )
        if len(candles) < config.MIN_CANDLES_FOR_SIGNAL:
            logging.info(f"{inst['ticker']}: мало свечей ({len(candles)}/{config.MIN_CANDLES_FOR_SIGNAL}), пропуск")
            continue

        df = pd.DataFrame([{
            'time': c.time,
            'open': float(c.open.units + c.open.nano / 1e9),
            'high': float(c.high.units + c.high.nano / 1e9),
            'low': float(c.low.units + c.low.nano / 1e9),
            'close': float(c.close.units + c.close.nano / 1e9),
            'volume': int(c.volume),
            'is_complete': getattr(c, 'is_complete', None),
        } for c in candles])
        df = df.set_index('time').sort_index()

        signal, reason = sm.get_signal(df, float(price), drop_incomplete=True)
        if signal == "HOLD":
            logging.info(f"{inst['ticker']}: HOLD ({reason})")
            continue
        if signal == "SELL":
            logging.info(f"{inst['ticker']}: SELL без позиции — шорт запрещён")
            continue

        lots = await rm.calculate_position_size(figi, float(price), df)
        if lots <= 0:
            logging.info(f"{inst['ticker']}: размер позиции 0, пропуск")
            continue

        info = await client.get_instrument_info(figi)
        if not info:
            continue

        trade_id = uuid.uuid4().hex[:12]
        order_send_time = datetime.now(timezone.utc).isoformat()
        research_logger.log_event("SIGNAL", {
            "trade_id": trade_id, "ticker": inst['ticker'], "figi": figi,
            "signal": signal, "reason": reason, "price_before_order": float(price),
            "requested_lots": lots, "lot_size": info['lot'],
        })

        order = await client.post_market_order(figi, lots, OrderDirection.ORDER_DIRECTION_BUY)
        if order is None:
            logging.error(f"{inst['ticker']}: заявка не отправлена — UNCERTAIN")
            client.uncertain_state = True
            continue

        status = getattr(order, 'execution_report_status', None)
        order_id = getattr(order, 'order_id', None)

        if status == OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_PARTIALLYFILL:
            logging.error(f"{inst['ticker']}: ЧАСТИЧНОЕ исполнение — UNCERTAIN")
            client.uncertain_state = True
            research_logger.log_event("PARTIAL_FILL", {"trade_id": trade_id, "order_id": order_id})
            continue
        if status != OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_FILL:
            logging.warning(f"{inst['ticker']}: не исполнено ({status})")
            continue

        state = await client.get_sandbox_order_state(order_id) if order_id else None
        exec_price = float(price)
        exec_lots = lots
        if state is not None:
            ep = getattr(state, 'executed_order_price', None)
            le = getattr(state, 'lots_executed', None)
            if le:
                exec_lots = int(le)
            if ep is not None and exec_lots > 0:
                total = float(ep.units) + float(ep.nano) / 1e9
                exec_price = total / exec_lots

        rm.record_trade(figi)
        sl_price, tp_price = rm.calculate_sl_tp(exec_price, signal, df)
        if sl_price is None or tp_price is None:
            logging.error(f"{inst['ticker']}: SL/TP не рассчитаны — UNCERTAIN")
            client.uncertain_state = True
            continue

        pos = monitor.register(
            figi=figi, ticker=inst['ticker'], direction=signal,
            entry_price=exec_price, quantity=exec_lots,
            sl_price=sl_price, tp_price=tp_price, lot_size=info['lot'],
            trade_id=trade_id, signal_reason=reason,
            signal_time=order_send_time, price_before_order=float(price),
            order_send_time=order_send_time,
        )
        pos.execution_price = exec_price
        pos.executed_lots = exec_lots

        logging.info(
            f"🚀 [BUY] {inst['ticker']} {exec_lots} лот. @ {exec_price:.4f} "
            f"SL={sl_price:.4f} TP={tp_price:.4f} (reason: {reason})"
        )
        research_logger.log_event("POSITION_OPENED", {
            "trade_id": trade_id, "ticker": inst['ticker'], "figi": figi,
            "order_id": order_id, "exec_price": exec_price,
            "exec_lots": exec_lots, "sl": sl_price, "tp": tp_price,
        })

        await notify(
            f"🚀 <b>{inst['ticker']}</b> BUY\n"
            f"Исполнено: {exec_lots} лот. @ {exec_price:.4f}\n"
            f"SL: {sl_price:.4f}\nTP: {tp_price:.4f}\n"
            f"<i>{reason}</i>"
        )


async def main():
    global telegram_bot, main_client, top_instruments, monitor_ref

    config.validate()

    if config.ENABLE_TELEGRAM and TelegramBot:
        try:
            telegram_bot = TelegramBot(
                config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID,
                config.TELEGRAM_ALLOWED_USER_ID,
                {'start_trading': start_trading, 'stop_trading': stop_trading, 'get_status': get_status},
            )
            await asyncio.wait_for(telegram_bot.application.initialize(), timeout=15.0)
            await asyncio.wait_for(telegram_bot.application.updater.start_polling(), timeout=15.0)
            logging.info("Telegram-бот запущен.")
            await notify("🤖 Tsclp_v2.1_volatile запущен. Управление — через trading.flag.")
            asyncio.create_task(_telegram_watchdog())
            logging.info("[tg-watchdog] Сторож запущен (проверка каждые 60 сек).")
        except Exception as e:
            logging.warning(f"Telegram недоступен ({e}). Робот работает БЕЗ уведомлений.")
            telegram_bot = None

    async with TinkoffClient(config.TINKOFF_API_TOKEN, config.USE_SANDBOX) as client:
        main_client = client
        rm = RiskManager(client)
        sm = StrategyManager()
        monitor = ExecutionMonitor(client)
        monitor_ref = monitor
        screener = Screener()

        instruments = await screener.select_top_volatile()
        if not instruments:
            client.uncertain_state = True
            msg = "⚠️ Скрининг не удался. Новые входы ЗАПРЕЩЕНЫ."
            logging.error(msg)
            research_logger.log_event("SCREEN_FAILED", {})
            await notify(msg)
            while True:
                await asyncio.sleep(60)

        top_instruments = instruments
        logging.info(f"[screener] Инструменты: {', '.join(i['ticker'] for i in instruments)}")
        rm.reset_daily_counts()
        await reconcile(client, monitor)

        while True:
            try:
                await trading_cycle(client, rm, sm, monitor)
            except Exception as e:
                logging.exception(f"Ошибка цикла: {e}")
                research_logger.log_event("CYCLE_ERROR", {"error": str(e)})
            await asyncio.sleep(config.TRADING_CYCLE_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Остановлено пользователем.")
    except Exception as e:
        logging.critical(f"Фатальная ошибка: {e}")