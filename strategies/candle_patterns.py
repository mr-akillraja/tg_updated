"""Deterministic candle-pattern helpers over completed OHLC bars.

Fixed definitions used by the single-indicator wrapper:

- bullish_engulfing: previous candle bearish, current bullish, current open <= previous close and current close >= previous open, current body >= 1.0 x previous body
- bearish_engulfing: mirror of bullish_engulfing
- hammer: lower wick >= 2.0 x body, upper wick <= 0.5 x body, body/range <= 0.4 -> BUY bias
- shooting_star: mirror of hammer -> SELL bias
- momentum_candle: body/range >= 0.7 in the direction of the move -> same direction

All functions are pure and operate only on OHLC values from a closed candle.
"""

import math


def _range(candle):
    if not isinstance(candle, dict):
        return 0.0
    try:
        return float(candle["high"]) - float(candle["low"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def _body(candle):
    if not isinstance(candle, dict):
        return 0.0
    try:
        return abs(float(candle["close"]) - float(candle["open"]))
    except (KeyError, TypeError, ValueError):
        return 0.0


def bullish_engulfing(previous, current):
    if not isinstance(previous, dict) or not isinstance(current, dict):
        return None
    prev_bearish = float(previous.get("close", 0.0)) < float(previous.get("open", 0.0))
    curr_bullish = float(current.get("close", 0.0)) > float(current.get("open", 0.0))
    prev_range = _range(previous)
    curr_range = _range(current)
    if prev_range <= 0 or curr_range <= 0:
        return None
    prev_body = _body(previous)
    curr_body = _body(current)
    if not (prev_bearish and curr_bullish):
        return None
    if float(current.get("open", 0.0)) > float(previous.get("close", 0.0)):
        return None
    if float(current.get("close", 0.0)) < float(previous.get("open", 0.0)):
        return None
    if curr_body < 1.0 * prev_body:
        return None
    return {"name": "bullish_engulfing", "direction": "BUY"}


def bearish_engulfing(previous, current):
    if not isinstance(previous, dict) or not isinstance(current, dict):
        return None
    prev_bearish = float(previous.get("close", 0.0)) < float(previous.get("open", 0.0))
    curr_bearish = float(current.get("close", 0.0)) < float(current.get("open", 0.0))
    prev_range = _range(previous)
    curr_range = _range(current)
    if prev_range <= 0 or curr_range <= 0:
        return None
    prev_body = _body(previous)
    curr_body = _body(current)
    if not (prev_bearish and curr_bearish):
        return None
    if float(current.get("open", 0.0)) < float(previous.get("close", 0.0)):
        return None
    if float(current.get("close", 0.0)) > float(previous.get("open", 0.0)):
        return None
    if curr_body < 1.0 * prev_body:
        return None
    return {"name": "bearish_engulfing", "direction": "SELL"}


def hammer(candle):
    if not isinstance(candle, dict):
        return None
    open_price = float(candle.get("open", 0.0))
    high = float(candle.get("high", open_price))
    low = float(candle.get("low", open_price))
    close = float(candle.get("close", open_price))
    rng = high - low
    if rng <= 0:
        return None
    body = abs(close - open_price)
    if close <= open_price:
        return None
    lower_wick = min(open_price, close) - low
    upper_wick = high - max(open_price, close)
    if lower_wick >= 2.0 * body and upper_wick <= 0.5 * body and body / rng <= 0.4:
        return {"name": "hammer", "direction": "BUY"}
    return None


def shooting_star(candle):
    if not isinstance(candle, dict):
        return None
    open_price = float(candle.get("open", 0.0))
    high = float(candle.get("high", open_price))
    low = float(candle.get("low", open_price))
    close = float(candle.get("close", open_price))
    rng = high - low
    if rng <= 0:
        return None
    body = abs(close - open_price)
    if close >= open_price:
        return None
    upper_wick = high - max(open_price, close)
    lower_wick = min(open_price, close) - low
    if upper_wick >= 2.0 * body and lower_wick <= 0.5 * body and body / rng <= 0.4:
        return {"name": "shooting_star", "direction": "SELL"}
    return None


def momentum_candle(candle, direction=None):
    if not isinstance(candle, dict):
        return None
    open_price = float(candle.get("open", 0.0))
    high = float(candle.get("high", open_price))
    low = float(candle.get("low", open_price))
    close = float(candle.get("close", open_price))
    rng = high - low
    if rng <= 0 or not math.isfinite(rng):
        return None
    body = close - open_price
    if direction is None:
        if body > 0:
            direction = "up"
        elif body < 0:
            direction = "down"
        else:
            direction = "flat"
    ratio = abs(body) / rng
    if ratio >= 0.7 and direction == "up":
        return {"name": "momentum_candle", "direction": "BUY"}
    if ratio >= 0.7 and direction == "down":
        return {"name": "momentum_candle", "direction": "SELL"}
    return None
