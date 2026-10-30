# execution.py
# Tsclp_v2.1_volatile — мониторинг позиций.
# resolve_closing: статус "1" (Fill).
# _extract_price: деление на lots_executed.
# ДОБАВЛЕНО: close_price_before_order для расчёта slippage на выходе.

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

import config


class PositionState(str, Enum):
    OPEN = "OPEN"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    UNCERTAIN = "UNCERTAIN"


@dataclass
class TrackedPosition:
    figi: str
    ticker: str
    direction: str
    entry_price: float
    quantity: int
    sl_price: float
    tp_price: float
    lot_size: int = 1
    state: PositionState = PositionState.OPEN
    close_reason: Optional[str] = None
    close_order_id: Optional[str] = None
    close_requested_at: Optional[str] = None
    close_price_before_order: float = 0.0
    executed_lots: int = 0
    execution_price: Optional[float] = None
    trade_id: str = ""
    signal_reason: str = ""
    signal_time: str = ""
    price_before_order: float = 0.0
    order_send_time: str = ""


class ExecutionMonitor:
    def __init__(self, client):
        self.client = client
        self.positions: Dict[str, TrackedPosition] = {}

    def register(self, **kwargs) -> TrackedPosition:
        pos = TrackedPosition(**kwargs)
        self.positions[pos.figi] = pos
        logging.info(
            f"[execution] OPEN {pos.ticker} {pos.direction} "
            f"entry={pos.entry_price:.4f} sl={pos.sl_price:.4f} tp={pos.tp_price:.4f} "
            f"qty={pos.quantity} lot={pos.lot_size}"
        )
        return pos

    def get_position(self, figi: str) -> Optional[TrackedPosition]:
        return self.positions.get(figi)

    def has_active_position(self, figi: str) -> bool:
        p = self.positions.get(figi)
        return p is not None and p.state in (PositionState.OPEN, PositionState.CLOSING)

    async def check_all(self, prices: Dict[str, float]) -> List[TrackedPosition]:
        to_close: List[TrackedPosition] = []
        if not prices:
            logging.error("[execution] Нет цен — uncertain_state=True")
            self.client.uncertain_state = True
            return to_close

        for pos in list(self.positions.values()):
            if pos.state != PositionState.OPEN:
                continue
            price = prices.get(pos.figi)
            if price is None or price <= 0:
                logging.warning(f"[execution] {pos.ticker}: нет цены — позиция без контроля")
                continue
            if pos.direction == "BUY":
                if price <= pos.sl_price:
                    pos.state = PositionState.CLOSING
                    pos.close_reason = "SL"
                    pos.close_price_before_order = price
                    to_close.append(pos)
                elif price >= pos.tp_price:
                    pos.state = PositionState.CLOSING
                    pos.close_reason = "TP"
                    pos.close_price_before_order = price
                    to_close.append(pos)
        return to_close

    async def resolve_closing(self, pos: TrackedPosition):
        if not pos.close_order_id:
            pos.state = PositionState.UNCERTAIN
            logging.error(f"[execution] {pos.ticker}: CLOSING без order_id → UNCERTAIN")
            return
        try:
            state = await self.client.get_sandbox_order_state(pos.close_order_id)
            if state is None:
                pos.state = PositionState.UNCERTAIN
                logging.error(f"[execution] {pos.ticker}: state=None → UNCERTAIN")
                return

            raw_status = getattr(state, "execution_report_status", None)
            try:
                status_int = int(raw_status) if raw_status is not None else -1
            except (TypeError, ValueError):
                status_int = -1

            lots_executed = int(getattr(state, "lots_executed", 0) or 0)
            logging.info(
                f"[resolve] {pos.ticker} order={pos.close_order_id} "
                f"raw_status={raw_status!r} status_int={status_int} "
                f"lots_executed={lots_executed} qty={pos.quantity}"
            )

            if status_int == 1:
                pos.state = PositionState.CLOSED
                pos.executed_lots = lots_executed or pos.quantity
                pos.execution_price = self._extract_price(state, pos.executed_lots)
                logging.info(f"[execution] CLOSED {pos.ticker} @ {pos.execution_price}")
            elif status_int == 5:
                pos.executed_lots = lots_executed
                if lots_executed >= pos.quantity:
                    pos.state = PositionState.CLOSED
                    pos.execution_price = self._extract_price(state, pos.executed_lots)
                else:
                    pos.state = PositionState.UNCERTAIN
                logging.warning(f"[execution] {pos.ticker}: PARTIALLYFILL {lots_executed}/{pos.quantity}")
            elif status_int in (2, 3):
                pos.state = PositionState.OPEN
                pos.close_order_id = None
                logging.warning(f"[execution] {pos.ticker}: закрытие отклонено/отменено — снова OPEN")
            else:
                pos.state = PositionState.UNCERTAIN
                logging.warning(f"[execution] {pos.ticker}: статус {status_int} → UNCERTAIN")
        except Exception as e:
            logging.error(f"[execution] {pos.ticker}: ошибка проверки статуса: {e}")
            pos.state = PositionState.UNCERTAIN

    @staticmethod
    def _extract_price(state, lots_executed: int) -> Optional[float]:
        """Возвращает цену за лот. API может вернуть сумму — делим на lots_executed."""
        try:
            ep = getattr(state, "executed_order_price", None)
            if ep is not None:
                units = getattr(ep, "units", 0)
                nano = getattr(ep, "nano", 0)
                total = float(units) + float(nano) / 1e9
                if lots_executed > 0:
                    return total / lots_executed
                return total
        except Exception:
            pass
        return None

    def active_count(self) -> int:
        return sum(
            1 for p in self.positions.values()
            if p.state in (PositionState.OPEN, PositionState.CLOSING)
        )