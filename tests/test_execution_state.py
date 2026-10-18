"""Execution regressions with deterministic clocks and no browser or broker calls."""
import asyncio
import json
import logging
import subprocess
import sys
import time
from types import SimpleNamespace
from datetime import datetime
from unittest.mock import AsyncMock, Mock

import pytest

import multi_agent_orchestrator as runtime
from bot.process_lock import ProcessLock
from bot.risk import RiskManager, TradeRecord
from agents.agent_pipeline_supervisor import CircuitBreaker, PipelineSupervisorAgent


def make_system(monkeypatch, tmp_path, entry_seconds=60, direction="BUY"):
    s = runtime.MultiAgentTradingSystem.__new__(runtime.MultiAgentTradingSystem)
    s.entry_seconds = entry_seconds
    bars = {
        30: [dict(open=1.1, high=1.11, low=1.09, close=1.105, ts=90.0)],
        60: [dict(open=1.1, high=1.11, low=1.09, close=1.105, ts=60.0)],
    }
    s.agent1 = SimpleNamespace(builders={
        period: SimpleNamespace(closed_snapshot=lambda p=period: list(bars[p]))
        for period in (30, 60)
    })
    s._current_asset = "EUR/USD OTC"
    s._last_evaluation_key = None
    s._last_attempted_entry = None
    s._execution_lock = asyncio.Lock()
    s._process_lock_held = True
    s._execution_uncertain = False
    s._seed_sources = {30: "test_fixture", 60: "test_fixture"}
    s.closed_events = 1
    s._accepted_count = 0
    s.observe_only = False
    s.active_trades = []
    s.trade_amount = 200.0
    s.max_trades_per_day = 20
    s.daily_loss_limit = 2000.0
    s.payout_minimum = .80
    s._order_state_path = tmp_path / "execution_state.json"
    s._attempted_setup_path = tmp_path / "last_entry_setup.json"
    s._check_circuit_breaker = lambda: False
    s._check_consecutive_loss_halt = lambda: (False, "")
    s._trade_capacity = lambda: (20, 10, 2000.0)
    s._daily_profit = s._daily_loss = 0.0
    s._trades_today = s._wins_today = s._consecutive_losses = 0
    s._loss_halt_time = 0.0
    s.bot = SimpleNamespace(
        get_asset_payout=AsyncMock(return_value=.85),
        get_order_result=AsyncMock(return_value=None),
        last_order={"id": "broker-123", "amount": 123.0, "duration": 60, "payout": .85},
    )
    s.supervisor = SimpleNamespace(
        safe_execute_trade=AsyncMock(return_value=False),
        execution_uncertain=False,
        record_outcome=Mock(),
    )
    monkeypatch.setattr(runtime.settings, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(runtime.settings, "JOURNAL_ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(runtime.settings, "RESET_TODAY_LOGS_ON_STARTUP", False)
    s.risk = RiskManager(max_daily_trades=20, max_daily_loss=2000)
    s.risk.record_trade = Mock(wraps=s.risk.record_trade)
    s.risk.update_pnl_and_journal = Mock(wraps=s.risk.update_pnl_and_journal)
    clock = SimpleNamespace(now=121.0)
    monkeypatch.setattr(runtime.time, "time", lambda: clock.now)
    s._quote = {"price": 1.105, "observed_at": clock.now}
    s._five_minute_filter = lambda direction=direction: {
        "passed": True, "reason": "5m_bullish" if direction == "BUY" else "5m_bearish",
        "vote": direction}
    monkeypatch.setattr(runtime, "evaluate_both", lambda *args: {
        side: {"passed": side == direction, "entry_close": 1.105}
        for side in ("BUY", "SELL")
    })
    return s, bars, clock


def advance(bars, seconds):
    for period in (30, 60):
        bars[period].append(dict(bars[period][-1], ts=bars[period][-1]["ts"] + seconds))


def test_legacy_evaluator_call_uses_supported_signature(monkeypatch, tmp_path, caplog):
    s, bars, clock = make_system(monkeypatch, tmp_path)
    seen = {}

    def legacy(*args):
        seen["args"] = args
        return {
            "BUY": {"passed": True, "rise": True, "reason": "ok", "entry_close": 1.105},
            "SELL": {"passed": False, "reason": "not_buy", "entry_close": 1.105},
        }

    monkeypatch.setattr(runtime, "select_entry_evaluator", lambda: legacy)
    monkeypatch.setattr(runtime.settings, "DEMO_STRATEGY_ENABLED", False)
    bars[30].append(dict(bars[30][-1], ts=120.0))
    clock.now = 121.0
    s._quote["observed_at"] = clock.now
    with caplog.at_level(logging.INFO, logger="multi_agent_orchestrator"):
        asyncio.run(s._evaluate_and_trade(1.105))
    assert len(seen["args"]) == 3
    assert seen["args"][2] == 60
    assert "awaiting_closed_confirmation" in caplog.text or "NO TRADE" in caplog.text


def test_new_30s_bar_logs_both_gates_without_executing_old_1m_setup(monkeypatch, tmp_path, caplog):
    s, bars, clock = make_system(monkeypatch, tmp_path)
    s._last_evaluation_key = (90.0, 60.0)
    bars[30].append(dict(bars[30][-1], ts=120.0))
    clock.now = 151.0
    s._quote["observed_at"] = clock.now
    with caplog.at_level(logging.INFO, logger="multi_agent_orchestrator"):
        asyncio.run(s._evaluate_and_trade(1.105))
    assert sum("OnlyGate]" in record.message for record in caplog.records) == 2
    s.bot.get_asset_payout.assert_not_awaited()
    s.supervisor.safe_execute_trade.assert_not_awaited()
    assert not s._order_state_path.exists()


def test_delayed_confirmation_never_submits_even_with_new_entry(monkeypatch, tmp_path, caplog):
    s, _, clock = make_system(monkeypatch, tmp_path)
    clock.now = 126.0  # Closed at 120; beyond the existing five-second decision window.
    s._quote["observed_at"] = clock.now
    with caplog.at_level(logging.INFO, logger="multi_agent_orchestrator"):
        asyncio.run(s._evaluate_and_trade(1.105))
    s.supervisor.safe_execute_trade.assert_not_awaited()
    assert any("stale_confirmation" in record.message for record in caplog.records)
    assert not s._order_state_path.exists()


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_submission_is_persisted_before_click_then_cleared_on_known_rejection(monkeypatch, tmp_path, direction):
    s, _, _ = make_system(monkeypatch, tmp_path, direction=direction)
    inspected = []

    async def execute(*args, **kwargs):
        state = json.loads(s._order_state_path.read_text())
        inspected.append(state)
        assert state["status"] == "submitting"
        assert state["asset"] == "EUR/USD OTC"
        assert state["direction"] == direction
        assert state["entry_ts"] == 60.0
        assert state["amount"] == 200.0
        assert state["duration"] == 60
        assert state["submitted_at"]
        return False

    s.supervisor.safe_execute_trade = AsyncMock(side_effect=execute)
    asyncio.run(s._evaluate_and_trade(1.105))
    assert len(inspected) == 1
    assert json.loads(s._order_state_path.read_text()) is None
    s.risk.record_trade.assert_not_called()
    assert not s.active_trades


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_uncertain_submission_persists_and_blocks_next_fresh_minute(monkeypatch, tmp_path, direction):
    s, bars, clock = make_system(monkeypatch, tmp_path, direction=direction)

    async def execute(*args, **kwargs):
        s.supervisor.execution_uncertain = True
        return False

    s.supervisor.safe_execute_trade = AsyncMock(side_effect=execute)

    async def run():
        await s._evaluate_and_trade(1.105)
        advance(bars, 60)
        clock.now = 181.0
        s._quote["observed_at"] = clock.now
        await s._evaluate_and_trade(1.105)

    asyncio.run(run())
    s.supervisor.safe_execute_trade.assert_awaited_once()
    assert s._execution_uncertain
    assert json.loads(s._order_state_path.read_text())["status"] == "submitting"
    s.risk.record_trade.assert_not_called()


def test_one_indicator_cooldown_stops_before_consuming_setup(monkeypatch, tmp_path, caplog):
    s = runtime.MultiAgentTradingSystem.__new__(runtime.MultiAgentTradingSystem)
    s.entry_seconds = 60
    s._current_asset = "EUR/USD OTC"
    s._execution_lock = asyncio.Lock()
    s._process_lock_held = True
    s._execution_uncertain = False
    s.active_trades = []
    s.observe_only = False
    s.trade_amount = 200.0
    s.payout_minimum = 0.80
    s._accepted_count = 0
    s.latest_decision = None
    s._quote = {"price": 1.1, "observed_at": time.time()}
    s.bot = SimpleNamespace(
        live_feed=SimpleNamespace(
            closed=lambda asset, period: [{"ts": 0.0, "open": 1.09, "high": 1.11, "low": 1.08, "close": 1.10, "ticks": 5}],
            momentum=lambda asset, period: {"direction": "flat", "ticks": 1},
            last_tick=lambda asset, max_age=None: {"price": 1.1, "recv": time.time(), "age_seconds": 0.1},
        ),
        get_asset_payout=AsyncMock(return_value=0.85),
    )
    s.risk = SimpleNamespace(approve=lambda *args, **kwargs: (True, "Approved"))
    s.supervisor = SimpleNamespace(cb=SimpleNamespace(should_stop=lambda: (True, "Circuit breaker cooling down — 250s left"), cool_down=True))
    s._persist_attempted_entry = Mock()
    s._persist_order_state = Mock()
    s._check_circuit_breaker = lambda: False
    s._check_consecutive_loss_halt = lambda: (False, "")
    s._trade_capacity = lambda: (20, 10, 2000.0)
    s._payout_cache = None
    s.supervisor.safe_execute_trade = AsyncMock(return_value=False)

    closed = {"ts": 0.0, "open": 1.09, "high": 1.11, "low": 1.08, "close": 1.10, "ticks": 5}
    opening = {"price": 1.10, "recv": time.time()}
    with caplog.at_level(logging.INFO, logger="multi_agent_orchestrator"):
        asyncio.run(s._one_indicator_trade(closed, opening))

    s._persist_attempted_entry.assert_not_called()
    s._persist_order_state.assert_not_called()
    assert s.latest_decision["decision"] == "WAIT"
    assert s.latest_decision["reason"] == "circuit_breaker_cooldown"
    assert s.latest_decision["seconds_left"] == 250
    assert not any("sending_order" in record.message for record in caplog.records)


def test_prearm_ftt_skips_when_readback_matches():
    bot = runtime.OlymtradeBot.__new__(runtime.OlymtradeBot)
    bot._order_lock = asyncio.Lock()
    amount_field = SimpleNamespace(
        input_value=AsyncMock(return_value="200"),
        fill=AsyncMock(),
        press=AsyncMock(),
    )
    duration_field = SimpleNamespace(
        input_value=AsyncMock(return_value="60s"),
        fill=AsyncMock(),
        press=AsyncMock(),
    )
    bot._page = SimpleNamespace(
        locator=lambda selector: {
            '[data-test="amount-input"] input': amount_field,
            '[data-test="trade-time-duration-input"] input': duration_field,
        }.get(selector)
    )
    bot.is_demo_account = AsyncMock(return_value=True)
    bot.get_current_quote = AsyncMock(return_value={"price": 1.1, "observed_at": time.time(), "stale": False})
    bot.get_asset_payout = AsyncMock(return_value=0.85)
    bot.set_amount = AsyncMock(return_value=True)
    bot._set_ftt_duration = AsyncMock(return_value=True)

    async def run():
        return await bot.prearm_ftt("EUR/USD OTC", amount=200.0, duration_seconds=60)

    assert asyncio.run(run()) is True
    bot.set_amount.assert_not_called()
    bot._set_ftt_duration.assert_not_called()


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_canceled_submission_remains_unresolved_and_blocks_next_minute(monkeypatch, tmp_path, direction):
    s, bars, clock = make_system(monkeypatch, tmp_path, direction=direction)
    s.supervisor = PipelineSupervisorAgent(
        s.bot, SimpleNamespace(approve=lambda *args, **kwargs: (True, "")), None, None)

    async def run():
        entered = asyncio.Event()

        async def pending_order(*args, **kwargs):
            entered.set()
            await asyncio.Future()

        s.bot.place_ftt_trade = AsyncMock(side_effect=pending_order)
        task = asyncio.create_task(s._evaluate_and_trade(1.105))
        await asyncio.wait_for(entered.wait(), timeout=1)
        submitted = s._order_state_path.read_text()
        assert json.loads(submitted)["status"] == "submitting"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert s._execution_uncertain and s.supervisor.execution_uncertain
        assert s.supervisor.last_execution_reason == "execution_uncertain: CancelledError"
        assert s.latest_decision["decision"] == "NO TRADE"
        assert s.latest_decision["reason"] == "execution_uncertain"
        assert not s._execution_lock.locked() and not s.supervisor._trade_lock.locked()
        assert s._order_state_path.read_text() == submitted
        assert json.loads(s._attempted_setup_path.read_text())["entry_ts"] == 60.0

        for period, history in bars.items():
            for _ in range(60 // period):
                history.append(dict(history[-1], ts=history[-1]["ts"] + period))
        clock.now = 181.0
        s._quote["observed_at"] = clock.now
        result = await s._evaluate_and_trade(1.105)
        assert result["reason"] == "order_unresolved"
        # The supervisor independently refuses retries as well.
        assert not await s.supervisor.safe_execute_trade(runtime.Signal.BUY, .95, "ftt")
        s.bot.place_ftt_trade.assert_awaited_once()
        assert s._order_state_path.read_text() == submitted

    asyncio.run(run())
    s.risk.record_trade.assert_not_called()
    assert not s.active_trades and s._accepted_count == 0


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_accepted_state_and_journal_use_platform_stake(monkeypatch, tmp_path, direction):
    s, _, _ = make_system(monkeypatch, tmp_path, direction=direction)
    s.supervisor.safe_execute_trade.return_value = True
    asyncio.run(s._evaluate_and_trade(1.105))
    persisted = json.loads(s._order_state_path.read_text())
    assert persisted["status"] == "accepted"
    assert persisted["order"] == s.active_trades[0]
    assert persisted["order"]["amount"] == 123.0
    record = s.risk.record_trade.call_args.args[0]
    assert record.amount == 123.0
    assert record.signal == persisted["order"]["signal"] == direction
    assert record.timestamp == persisted["order"]["timestamp"]
    assert record.result == "PENDING"
    assert s._accepted_count == 1


def test_rejected_setup_remains_consumed_after_restart(monkeypatch, tmp_path):
    s, _, _ = make_system(monkeypatch, tmp_path)
    asyncio.run(s._evaluate_and_trade(1.105))
    assert json.loads(s._attempted_setup_path.read_text()) == {
        "asset": "EUR/USD OTC", "entry_ts": 60.0, "entry_timeframe_seconds": 60,
        "entries_by_timeframe": {"60": 60.0}}
    restored = restore_system(monkeypatch, tmp_path, None)
    assert restored._last_attempted_entry == 60.0
    assert not restored._execution_uncertain


@pytest.mark.parametrize("entry_seconds", [30, 60])
@pytest.mark.parametrize("first_direction", ["BUY", "SELL"])
def test_rejected_entry_cannot_be_retried_in_opposite_direction(monkeypatch, tmp_path,
                                                              entry_seconds, first_direction):
    s, _, _ = make_system(monkeypatch, tmp_path, entry_seconds, direction=first_direction)

    async def run():
        first = await s._evaluate_and_trade(1.105)
        assert first["reason"] == "execution_rejected"
        opposite = "SELL" if first_direction == "BUY" else "BUY"
        monkeypatch.setattr(runtime, "evaluate_both", lambda *args: {
            side: {"passed": side == opposite, "entry_close": 1.105}
            for side in ("BUY", "SELL")})
        result = await s._evaluate_and_trade(1.105)
        assert result["direction"] == opposite
        assert result["reason"] == "setup_already_attempted"

    asyncio.run(run())
    s.supervisor.safe_execute_trade.assert_awaited_once()
    restored = restore_system(monkeypatch, tmp_path, None, entry_seconds=entry_seconds)
    assert restored._setup_already_attempted(120 - entry_seconds)
    s.risk.record_trade.assert_not_called()


def test_conflicting_directional_passes_do_not_submit_or_consume_setup(monkeypatch, tmp_path):
    s, _, _ = make_system(monkeypatch, tmp_path)
    monkeypatch.setattr(runtime, "evaluate_both", lambda *args: {
        side: {"passed": True, "entry_close": 1.105} for side in ("BUY", "SELL")})
    result = asyncio.run(s._evaluate_and_trade(1.105))
    assert result["direction"] is None
    assert result["reason"] == "conflicting_directional_setups"
    assert result["decision"] in {"WAIT", "NO TRADE"}
    s.bot.get_asset_payout.assert_not_awaited()
    s.supervisor.safe_execute_trade.assert_not_awaited()
    assert not s._attempted_setup_path.exists()


def test_overlapping_decision_tasks_serialize_one_order(monkeypatch, tmp_path):
    s, bars, clock = make_system(monkeypatch, tmp_path)

    async def run():
        submitting = asyncio.Event()
        release = asyncio.Event()

        async def execute(*args, **kwargs):
            submitting.set()
            await release.wait()
            return True

        s.supervisor.safe_execute_trade = AsyncMock(side_effect=execute)
        first = asyncio.create_task(s._evaluate_and_trade(1.105))
        await submitting.wait()
        # A second completed minute arrives while the first click is unresolved.
        advance(bars, 60)
        clock.now = 181.0
        s._quote["observed_at"] = clock.now
        second = asyncio.create_task(s._evaluate_and_trade(1.105))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)

    asyncio.run(run())
    s.supervisor.safe_execute_trade.assert_awaited_once()
    s.risk.record_trade.assert_called_once()
    assert len(s.active_trades) == s._accepted_count == 1


def pending_order(s):
    order = dict(s.bot.last_order, timestamp=datetime.now().isoformat(timespec="microseconds"), signal="BUY")
    s.risk.record_trade(TradeRecord(timestamp=order["timestamp"], asset=s._current_asset,
                                   mode="ftt", signal="BUY", confidence=.95, amount=order["amount"]))
    s.risk.record_trade.reset_mock()
    s.active_trades = [order]
    s._accepted_count = 1
    s._persist_order_state({"status": "accepted", "order": order})
    return order


def run_one_settlement_cycle(s, monkeypatch):
    async def stop_after_cycle(*args):
        raise asyncio.CancelledError
    monkeypatch.setattr(runtime.asyncio, "sleep", stop_after_cycle)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(s._settlement_loop())


def test_missing_broker_result_keeps_pending_without_invented_outcome(monkeypatch, tmp_path):
    s, _, _ = make_system(monkeypatch, tmp_path)
    order = pending_order(s)
    s.bot.get_order_result.return_value = None
    run_one_settlement_cycle(s, monkeypatch)
    assert s.active_trades == [order]
    assert json.loads(s._order_state_path.read_text())["status"] == "accepted"
    s.risk.update_pnl_and_journal.assert_not_called()
    s.supervisor.record_outcome.assert_not_called()
    assert s._trades_today == 0


@pytest.mark.parametrize("outcome,pnl", [("WIN", 103.75), ("LOSS", -123.0), ("DRAW", 0.0)])
def test_settlement_uses_exact_broker_result_and_actual_order(monkeypatch, tmp_path, outcome, pnl):
    s, _, _ = make_system(monkeypatch, tmp_path)
    order = pending_order(s)
    # Deliberately not 82% of the default 200, nor a recalculated quote outcome.
    s.bot.get_order_result.return_value = {"result": outcome, "pnl": pnl}
    run_one_settlement_cycle(s, monkeypatch)
    s.bot.get_order_result.assert_awaited_once_with(order)
    assert order["amount"] == 123.0
    s.risk.update_pnl_and_journal.assert_called_once_with(order["timestamp"], outcome, pnl)
    assert not s.active_trades
    assert json.loads(s._order_state_path.read_text()) is None
    assert s._trades_today == 1
    assert s._daily_profit == (pnl if outcome == "WIN" else 0.0)
    assert s._daily_loss == (-pnl if outcome == "LOSS" else 0.0)
    if outcome == "DRAW":
        s.supervisor.record_outcome.assert_not_called()
    else:
        s.supervisor.record_outcome.assert_called_once_with(outcome == "WIN")


@pytest.mark.parametrize("result", [{"result": "UNKNOWN", "pnl": 0},
                                    {"result": "WIN", "pnl": float("nan")}])
def test_unconfirmed_or_invalid_settlement_remains_pending(monkeypatch, tmp_path, result):
    s, _, _ = make_system(monkeypatch, tmp_path)
    order = pending_order(s)
    s.bot.get_order_result.return_value = result
    run_one_settlement_cycle(s, monkeypatch)
    assert s.active_trades == [order]
    s.risk.update_pnl_and_journal.assert_not_called()
    assert json.loads(s._order_state_path.read_text())["status"] == "accepted"



def restore_system(monkeypatch, tmp_path, state, entry_seconds=60):
    (tmp_path / "execution_state.json").write_text(json.dumps(state))
    monkeypatch.setattr(runtime.settings, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(runtime.settings, "TRADE_AMOUNT", 200.0)
    monkeypatch.setattr(runtime.settings, "JOURNAL_ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(runtime.settings, "RESET_TODAY_LOGS_ON_STARTUP", False)
    risk = RiskManager(max_daily_trades=20, max_daily_loss=2000)
    risk.record_trade = Mock(wraps=risk.record_trade)
    monkeypatch.setattr(runtime, "RiskManager", lambda **kwargs: risk)
    # Keep actual state restoration but replace services with inert dependencies.
    for name in ("OlymtradeBot", "AssetSelectorAgent", "RLFeedbackAgent",
                 "RegimeDetector", "ChartAnalyzer", "MultiTimeframeConsensusAnalyzer",
                 "PipelineSupervisorAgent"):
        monkeypatch.setattr(runtime, name, lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(runtime, "PipelineSupervisorAgent",
                        lambda *args, **kwargs: SimpleNamespace(cb=CircuitBreaker()))
    return runtime.MultiAgentTradingSystem(entry_seconds=entry_seconds)


@pytest.mark.parametrize("state", [
    {"status": "submitting", "direction": "BUY"},
    {"status": "accepted", "order": {
        "id": "existing-order", "timestamp": "2026-09-29T01:00:00",
        "signal": "BUY", "amount": 123.0, "asset": "EUR/USD OTC",
    }},
])
def test_restart_restores_unresolved_execution_before_any_new_decision(monkeypatch, tmp_path, state):
    s = restore_system(monkeypatch, tmp_path, state)
    if state["status"] == "submitting":
        assert s._execution_uncertain
        assert not s.active_trades
        s.risk.record_trade.assert_not_called()
    else:
        assert s.active_trades == [state["order"]]
        s.risk.record_trade.assert_called_once()
        recovered = s.risk.record_trade.call_args.args[0]
        assert recovered.timestamp == state["order"]["timestamp"]
        assert recovered.signal == state["order"]["signal"]
        assert recovered.asset == state["order"]["asset"]
        assert recovered.amount == state["order"]["amount"]
        assert recovered.result == "PENDING"
    assert not s._process_lock_held


@pytest.mark.parametrize("order", [{"id": "incomplete-order"}, {}])
def test_malformed_accepted_state_keeps_execution_blocked(monkeypatch, tmp_path, order):
    state = {"status": "accepted", "order": order}
    s = restore_system(monkeypatch, tmp_path, state)
    assert s._execution_uncertain
    assert s.active_trades == [order]
    s.risk.record_trade.assert_not_called()
    assert json.loads(s._order_state_path.read_text()) == state


@pytest.mark.asyncio
async def test_startup_reconcile_settles_accepted_order_seen_in_history(monkeypatch, tmp_path, caplog):
    s = runtime.MultiAgentTradingSystem.__new__(runtime.MultiAgentTradingSystem)
    s._order_state_path = tmp_path / "execution_state.json"
    s._execution_uncertain = False
    s.active_trades = []
    s._accepted_count = 0
    s.risk = RiskManager(max_daily_trades=20, max_daily_loss=2000)
    s.risk.update_pnl_and_journal = Mock(return_value=True)
    s.risk.record_trade = Mock()
    s.supervisor = SimpleNamespace(execution_uncertain=False, record_outcome=Mock())
    s.bot = SimpleNamespace(
        _order_rows=AsyncMock(side_effect=[[], [{"id": "broker-42", "status": "WIN", "pnl": 10.0}]]),
        get_order_result=AsyncMock(return_value={"result": "WIN", "pnl": 10.0}),
    )
    s._persist_order_state({
        "status": "accepted",
        "order": {"id": "broker-42", "timestamp": "2026-10-13T00:00:00", "signal": "BUY",
                  "amount": 123.0, "asset": "EUR/USD OTC"},
    })
    with caplog.at_level(logging.INFO, logger="multi_agent_orchestrator"):
        await s._reconcile_execution_state()
    assert json.loads(s._order_state_path.read_text()) is None
    assert not s.active_trades
    s.risk.update_pnl_and_journal.assert_called_once_with("2026-10-13T00:00:00", "WIN", 10.0)


@pytest.mark.asyncio
async def test_startup_reconcile_clears_missing_accepted_order_after_attempts(monkeypatch, tmp_path, caplog):
    s = runtime.MultiAgentTradingSystem.__new__(runtime.MultiAgentTradingSystem)
    s._order_state_path = tmp_path / "execution_state.json"
    s._execution_uncertain = False
    s.active_trades = []
    s.risk = RiskManager(max_daily_trades=20, max_daily_loss=2000)
    s.risk.update_pnl_and_journal = Mock()
    s.risk.record_trade = Mock()
    s.supervisor = SimpleNamespace(execution_uncertain=False, record_outcome=Mock())
    s.bot = SimpleNamespace(
        _order_rows=AsyncMock(return_value=[]),
        get_order_result=AsyncMock(return_value=None),
    )
    s._persist_order_state({
        "status": "accepted",
        "order": {"id": "missing-order", "timestamp": "2026-10-13T00:00:00", "signal": "BUY",
                  "amount": 123.0, "asset": "EUR/USD OTC"},
    })
    with caplog.at_level(logging.WARNING, logger="multi_agent_orchestrator"):
        await s._reconcile_execution_state()
    assert json.loads(s._order_state_path.read_text()) is None
    assert "state_cleared reason=order_not_found" in caplog.text
    assert s._execution_uncertain is False


@pytest.mark.asyncio
async def test_startup_reconcile_clears_uncertain_submission_when_no_order_created(monkeypatch, tmp_path, caplog):
    s = runtime.MultiAgentTradingSystem.__new__(runtime.MultiAgentTradingSystem)
    s._order_state_path = tmp_path / "execution_state.json"
    s._execution_uncertain = True
    s.active_trades = []
    s.risk = RiskManager(max_daily_trades=20, max_daily_loss=2000)
    s.risk.record_trade = Mock()
    s.supervisor = SimpleNamespace(execution_uncertain=True)
    s.bot = SimpleNamespace(_order_rows=AsyncMock(return_value=[]))
    s._persist_order_state({
        "status": "submitting",
        "submitted_at": "2026-10-13T00:00:01.000000",
        "direction": "BUY",
        "asset": "EUR/USD OTC",
        "amount": 200.0,
        "duration": 60,
    })
    with caplog.at_level(logging.WARNING, logger="multi_agent_orchestrator"):
        await s._reconcile_execution_state()
    assert json.loads(s._order_state_path.read_text()) is None
    assert s._execution_uncertain is False
    assert "state_cleared reason=uncertain_order_missing" in caplog.text


def test_execution_pause_log_is_emitted_while_entries_are_blocked(caplog):
    s = runtime.MultiAgentTradingSystem.__new__(runtime.MultiAgentTradingSystem)
    s.active_trades = [{"id": "paused-order"}]
    s._execution_uncertain = False
    s._last_pause_log_ts = 0.0
    with caplog.at_level(logging.WARNING, logger="multi_agent_orchestrator"):
        s._log_execution_pause()
    assert "entries_paused reason=active_trades order_id=paused-order" in caplog.text


def test_process_lock_excludes_an_independent_process_then_releases(tmp_path):
    path = tmp_path / "shared.lock"
    code = ("from bot.process_lock import ProcessLock\n"
            "import sys\n"
            "try:\n"
            "    with ProcessLock(sys.argv[1]): pass\n"
            "except RuntimeError:\n"
            "    raise SystemExit(23)\n")
    command = [sys.executable, "-c", code, str(path)]
    with ProcessLock(path):
        blocked = subprocess.run(command, capture_output=True, text=True, timeout=10)
        assert blocked.returncode == 23, blocked.stderr
    allowed = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert allowed.returncode == 0, allowed.stderr


@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("delay", [2.0, 6.0])
def test_real_passing_setup_survives_delayed_trend_response_only_within_window(
        monkeypatch, tmp_path, side, delay):
    from test_closed_strategy import buy_fixture, mirror
    from strategies.closed_candle import evaluate_both
    from agents.agent_pipeline_supervisor import PipelineSupervisorAgent
    from models.predictor import Signal
    s, bars, clock = make_system(monkeypatch, tmp_path, direction=side)
    trend, entry = buy_fixture()
    if side == "SELL":
        trend, entry = mirror(trend), mirror(entry)
    assert evaluate_both(trend, entry)[side]["passed"]
    monkeypatch.setattr(runtime, "evaluate_both", evaluate_both)
    monkeypatch.setattr(runtime.settings, "TRADE_AMOUNT", 200.0)
    monkeypatch.setattr(runtime.settings, "FTT_DURATION", 60)
    monkeypatch.setattr(runtime.settings, "TRAINING_MODE", False)
    s.bot.last_order.update(amount=200.0)
    s.bot.place_ftt_trade = AsyncMock(return_value=True)
    # Real gate -> real supervisor -> real journal risk; only broker I/O is fake.
    s.supervisor = PipelineSupervisorAgent(s.bot, s.risk, s.agent1, None)
    s.supervisor._current_asset = s._current_asset
    bars[30][:] = trend[:-1]
    bars[60][:] = entry
    s._last_evaluation_key = (trend[-2]["ts"], entry[-2]["ts"])
    clock.now = 2401.0
    s._quote["observed_at"] = clock.now
    async def run():
        await s._evaluate_and_trade(entry[-1]["close"])
        s.bot.place_ftt_trade.assert_not_awaited()
        assert s._last_attempted_entry is None
        bars[30].append(trend[-1])
        clock.now = 2400.0 + delay
        s._quote["observed_at"] = clock.now
        await s._evaluate_and_trade(entry[-1]["close"])
        await s._evaluate_and_trade(entry[-1]["close"])
    asyncio.run(run())
    if delay <= s.CONFIRMATION_WINDOW:
        s.bot.place_ftt_trade.assert_awaited_once_with(
            Signal.BUY if side == "BUY" else Signal.SELL, 60, asset=s._current_asset,
            entry_deadline=2405.0)
        assert s._accepted_count == 1
        assert s.risk.daily_snapshot()["accepted_count"] == 1
        assert s.active_trades[0]["signal"] == side
    else:
        s.bot.place_ftt_trade.assert_not_awaited()
        assert s._accepted_count == 0
        assert not s._order_state_path.exists()


@pytest.mark.parametrize("bullish_filter", [True, False])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_real_closed_setup_and_five_minute_filter_reach_broker_only_when_both_pass(
        monkeypatch, tmp_path, bullish_filter, side):
    from test_closed_strategy import buy_fixture, mirror
    from strategies.closed_candle import evaluate_both

    s, bars, clock = make_system(monkeypatch, tmp_path)
    trend, entry = buy_fixture()
    assert evaluate_both(trend, entry)["BUY"]["passed"]
    monkeypatch.setattr(runtime, "evaluate_both", evaluate_both)
    monkeypatch.setattr(runtime.settings, "TRADE_AMOUNT", 200.0)
    monkeypatch.setattr(runtime.settings, "FTT_DURATION", 60)
    monkeypatch.setattr(runtime.settings, "TRAINING_MODE", False)

    # Both broader histories end in the exact same passing 1m BUY candles.
    # Earlier prices determine whether the real 5m EMA direction permits entry.
    prefix = []
    for index in range(80):
        close = 95 + index * .03 if bullish_filter else 150 - index * .3
        prefix.append(dict(open=close, close=close, high=close + .15, low=close - .15,
                           ts=-4800 + index * 60))
    minutes = prefix + entry
    five_minute = []
    for index in range(0, len(minutes), 5):
        group = minutes[index:index + 5]
        five_minute.append(dict(open=group[0]["open"], close=group[-1]["close"],
                                high=max(bar["high"] for bar in group),
                                low=min(bar["low"] for bar in group), ts=group[0]["ts"],
                                source="complete_test_minute_aggregation"))
    if side == "SELL":
        trend, entry, five_minute = mirror(trend), mirror(entry), mirror(five_minute)
    bars[30][:], bars[60][:] = trend, entry
    s.agent1.builders[300] = SimpleNamespace(closed_snapshot=lambda: list(five_minute))
    del s._five_minute_filter  # Use the runtime's real completed-5m EMA filter.
    clock.now = 2401.0
    s._quote.update(price=entry[-1]["close"], observed_at=clock.now)
    actual_filter = s._five_minute_filter(side)
    assert actual_filter["count"] == actual_filter["required"] == 24
    is_bullish = bullish_filter if side == "BUY" else not bullish_filter
    assert actual_filter["direction"] == ("bullish" if is_bullish else "bearish")
    assert actual_filter["passed"] is bullish_filter

    s.bot.last_order.update(amount=200.0)
    s.bot.place_ftt_trade = AsyncMock(return_value=True)
    s.risk.approve = Mock(wraps=s.risk.approve)
    s.supervisor = PipelineSupervisorAgent(s.bot, s.risk, s.agent1, None)
    s.supervisor._current_asset = s._current_asset

    async def run():
        result = await s._evaluate_and_trade(entry[-1]["close"])
        assert result[f"{side.lower()}_evaluation"]["passed"]
        assert result["entry_evaluation"] == result[f"{side.lower()}_evaluation"]
        assert result["direction"] == side
        assert result["trend_filter_5m"]["passed"] is bullish_filter
        assert result["reason"] == ("order_accepted" if bullish_filter else
                                    "5m_bearish" if side == "BUY" else "5m_bullish")
        await s._evaluate_and_trade(entry[-1]["close"])

    asyncio.run(run())
    if bullish_filter:
        s.bot.place_ftt_trade.assert_awaited_once_with(
            runtime.Signal[side], 60, asset=s._current_asset, entry_deadline=2405.0)
        s.risk.approve.assert_called_once()
        s.risk.record_trade.assert_called_once()
        assert s._accepted_count == s.risk.daily_snapshot()["accepted_count"] == 1
        assert len(s.active_trades) == 1
        assert json.loads(s._order_state_path.read_text())["status"] == "accepted"
    else:
        s.bot.place_ftt_trade.assert_not_awaited()
        s.risk.approve.assert_not_called()
        s.risk.record_trade.assert_not_called()
        assert s._accepted_count == s.risk.daily_snapshot()["accepted_count"] == 0
        assert not s._order_state_path.exists()
        assert s._last_attempted_entry is None
