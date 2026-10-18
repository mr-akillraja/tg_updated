import asyncio
import json
from types import SimpleNamespace

import pytest

import multi_agent_orchestrator as runtime
from bot.live_feed import LiveTickFeed, candle_momentum
from strategies.one_indicator import evaluate_one_indicator


class Clock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t


def frame(pair, price, ts):
    return json.dumps([{"e": 1, "t": 2, "d": [{"p": pair, "q": price, "t": ts}]}])


def test_tick_updates_price_and_forming_candle_instantly():
    clk = Clock(1000.0)
    feed = LiveTickFeed(clock=clk)
    feed.on_frame(frame("EURUSD_OTC", 1.1000, 1000.2))
    clk.t = 1001.0
    feed.on_frame(frame("EURUSD_OTC", 1.1004, 1001.0))
    clk.t = 1002.0
    feed.on_frame(frame("EURUSD_OTC", 1.0998, 1002.0))
    tick = feed.last_tick("EUR/USD OTC", max_age=1.0)
    assert tick["price"] == 1.0998
    f = feed.forming("EUR/USD OTC", 30)
    assert (f["open"], f["high"], f["low"], f["close"]) == (1.1000, 1.1004, 1.0998, 1.0998)
    m = feed.momentum("EUR/USD OTC", 30)
    assert m["direction"] == "down" and m["up_ticks"] == 1 and m["down_ticks"] == 1


def test_rollover_fires_at_first_tick_of_new_candle():
    clk = Clock(1020.0)
    feed = LiveTickFeed(clock=clk)
    events = []
    feed.add_listener(lambda code, p, closed, opening: events.append((p, closed, opening)))
    feed.ingest_tick("EURUSD_OTC", 1.10, 1015.0)  # bucket 990 (30s)
    clk.t = 1021.0
    feed.ingest_tick("EURUSD_OTC", 1.11, 1021.0)  # bucket 1020 -> closes 990
    assert [e[0] for e in events if e[0] == 30] == [30]
    closed = [e for e in events if e[0] == 30][0][1]
    assert closed["ts"] == 990.0 and closed["close"] == 1.10
    assert sorted(e[0] for e in events) == [30, 60]


def test_closed_candle_keeps_tick_counts_and_momentum_summary():
    feed = LiveTickFeed(clock=Clock(1000.0))
    for price, ts in [(1.10, 970.0), (1.11, 990.0), (1.12, 1000.0), (1.11, 1010.0), (1.10, 1020.0)]:
        feed.ingest_tick("EURUSD_OTC", price, ts)
    closed = feed.closed("EUR/USD OTC", 30)[0]
    assert closed["up_ticks"] == 2 and closed["down_ticks"] == 1
    momentum = candle_momentum(closed)
    assert momentum["direction"] == "up"
    assert momentum["body"] == pytest.approx(0.02)
    assert momentum["body_ratio"] == pytest.approx(0.5)


def test_broker_history_overrides_tick_candles_and_ignores_forming():
    clk = Clock(2000.0)
    feed = LiveTickFeed(clock=clk)
    feed.ingest_tick("EURUSD_OTC", 1.0, 1790.0)
    feed.ingest_tick("EURUSD_OTC", 1.0, 1821.0)
    feed.merge_history("EURUSD_OTC", 30, [
        {"ts": 1770.0, "open": 1, "high": 2, "low": 0.5, "close": 1.5},
        {"ts": 1995.0 - 1995 % 30, "open": 9, "high": 9, "low": 9, "close": 9}])  # forming -> skipped
    ts = [c["ts"] for c in feed.closed("EURUSD_OTC", 30)]
    assert 1770.0 in ts and 1980.0 not in ts


def test_partial_startup_candle_merged_with_broker_history_stays_consistent(monkeypatch, tmp_path):
    clk = Clock(2000.0)
    feed = LiveTickFeed(clock=clk)
    stale = {
        "ts": 1950.0,
        "open": 1.08040,
        "high": 1.08040,
        "low": 1.08005,
        "close": 1.07995,
        "ticks": 4,
        "up_ticks": 1,
        "down_ticks": 2,
    }
    final = {
        "ts": 1950.0,
        "open": 1.07998,
        "high": 1.08003,
        "low": 1.07995,
        "close": 1.07998,
    }
    series = feed._get("EURUSDOTC", 30)
    series.closed.append(stale)
    feed.merge_history("EURUSD_OTC", 30, [final])
    closed = feed.closed("EUR/USD OTC", 30)[0]
    assert closed["open"] == pytest.approx(1.07998)
    assert closed["close"] == pytest.approx(1.07998)
    assert closed["high"] == pytest.approx(1.08003)

    s = runtime.MultiAgentTradingSystem.__new__(runtime.MultiAgentTradingSystem)
    s.entry_seconds = 30
    s._current_asset = "EUR/USD OTC"
    s._accepted_count = 0
    s.latest_decision = None
    s.bot = SimpleNamespace(live_feed=feed)
    s.supervisor = SimpleNamespace(cb=SimpleNamespace(should_stop=lambda: (False, "")))
    monkeypatch.setattr(runtime, "evaluate_one_indicator", lambda *args, **kwargs: {"direction": None, "reason": "wait_for_setup"})

    payload = asyncio.run(s._one_indicator_trade(stale, {"price": 1.07998, "recv": 2000.0}))
    assert payload["closed_candle"]["open"] == pytest.approx(1.07998)
    assert payload["closed_candle"]["close"] == pytest.approx(1.07998)
    assert payload["closed_candle"]["ticks"] == 4
    assert payload["closed_momentum"]["ticks"] == 4
    assert payload["closed_momentum"]["down_ticks"] == 2
    assert payload["closed_momentum"]["up_ticks"] == 1
    assert 0.0 <= payload["closed_momentum"]["body_ratio"] <= 1.0


def test_bad_frames_are_ignored():
    feed = LiveTickFeed(clock=Clock(1.0))
    for bad in ("not json", "[]", json.dumps([{"e": 1, "d": [{"p": "X", "q": "abc"}]}]),
                json.dumps([{"e": 10, "d": [{"p": "X", "q": 1.0, "candles": []}]}])):
        feed.on_frame(bad)
    assert feed.ticks_seen == 0


def _series(closes, step=30):
    return [{"ts": 30.0 * i, "open": c, "high": c, "low": c, "close": c} for i, c in enumerate(closes)]


def test_one_indicator_buy_sell_wait_and_gap():
    up = _series([1 + 0.001 * i for i in range(40)])
    assert evaluate_one_indicator(up)["direction"] == "BUY"
    down = _series([2 - 0.001 * i for i in range(40)])
    assert evaluate_one_indicator(down)["direction"] == "SELL"
    assert evaluate_one_indicator(up[:10])["reason"].startswith("warmup")
    gapped = up[:35] + [dict(c, ts=c["ts"] + 30) for c in up[35:]]
    assert evaluate_one_indicator(gapped)["reason"] == "history_gap"
    flat = _series([1.0] * 40)
    assert evaluate_one_indicator(flat)["direction"] is None
