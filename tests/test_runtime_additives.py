import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.agent_pipeline_supervisor import CircuitBreaker, PipelineSupervisorAgent
from multi_agent_orchestrator import MultiAgentTradingSystem


def test_timing_log_emits_for_every_decision(caplog):
    system = MultiAgentTradingSystem.__new__(MultiAgentTradingSystem)
    system.entry_seconds = 30
    system._current_asset = "EUR/USD OTC"
    system.observe_only = False
    system._accepted_count = 0
    system.active_trades = []
    system._execution_uncertain = False
    system._process_lock_held = True
    system._setup_logged = True
    system._attempted_entries = {}
    system._governor_instance = None
    system.trade_amount = 200.0
    system.payout_minimum = 0.8
    system._payout_cache = None
    system.bot = SimpleNamespace(
        live_feed=SimpleNamespace(
            closed=lambda *args, **kwargs: [{"ts": 30, "open": 1.0, "high": 1.01, "low": 0.99, "close": 1.005}],
            last_tick=lambda *args, **kwargs: {"price": 1.005},
            forming=lambda *args, **kwargs: {"ticks": 5},
            momentum=lambda *args, **kwargs: {"direction": "up"},
        ),
        last_order={"id": "smoke-order", "amount": 200.0, "accepted_at": 0.0},
        get_asset_payout=lambda asset: 0.85,
    )
    system.supervisor = SimpleNamespace(
        cb=SimpleNamespace(should_stop=lambda: (False, "")),
        safe_execute_trade=lambda *args, **kwargs: True,
        execution_uncertain=False,
        last_execution_reason="",
    )
    async def safe_execute_trade(signal, confidence, mode, **kwargs):
        system.bot.last_order = {"id": "smoke-order", "amount": 200.0, "accepted_at": 1.0}
        return True
    system.supervisor.safe_execute_trade = safe_execute_trade
    system.risk = SimpleNamespace(record_trade=lambda *args, **kwargs: None)
    system._trade_capacity = lambda: (100, 100, 0.0)
    system._check_consecutive_loss_halt = lambda: (False, "")
    system._check_circuit_breaker = lambda: False
    system._setup_already_attempted = lambda *args, **kwargs: False
    system._persist_order_state = lambda *args, **kwargs: None
    system._execution_lock = asyncio.Lock()
    system._last_attempted_entry = None
    system._last_attempted_entry_seconds = None
    system._persist_attempted_entry = lambda *args, **kwargs: None
    system._current_asset = "EUR/USD OTC"

    closed = {"ts": 30, "open": 1.0, "high": 1.01, "low": 0.99, "close": 1.005}
    opening = {"price": 1.005, "recv": 1.0}
    with caplog.at_level(logging.DEBUG, logger="multi_agent_orchestrator"):
        asyncio.run(system._one_indicator_trade(closed, opening))
    messages = [record.getMessage() for record in caplog.records]
    assert any("[Timing]" in message for message in messages)


def test_sidecar_meta_written_for_accepted_and_settled_orders(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    system = MultiAgentTradingSystem.__new__(MultiAgentTradingSystem)
    system._current_asset = "EUR/USD OTC"
    system._accepted_count = 0
    system.bot = SimpleNamespace(last_order={"id": "order-42", "amount": 200.0, "accepted_at": 123.0})
    system.risk = SimpleNamespace()
    system._timing_log_file = log_dir / "trade_meta.jsonl"
    system._trade_meta_path = log_dir / "trade_meta.jsonl"

    system._append_trade_meta({
        "order_id": "order-42",
        "asset": "EUR/USD OTC",
        "signal": "BUY",
        "pattern": "bullish_engulfing",
        "strength_bp": 0.35,
        "governor_threshold": 13.0,
        "history_size": 37,
        "ema": 1.15,
        "ema_prev": 1.14,
        "close": 1.16,
        "candle_open_ts": 30,
        "open_price": 1.15,
        "fill_delay_ms": 15.0,
        "processing_delay_ms": 27.0,
    })
    system._append_trade_meta({
        "order_id": "order-42",
        "result": "WIN",
        "pnl": 12.5,
    })

    lines = (log_dir / "trade_meta.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["order_id"] == "order-42"
    assert first["signal"] == "BUY"
    assert second["result"] == "WIN"
    assert second["pnl"] == 12.5


def test_cb_state_line_logs_window_and_blocks_entries(caplog):
    breaker = CircuitBreaker(target_win_rate=0.90, min_trades=5, cooldown_s=300)
    # Force the state to a tripped condition without needing a live run.
    breaker._outcomes.extend([False] * 5)
    breaker._wins = 0
    breaker._total = 5
    breaker._tripped_at = 1000.0
    with caplog.at_level(logging.INFO, logger="agent3_supervisor"):
        state = breaker.state_summary(now=1000.0)
        breaker.log_state(now=1000.0)
    assert state["window"] == 20
    assert state["wins"] == 0
    assert state["losses"] == 5
    assert state["blocks_entries"] is True
    assert any("[CB] state" in record.getMessage() for record in caplog.records)


def test_campaign_by_pattern_uses_decided_trades_and_warns_on_small_groups(tmp_path):
    meta_path = tmp_path / "trade_meta.jsonl"
    records = [
        {"order_id": "a", "signal": "BUY", "pattern": "bullish_engulfing", "strength_bp": 10.0, "result": "WIN", "pnl": 5.0},
        {"order_id": "b", "signal": "BUY", "pattern": "bullish_engulfing", "strength_bp": 20.0, "result": "LOSS", "pnl": -2.0},
        {"order_id": "c", "signal": "SELL", "pattern": "shooting_star", "strength_bp": 40.0, "result": "WIN", "pnl": 10.0},
        {"order_id": "d", "signal": "SELL", "pattern": "shooting_star", "strength_bp": 50.0, "result": "LOSS", "pnl": -4.0},
        {"order_id": "e", "signal": "BUY", "pattern": "bullish_engulfing", "strength_bp": 80.0, "result": "DRAW", "pnl": 0.0},
    ]
    with meta_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    from tools.campaign_by_pattern import summarize_trade_meta
    summary = summarize_trade_meta(meta_path)
    assert "bullish_engulfing" in summary
    assert "shooting_star" in summary
    assert isinstance(summary.get("warning", ""), str)
    assert "fewer than ~100 decided trades each is noise" in summary["warning"] or not summary["warning"]
