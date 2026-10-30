# strategy.py
# Tsclp_v2.0 — стратегия.
#
# ИЗМЕНЕНИЯ ПО СРАВНЕНИЮ С v1.0 (документированы):
# 1. Гистерезис ADX: в v1.0 режим определялся по 2 последним значениям ADX > 25,
#    _regime_streak считался, но не использовался. В v2.0 режим меняется только
#    после ADX_HYSTERESIS_CANDLES подряд подтверждений. Это МОЖЕТ изменить сигналы.
# 2. Фильтр MA100: в v1.0 при len < TREND_FILTER_MA_PERIOD возвращал True
#    (фильтр пропускал). В v2.0 возвращает False → сигнал запрещён (HOLD).
# 3. Незакрытая свеча: параметр drop_incomplete. По умолчанию False
#    (не фильтруем), пока не подтверждено поле is_complete в SDK.
# 4. Сигналы входа/выхода (MA 9/21, BB+RSI, ADX 25, объём 1.5x) НЕ изменены.

import logging
from typing import Optional, Tuple

import pandas as pd
import pandas_ta_classic as ta

import config


class StrategyManager:
    def __init__(self):
        self.min_candles_required = config.MIN_CANDLES_FOR_SIGNAL
        self._last_regime = "UNKNOWN"
        self._regime_streak = 0

    def get_market_regime(self, candles_df: pd.DataFrame) -> str:
        """
        Определяет режим рынка по ADX с гистерезисом.

        Исходное поведение v1.0: TREND, если последние 2 значения ADX > порога.
        Новое поведение v2.0: режим меняется только после ADX_HYSTERESIS_CANDLES
        подряд идущих значений, подтверждающих новый режим.
        """
        if candles_df is None or candles_df.empty:
            return "UNKNOWN"
        try:
            if len(candles_df) < config.ADX_PERIOD + 1:
                return "UNKNOWN"

            adx_data = ta.adx(
                high=candles_df['high'],
                low=candles_df['low'],
                close=candles_df['close'],
                length=config.ADX_PERIOD,
            )
            if adx_data is None or adx_data.empty:
                return "UNKNOWN"

            adx_col = f"ADX_{config.ADX_PERIOD}"
            if adx_col not in adx_data.columns:
                candidates = adx_data.filter(like='ADX').columns
                if len(candidates) == 0:
                    return "UNKNOWN"
                adx_col = candidates[0]

            recent = adx_data[adx_col].dropna().tail(config.ADX_HYSTERESIS_CANDLES)
            if len(recent) < config.ADX_HYSTERESIS_CANDLES:
                return "UNKNOWN"

            above = all(v > config.ADX_TREND_THRESHOLD for v in recent)
            candidate = "TREND" if above else "RANGE"

            if candidate == self._last_regime:
                self._regime_streak += 1
                return self._last_regime

            self._regime_streak += 1
            if self._regime_streak >= config.ADX_HYSTERESIS_CANDLES:
                self._last_regime = candidate
                self._regime_streak = 0
                return self._last_regime

            if self._last_regime == "UNKNOWN":
                self._last_regime = candidate
                self._regime_streak = 0
                return self._last_regime
            return self._last_regime

        except Exception as e:
            logging.error(f"ADX error: {e}")
            return "UNKNOWN"

    def get_signal(
        self,
        candles_df: pd.DataFrame,
        last_price: float,
        drop_incomplete: bool = False,
    ) -> Tuple[str, str]:
        """
        Возвращает (signal, reason).
        signal ∈ {"BUY", "SELL", "HOLD"}.

        drop_incomplete: если True — отбрасывает последнюю свечу, если
        в DataFrame есть колонка 'is_complete' и её значение False.
        По умолчанию False (не фильтруем), пока не подтверждено в SDK.
        """
        prepared = self._prepare_dataframe(candles_df, drop_incomplete=drop_incomplete)
        if prepared is None or len(prepared) < self.min_candles_required:
            return "HOLD", "Недостаточно данных"

        regime = self.get_market_regime(prepared)

        if regime == "TREND":
            signal, reason = self._ma_crossover_signal(prepared)
        elif regime == "RANGE":
            signal, reason = self._bb_rsi_signal(prepared)
        else:
            return "HOLD", "Режим не определён"

        if signal in {"BUY", "SELL"}:
            ok, why = self._volume_confirmation(prepared)
            if not ok:
                return "HOLD", f"Отклонено объёмом ({why})"
        return signal, reason

    def _trend_filter_ok(self, candles_df: pd.DataFrame, signal: str) -> bool:
        """
        v1.0: при len < TREND_FILTER_MA_PERIOD возвращал True (фильтр пропускал).
        v2.0: при len < TREND_FILTER_MA_PERIOD возвращает False (сигнал запрещён).
        Это соответствует ТЗ: "При недостатке данных для MA100 явно запрещай сигнал".
        """
        if len(candles_df) < config.TREND_FILTER_MA_PERIOD:
            return False
        ma = ta.sma(candles_df['close'], length=config.TREND_FILTER_MA_PERIOD)
        if ma is None or ma.empty or pd.isna(ma.iloc[-1]):
            return False
        last_ma = ma.iloc[-1]
        last_close = candles_df['close'].iloc[-1]
        if signal == "BUY" and last_close < last_ma:
            return False
        if signal == "SELL" and last_close > last_ma:
            return False
        return True

    def _ma_crossover_signal(self, candles_df: pd.DataFrame) -> Tuple[str, str]:
        try:
            if len(candles_df) < config.MA_SLOW_PERIOD + 2:
                return "HOLD", "Мало данных для MA"
            fast = ta.sma(candles_df['close'], length=config.MA_FAST_PERIOD)
            slow = ta.sma(candles_df['close'], length=config.MA_SLOW_PERIOD)
            if fast is None or slow is None:
                return "HOLD", "MA не рассчитаны"
            lf, ls = fast.iloc[-1], slow.iloc[-1]
            pf, ps = fast.iloc[-2], slow.iloc[-2]
            if any(pd.isna(x) for x in (lf, ls, pf, ps)):
                return "HOLD", "MA NaN"

            # --- ОТЛАДКА: удалить после диагностики ---
            logging.info(
                f"[DEBUG MA] fast_prev={pf:.4f} fast_last={lf:.4f} "
                f"slow_prev={ps:.4f} slow_last={ls:.4f} len={len(candles_df)}"
            )
            # --- КОНЕЦ ОТЛАДКИ ---
            if pf < ps and lf > ls:
                if not self._trend_filter_ok(candles_df, "BUY"):
                    return "HOLD", "MA↑, но фильтр MA100 не пройден"
                return "BUY", f"MA({config.MA_FAST_PERIOD}/{config.MA_SLOW_PERIOD}) ↑"
            if pf > ps and lf < ls:
                if not self._trend_filter_ok(candles_df, "SELL"):
                    return "HOLD", "MA↓, но фильтр MA100 не пройден"
                return "SELL", f"MA({config.MA_FAST_PERIOD}/{config.MA_SLOW_PERIOD}) ↓"
            return "HOLD", "Нет пересечения MA"
        except Exception as e:
            logging.error(f"MA error: {e}")
            return "HOLD", "Ошибка MA"

    def _bb_rsi_signal(self, candles_df: pd.DataFrame) -> Tuple[str, str]:
        try:
            if len(candles_df) < config.BB_PERIOD + config.RSI_PERIOD:
                return "HOLD", "Мало данных для BB+RSI"
            bb = ta.bbands(candles_df['close'], length=config.BB_PERIOD, std=config.BB_STD_DEV)
            rsi = ta.rsi(candles_df['close'], length=config.RSI_PERIOD)
            if bb is None or rsi is None or bb.empty or rsi.empty:
                return "HOLD", "BB/RSI не рассчитаны"

            bbl_cols = bb.filter(like='BBL').columns
            bbu_cols = bb.filter(like='BBU').columns
            if len(bbl_cols) == 0 or len(bbu_cols) == 0:
                return "HOLD", "Колонки BB не найдены"

            lower = bb[bbl_cols[0]].iloc[-1]
            upper = bb[bbu_cols[0]].iloc[-1]
            rsi_val = rsi.iloc[-1]
            last_close = candles_df['close'].iloc[-1]

            if any(pd.isna(x) for x in (lower, upper, rsi_val)):
                return "HOLD", "BB/RSI NaN"

            if last_close < lower and rsi_val < config.RSI_OVERSOLD:
                if not self._trend_filter_ok(candles_df, "BUY"):
                    return "HOLD", f"BB↓ RSI={rsi_val:.1f}, фильтр MA100 не пройден"
                return "BUY", f"BB↓ RSI={rsi_val:.1f}<{config.RSI_OVERSOLD}"
            if last_close > upper and rsi_val > config.RSI_OVERBOUGHT:
                if not self._trend_filter_ok(candles_df, "SELL"):
                    return "HOLD", f"BB↑ RSI={rsi_val:.1f}, фильтр MA100 не пройден"
                return "SELL", f"BB↑ RSI={rsi_val:.1f}>{config.RSI_OVERBOUGHT}"
            return "HOLD", "Нет сигнала BB+RSI"
        except Exception as e:
            logging.error(f"BB+RSI error: {e}")
            return "HOLD", "Ошибка BB+RSI"

    def _prepare_dataframe(
        self,
        candles_df: pd.DataFrame,
        drop_incomplete: bool = False,
    ) -> Optional[pd.DataFrame]:
        if candles_df is None or candles_df.empty:
            return None
        df = candles_df.copy()
        df = df.sort_index()
        df = df[~df.index.duplicated(keep='last')]
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=['high', 'low', 'close'])

        # Фильтр незакрытой свечи — только если явно запрошено и колонка есть.
        # В v2.0 по умолчанию False: не делаем неподтверждённых предположений.
        if drop_incomplete and 'is_complete' in df.columns and not df.empty:
            if not bool(df['is_complete'].iloc[-1]):
                df = df.iloc[:-1]

        return df if not df.empty else None

    def _volume_confirmation(self, candles_df: pd.DataFrame) -> Tuple[bool, Optional[str]]:
        if 'volume' not in candles_df.columns or candles_df['volume'].isna().all():
            return False, "нет данных об объёме"
        window = min(config.VOLUME_CONFIRMATION_WINDOW, len(candles_df))
        if window < 3:
            return False, "мало свечей для объёма"
        recent = candles_df['volume'].tail(window)
        avg = recent[:-1].mean()
        last = recent.iloc[-1]
        if avg <= 0:
            return False, "avg volume = 0"
        if last >= avg * config.VOLUME_SPIKE_FACTOR:
            return True, None
        return False, f"объём {last:.0f} < {avg * config.VOLUME_SPIKE_FACTOR:.0f}"