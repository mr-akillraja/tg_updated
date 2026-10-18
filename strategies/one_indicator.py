"""strategies/one_indicator.py - strengthened EMA9 slope entry rule.

This keeps the existing EMA9 crossover/slope signal as the core trigger but adds
configurable confirmation filters to reduce low-quality entries: slope strength,
price-vs-EMA distance, candle agreement, 5m trend alignment, chop filters,
RSI exhaustion guards, tick-bias confirmation, stale-feed data checks, and
cooldown rules. Browser automation and risk logic remain untouched.
"""

import math
from collections.abc import Sequence
from typing import Optional

from config import settings
from strategies.closed_candle import ema, rsi_series


def _cfg_int(name: str, default: int) -> int:
    value = getattr(settings, name, default)
    return int(value if value is not None else default)


def _cfg_float(name: str, default: float) -> float:
    value = getattr(settings, name, default)
    return float(value if value is not None else default)


def _cfg_bool(name: str, default: bool) -> bool:
    value = getattr(settings, name, default)
    return bool(value if value is not None else default)


def _clean(candles: Sequence[dict], period_seconds: int) -> Optional[str]:
    previous = None
    for candle in candles:
        try:
            ts = float(candle["ts"])
            close = float(candle["close"])
        except (KeyError, TypeError, ValueError):
            return "invalid_candle"
        if not (math.isfinite(ts) and math.isfinite(close)) or close <= 0:
            return "invalid_candle"
        if previous is not None and ts - previous != period_seconds:
            return "history_gap"
        previous = ts
    return None


def _atr(candles: Sequence[dict], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    true_ranges = []
    for index in range(1, len(candles)):
        current = candles[index]
        previous = candles[index - 1]
        high = float(current["high"])
        low = float(current["low"])
        prev_close = float(previous["close"])
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if not true_ranges:
        return 0.0
    if len(true_ranges) < period:
        return sum(true_ranges) / len(true_ranges)
    return sum(true_ranges[-period:]) / period


def _recent_range(candles: Sequence[dict], window: int = 12) -> float:
    if not candles:
        return 0.0
    recent = candles[-window:] if len(candles) > window else candles
    ranges = [max(float(candle["high"]) - float(candle["low"]), 0.0) for candle in recent]
    return sum(ranges) / len(ranges) if ranges else 0.0


def _trend_context(trend_candles: Optional[Sequence[dict]], fast_ema: int = 9,
                  slow_ema: int = 21, strong_threshold: float = 0.015) -> dict:
    if not trend_candles or len(trend_candles) < max(fast_ema, slow_ema):
        return {"bias": "neutral", "strong": False}
    closes = [float(candle["close"]) for candle in trend_candles]
    fast = ema(closes, fast_ema)
    slow = ema(closes, slow_ema)
    if len(fast) < 2 or len(slow) < 2:
        return {"bias": "neutral", "strong": False}
    bias = "bullish" if fast[-1] > slow[-1] else "bearish" if fast[-1] < slow[-1] else "neutral"
    spread_pct = abs(fast[-1] - slow[-1]) / max(abs(slow[-1]), 1e-9)
    return {"bias": bias, "strong": spread_pct > strong_threshold, "spread_pct": spread_pct}


def _ema_crossovers(values: Sequence[float], ema_period: int, window: int = 12) -> int:
    if len(values) < ema_period + 2:
        return 0
    ema_values = ema(values, ema_period)
    count = 0
    window_vals = values[-(window + 1):]
    ema_window = ema_values[-(window + 1):]
    for idx in range(1, len(window_vals)):
        prev_close = window_vals[idx - 1]
        curr_close = window_vals[idx]
        prev_ema = ema_window[idx - 1]
        curr_ema = ema_window[idx]
        if (prev_close - prev_ema) * (curr_close - curr_ema) < 0:
            count += 1
    return count


def evaluate_one_indicator(closed: Sequence[dict], period_seconds: int = 30,
                           ema_period: int = 9,
                           slope_bars: int = 2,
                           min_candles: Optional[int] = None,
                           opening: Optional[float] = None,
                           trend_candles: Optional[Sequence[dict]] = None,
                           recent_outcomes: Optional[Sequence[dict]] = None,
                           feed_stale: Optional[bool] = None,
                           forming_fresh: Optional[bool] = None,
                           tick_bias: Optional[str] = None,
                           min_filters_passed_mode: Optional[bool] = None,
                           ) -> dict:
    """Return BUY / SELL / WAIT with a specific rejection reason string."""
    ema_period = int(ema_period or _cfg_int("ONE_INDICATOR_EMA_PERIOD", 9))
    slope_bars = int(slope_bars or _cfg_int("ONE_INDICATOR_SLOPE_BARS", 2))
    min_candles = int(min_candles if min_candles is not None else _cfg_int("ONE_INDICATOR_MIN_CANDLES", 30))
    slope_atr_fraction = _cfg_float("ONE_INDICATOR_SLOPE_ATR_FRACTION", 0.15)
    slope_range_fraction = _cfg_float("ONE_INDICATOR_SLOPE_RANGE_FRACTION", 0.12)
    price_atr_fraction = _cfg_float("ONE_INDICATOR_PRICE_ATR_FRACTION", 0.25)
    body_ratio_min = _cfg_float("ONE_INDICATOR_BODY_RATIO_MIN", 0.40)
    wick_max_ratio = _cfg_float("ONE_INDICATOR_OPPOSING_WICK_MAX_RATIO", 0.35)
    trend_align = _cfg_bool("ONE_INDICATOR_TREND_ALIGN", True)
    trend_strong_threshold = _cfg_float("ONE_INDICATOR_TREND_STRONG_THRESHOLD", 0.015)
    chop_filter = _cfg_bool("ONE_INDICATOR_CHOP_FILTER", True)
    chop_window = _cfg_int("ONE_INDICATOR_CROSSOVERS_WINDOW", 12)
    max_crossovers = _cfg_int("ONE_INDICATOR_MAX_CROSSOVERS", 3)
    chop_bandwidth_max = _cfg_float("ONE_INDICATOR_CHOP_BANDWIDTH_MAX", 0.08)
    rsi_period = _cfg_int("ONE_INDICATOR_RSI_PERIOD", 14)
    rsi_overbought = _cfg_int("ONE_INDICATOR_RSI_OVERBOUGHT", 70)
    rsi_oversold = _cfg_int("ONE_INDICATOR_RSI_OVERSOLD", 30)
    tick_bias_confirm = _cfg_bool("ONE_INDICATOR_TICK_BIAS_CONFIRM", True)
    stale_max_age = _cfg_float("ONE_INDICATOR_STALE_MAX_AGE_SECONDS", 2.0)
    cooldown_candles = _cfg_int("ONE_INDICATOR_COOLDOWN_CANDLES", 3)
    consecutive_loss_halt = _cfg_int("ONE_INDICATOR_CONSECUTIVE_LOSS_HALT", 3)
    trend_neutral_allowed = _cfg_bool("ONE_INDICATOR_TREND_NEUTRAL_ALLOWED", True)
    min_filters_passed_mode = _cfg_bool("ONE_INDICATOR_MIN_FILTERS_PASSED_MODE", False) if min_filters_passed_mode is None else bool(min_filters_passed_mode)
    soft_filters_required = _cfg_int("ONE_INDICATOR_SOFT_FILTERS_REQUIRED", 3)

    bars = list(closed)
    out = {"direction": None, "reason": "", "indicator": f"EMA{ema_period}",
           "ema": None, "ema_prev": None, "close": None, "candles": len(bars),
           "period_seconds": period_seconds, "slope": None, "atr14": None,
           "avg_range": None, "rsi": None, "trend_bias": "neutral",
           "trend_strong": False, "feed_stale": feed_stale, "forming_fresh": forming_fresh,
           "rejected_by": None, "slope_to_atr_ratio": None, "slope_threshold": None,
           "soft_filters": {}, "soft_passes": 0, "soft_total": 0,
           "diagnostics": []}

    if len(bars) < min_candles:
        out["reason"] = "warmup_insufficient_closed_candles"
        return out

    problem = _clean(bars[-min(len(bars), max(min_candles, ema_period + slope_bars + 4)):], period_seconds)
    if problem:
        out["reason"] = problem
        return out

    closes = [float(candle["close"]) for candle in bars]
    avg = ema(closes, ema_period)
    if len(avg) < 2:
        out["reason"] = "insufficient_ema_history"
        return out

    ema_now = float(avg[-1])
    ema_prev = float(avg[-2]) if len(avg) >= 2 else ema_now
    close = float(closes[-1])
    atr = _atr(bars)
    avg_range = _recent_range(bars)
    slope = ema_now - ema_prev
    slope_to_atr = abs(slope) / atr if atr > 0 else 0.0
    slope_to_range = abs(slope) / avg_range if avg_range > 0 else 0.0
    threshold = max(slope_atr_fraction * atr, slope_range_fraction * avg_range, 1e-12)
    out.update(ema=ema_now, ema_prev=ema_prev, close=close, slope=slope,
               atr14=atr, avg_range=avg_range, last_closed_ts=float(bars[-1]["ts"]),
               slope_to_atr_ratio=slope_to_atr, slope_threshold=threshold,
               threshold_mode="max(atr_fraction, range_fraction)")

    if abs(slope) < threshold:
        out["reason"] = "weak_slope"
        out["rejected_by"] = "weak_slope"
        out["diagnostics"].append({"filter": "weak_slope", "slope": float(slope), "atr14": float(atr),
                                   "slope_to_atr_ratio": float(slope_to_atr), "threshold": float(threshold),
                                   "threshold_fraction": float(slope_atr_fraction), "source": "atr_or_range"})
        return out

    if close > ema_now and slope <= 0:
        out["reason"] = "close_above_ema_without_positive_slope"
        return out
    if close < ema_now and slope >= 0:
        out["reason"] = "close_below_ema_without_negative_slope"
        return out

    if abs(close - ema_now) < price_atr_fraction * atr:
        out["reason"] = "weak_price_vs_ema"
        return out

    last = bars[-1]
    range_size = max(float(last["high"]) - float(last["low"]), 1e-12)
    body = abs(float(last["close"]) - float(last["open"]))
    body_ratio = body / range_size if range_size > 1e-12 else 0.0
    wick_upper = max(0.0, float(last["high"]) - max(float(last["open"]), float(last["close"])))
    wick_lower = max(0.0, min(float(last["open"]), float(last["close"])) - float(last["low"]))
    candle_direction = "up" if float(last["close"]) > float(last["open"]) else "down" if float(last["close"]) < float(last["open"]) else "flat"
    opposing_wick = wick_upper if candle_direction == "down" else wick_lower
    if range_size > 1e-12 and body_ratio < body_ratio_min:
        out["reason"] = "weak_body"
        return out
    if range_size > 1e-12 and candle_direction != "flat" and opposing_wick / range_size > wick_max_ratio:
        out["reason"] = "candle_disagrees"
        return out

    if recent_outcomes:
        recent_losses = list(recent_outcomes)[-cooldown_candles:]
        if any(str(item.get("result", "")).upper() == "LOSS" for item in recent_losses):
            out["reason"] = "cooldown"
            return out
        consecutive = 0
        for item in reversed(list(recent_outcomes)):
            if str(item.get("result", "")).upper() == "LOSS":
                consecutive += 1
            else:
                break
        if consecutive >= consecutive_loss_halt:
            out["reason"] = "cooldown"
            return out

    if feed_stale is not None and feed_stale:
        out["reason"] = "stale_feed"
        return out
    if forming_fresh is not None and not forming_fresh:
        out["reason"] = "stale_forming_candle"
        return out

    rsi_series_values = rsi_series(closes, rsi_period)
    if rsi_series_values and rsi_series_values[-1] is not None:
        rsi_value = float(rsi_series_values[-1])
        out["rsi"] = rsi_value
        rsi_ok = not ((close > ema_now and rsi_value <= rsi_oversold) or (close < ema_now and rsi_value >= rsi_overbought))
        out["soft_filters"]["rsi_guard"] = rsi_ok
        out["soft_total"] += 1
        if rsi_ok:
            out["soft_passes"] += 1
        if (close > ema_now and rsi_value <= rsi_oversold) or (close < ema_now and rsi_value >= rsi_overbought):
            out["reason"] = "rsi_guard"
            out["rejected_by"] = "rsi_guard"
            out["diagnostics"].append({"filter": "rsi_guard", "rsi": float(rsi_value), "thresholds": {"oversold": rsi_oversold, "overbought": rsi_overbought}})
            return out
    else:
        out["reason"] = "insufficient_rsi_history"
        out["rejected_by"] = "insufficient_rsi_history"
        return out

    if trend_candles is not None:
        trend = _trend_context(trend_candles, 9, 21, trend_strong_threshold)
        out["trend_bias"] = trend.get("bias", "neutral")
        out["trend_strong"] = bool(trend.get("strong", False))
        trend_ok = True
        if trend_align:
            if slope > 0 and trend.get("bias") == "bearish":
                trend_ok = False
                out["reason"] = "trend_disagrees"
                out["rejected_by"] = "trend_disagrees"
                out["diagnostics"].append({"filter": "trend_disagrees", "bias": trend.get("bias"), "slope": float(slope)})
                return out
            if slope < 0 and trend.get("bias") == "bullish":
                trend_ok = False
                out["reason"] = "trend_disagrees"
                out["rejected_by"] = "trend_disagrees"
                out["diagnostics"].append({"filter": "trend_disagrees", "bias": trend.get("bias"), "slope": float(slope)})
                return out
            if not trend_neutral_allowed and trend.get("bias") == "neutral":
                trend_ok = False
                out["reason"] = "trend_neutral"
                out["rejected_by"] = "trend_neutral"
                out["diagnostics"].append({"filter": "trend_neutral", "bias": trend.get("bias")})
                return out
        out["soft_filters"]["trend_alignment"] = trend_ok
        out["soft_total"] += 1
        if trend_ok:
            out["soft_passes"] += 1

    if chop_filter:
        crossovers = _ema_crossovers(closes, ema_period, chop_window)
        if crossovers > max_crossovers:
            out["reason"] = "chop"
            return out
        bb = _ema_crossovers(closes, ema_period, chop_window)
        if bb and atr > 0:
            if abs(slope) < 0.5 * atr:
                out["reason"] = "chop"
                return out

    if tick_bias_confirm and tick_bias is not None:
        tick_ok = True
        if tick_bias not in {"up", "down", "flat"}:
            tick_ok = True
        elif slope > 0 and tick_bias == "down":
            tick_ok = False
            out["reason"] = "tick_bias_disagrees"
            out["rejected_by"] = "tick_bias_disagrees"
            out["diagnostics"].append({"filter": "tick_bias_disagrees", "tick_bias": tick_bias, "slope": float(slope)})
            return out
        elif slope < 0 and tick_bias == "up":
            tick_ok = False
            out["reason"] = "tick_bias_disagrees"
            out["rejected_by"] = "tick_bias_disagrees"
            out["diagnostics"].append({"filter": "tick_bias_disagrees", "tick_bias": tick_bias, "slope": float(slope)})
            return out
        out["soft_filters"]["tick_bias"] = tick_ok
        out["soft_total"] += 1
        if tick_ok:
            out["soft_passes"] += 1

    if min_filters_passed_mode and out.get("soft_total", 0) > 0 and out.get("soft_passes", 0) < min(soft_filters_required, out["soft_total"]):
        out["reason"] = "soft_filters_insufficient"
        out["rejected_by"] = "soft_filters_insufficient"
        out["diagnostics"].append({"filter": "soft_filters_insufficient", "soft_passes": out["soft_passes"], "soft_total": out["soft_total"], "required": min(soft_filters_required, out["soft_total"])})
        return out

    signal = "BUY" if slope > 0 and close > ema_now else "SELL" if slope < 0 and close < ema_now else None
    if signal is None:
        out["reason"] = "no_ema_alignment"
        return out
    if signal == "BUY" and candle_direction == "down":
        out["reason"] = "candle_disagrees"
        return out
    if signal == "SELL" and candle_direction == "up":
        out["reason"] = "candle_disagrees"
        return out

    out["direction"] = signal
    out["reason"] = f"{signal.lower()}_ema_confirmed"
    return out
