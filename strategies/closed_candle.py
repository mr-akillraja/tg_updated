"""Deterministic two-direction gates over *completed* broker candles.

The caller supplies chronological OHLC snapshots and must exclude the forming
candle. No prices, candles, or indicator values are fabricated here. Both
directions use the same indicators, confirmed pivots, and candle timestamps.
"""

import math
from collections.abc import Sequence
from datetime import datetime, timezone


TREND_MIN_CANDLES = 53  # EMA50 must also have 50 observations three bars ago.
ENTRY_MIN_CANDLES = 21
PULLBACK_WINDOW_CANDLES = 5


def ema(values: Sequence[float], period: int) -> list[float]:
    """First-observation seeded EMA, matching the existing strategy convention."""
    if not len(values):
        return []
    result = [float(values[0])]
    weight = 2.0 / (period + 1.0)
    for value in values[1:]:
        result.append(float(value) * weight + result[-1] * (1.0 - weight))
    return result


def rsi_series(values: Sequence[float], period: int = 14) -> list:
    """Wilder RSI with zero moves retained in each period's gain/loss average."""
    result = [None] * len(values)
    if len(values) <= period:
        return result
    changes = [float(right) - float(left) for left, right in zip(values, values[1:])]
    gain = sum(max(change, 0.0) for change in changes[:period]) / period
    loss = sum(max(-change, 0.0) for change in changes[:period]) / period

    def value():
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    result[period] = value()
    for index in range(period + 1, len(values)):
        change = changes[index - 1]
        gain = (gain * (period - 1) + max(change, 0.0)) / period
        loss = (loss * (period - 1) + max(-change, 0.0)) / period
        result[index] = value()
    return result


def confirmed_swings(candles: Sequence[dict]) -> tuple[list, list]:
    """Strict pivots with the existing two closed bars on either side."""
    highs, lows = [], []
    for index in range(2, len(candles) - 2):
        candle = candles[index]
        neighbours = list(candles[index - 2:index]) + list(candles[index + 1:index + 3])
        point = {"ts": candle["ts"], "index": index}
        if candle["high"] > max(other["high"] for other in neighbours):
            highs.append({**point, "price": float(candle["high"])})
        if candle["low"] < min(other["low"] for other in neighbours):
            lows.append({**point, "price": float(candle["low"])})
    return highs, lows


def _structure(highs, lows):
    if len(highs) < 2 or len(lows) < 2:
        return "insufficient"
    if highs[-1]["price"] > highs[-2]["price"] and lows[-1]["price"] > lows[-2]["price"]:
        return "HH+HL"
    if highs[-1]["price"] < highs[-2]["price"] and lows[-1]["price"] < lows[-2]["price"]:
        return "LH+LL"
    return "mixed"


def _valid_candles(candles):
    previous_ts = None
    for candle in candles:
        try:
            values = [float(candle[field]) for field in ("open", "high", "low", "close", "ts")]
        except (KeyError, TypeError, ValueError):
            return False
        opening, high, low, close, timestamp = values
        if not all(math.isfinite(value) for value in values):
            return False
        if low <= 0 or high < max(opening, close) or low > min(opening, close):
            return False
        if previous_ts is not None and timestamp <= previous_ts:
            return False
        previous_ts = timestamp
    return True


def _validate_closed_history(candles):
    return _valid_candles(candles)


def _atr14(candles: Sequence[dict], period: int = 14) -> float | None:
    """Average true range for the last completed candles."""
    if len(candles) < 2:
        return None
    true_ranges = []
    for index in range(1, len(candles)):
        current = candles[index]
        previous = candles[index - 1]
        high = float(current["high"])
        low = float(current["low"])
        prev_close = float(previous["close"])
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if len(true_ranges) < period:
        return None
    return sum(true_ranges[-period:]) / period


def evaluate_demo_continuation(trend_candles: Sequence[dict], entry_candles: Sequence[dict],
                               entry_timeframe_seconds: int = 30,
                               touch_tolerance_multiplier: float = 0.15) -> dict:
    """Experimental completed-candle continuation rule set for demo/paper mode.

    This strategy intentionally ignores the legacy EMA20/EMA50 and swing-break
    gates. It still uses completed candles only and logs the legacy diagnostics
    separately for comparison.
    """
    if entry_timeframe_seconds not in (30, 60):
        raise ValueError("Entry confirmation requires 30-second or 60-second candles")
    trend, entry = list(trend_candles), list(entry_candles)
    if not _validate_closed_history(trend) or not _validate_closed_history(entry):
        return {
            "BUY": {"direction": "BUY", "passed": False, "status": "invalid_data",
                    "reason": "invalid_ohlc_or_timestamps", "failed_rules": ["invalid_ohlc_or_timestamps"],
                    "failed_rule_count": 1, "trend_timeframe_seconds": 30,
                    "entry_timeframe_seconds": entry_timeframe_seconds, "closed_only": True,
                    "ema9": None, "ema21": None, "rsi": None, "rsi_previous": None,
                    "atr14": None, "pullback_touch_tolerance": None},
            "SELL": {"direction": "SELL", "passed": False, "status": "invalid_data",
                     "reason": "invalid_ohlc_or_timestamps", "failed_rules": ["invalid_ohlc_or_timestamps"],
                     "failed_rule_count": 1, "trend_timeframe_seconds": 30,
                     "entry_timeframe_seconds": entry_timeframe_seconds, "closed_only": True,
                     "ema9": None, "ema21": None, "rsi": None, "rsi_previous": None,
                     "atr14": None, "pullback_touch_tolerance": None},
        }
    if len(entry) < 21 or len(trend) < 21:
        return {
            "BUY": {"direction": "BUY", "passed": False, "status": "insufficient_data",
                    "reason": "insufficient_closed_candles", "failed_rules": ["insufficient_closed_candles"],
                    "failed_rule_count": 1, "trend_timeframe_seconds": 30,
                    "entry_timeframe_seconds": entry_timeframe_seconds, "closed_only": True,
                    "ema9": None, "ema21": None, "rsi": None, "rsi_previous": None,
                    "atr14": None, "pullback_touch_tolerance": None},
            "SELL": {"direction": "SELL", "passed": False, "status": "insufficient_data",
                     "reason": "insufficient_closed_candles", "failed_rules": ["insufficient_closed_candles"],
                     "failed_rule_count": 1, "trend_timeframe_seconds": 30,
                     "entry_timeframe_seconds": entry_timeframe_seconds, "closed_only": True,
                     "ema9": None, "ema21": None, "rsi": None, "rsi_previous": None,
                     "atr14": None, "pullback_touch_tolerance": None},
        }

    ec = [float(candle["close"]) for candle in entry]
    e9 = ema(ec, 9)
    e21 = ema(ec, 21)
    rsi_values = rsi_series(ec)
    atr14 = _atr14(entry, 14)
    tolerance = max(0.0, float(touch_tolerance_multiplier) * (atr14 if atr14 is not None else 0.0))
    latest = entry[-1]
    previous = entry[-2]
    current_close = float(latest["close"])
    current_open = float(latest["open"])
    previous_close = float(previous["close"])
    previous_high = float(previous["high"])
    previous_low = float(previous["low"])
    rsi_value = rsi_values[-1]
    rsi_previous = rsi_values[-2] if len(rsi_values) >= 2 else None
    touch_candidates = entry[-3:]
    pullback_touch = (
        any(abs(float(item["close"]) - e9[-1]) <= tolerance
            or abs(float(item["low"]) - e9[-1]) <= tolerance
            or abs(float(item["high"]) - e9[-1]) <= tolerance for item in touch_candidates)
        or abs(current_close - e9[-1]) <= tolerance
    )

    def build_result(direction: str):
        failures = []

        def check(condition, name):
            if not condition:
                failures.append(name)

        if direction == "BUY":
            check(e9[-1] > e21[-1], "ema9_vs_ema21")
            check(len(e21) >= 4 and e21[-1] > e21[-4], "ema21_higher_three_candles_ago")
            check(pullback_touch, "ema9_touch_within_tolerance")
            check(current_close > e9[-1], "latest_close_above_ema9")
            check(current_close > previous_high or current_close > previous_close,
                  "latest_close_above_previous_high")
            check(current_close > current_open, "latest_candle_is_bullish")
            check(45.0 <= rsi_value <= 75.0, "rsi14_band")
            check(rsi_previous is not None and rsi_value > rsi_previous, "rsi14_rising")
        else:
            check(e9[-1] < e21[-1], "ema9_vs_ema21")
            check(len(e21) >= 4 and e21[-1] < e21[-4], "ema21_lower_three_candles_ago")
            check(pullback_touch, "ema9_touch_within_tolerance")
            check(current_close < e9[-1], "latest_close_below_ema9")
            check(current_close < previous_low or current_close < previous_close,
                  "latest_close_below_previous_low")
            check(current_close < current_open, "latest_candle_is_bearish")
            check(25.0 <= rsi_value <= 65.0, "rsi14_band")
            check(rsi_previous is not None and rsi_value < rsi_previous, "rsi14_falling")

        return {
            "direction": direction,
            "passed": not failures,
            "status": "pass" if not failures else "fail",
            "reason": failures[0] if failures else "demo_continuation_confirmed",
            "failed_rules": failures,
            "failed_rule_count": len(failures),
            "trend_timeframe_seconds": 30,
            "entry_timeframe_seconds": entry_timeframe_seconds,
            "closed_only": True,
            "trend_close": float(trend[-1]["close"]),
            "entry_close": current_close,
            "ema9": e9[-1],
            "ema21": e21[-1],
            "ema21_3ago": e21[-4] if len(e21) >= 4 else None,
            "atr14": atr14,
            "rsi": rsi_value,
            "rsi_previous": rsi_previous,
            "rsi_dir": "rising" if rsi_previous is not None and rsi_value > rsi_previous else "falling" if rsi_previous is not None and rsi_value < rsi_previous else "flat",
            "previous_high": previous_high,
            "previous_low": previous_low,
            "pullback_touch_tolerance": tolerance,
            "pullback_recent_candidates": [
                {"ts": item["ts"], "close": float(item["close"]), "low": float(item["low"]), "high": float(item["high"]) }
                for item in touch_candidates
            ],
            "legacy_gate_diagnostics": "not_required_for_demo_strategy",
        }

    return {"BUY": build_result("BUY"), "SELL": build_result("SELL")}


def evaluate_both(trend_candles: Sequence[dict], entry_candles: Sequence[dict],
                  entry_timeframe_seconds: int = 60) -> dict:
    """Evaluate BUY and SELL and return JSON-safe diagnostics for *all* rules.

    Insufficient or malformed input fails closed. The strategy periods, strict
    two-sided pivot lookback, BUY RSI band 50..65 / SELL 35..50, two-times
    range cap, and obstacle clearance (BUY 1.5 / SELL 1.0) are unchanged.
    Trend swing structure is diagnostic, as in the two existing strategies.
    Entry rules can use completed 30-second or 60-second candles; changing the
    candle period does not change any indicator period or rule threshold.
    """
    if entry_timeframe_seconds not in (30, 60):
        raise ValueError("Entry confirmation requires 30-second or 60-second candles")
    entry_prefix = "30s" if entry_timeframe_seconds == 30 else "1m"
    trend, entry = list(trend_candles), list(entry_candles)
    metrics = {
        "trend_count": len(trend), "entry_count": len(entry),
        "trend_required": TREND_MIN_CANDLES, "entry_required": ENTRY_MIN_CANDLES,
        "trend_timeframe_seconds": 30, "entry_timeframe_seconds": entry_timeframe_seconds,
        "closed_only": True,
        "trend_ts": None, "entry_ts": None,
        "trend_open_at": None, "trend_close_at": None,
        "entry_open_at": None, "entry_close_at": None,
        "trend_close": None, "ema20": None, "ema50": None,
        "ema20_3ago": None, "ema50_3ago": None,
        "structure": "insufficient", "trend_swing_highs": [], "trend_swing_lows": [],
        "entry_close": None, "entry_low": None, "entry_high": None,
        "ema9": None, "ema21": None, "entry_structure": "insufficient",
        "entry_swing_highs": [], "entry_swing_lows": [],
        "rsi": None, "rsi_previous": None, "rsi_dir": "unavailable",
        "pullback_closes_ema21": [], "pullback_window_candles": PULLBACK_WINDOW_CANDLES,
        "confirmation_range": None,
        "avg_prev_10_range": None, "previous_high": None, "previous_low": None,
        "nearest_resistance": None, "resistance_distance": None,
        "nearest_support": None, "support_distance": None,
        "support_resistance_reference": "entry_close",
    }

    def direction_requirements(direction):
        multiplier = 1.5 if direction == "BUY" else 1.0
        candle_range = metrics["confirmation_range"]
        return {
            "required_rsi_band": [50.0, 65.0] if direction == "BUY" else [35.0, 50.0],
            "required_obstacle_clearance": (
                multiplier * candle_range if candle_range is not None else None),
        }

    def blocked(reason, status):
        return {direction: {**metrics, **direction_requirements(direction),
                            "direction": direction, "passed": False,
                            "status": status, "reason": reason, "failed_rules": [reason],
                            "failed_rule_count": 1}
                for direction in ("BUY", "SELL")}

    def boundary_at(timestamp):
        try:
            return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            # Human-readable metadata must not alter candle validation or gates.
            return None

    if not _valid_candles(trend) or not _valid_candles(entry):
        return blocked("invalid_ohlc_or_timestamps", "invalid_data")

    tc = [float(candle["close"]) for candle in trend]
    ec = [float(candle["close"]) for candle in entry]
    t20, t50 = ema(tc, 20), ema(tc, 50)
    e9, e21 = ema(ec, 9), ema(ec, 21)
    trend_highs, trend_lows = confirmed_swings(trend)
    entry_highs, entry_lows = confirmed_swings(entry)
    rsi_values = rsi_series(ec)
    metrics.update({
        "structure": _structure(trend_highs, trend_lows),
        "trend_swing_highs": trend_highs[-2:], "trend_swing_lows": trend_lows[-2:],
        "entry_structure": _structure(entry_highs, entry_lows),
        "entry_swing_highs": entry_highs[-2:], "entry_swing_lows": entry_lows[-2:],
    })
    if trend:
        metrics.update(trend_ts=trend[-1]["ts"], trend_close=tc[-1],
                       trend_open_at=boundary_at(trend[-1]["ts"]),
                       trend_close_at=boundary_at(float(trend[-1]["ts"]) + 30))
    if len(trend) >= 20:
        metrics["ema20"] = t20[-1]
    if len(trend) >= 23:
        metrics["ema20_3ago"] = t20[-4]
    if len(trend) >= 50:
        metrics["ema50"] = t50[-1]
    if len(trend) >= 53:
        metrics["ema50_3ago"] = t50[-4]
    if entry:
        metrics.update(entry_ts=entry[-1]["ts"], entry_close=ec[-1],
                       entry_open_at=boundary_at(entry[-1]["ts"]),
                       entry_close_at=boundary_at(float(entry[-1]["ts"]) + entry_timeframe_seconds),
                       entry_low=float(entry[-1]["low"]), entry_high=float(entry[-1]["high"]),
                       confirmation_range=float(entry[-1]["high"] - entry[-1]["low"]))
        metrics["rsi"] = rsi_values[-1]
        metrics["rsi_previous"] = rsi_values[-2] if len(entry) >= 2 else None
        if metrics["rsi"] is not None and metrics["rsi_previous"] is not None:
            delta = metrics["rsi"] - metrics["rsi_previous"]
            metrics["rsi_dir"] = "rising" if delta > 0 else "falling" if delta < 0 else "flat"
        resistance = [point["price"] for point in trend_highs if point["price"] > ec[-1]]
        support = [point["price"] for point in trend_lows if point["price"] < ec[-1]]
        if resistance:
            metrics.update(nearest_resistance=min(resistance), resistance_distance=min(resistance) - ec[-1])
        if support:
            metrics.update(nearest_support=max(support), support_distance=ec[-1] - max(support))
    if len(entry) >= 2:
        metrics.update(previous_high=float(entry[-2]["high"]), previous_low=float(entry[-2]["low"]))
    if len(entry) >= 9:
        metrics["ema9"] = e9[-1]
    if len(entry) >= 21:
        metrics["ema21"] = e21[-1]
        metrics["pullback_closes_ema21"] = [
            {"ts": entry[index]["ts"], "close": ec[index], "ema21": e21[index]}
            for index in range(len(entry) - PULLBACK_WINDOW_CANDLES, len(entry))
        ]
    if len(entry) >= 11:
        metrics["avg_prev_10_range"] = sum(float(candle["high"] - candle["low"])
                                             for candle in entry[-11:-1]) / 10.0
    if len(trend) < TREND_MIN_CANDLES or len(entry) < ENTRY_MIN_CANDLES:
        return blocked("insufficient_closed_candles", "insufficient_data")

    results = {}
    for direction, sign in (("BUY", 1), ("SELL", -1)):
        failures = []

        def check(condition, name):
            if not condition:
                failures.append(name)

        check(sign * (tc[-1] - t20[-1]) > 0, "30s_close_vs_ema20")
        check(sign * (t20[-1] - t50[-1]) > 0, "30s_ema20_vs_ema50")
        check(sign * (t20[-1] - t20[-4]) > 0 and sign * (t50[-1] - t50[-4]) > 0,
              "30s_ema_slope_3_candles")
        check(sign * (e9[-1] - e21[-1]) > 0, f"{entry_prefix}_ema9_vs_ema21")
        check(sign * (ec[-1] - e21[-1]) > 0, f"{entry_prefix}_close_vs_ema21")
        check(all(sign * (pair["close"] - pair["ema21"]) >= 0
                  for pair in metrics["pullback_closes_ema21"]), f"{entry_prefix}_pullback_close_vs_ema21")
        pullback_swings = entry_lows if sign == 1 else entry_highs
        breakout_swings = entry_highs if sign == 1 else entry_lows
        if len(pullback_swings) >= 2:
            check(sign * (pullback_swings[-1]["price"] - pullback_swings[-2]["price"]) > 0,
                  f"{entry_prefix}_pullback_swing_progression")
            extreme = metrics["entry_low"] if sign == 1 else metrics["entry_high"]
            check(sign * (extreme - pullback_swings[-2]["price"]) > 0,
                  f"{entry_prefix}_pullback_vs_prior_swing")
        elif sign == -1:
            check(entry[-3]["high"] > entry[-2]["high"] > entry[-1]["high"],
                  f"{entry_prefix}_confirmed_pullback_swings_or_descending_highs")
        else:
            check(False, f"{entry_prefix}_confirmed_pullback_swings")
        if breakout_swings:
            check(sign * (ec[-1] - breakout_swings[-1]["price"]) > 0,
                  f"{entry_prefix}_close_breaks_swing")
        elif sign == -1:
            check(entry[-3]["low"] > entry[-2]["low"] > entry[-1]["low"],
                  f"{entry_prefix}_confirmed_breakout_swing_or_descending_lows")
        else:
            check(False, f"{entry_prefix}_confirmed_breakout_swing")
        check(sign * (ec[-1] - e9[-1]) > 0, f"{entry_prefix}_confirmation_close_vs_ema9")
        previous_extreme = metrics["previous_high"] if sign == 1 else metrics["previous_low"]
        check(sign * (ec[-1] - previous_extreme) > 0,
              f"{entry_prefix}_close_breaks_previous_candle")
        check(metrics["confirmation_range"] <= 2.0 * metrics["avg_prev_10_range"],
              f"{entry_prefix}_confirmation_range")
        band = (50.0, 65.0) if sign == 1 else (35.0, 50.0)
        check(band[0] <= metrics["rsi"] <= band[1], f"{entry_prefix}_rsi14_band")
        check(metrics["rsi_dir"] == ("rising" if sign == 1 else "falling"),
              f"{entry_prefix}_rsi14_direction")
        obstacle_distance = metrics["resistance_distance"] if sign == 1 else metrics["support_distance"]
        clearance = 1.5 if sign == 1 else 1.0
        check(obstacle_distance is None or obstacle_distance > clearance * metrics["confirmation_range"],
              "30s_obstacle_clearance")
        results[direction] = {**metrics, **direction_requirements(direction),
                              "direction": direction, "passed": not failures,
                              "status": "fail" if failures else "pass",
                              "reason": failures[0] if failures else "closed_candle_setup_confirmed",
                              "failed_rules": failures, "failed_rule_count": len(failures)}
    return results
