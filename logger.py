# logger.py
# Tsclp_v2.1_volatile — журнал событий и сделок.
# CSV для итоговых сделок, JSONL для детальной истории.
# НЕ перезаписывает накопленные данные.

import csv
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import config

EVENTS_FILE = "research_events.jsonl"
TRADES_FILE = "research_trades.csv"

TRADE_FIELDS = [
    "trade_id", "timestamp_utc", "ticker", "figi", "lot_size",
    "side", "signal_reason", "signal_time",
    "price_before_order", "order_send_time", "order_id",
    "execution_status", "requested_lots", "executed_lots",
    "execution_price", "exit_reason", "exit_price", "exit_time",
    "commission_rub", "slippage_rub", "gross_pnl_rub", "net_pnl_rub",
    "error_note",
]


def _ensure_files():
    if not os.path.isfile(EVENTS_FILE):
        with open(EVENTS_FILE, "w", encoding="utf-8"):
            pass
    if not os.path.isfile(TRADES_FILE):
        with open(TRADES_FILE, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=TRADE_FIELDS, delimiter=";")
            writer.writeheader()


def log_event(event_type: str, payload: dict):
    """Записывает событие в JSONL. Добавляет timestamp и версию."""
    _ensure_files()
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        "config_profile": getattr(config, "PROFILE", "?"),
        "commission_mode": getattr(config, "COMMISSION_MODE", "?"),
        **payload,
    }
    try:
        with open(EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logging.error(f"[logger] Не удалось записать событие: {e}")


def log_trade(trade: dict):
    """Записывает сделку в CSV. Не перезаписывает файл."""
    _ensure_files()
    # Заполняем отсутствующие поля пустыми строками
    row = {field: trade.get(field, "") for field in TRADE_FIELDS}
    try:
        with open(TRADES_FILE, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=TRADE_FIELDS, delimiter=";")
            writer.writerow(row)
    except Exception as e:
        logging.error(f"[logger] Не удалось записать сделку: {e}")