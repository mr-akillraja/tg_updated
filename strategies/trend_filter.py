"""Directional context from completed five-minute candles only.

The broader filter uses the existing EMA9/21 periods and EMA convention. Entry
confirmation and RSI, pullback, swing, and clearance rules remain the separate
30-second/one-minute strategy's responsibility.
"""

import math

from strategies.closed_candle import _valid_candles, ema


TIMEFRAME_SECONDS = 300
MIN_CANDLES = 24  # EMA21 is mature at both ends of the three-bar slope.


def evaluate_five_minute_trend(candles, now, direction="BUY"):
    """Filter the requested BUY or SELL side without a confidence score.

    Input must be a contiguous sequence of aligned completed five-minute OHLC
    candles, ending at the most recent expected close. A forming bar is rejected
    even when its current price appears to strengthen the trend.
    """
    if direction not in ("BUY", "SELL"):
        raise ValueError("Five-minute filter direction must be BUY or SELL")
    bars = list(candles)
    result = {
        "passed": False, "status": "unavailable", "vote": "HOLD",
        "direction": "unavailable", "reason": None, "failed_rules": [],
        "required_direction": direction,
        "closed_only": True, "timeframe_seconds": TIMEFRAME_SECONDS,
        "count": len(bars), "required": MIN_CANDLES, "closed_ts": None,
        "source": None, "close": None, "ema9": None, "ema21": None,
        "ema9_3ago": None, "ema21_3ago": None,
        "ema9_slope_3_candles": None, "ema21_slope_3_candles": None,
    }

    def blocked(reason, status):
        return dict(result, reason=reason, status=status, failed_rules=[reason])

    try:
        now = float(now)
    except (TypeError, ValueError, OverflowError):
        return blocked("invalid_evaluation_time", "invalid_data")
    if not math.isfinite(now):
        return blocked("invalid_evaluation_time", "invalid_data")
    if not _valid_candles(bars):
        return blocked("invalid_5m_candles", "invalid_data")
    if not bars:
        return blocked("5m_warmup", "insufficient_data")
    bars = [dict(candle, **{key: float(candle[key])
                           for key in ("open", "high", "low", "close", "ts")})
            for candle in bars]
    sources = {str(candle.get("source", "unspecified")) for candle in bars}
    result.update(closed_ts=bars[-1]["ts"], source=next(iter(sources)) if len(sources) == 1 else "mixed")
    if any(candle["ts"] % TIMEFRAME_SECONDS for candle in bars):
        return blocked("5m_unaligned_candles", "invalid_data")
    if any(candle["ts"] + TIMEFRAME_SECONDS > now or candle.get("closed") is False
           for candle in bars):
        return blocked("5m_forming_candle", "invalid_data")
    expected = math.floor(now / TIMEFRAME_SECONDS) * TIMEFRAME_SECONDS - TIMEFRAME_SECONDS
    if bars[-1]["ts"] != expected:
        return blocked("stale_5m_candles", "stale_data")
    if any(right["ts"] - left["ts"] != TIMEFRAME_SECONDS
           for left, right in zip(bars, bars[1:])):
        return blocked("5m_history_gap", "invalid_data")
    if len(bars) < MIN_CANDLES:
        return blocked("5m_warmup", "insufficient_data")

    closes = [candle["close"] for candle in bars]
    fast, slow = ema(closes, 9), ema(closes, 21)
    result.update(close=closes[-1], ema9=fast[-1], ema21=slow[-1],
                  ema9_3ago=fast[-4], ema21_3ago=slow[-4],
                  ema9_slope_3_candles=fast[-1] - fast[-4],
                  ema21_slope_3_candles=slow[-1] - slow[-4])
    differences = {
        "5m_close_vs_ema21": closes[-1] - slow[-1],
        "5m_ema9_vs_ema21": fast[-1] - slow[-1],
        "5m_ema9_slope_3_candles": fast[-1] - fast[-4],
        "5m_ema21_slope_3_candles": slow[-1] - slow[-4],
    }
    bullish = all(difference > 0 for difference in differences.values())
    bearish = all(difference < 0 for difference in differences.values())
    market_direction = "bullish" if bullish else "bearish" if bearish else "neutral"
    passed = bullish if direction == "BUY" else bearish
    sign = 1 if direction == "BUY" else -1
    return dict(result, passed=passed, status="pass" if passed else "fail",
                vote="BUY" if bullish else "SELL" if bearish else "NEUTRAL",
                direction=market_direction, reason=f"5m_{market_direction}",
                failed_rules=[rule for rule, difference in differences.items() if sign * difference <= 0])
