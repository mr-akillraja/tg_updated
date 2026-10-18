"""Integration regressions for shared closed-candle decisions and execution."""
import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

import multi_agent_orchestrator as runtime
from agents.agent_multi_timeframe import MultiTimeframeAgent
from bot.process_lock import ProcessLock
from agents.agent_pipeline_supervisor import PipelineSupervisorAgent
from models.predictor import Signal


def system(observe_only=True):
    s = runtime.MultiAgentTradingSystem.__new__(runtime.MultiAgentTradingSystem)
    s.entry_seconds = 60
    s.agent1 = MultiTimeframeAgent("EUR/USD OTC")
    s._current_asset = "EUR/USD OTC"
    s._last_evaluation_key = None
    s._last_attempted_entry = None
    s._execution_lock = asyncio.Lock()
    s._process_lock_held = True
    s._execution_uncertain = False
    s._seed_sources = {30: "broker_browser_sample", 60: "broker_browser_sample"}
    s.closed_events = 0
    s._accepted_count = 0
    s.observe_only = observe_only
    s.active_trades = []
    s.trade_amount = 200.0
    s.payout_minimum = 0.80
    s._check_circuit_breaker = lambda: False
    s._check_consecutive_loss_halt = lambda: (False, "")
    s._trade_capacity = lambda: (20, 10, 2000.0)
    s._quote = {"price": 1.1, "observed_at": runtime.time.time()}
    s._five_minute_filter = lambda direction="BUY": {
        "passed": direction == "BUY", "reason": "5m_bullish", "vote": "BUY"}
    return s


def fill_closed(s):
    for t in range(0, 121):
        s.agent1.add_tick(1.1 + t * 0.000001, ts=float(t))


def test_ten_candle_closes_produce_ten_shared_pairs_not_poll_logs(caplog):
    s = system()
    async def run():
        for t in range(0, 301):
            closed = s.agent1.add_tick(1.1 + t * 0.000001, ts=float(t))
            if closed.intersection({30, 60}):
                s.closed_events += 1
            # Even an accidental evaluation every poll cannot duplicate a log.
            await s._evaluate_and_trade(1.1)
    with caplog.at_level(logging.INFO, logger="multi_agent_orchestrator"):
        asyncio.run(run())
    gate_lines = [r.message for r in caplog.records if "OnlyGate]" in r.message]
    assert len(gate_lines) == 20
    for i in range(0, len(gate_lines), 2):
        buy = json.loads(gate_lines[i].split("] ", 1)[1])
        sell = json.loads(gate_lines[i + 1].split("] ", 1)[1])
        assert buy["trend_closed_ts"] == sell["trend_closed_ts"]
        assert buy["entry_closed_ts"] == sell["entry_closed_ts"]
        assert buy["closed_only"] and sell["closed_only"]
        assert not buy["passed"] and not sell["passed"]
    assert s.closed_events == 10


def test_forming_candle_changes_do_not_re_evaluate(caplog):
    s = system()
    fill_closed(s)
    async def run():
        await s._evaluate_and_trade(1.1)
        for t in range(121, 130):
            s.agent1.add_tick(1.5, ts=float(t))
            await s._evaluate_and_trade(1.5)
    with caplog.at_level(logging.INFO, logger="multi_agent_orchestrator"):
        asyncio.run(run())
    assert sum("OnlyGate]" in r.message for r in caplog.records) == 2


def test_failed_order_not_journaled_and_same_entry_not_retried(monkeypatch):
    s = system(observe_only=False)
    monkeypatch.setattr(runtime.time, "time", lambda: 121.0)
    s._quote["observed_at"] = 121.0
    fill_closed(s)
    calls = []
    records = []
    async def execute(*args, **kwargs):
        calls.append(args)
        return False
    async def payout(*args):
        return 0.85
    s.bot = SimpleNamespace(get_asset_payout=payout)
    s.supervisor = SimpleNamespace(safe_execute_trade=execute, execution_uncertain=False)
    s.risk = SimpleNamespace(record_trade=records.append)
    monkeypatch.setattr(runtime, "evaluate_both", lambda *a: {
        "BUY": {"passed": True}, "SELL": {"passed": False}})
    async def run():
        await s._evaluate_and_trade(1.1)
        for t in range(121, 151):
            s.agent1.add_tick(1.1, ts=float(t))
        await s._evaluate_and_trade(1.1)
    asyncio.run(run())
    assert len(calls) == 1
    assert not records


def test_accepted_order_records_actual_amount_and_blocks_overlap(monkeypatch):
    s = system(observe_only=False)
    monkeypatch.setattr(runtime.time, "time", lambda: 121.0)
    s._quote["observed_at"] = 121.0
    fill_closed(s)
    records = []
    calls = []
    async def execute(*args, **kwargs):
        calls.append(args)
        return True
    async def payout(*args):
        return 0.85
    s.bot = SimpleNamespace(get_asset_payout=payout,
        last_order={"id": "demo-order-1", "amount": 123.0, "duration": 60, "payout": 0.85})
    s.supervisor = SimpleNamespace(safe_execute_trade=execute, execution_uncertain=False)
    s.risk = SimpleNamespace(record_trade=records.append)
    monkeypatch.setattr(runtime, "evaluate_both", lambda *a: {
        "BUY": {"passed": True}, "SELL": {"passed": False}})
    async def run():
        await s._evaluate_and_trade(1.1)
        for t in range(121, 181):
            s.agent1.add_tick(1.1, ts=float(t))
        await s._evaluate_and_trade(1.1)
    asyncio.run(run())
    assert len(calls) == len(records) == 1
    assert records[0].amount == 123.0
    assert s.active_trades[0]["id"] == "demo-order-1"


def test_second_orchestrator_cannot_acquire_same_lock(tmp_path):
    path = tmp_path / "shared.lock"
    with ProcessLock(path):
        with pytest.raises(RuntimeError, match="Another unified"):
            with ProcessLock(path):
                pass
    with ProcessLock(path):
        pass


def test_uncertain_click_not_retried():
    calls = []
    class Bot:
        async def place_ftt_trade(self, *a, **kw):
            calls.append(1)
            raise TimeoutError("acceptance unknown")
    risk = SimpleNamespace(approve=lambda *a, **kw: (True, ""))
    supervisor = PipelineSupervisorAgent(Bot(), risk, SimpleNamespace(asset="EUR/USD OTC"), None)
    async def run():
        assert not await supervisor.safe_execute_trade(Signal.BUY, 0.95, "ftt")
        assert not await supervisor.safe_execute_trade(Signal.SELL, 0.95, "ftt")
    asyncio.run(run())
    assert len(calls) == 1
    assert supervisor.execution_uncertain
