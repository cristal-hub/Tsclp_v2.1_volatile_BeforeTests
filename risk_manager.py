# risk_manager.py
# Tsclp_v2.1_volatile — управление рисками.
#
# ИСПРАВЛЕНИЕ: единицы quantity задаются явно через config.POSITION_QUANTITY_IS_LOTS.
# True  — pos.quantity в ЛОТАХ (умножать на lot НЕ нужно).
# False — pos.quantity в ШТУКАХ (умножать на lot нужно).
# Торговая логика (размер, SL/TP, комиссия) НЕ изменена.

import logging
from typing import Optional, Tuple
from datetime import date

import pandas as pd
import pandas_ta_classic as ta

import config


class RiskManager:
    def __init__(self, tinkoff_client):
        self.tinkoff_client = tinkoff_client
        self.daily_trade_counts = {}
        self.last_reset_date = date.today()

    def reset_daily_counts(self):
        self.daily_trade_counts = {}
        self.last_reset_date = date.today()

    def _maybe_reset_by_date(self):
        if date.today() != self.last_reset_date:
            self.reset_daily_counts()

    async def calculate_position_size(self, figi: str, last_price: float,
                                      candles_df: Optional[pd.DataFrame] = None) -> int:
        self._maybe_reset_by_date()

        if self.daily_trade_counts.get(figi, 0) >= config.MAX_TRADES_PER_INSTRUMENT_PER_DAY:
            return 0

        try:
            account_balance = await self.tinkoff_client.get_account_balance()
            if account_balance <= 0:
                return 0

            risk_amount = account_balance * config.RISK_PER_TRADE
            if risk_amount <= 0:
                return 0

            instrument_info = await self.tinkoff_client.get_instrument_info(figi)
            if not instrument_info:
                return 0

            lot_size = instrument_info['lot']
            if not lot_size or lot_size <= 0:
                return 0

            lot_cost = last_price * lot_size
            if lot_cost <= 0:
                return 0

            base_risk = last_price * max(config.STOP_LOSS_PERCENT, config.MIN_STOP_LOSS_PERCENT)
            friction = last_price * (config.COMMISSION_PER_SIDE * 2 + config.SLIPPAGE_PERCENT)
            per_share_risk = base_risk + friction

            atr_risk = self._calculate_volatility_risk(candles_df)
            if atr_risk is not None:
                atr_cap = last_price * config.MAX_ATR_STOP_PERCENT
                atr_risk = min(atr_risk, atr_cap)
                per_share_risk = max(per_share_risk, atr_risk + friction)

            if per_share_risk <= 0:
                return 0

            position_size_by_risk = int(risk_amount / (per_share_risk * lot_size))
            if position_size_by_risk <= 0:
                return 0

            max_position_value = account_balance * config.MAX_POSITION_SHARE_PER_INSTRUMENT
            position_size_by_capital = int(max_position_value / lot_cost)
            if position_size_by_capital <= 0:
                return 0

            positions = await self.tinkoff_client.get_positions()
            current_exposure = 0.0
            for f, qty in positions.items():
                info = await self.tinkoff_client.get_instrument_info(f)
                prices = await self.tinkoff_client.get_last_prices([f])
                p = prices.get(f, 0)
                if not info or not p:
                    continue
                # Явная семантика: см. config.POSITION_QUANTITY_IS_LOTS
                if config.POSITION_QUANTITY_IS_LOTS:
                    shares = qty * info['lot']  # qty в ЛОТАХ → умножаем на lot для штук
                else:
                    shares = qty                # qty в ШТУКАХ → уже штуки
                current_exposure += shares * p

            max_total = account_balance * config.MAX_TOTAL_EXPOSURE
            if current_exposure + lot_cost * position_size_by_capital > max_total:
                allowed_value = max(0.0, max_total - current_exposure)
                position_size_by_capital = int(allowed_value / lot_cost)

            position_size = min(position_size_by_risk, position_size_by_capital)
            return position_size if position_size > 0 else 0
        except Exception as e:
            logging.error(f"Ошибка расчёта позиции {figi}: {e}")
            return 0

    def _calculate_volatility_risk(self, candles_df: Optional[pd.DataFrame]) -> Optional[float]:
        if candles_df is None or candles_df.empty:
            return None
        if not {'high', 'low', 'close'}.issubset(candles_df.columns):
            return None
        period = config.ATR_PERIOD_INTRADAY
        if len(candles_df) < period:
            return None
        try:
            atr_series = ta.atr(
                high=candles_df['high'], low=candles_df['low'],
                close=candles_df['close'], length=period,
            )
            if atr_series is None or atr_series.empty:
                return None
            atr_val = atr_series.iloc[-1]
            if pd.isna(atr_val) or atr_val <= 0:
                return None
            return float(atr_val) * config.ATR_STOP_MULTIPLIER
        except Exception as e:
            logging.error(f"ATR risk error: {e}")
            return None

    def calculate_sl_tp(self, entry_price: float, direction: str,
                        candles_df: Optional[pd.DataFrame] = None) -> Tuple[Optional[float], Optional[float]]:
        if entry_price <= 0:
            return None, None
        base_stop_distance = entry_price * max(config.STOP_LOSS_PERCENT, config.MIN_STOP_LOSS_PERCENT)
        atr_risk = self._calculate_volatility_risk(candles_df)
        if atr_risk is not None:
            atr_cap = entry_price * config.MAX_ATR_STOP_PERCENT
            atr_risk = min(atr_risk, atr_cap)
            base_stop_distance = max(base_stop_distance, atr_risk)
        take_profit_distance = base_stop_distance * config.TAKE_PROFIT_RATIO
        if direction == "BUY":
            sl = entry_price - base_stop_distance
            tp = entry_price + take_profit_distance
            if sl >= entry_price:
                return None, None
        elif direction == "SELL":
            sl = entry_price + base_stop_distance
            tp = entry_price - take_profit_distance
            if sl <= entry_price:
                return None, None
        else:
            return None, None
        if sl <= 0 or tp <= 0:
            return None, None
        return sl, tp

    def record_trade(self, figi: str):
        self.daily_trade_counts[figi] = self.daily_trade_counts.get(figi, 0) + 1