"""EMA9 decision wrapper with candle-pattern confirmation.

This module keeps the EMA9 entry rule as the base signal and adds candle-pattern
confirmation when ACTIVE_STRATEGY is set to ema_pattern. The original strategy
code remains intact and is used for the legacy mode.
"""

import json
import logging
from typing import Iterable, Sequence

from config import settings
from strategies.closed_candle import ema
from strategies.candle_patterns import (
    bearish_engulfing,
    bullish_engulfing,
    hammer,
    momentum_candle,
    shooting_star,
)

_LOG = logging.getLogger("ema_pattern")


def _ema_only_decision(closed: Sequence[dict], period_seconds: int = 30):
    bars = list(closed)
    if len(bars) < 2:
        return {"direction": None, "reason": "warmup_insufficient_closed_candles", "indicator": "EMA9", "ema": None, "ema_prev": None, "close": None, "candles": len(bars), "period_seconds": period_seconds}
    closes = [float(c.get("close", 0.0)) for c in bars if isinstance(c, dict) and "close" in c]
    if len(closes) < 2:
        return {"direction": None, "reason": "warmup_insufficient_closed_candles", "indicator": "EMA9", "ema": None, "ema_prev": None, "close": closes[-1] if closes else None, "candles": len(closes), "period_seconds": period_seconds}
    ema_values = ema(closes, 9)
    ema_now = float(ema_values[-1])
    ema_prev = float(ema_values[-2])
    close = float(closes[-1])
    slope = ema_now - ema_prev
    if slope > 0 and close > ema_now:
        return {"direction": "BUY", "reason": "buy_ema_rule", "indicator": "EMA9", "ema": ema_now, "ema_prev": ema_prev, "close": close, "candles": len(closes), "period_seconds": period_seconds, "slope": slope}
    if slope < 0 and close < ema_now:
        return {"direction": "SELL", "reason": "sell_ema_rule", "indicator": "EMA9", "ema": ema_now, "ema_prev": ema_prev, "close": close, "candles": len(closes), "period_seconds": period_seconds, "slope": slope}
    return {"direction": None, "reason": "no_ema_alignment", "indicator": "EMA9", "ema": ema_now, "ema_prev": ema_prev, "close": close, "candles": len(closes), "period_seconds": period_seconds, "slope": slope}


def _pattern_on_last_candle(closed: Sequence[dict]):
    if not closed:
        return None
    if len(closed) >= 2:
        previous = closed[-2]
        current = closed[-1]
        for fn in (bullish_engulfing, bearish_engulfing):
            result = fn(previous, current)
            if result is not None:
                return result
    current = closed[-1]
    for fn in (hammer, shooting_star, momentum_candle):
        result = fn(current)
        if result is not None:
            return result
    return None


def evaluate_one_indicator(closed: Sequence[dict], period_seconds: int = 30, *args, **kwargs):
    strategy = str(getattr(settings, "ACTIVE_STRATEGY", "ema_pattern") or "ema_pattern").lower()
    if strategy == "legacy":
        from strategies.one_indicator import evaluate_one_indicator as legacy_eval
        return legacy_eval(closed, period_seconds=period_seconds, *args, **kwargs)
    if strategy == "ema_only":
        return _ema_only_decision(closed, period_seconds=period_seconds)

    bare = _ema_only_decision(closed, period_seconds=period_seconds)
    direction = bare.get("direction")
    pattern = _pattern_on_last_candle(closed)
    log_payload = {
        "ts": None if not closed else (closed[-1].get("ts") if isinstance(closed[-1], dict) else None),
        "pattern": None if pattern is None else pattern.get("name"),
        "direction": None if pattern is None else pattern.get("direction"),
        "ema_direction": direction,
        "decision": "TAKE" if direction and pattern and pattern.get("direction") == direction else "WAIT",
        "reason": "pattern_no_confirmation",
    }
    if direction is None:
        log_payload["reason"] = "pattern_no_confirmation"
        _LOG.info("[Pattern] %s", json.dumps(log_payload, sort_keys=True, default=str))
        blocked = dict(bare)
        blocked["signal"] = bare.get("direction")
        blocked["strength_bp"] = abs(float(bare.get("ema", 0.0)) - float(bare.get("ema_prev", 0.0))) / float(bare.get("close") or 1.0) * 10000.0 if bare.get("ema") is not None and bare.get("ema_prev") is not None and bare.get("close") not in (None, 0) else None
        blocked["reason"] = "pattern_no_confirmation"
        blocked["direction"] = None
        blocked["pattern"] = None
        return blocked
    if pattern is None:
        log_payload["reason"] = "pattern_no_confirmation"
        _LOG.info("[Pattern] %s", json.dumps(log_payload, sort_keys=True, default=str))
        blocked = dict(bare)
        blocked["signal"] = bare.get("direction")
        blocked["strength_bp"] = abs(float(bare.get("ema", 0.0)) - float(bare.get("ema_prev", 0.0))) / float(bare.get("close") or 1.0) * 10000.0 if bare.get("ema") is not None and bare.get("ema_prev") is not None and bare.get("close") not in (None, 0) else None
        blocked["reason"] = "pattern_no_confirmation"
        blocked["direction"] = None
        blocked["pattern"] = None
        return blocked
    if pattern.get("direction") != direction:
        log_payload["reason"] = "pattern_conflict"
        _LOG.info("[Pattern] %s", json.dumps(log_payload, sort_keys=True, default=str))
        blocked = dict(bare)
        blocked["signal"] = bare.get("direction")
        blocked["strength_bp"] = abs(float(bare.get("ema", 0.0)) - float(bare.get("ema_prev", 0.0))) / float(bare.get("close") or 1.0) * 10000.0 if bare.get("ema") is not None and bare.get("ema_prev") is not None and bare.get("close") not in (None, 0) else None
        blocked["reason"] = "pattern_conflict"
        blocked["direction"] = None
        blocked["pattern"] = pattern
        return blocked

    result = dict(bare)
    result["pattern"] = pattern
    result["reason"] = f"pattern_confirmed_{direction.lower()}"
    result["direction"] = direction
    result["signal"] = direction
    result["strength_bp"] = abs(float(result.get("ema", 0.0)) - float(result.get("ema_prev", 0.0))) / float(result.get("close") or 1.0) * 10000.0 if result.get("ema") is not None and result.get("ema_prev") is not None and result.get("close") not in (None, 0) else None
    log_payload["decision"] = "TAKE"
    log_payload["reason"] = result["reason"]
    _LOG.info("[Pattern] %s", json.dumps(log_payload, sort_keys=True, default=str))
    return result
