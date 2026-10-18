"""Regression tests for provenance, candle finalization, and real-data warmup."""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from agents.agent_multi_timeframe import CandleBuilder, MultiTimeframeAgent, _rsi
from data import fetcher


def history(count=180, period=60, start=0):
    frame = pd.DataFrame({"open": np.full(count, 1.1), "high": np.full(count, 1.2),
                          "low": np.full(count, 1.0), "close": np.full(count, 1.15)},
                         index=pd.to_datetime(np.arange(count) * period + start, unit="s", utc=True))
    frame.attrs["timeframe_seconds"] = period
    return frame


def test_m1_seed_never_populates_30s_and_preserves_timestamps(caplog):
    agent = MultiTimeframeAgent("EUR/USD OTC")
    frame = history()
    with caplog.at_level("INFO"):
        agent.seed_from_dataframe(frame, source="verified_test_history", now=10800)
    assert len(agent.builders[30]) == 0
    assert len(agent.builders[60]) == 70
    assert agent.builders[60].latest_closed_ts == frame.index[-1].timestamp()
    assert len(agent.builders[300]) == 36
    assert "timeframe=30s source=broker_browser_sample closed_count=0" in caplog.text
    assert "timeframe=60s source=verified_test_history closed_count=70" in caplog.text


def test_native_30s_seed_requires_matching_resolution():
    agent = MultiTimeframeAgent("EUR/USD OTC")
    with pytest.raises(ValueError, match="resolution"):
        agent.seed_from_dataframe(history(), timeframe=30)
    frame = history(50, period=30)
    agent.seed_from_dataframe(frame, timeframe=30, source="broker_native_fixture", now=1500)
    assert len(agent.builders[30]) == 50
    assert len(agent.builders[60]) == 0
    assert agent.builders[30].latest_closed_ts == 1470


def test_seed_rejects_untimestamped_and_unclosed_bars():
    agent = MultiTimeframeAgent("test")
    with pytest.raises(ValueError, match="DatetimeIndex"):
        agent.seed_from_dataframe(history().reset_index(drop=True))
    agent.seed_from_dataframe(history(3), now=125)
    assert agent.builders[60].get_closes().size == 2
    assert agent.builders[60].latest_closed_ts == 60
    assert len(agent.builders[300]) == 0


def test_five_minute_seed_requires_complete_aligned_minutes():
    agent = MultiTimeframeAgent("test")
    agent.seed_from_dataframe(history(12, start=60), now=780)
    bars = agent.builders[300].closed_snapshot()
    assert [bar["ts"] for bar in bars] == [300]


def test_seed_uses_only_contiguous_tail_after_missing_bar():
    agent = MultiTimeframeAgent("test")
    frame = history(10).drop(pd.Timestamp(300, unit="s", tz="UTC"))
    agent.seed_from_dataframe(frame, now=600)
    assert [bar["ts"] for bar in agent.builders[60].closed_snapshot()] == [360, 420, 480, 540]


def test_forming_candle_is_invisible_and_closed_snapshot_is_detached():
    builder = CandleBuilder(30)
    for ts in range(30):
        assert builder.add_tick(1 + ts / 1000, ts) is False
    assert len(builder) == 0
    assert builder.get_closes().size == 0
    assert builder.add_tick(9, 30) is True
    assert len(builder) == 1
    assert builder.get_opens().tolist() == [1]
    assert builder.get_closes().tolist() == [1.029]
    assert builder.get_highs().tolist() == [1.029]
    assert builder.get_lows().tolist() == [1]
    snapshot = builder.closed_snapshot()
    snapshot[0]["close"] = 100
    assert builder.get_closes().tolist() == [1.029]


def test_startup_partial_bar_is_dropped():
    builder = CandleBuilder(30)
    for ts in range(17, 60):
        assert builder.add_tick(1.1, ts) is False
    assert builder.add_tick(1.2, 60) is True
    assert [bar["ts"] for bar in builder.closed_snapshot()] == [30]


def test_quote_gap_clears_history_and_requires_a_new_complete_bar():
    builder = CandleBuilder(30)
    for ts in range(61):
        builder.add_tick(1.1, ts)
    assert len(builder) == 2
    assert builder.add_tick(1.2, 70) is False
    assert len(builder) == 0
    for ts in range(71, 120):
        assert builder.add_tick(1.2, ts) is False
    assert builder.add_tick(1.3, 120) is True
    assert builder.latest_closed_ts == 90


def test_invalid_and_out_of_order_quotes_cannot_mutate_a_bar():
    builder = CandleBuilder(30)
    builder.add_tick(1.1, 0)
    builder.add_tick(1.2, 1)
    for price, ts in [(99, 0.5), (99, 1), (float("nan"), 2), (-2, 2), (2, float("inf"))]:
        assert builder.add_tick(price, ts) is False
    for ts in range(2, 31):
        builder.add_tick(1.1, ts)
    assert builder.get_highs().tolist() == [1.2]


def test_forming_snapshot_updates_geometry_without_publishing_a_closed_bar():
    builder = CandleBuilder(30)
    builder.add_tick(1.10, 0)
    builder.add_tick(1.14, 1)
    builder.add_tick(1.08, 2)
    builder.add_tick(1.12, 3)
    snapshot = builder.forming_snapshot(3.5)
    assert snapshot["open"] == 1.10
    assert snapshot["high"] == 1.14
    assert snapshot["low"] == 1.08
    assert snapshot["close"] == snapshot["live_price"] == 1.12
    assert snapshot["body"] == pytest.approx(0.02)
    assert snapshot["upper_wick"] == pytest.approx(0.02)
    assert snapshot["lower_wick"] == pytest.approx(0.02)
    assert snapshot["direction"] == "bullish"
    assert snapshot["remaining_seconds"] == 26.5
    assert snapshot["provisional"] and snapshot["complete_from_open"]
    assert not snapshot["stale"]
    assert snapshot["observed_at"] == 3
    snapshot["high"] = 99
    assert builder.forming_snapshot(4)["high"] == 1.14
    assert builder.get_closes().size == 0


def test_forming_snapshot_marks_partial_and_stale_observations():
    builder = CandleBuilder(30)
    assert builder.forming_snapshot(0) is None
    builder.add_tick(1.1, 17)
    assert builder.forming_snapshot(17)["complete_from_open"] is False
    assert builder.forming_snapshot(23)["stale"] is True
    snapshot = builder.forming_snapshot(30)
    assert snapshot["stale"] is True
    assert snapshot["remaining_seconds"] == 0
    assert snapshot["observed_at"] == 17


def test_native_forming_overlay_never_closes_or_invalidates_seeded_history():
    agent = MultiTimeframeAgent("test")
    agent.seed_from_dataframe(history(60, period=30), timeframe=30,
                              source="broker_native_fixture", now=1800)
    builder = agent.builders[30]
    before = builder.closed_snapshot()
    assert builder.update_forming(1.1, 1800)
    assert builder.forming_snapshot(1800)["complete_from_open"] is True
    assert builder.update_forming(1.2, 1810)
    assert builder.forming_snapshot(1810)["complete_from_open"] is False
    assert builder.update_forming(1.3, 1830)
    assert builder.forming_snapshot(1830)["ts"] == 1830
    assert builder.closed_snapshot() == before
    assert builder.source == "broker_native_fixture"
    assert builder._current is None and builder._last_tick_ts is None


def test_native_forming_ohlc_restores_complete_observation_and_preserves_extremes():
    builder = CandleBuilder(60)
    builder.update_forming(1.21, 65)
    native = {"open": 1.1, "high": 1.2, "low": 1.05, "close": 1.18,
              "ts": 60, "observed_at": 65, "source": "broker_native_ws"}
    builder.update_forming(1.19, 66, native=native)
    snapshot = builder.forming_snapshot(66)
    assert snapshot["complete_from_open"] is True
    assert snapshot["open"] == 1.1
    assert snapshot["high"] == 1.21
    assert snapshot["low"] == 1.05
    assert snapshot["close"] == 1.19
    assert snapshot["source"] == "broker_native_ws:sampled_overlay"
    assert snapshot["native_observed_at"] == 65
    assert native["high"] == 1.2
    assert len(builder) == 0
    for price, ts in [(99, 65), (99, 66), (float("nan"), 67), (0, 67)]:
        assert builder.update_forming(price, ts) is False
    assert builder.forming_snapshot(66) == snapshot
    builder.update_forming(1.2, 120, native=native)
    assert builder.forming_snapshot(120)["open"] == 1.2


def test_cached_native_baseline_cannot_repair_a_later_quote_outage():
    builder = CandleBuilder(60)
    native = {"open": 1.1, "high": 1.2, "low": 1.05, "close": 1.18,
              "ts": 60, "observed_at": 65, "source": "broker_native_ws"}
    for ts in range(66, 76):
        builder.update_forming(1.19, ts, native=native)
        # Continuous quote sampling maintains coverage when the baseline ages.
        assert builder.forming_snapshot(ts)["complete_from_open"] is True
        assert builder.forming_snapshot(ts)["native_observed_at"] == 65
    builder.update_forming(1.21, 90, native=native)
    assert builder.forming_snapshot(90)["complete_from_open"] is False
    builder.update_forming(1.22, 91, native=native)
    assert builder.forming_snapshot(91)["complete_from_open"] is False
    # Only a new native observation can fill the lost interval.
    builder.update_forming(1.23, 92, native=dict(native, observed_at=92, high=1.3))
    assert builder.forming_snapshot(92)["complete_from_open"] is True
    assert builder.forming_snapshot(92)["high"] == 1.3


def test_invalidating_live_observation_does_not_freshen_cached_native_baseline():
    builder = CandleBuilder(60)
    native = {"open": 1.1, "high": 1.2, "low": 1.05, "close": 1.18,
              "ts": 60, "observed_at": 65, "source": "broker_native_ws"}
    builder.update_forming(1.19, 66, native=native)
    builder.invalidate("stale_quote")
    builder.update_forming(1.19, 100, native=native)
    snapshot = builder.forming_snapshot(100)
    assert snapshot["observed_at"] == 100
    assert snapshot["native_observed_at"] == 65
    assert snapshot["complete_from_open"] is False
    for observed_at in (None, float("nan"), 101):
        other = CandleBuilder(60)
        other.update_forming(1.19, 100, native=dict(native, observed_at=observed_at))
        assert other.forming_snapshot(100)["complete_from_open"] is False


def test_feed_suspension_preserves_native_history_sr_and_observed_extremes():
    agent = MultiTimeframeAgent("test")
    agent.seed_from_dataframe(history(180), source="broker_native_ws", now=10800)
    agent.seed_from_dataframe(history(180, period=30, start=5400), timeframe=30,
                              source="broker_native_ws", now=10800)
    for period in (30, 60):
        native = {"open": 1.1, "high": 1.3, "low": 1.05, "close": 1.2,
                  "ts": 10800, "observed_at": 10801, "source": "broker_native_ws"}
        agent.builders[period].update_forming(1.19, 10801, native=native)
    closed = {period: builder.closed_snapshot() for period, builder in agent.builders.items()}
    sr = agent.sr_engine
    agent.suspend_live_observations("stale_quote", now=10803)
    assert [len(agent.builders[period]) for period in (30, 60, 300)] == [180, 70, 36]
    assert agent.sr_engine is sr
    for period, builder in agent.builders.items():
        assert builder.closed_snapshot() == closed[period]
        if period == 300:
            continue
        snapshot = builder.forming_snapshot(10803)
        assert snapshot["stale"] and not snapshot["complete_from_open"]
        assert snapshot["observed_at"] == snapshot["native_observed_at"] == 10801
        assert snapshot["high"] == 1.3 and snapshot["low"] == 1.05
        assert not builder.update_forming(9, 10802, native=native)
        builder.update_forming(1.2, 10804, native=native)
        snapshot = builder.forming_snapshot(10804)
        # A baseline predating suspension cannot repair even a short outage.
        assert not snapshot["stale"] and not snapshot["complete_from_open"]
        assert snapshot["high"] == 1.3
        builder.update_forming(1.21, 10805, native=dict(native, observed_at=10805))
        assert builder.forming_snapshot(10805)["complete_from_open"] is True
        assert builder.closed_snapshot() == closed[period]


def test_suspended_sampled_candle_remains_partial_and_cannot_bridge_closed_history():
    builder = CandleBuilder(30)
    for ts in range(36):
        builder.add_tick(1.1 if ts < 32 else 1.2, ts)
    closed = builder.closed_snapshot()
    before = builder.forming_snapshot(35)
    builder.mark_feed_gap(now=37)
    snapshot = builder.forming_snapshot(37)
    assert builder.closed_snapshot() == closed
    assert snapshot["observed_at"] == before["observed_at"] == 35
    assert snapshot["open"] == 1.1 and snapshot["high"] == 1.2
    assert snapshot["stale"] and not snapshot["complete_from_open"]
    assert not builder.add_tick(9, 36)
    builder.add_tick(1.15, 38)
    snapshot = builder.forming_snapshot(38)
    assert not snapshot["stale"] and not snapshot["complete_from_open"]
    assert snapshot["open"] == 1.1 and snapshot["high"] == 1.2
    for ts in range(39, 61):
        assert builder.add_tick(1.15, ts) is False
    assert len(builder) == 0  # The partial 30s bar breaks continuity with ts=0.
    for ts in range(61, 90):
        assert builder.add_tick(1.16, ts) is False
    assert builder.add_tick(1.16, 90) is True
    assert [bar["ts"] for bar in builder.closed_snapshot()] == [60]


def test_suspended_sampled_feed_does_not_publish_skipped_intervals():
    builder = CandleBuilder(30)
    for ts in range(36):
        builder.add_tick(1.1, ts)
    builder.mark_feed_gap(now=40)
    assert len(builder) == 1
    assert builder.add_tick(1.2, 100) is False
    assert len(builder) == 0
    assert not builder.forming_snapshot(100)["complete_from_open"]
    for ts in range(101, 150):
        assert builder.add_tick(1.2, ts) is False
    assert builder.add_tick(1.2, 150) is True
    assert [bar["ts"] for bar in builder.closed_snapshot()] == [120]


def test_native_refresh_preserves_only_active_forming_overlay():
    agent = MultiTimeframeAgent("test")
    frame = history(3, period=30)
    agent.seed_from_dataframe(frame, timeframe=30, source="broker_native_fixture", now=90)
    builder = agent.builders[30]
    builder.update_forming(1.1, 90)
    builder.update_forming(1.2, 91)
    before = builder.forming_snapshot(92)
    agent.seed_from_dataframe(frame, timeframe=30, source="broker_native_fixture", now=92)
    assert builder.forming_snapshot(92) == before
    assert len(builder) == 3
    agent.seed_from_dataframe(frame, timeframe=30, source="broker_native_fixture", now=120)
    assert builder.forming_snapshot(120) is None


def test_native_refresh_keeps_baseline_provenance_and_original_observation_time():
    agent = MultiTimeframeAgent("test")
    frame = history(3, period=30)
    agent.seed_from_dataframe(frame, timeframe=30, source="broker_native_ws", now=90)
    builder = agent.builders[30]
    native = {"open": 1.1, "high": 1.2, "low": 1.05, "close": 1.18,
              "ts": 90, "observed_at": 95, "source": "broker_native_ws"}
    builder.update_forming(1.19, 96, native=native)
    agent.seed_from_dataframe(frame, timeframe=30, source="broker_native_ws", now=97)
    snapshot = builder.forming_snapshot(97)
    assert snapshot["source"] == "broker_native_ws:sampled_overlay"
    assert snapshot["native_observed_at"] == 95
    assert snapshot["observed_at"] == 96
    assert snapshot["complete_from_open"] is True


def test_incremental_five_minute_aggregation_preserves_longer_trend_history():
    agent = MultiTimeframeAgent("test")
    agent.seed_from_dataframe(history(180), source="broker_native_fixture", now=10800)
    minutes = agent.builders[60]
    for minute in range(180, 245):
        minutes.candles.append({"ts": minute * 60, "open": 1.1, "high": 1.2,
                                "low": 1.0, "close": 1.15, "source": "broker_browser_sample"})
        agent.aggregate_five_minute()
    bars = agent.builders[300].closed_snapshot()
    assert len(minutes) == 70
    assert len(bars) == 40
    assert [bar["ts"] for bar in bars] == list(range(2700, 14700, 300))
    assert bars[-1]["close"] == 1.15


def test_five_minute_aggregation_discards_history_after_missing_minutes():
    agent = MultiTimeframeAgent("test")
    agent.seed_from_dataframe(history(180), source="broker_native_fixture", now=10800)
    minutes = agent.builders[60]
    # The next closed minute skips the required 10800 minute.
    for ts in range(10860, 11341, 60):
        minutes.candles.append({"ts": ts, "open": 1.1, "high": 1.2,
                                "low": 1.0, "close": 1.15, "source": "broker_browser_sample"})
        agent.aggregate_five_minute()
        if ts < 11340:
            assert len(agent.builders[300]) == 0
    assert [bar["ts"] for bar in agent.builders[300].closed_snapshot()] == [11100]


def test_live_warmup_requires_fifty_real_completed_30s_intervals():
    agent = MultiTimeframeAgent("EUR/USD OTC")
    closed_events = []
    for ts in range(1501):
        periods = agent.add_tick(1.1, ts)
        if 30 in periods:
            closed_events.append(ts)
        if ts == 1499:
            assert len(agent.builders[30]) == 49
    assert closed_events == list(range(30, 1501, 30))
    assert len(agent.builders[30]) == 50
    assert len(agent.builders[60]) == 25
    assert agent.builders[30].closed_snapshot()[-1]["source"] == "broker_browser_sample"


def test_compatibility_bootstrap_does_not_invent_history():
    agent = MultiTimeframeAgent("test")
    agent.bootstrap(1.1)
    assert all(len(builder) == 0 for builder in agent.builders.values())


def test_rsi_averages_every_change_and_uses_wilder_smoothing():
    closes = np.r_[1.0, 1.0 + np.cumsum([1] * 13 + [-1])]
    assert _rsi(closes) == pytest.approx(100 * 13 / 14)
    # One subsequent loss smooths both previous averages with Wilder's 1/14.
    assert _rsi(np.r_[closes, closes[-1] - 1]) == pytest.approx(100 * 169 / 196)
    assert _rsi(np.arange(15, dtype=float)) == 100
    assert _rsi(np.arange(15, 0, -1, dtype=float)) == 0
    assert _rsi(np.ones(15)) == 50
    assert np.isnan(_rsi(np.ones(14)))


def test_yahoo_rejects_otc_and_non_native_intervals_without_network(monkeypatch):
    def fail_if_requested(*args, **kwargs):
        raise AssertionError("unsupported data must not trigger a network request")
    monkeypatch.setattr(fetcher, "yf", SimpleNamespace(Ticker=fail_if_requested), raising=False)
    monkeypatch.setattr(fetcher, "_YF_AVAILABLE", True)
    for asset, interval in [("EUR/USD OTC", "M1"), ("EURUSD-OTC", 60), ("EURUSD", 30), ("EURUSD", "30s")]:
        with pytest.raises(fetcher.MarketDataUnavailable):
            fetcher.fetch_candles(asset, interval)


def test_empty_yahoo_response_fails_without_synthetic_fallback(monkeypatch):
    monkeypatch.setattr(fetcher, "yf", SimpleNamespace(Ticker=lambda _: SimpleNamespace(history=lambda **kw: pd.DataFrame())), raising=False)
    monkeypatch.setattr(fetcher, "_YF_AVAILABLE", True)
    fetcher._cache.clear()
    with pytest.raises(fetcher.MarketDataUnavailable, match="no 1m history"):
        fetcher.fetch_candles("EURUSD", "M1")


def test_yahoo_errors_fail_without_synthetic_fallback(monkeypatch):
    def fail(**kwargs):
        raise ConnectionError("test outage")
    monkeypatch.setattr(fetcher, "yf", SimpleNamespace(Ticker=lambda _: SimpleNamespace(history=fail)), raising=False)
    monkeypatch.setattr(fetcher, "_YF_AVAILABLE", True)
    fetcher._cache.clear()
    with pytest.raises(fetcher.MarketDataUnavailable, match="test outage"):
        fetcher.fetch_candles("EURUSD", "M1")
