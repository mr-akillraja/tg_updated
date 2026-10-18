import json

from bot.trade_governor import TradeGovernor
from strategies.candle_patterns import (
    bearish_engulfing,
    bullish_engulfing,
    hammer,
    momentum_candle,
    shooting_star,
)
from bot.live_feed import LiveTickFeed


def test_candle_pattern_examples():
    bearish_prev = {"open": 1.10, "high": 1.11, "low": 1.09, "close": 1.095}
    bearish_cur = {"open": 1.10, "high": 1.12, "low": 1.08, "close": 1.08}
    assert bearish_engulfing(bearish_prev, bearish_cur) == {"name": "bearish_engulfing", "direction": "SELL"}

    bullish_prev = {"open": 1.12, "high": 1.13, "low": 1.08, "close": 1.11}
    bullish_cur = {"open": 1.10, "high": 1.16, "low": 1.08, "close": 1.14}
    assert bullish_engulfing(bullish_prev, bullish_cur) == {"name": "bullish_engulfing", "direction": "BUY"}

    hammer_candle = {"open": 1.10, "high": 1.1055, "low": 1.09, "close": 1.105}
    assert hammer(hammer_candle) == {"name": "hammer", "direction": "BUY"}

    star = {"open": 1.10, "high": 1.12, "low": 1.095, "close": 1.095}
    assert shooting_star(star) == {"name": "shooting_star", "direction": "SELL"}

    strong_up = {"open": 1.10, "high": 1.13, "low": 1.095, "close": 1.13}
    assert momentum_candle(strong_up, "up") == {"name": "momentum_candle", "direction": "BUY"}
    assert momentum_candle({"open": 1.10, "high": 1.12, "low": 1.09, "close": 1.09}, "down") is None

    zero_range = {"open": 1.10, "high": 1.10, "low": 1.10, "close": 1.10}
    assert bearish_engulfing(zero_range, zero_range) is None
    assert momentum_candle(zero_range, "up") is None


def test_governor_seconds_since_last_uses_null_in_json_output():
    g = TradeGovernor(max_per_hour=2, min_spacing=10, percentile=90, window=5, min_history=1, max_per_day=3, state_path=None)
    decision = g.evaluate(0.5, now=1000.0)
    assert decision["seconds_since_last"] is None
    payload = json.dumps({"seconds_since_last": decision["seconds_since_last"]})
    assert "Infinity" not in payload
    assert json.loads(payload)["seconds_since_last"] is None


def test_merge_history_preserves_tick_stats():
    feed = LiveTickFeed(clock=lambda: 2000.0)
    feed.ingest_tick("EURUSD_OTC", 1.0, 1790.0)
    feed.ingest_tick("EURUSD_OTC", 1.0, 1821.0)
    feed._get("EURUSDOTC", 30).closed.append({
        "ts": 1800.0,
        "open": 1.0,
        "high": 1.1,
        "low": 0.9,
        "close": 1.05,
        "ticks": 6,
        "up_ticks": 3,
        "down_ticks": 2,
        "source": "live_ticks",
    })
    feed.merge_history("EURUSD_OTC", 30, [{
        "ts": 1800.0,
        "open": 1.02,
        "high": 1.06,
        "low": 0.98,
        "close": 1.04,
    }])
    candle = next(c for c in feed.closed("EURUSD_OTC", 30) if c["ts"] == 1800.0)
    assert candle["ticks"] == 6
    assert candle["up_ticks"] == 3
    assert candle["down_ticks"] == 2
