"""One-second monitoring keeps provisional observations separate from orders."""
import asyncio
import math
from unittest.mock import AsyncMock, Mock

import pytest

import multi_agent_orchestrator as runtime
from agents.agent_multi_timeframe import MultiTimeframeAgent
from models.predictor import Signal
from test_execution_state import make_system


def monitoring_system(monkeypatch, tmp_path, direction="BUY"):
    system, bars, clock = make_system(monkeypatch, tmp_path, direction=direction)
    system.agent1 = MultiTimeframeAgent("EUR/USD")
    for period in (30, 60):
        system.agent1.builders[period].candles.extend(bars[period])
    system._seed_sources = {30: "broker_native_ws", 60: "broker_native_ws"}
    system._quote = {"price": 1.105, "observed_at": clock.now, "read_at": clock.now}
    system._five_minute_filter = Mock(side_effect=lambda requested="BUY": {
        "passed": requested == direction,
        "reason": "5m_bullish" if direction == "BUY" else "5m_bearish", "vote": direction})
    system.latest_decision = None
    system._feed_missing = False
    system._decision_tasks = set()
    system.max_runtime = 5
    system.max_candle_closes = 0
    system.setup = AsyncMock(return_value=True)

    def native_forming(asset, period):
        return {"ts": math.floor(clock.now / period) * period,
                "open": 1.1, "high": 1.11, "low": 1.09, "close": 1.105,
                "observed_at": clock.now, "source": "broker_native_ws"}

    system.bot.get_native_forming_candle = Mock(side_effect=native_forming)

    async def fresh_quote(asset):
        return {"price": 1.105 + (clock.now - 121) / 10000,
                "observed_at": clock.now, "read_at": clock.now}

    system.bot.get_current_quote = AsyncMock(side_effect=fresh_quote)
    decisions = []
    publish = system._publish_decision

    def capture(context, decision, reason):
        value = publish(context, decision, reason)
        decisions.append(value)
        return value

    system._publish_decision = capture
    real_sleep = asyncio.sleep
    sleeps = []

    async def advance_clock(seconds):
        # Give this tick's decision a chance to run before advancing wall time.
        await real_sleep(0)
        sleeps.append(seconds)
        clock.now += seconds
        await real_sleep(0)

    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(runtime.asyncio, "sleep", advance_clock)

    async def finish():
        if system._decision_tasks:
            await asyncio.gather(*system._decision_tasks)

    system._shutdown = AsyncMock(side_effect=finish)
    return system, clock, decisions, sleeps


def test_run_evaluates_fresh_quotes_and_both_forming_bars_every_second(monkeypatch, tmp_path):
    system, _, decisions, sleeps = monitoring_system(monkeypatch, tmp_path)
    system.observe_only = True
    asyncio.run(system.run(lock_acquired=True))

    assert [decision["evaluated_at"] for decision in decisions] == [121, 122, 123, 124, 125]
    assert sleeps == [1.0] * 5
    assert system.bot.get_current_quote.await_count == 5
    assert len({decision["live_price"] for decision in decisions}) == 5
    for period in (30, 60):
        candles = [decision["candles"][str(period)] for decision in decisions]
        assert all(candle["current"] and candle["provisional"] for candle in candles)
        assert all(not candle["can_confirm_entry"] for candle in candles)
        expected_remaining = [29, 28, 27, 26, 25] if period == 30 else [59, 58, 57, 56, 55]
        assert [candle["candle"]["remaining_seconds"] for candle in candles] == expected_remaining
    system.supervisor.safe_execute_trade.assert_not_awaited()


def test_stale_tick_still_publishes_a_decision_without_current_price(monkeypatch, tmp_path):
    system, clock, decisions, _ = monitoring_system(monkeypatch, tmp_path)
    system.observe_only = True

    async def quote(asset):
        return {"price": 1.105, "observed_at": clock.now - (3 if clock.now == 123 else 0),
                "read_at": clock.now, "stale": clock.now == 123}

    system.bot.get_current_quote.side_effect = quote
    before = {period: builder.closed_snapshot() for period, builder in system.agent1.builders.items()}
    asyncio.run(system.run(lock_acquired=True))
    assert len(decisions) == 5
    stale = decisions[2]
    assert stale["evaluated_at"] == 123
    assert stale["decision"] == "NO TRADE"
    assert stale["data_status"] == "stale_or_unavailable"
    assert stale["live_price"] is None
    assert all(candle["live_price"] is None and not candle["current"]
               for candle in stale["candles"].values())
    assert {period: builder.closed_snapshot() for period, builder in system.agent1.builders.items()} == before
    assert decisions[3]["buy_evaluation"]["passed"]


def test_source_marked_stale_cannot_publish_current_forming_values(monkeypatch, tmp_path):
    system, _, _, _ = monitoring_system(monkeypatch, tmp_path)
    system._update_forming_candles(1.105)
    system._quote["stale"] = True
    asyncio.run(system._evaluate_and_trade(1.105))
    assert system.latest_decision["live_price"] is None
    assert all(candle["live_price"] is None and not candle["current"]
               for candle in system.latest_decision["candles"].values())
    system.supervisor.safe_execute_trade.assert_not_awaited()


def test_malformed_quote_timestamp_publishes_no_trade_without_nonfinite_json(monkeypatch, tmp_path):
    system, _, _, _ = monitoring_system(monkeypatch, tmp_path)
    system._update_forming_candles(1.105)
    system._quote["observed_at"] = float("nan")
    asyncio.run(system._evaluate_and_trade(1.105))
    assert system.latest_decision["decision"] == "NO TRADE"
    assert system.latest_decision["quote_observed_at"] is None
    assert system.latest_decision["quote_age_seconds"] is None
    assert system.latest_decision["live_price"] is None
    assert all(candle["live_price"] is None for candle in system.latest_decision["candles"].values())
    system.supervisor.safe_execute_trade.assert_not_awaited()


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_quote_expiring_during_payout_check_prevents_order(monkeypatch, tmp_path, direction):
    system, clock, decisions, _ = monitoring_system(monkeypatch, tmp_path, direction=direction)
    system._update_forming_candles(1.105)

    async def payout(asset):
        clock.now = 124
        return .85

    system.bot.get_asset_payout.side_effect = payout
    asyncio.run(system._evaluate_and_trade(1.105))
    assert [decision["decision"] for decision in decisions] == ["WAIT", "NO TRADE"]
    assert decisions[0]["reason"] == "checking_demo_order_conditions"
    assert decisions[-1]["reason"] == "quote_expired_during_preflight"
    assert decisions[-1]["evaluated_at"] == 124
    assert decisions[-1]["live_price"] is None
    system.supervisor.safe_execute_trade.assert_not_awaited()


def test_sell_confirmation_cannot_authorize_entry_against_bullish_filter(monkeypatch, tmp_path):
    system, _, _, _ = monitoring_system(monkeypatch, tmp_path)
    system._quote["price"] = 1.12
    system._update_forming_candles(1.12)
    monkeypatch.setattr(runtime, "evaluate_both", lambda *args: {
        "BUY": {"passed": False, "reason": "awaiting_closed_buy_confirmation"},
        "SELL": {"passed": True, "reason": "closed_candle_setup_confirmed"},
    })
    asyncio.run(system._evaluate_and_trade(1.12))
    assert system.latest_decision["decision"] == "NO TRADE"
    assert system.latest_decision["direction"] == "SELL"
    assert system.latest_decision["reason"] == "5m_bullish"
    assert system.latest_decision["blocking_stage"] == "trend_filter"
    assert system.latest_decision["candles"]["30"]["candle"]["direction"] == "bullish"
    assert system.latest_decision["candles"]["60"]["candle"]["direction"] == "bullish"
    system.supervisor.safe_execute_trade.assert_not_awaited()


def test_repeated_seconds_submit_a_confirmed_buy_setup_only_once(monkeypatch, tmp_path):
    system, _, decisions, _ = monitoring_system(monkeypatch, tmp_path)
    system.supervisor.safe_execute_trade.return_value = True
    system.bot.last_order["accepted_at"] = 121.75
    asyncio.run(system.run(lock_acquired=True))
    system.supervisor.safe_execute_trade.assert_awaited_once_with(Signal.BUY, .95, "ftt", entry_deadline=125.0)
    assert len(system.active_trades) == 1
    assert system.active_trades[0]["accepted_at"] == 121.75
    assert system.active_trades[0]["duration"] == 60
    assert [decision["decision"] for decision in decisions[:3]] == ["WAIT", "BUY", "BUY"]
    assert decisions[0]["reason"] == "checking_demo_order_conditions"
    assert decisions[2]["reason"] == "order_accepted"
    assert decisions[2]["accepted_orders_this_run"] == 1
    assert all(decision["decision"] == "WAIT" for decision in decisions[3:])


def test_slow_execution_does_not_suspend_per_second_market_decisions(monkeypatch, tmp_path):
    system, _, decisions, _ = monitoring_system(monkeypatch, tmp_path)

    async def run():
        release = asyncio.Event()

        async def execute(*args, **kwargs):
            await release.wait()
            return True

        async def finish():
            release.set()
            await asyncio.gather(*system._decision_tasks)

        system.supervisor.safe_execute_trade = AsyncMock(side_effect=execute)
        system._shutdown = AsyncMock(side_effect=finish)
        await system.run(lock_acquired=True)

    asyncio.run(run())
    assert [decision["evaluated_at"] for decision in decisions[:6]] == [121, 121, 122, 123, 124, 125]
    assert [decision["decision"] for decision in decisions] == ["WAIT", "BUY", "WAIT", "WAIT", "WAIT", "WAIT", "BUY"]
    assert decisions[0]["reason"] == "checking_demo_order_conditions"
    assert all(decision["reason"] == "order_unresolved" for decision in decisions[2:6])
    assert decisions[-1]["reason"] == "order_accepted"
    assert decisions[-1]["evaluated_at"] >= decisions[-2]["evaluated_at"]
    assert decisions[-1]["submission_evaluated_at"] == 121
    system.supervisor.safe_execute_trade.assert_awaited_once()
    assert len(system.active_trades) == 1


def test_slow_payout_check_publishes_first_tick_and_keeps_monitoring(monkeypatch, tmp_path):
    system, _, decisions, _ = monitoring_system(monkeypatch, tmp_path)

    async def run():
        release = asyncio.Event()

        async def payout(asset):
            await release.wait()
            return .85

        async def finish():
            release.set()
            await asyncio.gather(*system._decision_tasks)

        system.bot.get_asset_payout.side_effect = payout
        system._shutdown = AsyncMock(side_effect=finish)
        await system.run(lock_acquired=True)

    asyncio.run(run())
    assert [decision["evaluated_at"] for decision in decisions[:5]] == [121, 122, 123, 124, 125]
    assert all(decision["decision"] == "WAIT" for decision in decisions)
    assert decisions[0]["reason"] == "checking_demo_order_conditions"
    assert all(decision["reason"] == "order_unresolved" for decision in decisions[1:5])
    assert decisions[-1]["reason"] == "awaiting_next_closed_confirmation"
    assert system.bot.get_current_quote.await_count == 5
    system.bot.get_asset_payout.assert_awaited_once()
    system.supervisor.safe_execute_trade.assert_not_awaited()


def test_observe_only_cannot_call_stale_closed_trend_a_buy(monkeypatch, tmp_path):
    system, _, _, _ = monitoring_system(monkeypatch, tmp_path)
    system.observe_only = True
    system.agent1.builders[30].candles[-1]["ts"] = 60
    asyncio.run(system._evaluate_and_trade(1.105))
    assert system.latest_decision["decision"] == "NO TRADE"
    assert system.latest_decision["reason"] == "stale_candle_snapshot"


def test_fresh_quote_does_not_hide_stale_history_behind_failed_entry_rule(monkeypatch, tmp_path):
    system, _, _, _ = monitoring_system(monkeypatch, tmp_path)
    system.agent1.builders[30].candles[-1]["ts"] = 60
    monkeypatch.setattr(runtime, "evaluate_both", lambda *args: {
        "BUY": {"passed": False, "reason": "1m_close_vs_ema21", "failed_rules": ["1m_close_vs_ema21"]},
        "SELL": {"passed": False},
    })
    asyncio.run(system._evaluate_and_trade(1.105))
    result = system.latest_decision
    assert result["quote_status"] == "fresh"
    assert result["closed_candle_status"] == result["data_status"] == "stale_or_unavailable"
    assert result["live_price"] == 1.105
    assert result["blocking_stage"] == "data"
    assert result["reason"] == "stale_candle_snapshot"
    assert result["closed_candles"]["30"]["expected_open_ts"] == 90
    assert result["stale_closed_timeframes"] == [30]
    system.supervisor.safe_execute_trade.assert_not_awaited()


def test_closed_history_gap_blocks_otherwise_passing_setup(monkeypatch, tmp_path):
    system, _, _, _ = monitoring_system(monkeypatch, tmp_path)
    system.agent1.builders[30].candles.appendleft(
        dict(open=1.1, high=1.11, low=1.09, close=1.105, ts=0))
    asyncio.run(system._evaluate_and_trade(1.105))
    assert system.latest_decision["closed_candles"]["30"]["contiguous"] is False
    assert system.latest_decision["stale_closed_timeframes"] == [30]
    assert system.latest_decision["reason"] == "stale_candle_snapshot"
    system.supervisor.safe_execute_trade.assert_not_awaited()


@pytest.mark.parametrize("uncertain", [False, True])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_rejected_submission_replaces_confirmation_with_actual_result(monkeypatch, tmp_path, uncertain, direction):
    system, _, _, _ = monitoring_system(monkeypatch, tmp_path, direction=direction)
    system.supervisor.execution_uncertain = uncertain
    system.supervisor.last_execution_reason = "broker_order_not_accepted"
    asyncio.run(system._evaluate_and_trade(1.105))
    result = system.latest_decision
    assert result["decision"] == "NO TRADE"
    assert result["blocking_stage"] == "execution"
    assert result["reason"] == ("execution_uncertain" if uncertain else "execution_rejected")
    assert result["execution_detail"] == "broker_order_not_accepted"
    assert result["accepted_orders_this_run"] == 0
    assert result["direction"] == direction
    assert result["entry_evaluation"] == result[f"{direction.lower()}_evaluation"]


@pytest.mark.parametrize("second", [0, 2, 5, 9, 41, 55, 59])
def test_chart_reconciliation_never_occupies_entry_window(monkeypatch, tmp_path, second):
    system, clock, _, _ = monitoring_system(monkeypatch, tmp_path)
    clock.now = 120 + second
    reconcile = AsyncMock()
    monkeypatch.setattr(runtime, "reconcile_selected_strategy_chart", reconcile)
    asyncio.run(system._sync_chart_display())
    reconcile.assert_not_awaited()


def test_chart_reconciliation_runs_between_entry_windows_and_clears_lock(monkeypatch, tmp_path):
    system, clock, _, _ = monitoring_system(monkeypatch, tmp_path)
    clock.now = 140
    system.bot._page = object()
    reconcile = AsyncMock(return_value={"verified": True, "view": "trend"})
    monkeypatch.setattr(runtime, "reconcile_selected_strategy_chart", reconcile)
    asyncio.run(system._sync_chart_display())
    reconcile.assert_awaited_once_with(system.bot._page, timeout_seconds=8, preferred_view="entry")
    assert not system._execution_lock.locked()
    assert not system._chart_sync_active


def test_chart_probe_does_not_hide_actual_failed_entry_rules(monkeypatch, tmp_path):
    system, clock, _, _ = monitoring_system(monkeypatch, tmp_path)
    clock.now = 140
    system._quote["observed_at"] = clock.now
    system._chart_sync_active = True
    monkeypatch.setattr(runtime, "evaluate_both", lambda *args: {
        "BUY": {"passed": False, "reason": "1m_rsi14_band"}, "SELL": {"passed": False}})

    async def run():
        async with system._execution_lock:
            await system._evaluate_and_trade(1.105)

    asyncio.run(run())
    assert system.latest_decision["reason"] == "1m_rsi14_band"
    assert system.latest_decision["blocking_stage"] == "entry_rules"
    system.supervisor.safe_execute_trade.assert_not_awaited()


@pytest.mark.parametrize("entry_seconds, entry_label", [(30, "30s"), (60, "1m")])
def test_console_summary_does_not_alter_full_json_for_file_handlers(entry_seconds, entry_label):
    import json
    import logging
    payload = {"decision": "WAIT", "blocking_stage": "entry_rules", "reason": "1m_rsi14_band",
               "entry_timeframe_seconds": entry_seconds,
               "quote_status": "fresh", "quote_age_seconds": .125,
               "closed_candle_status": "fresh", "stale_closed_timeframes": [],
               "entry_timing": {"age_seconds": 1.25, "within_window": True},
               "entry_candle": {"indicators": {"rsi14": 51.25}},
               "candles": {"30": {"indicators": {"rsi14": 99}},
                           "60": {"indicators": {"rsi14": 10}}},
               "buy_evaluation": {"trend_close": 1.08374, "entry_close": 1.08364,
                                  "rsi": 49.69, "failed_rules": ["1m_rsi14_band", "obstacle_clearance"]}}
    original = "[Decision] " + json.dumps(payload)
    record = logging.LogRecord("test", logging.INFO, "", 0, "%s", (original,), None)
    formatted = runtime.MonitorConsoleFormatter("%(message)s").format(record)
    assert f"entry.{entry_label}.closed=1.08364" in formatted
    assert "30s.closed=1.08374" in formatted
    assert "stage=entry_rules" in formatted
    assert "quote_status=fresh quote_age_seconds=0.125" in formatted
    assert "closed_history=fresh stale_timeframes=none" in formatted
    assert "entry_age_seconds=1.250 entry_window=open" in formatted
    assert f"{entry_label}.RSI.live=51.25000" in formatted
    assert "failed_count=2" in formatted
    assert "obstacle_clearance" not in formatted
    assert record.getMessage() == original
    assert json.loads(record.getMessage().split("[Decision] ", 1)[1]) == payload


def test_quote_age_diagnostic_explains_rejection_just_after_two_seconds(monkeypatch, tmp_path):
    import json
    import logging
    system, clock, _, _ = monitoring_system(monkeypatch, tmp_path)
    system._quote["observed_at"] = clock.now - 2.002
    asyncio.run(system._evaluate_and_trade(1.105))
    result = system.latest_decision
    assert result["quote_age_seconds"] == pytest.approx(2.002)
    assert result["quote_status"] == "stale_or_unavailable"
    assert result["reason"] == "stale_or_unavailable_quote"
    assert result["stale_closed_timeframes"] == []
    record = logging.LogRecord("test", logging.INFO, "", 0, "[Decision] %s", (json.dumps(result),), None)
    assert "quote_age_seconds=2.002" in runtime.MonitorConsoleFormatter("%(message)s").format(record)
    system.supervisor.safe_execute_trade.assert_not_awaited()


def test_native_refresh_log_does_not_claim_selected_quote_or_closed_history_fresh(monkeypatch, tmp_path, caplog):
    import json
    import logging
    system, _, _, _ = monitoring_system(monkeypatch, tmp_path)
    system.bot.refresh_native_market_data = AsyncMock(return_value={30: True, 60: False})

    async def stop_after_refresh(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(runtime.asyncio, "sleep", stop_after_refresh)
    with caplog.at_level(logging.INFO, logger="multi_agent_orchestrator"):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(system._market_refresh_loop())
    record = next(record for record in caplog.records if record.getMessage().startswith("[MarketData] "))
    assert json.loads(record.getMessage().split("[MarketData] ", 1)[1]) == {
        "scope": "native_forming_refresh",
        "native_forming_fresh_by_timeframe": {"30": True, "60": False},
        "closed_history_managed_separately": True,
    }


def test_marketdata_change_filter_keeps_only_stable_changes_and_summary():
    import json
    import logging
    from utils.log_filters import MarketDataChangeFilter

    logger = logging.getLogger("marketdata-filter-test")
    logger.handlers.clear()
    logger.propagate = False
    handlers = []
    stream = logging.StreamHandler()
    stream.setLevel(logging.INFO)
    logger.addHandler(stream)
    handlers.append(stream)
    logger.setLevel(logging.INFO)
    logger.addFilter(MarketDataChangeFilter())

    payload = {"30": True, "60": True}
    def record_at(value, seconds):
        message = "[MarketData] " + json.dumps({
            "native_forming_fresh_by_timeframe": value,
            "scope": "native_forming_refresh",
            "closed_history_managed_separately": True,
        }, sort_keys=True)
        rec = logging.LogRecord("marketdata-filter-test", logging.INFO, __file__, 0, message, (), None)
        rec.created = seconds
        return rec

    filtered = []
    for idx in range(100):
        rec = record_at(payload, 1_700_000_000 + idx)
        if logger.filters[0].filter(rec):
            filtered.append(rec.getMessage())

    assert len(filtered) <= 1

    flip_3s = [
        record_at({"30": False, "60": True}, 1_700_000_000),
        record_at({"30": False, "60": True}, 1_700_000_000 + 1),
        record_at({"30": False, "60": True}, 1_700_000_000 + 3),
    ]
    allowed = [logger.filters[0].filter(rec) for rec in flip_3s]
    assert allowed[-1] is True
    assert allowed[0] is False

    short_flip = [
        record_at({"30": True, "60": True}, 1_700_001_000),
        record_at({"30": False, "60": True}, 1_700_001_001),
        record_at({"30": False, "60": True}, 1_700_001_001 + 1),
    ]
    assert logger.filters[0].filter(short_flip[0]) is True
    assert logger.filters[0].filter(short_flip[1]) is False
    assert logger.filters[0].filter(short_flip[2]) is False

    for h in handlers:
        h.close()
    logger.handlers.clear()
    logger.filters.clear()


@pytest.mark.parametrize("slope, passed", [(0.0001, True), (-0.0001, False), (0, False)])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_five_minute_filter_uses_closed_direction_without_contrarian_votes(monkeypatch, slope, passed, direction):
    if direction == "SELL":
        slope = -slope
    system = runtime.MultiAgentTradingSystem.__new__(runtime.MultiAgentTradingSystem)
    system.agent1 = MultiTimeframeAgent("EUR/USD")
    builder = system.agent1.builders[300]
    builder.candles.extend({"ts": i * 300, "open": 1.1 + i * slope,
                            "high": 1.1 + i * slope + .0002, "low": 1.1 + i * slope - .0002,
                            "close": 1.1 + i * slope} for i in range(35))
    builder.add_tick(99, 10501)
    system.agent1._analyze_tf = Mock(side_effect=AssertionError("Contrarian score must not gate direction"))
    monkeypatch.setattr(runtime.time, "time", lambda: 10501)
    result = system._five_minute_filter(direction)
    assert result["passed"] is passed
    assert result["closed_ts"] == 10200
    assert result["close"] == 1.1 + 34 * slope
    system.agent1._analyze_tf.assert_not_called()

    system.agent1._analyze_tf.reset_mock()
    monkeypatch.setattr(runtime.time, "time", lambda: 10801)
    assert system._five_minute_filter(direction)["reason"] == "stale_5m_candles"
    system.agent1._analyze_tf.assert_not_called()


def test_monitor_candle_and_expiry_settings_are_distinct(monkeypatch, tmp_path):
    system, _, _, _ = monitoring_system(monkeypatch, tmp_path)
    assert runtime.settings.POLL_INTERVAL == system.TICK_INTERVAL == system.DECISION_INTERVAL == 1
    assert runtime.settings.ACTIVE_CANDLE_SECONDS == (30, 60)
    assert runtime.settings.TREND_FILTER_SECONDS == 300
    assert runtime.settings.FTT_DURATION == runtime.FTT_DURATION_S == 60
    system.observe_only = True
    asyncio.run(system._evaluate_and_trade(1.105))
    assert system.latest_decision["monitor_interval_seconds"] == 1
    assert system.latest_decision["active_candle_seconds"] == [30, 60]
    assert system.latest_decision["trade_expiry_seconds"] == 60
    assert system.latest_decision["expiry_basis"] == "actual_broker_entry"
