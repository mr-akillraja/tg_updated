"""End-to-end durable daily controls and conservative legacy reconciliation."""
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from bot.risk import RiskManager, TradeRecord
from config import settings
import multi_agent_orchestrator as runtime


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "JOURNAL_ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(settings, "RESET_TODAY_LOGS_ON_STARTUP", False)
    monkeypatch.setattr(settings, "MAX_DAILY_TRADES", 2000)
    monkeypatch.setattr(settings, "MAX_DAILY_LOSS_USD", 10000)
    for name in ("OlymtradeBot", "AssetSelectorAgent", "RLFeedbackAgent",
                 "RegimeDetector", "ChartAnalyzer", "MultiTimeframeConsensusAnalyzer"):
        monkeypatch.setattr(runtime, name, lambda *args, **kwargs: SimpleNamespace())
    return RiskManager(max_daily_trades=20, max_daily_loss=2000)


def record(manager, seconds=0, result="PENDING", pnl=0, day_offset=0, mode="ftt", test=False):
    at = datetime.now() + timedelta(days=day_offset, seconds=seconds)
    row = TradeRecord(timestamp=at.isoformat(), asset="EUR/USD OTC", mode=mode,
                      signal="BUY", confidence=.95, amount=200, result=result, pnl=pnl,
                      state_key="DEMO_EXECUTION_TEST" if test else "CLOSED_CANDLE_GATE",
                      settled_at=at.isoformat() if result != "PENDING" else "")
    manager.record_trade(row)
    return row


def proof(**overrides):
    value = dict(source="platform_active_order_list", demo=True,
                 no_active_ftt=True, observed_at=time.time())
    return dict(value, **overrides)


def test_restart_restores_local_controls_and_uses_one_limit_pair(isolated):
    record(isolated, -3, "LOSS", -200)
    record(isolated, -2, "WIN", 170)
    record(isolated, -1)
    system = runtime.MultiAgentTradingSystem()
    assert system.risk.max_daily_trades == system.max_trades_per_day == 20
    assert system.risk.max_daily_loss == system.daily_loss_limit == 2000
    assert system._trades_today == 3
    assert system._settled_today == 2
    assert system._daily_loss == 200
    assert system._daily_profit == 170
    assert system._trade_capacity() == (17, 8, 1600)
    assert not system._check_circuit_breaker()


def test_restart_cannot_bypass_trade_cap(isolated):
    for index in range(20):
        record(isolated, -30 + index, "DRAW")
    system = runtime.MultiAgentTradingSystem()
    assert system._trade_capacity()[0] == 0
    assert system._check_circuit_breaker()


def test_restart_preserves_loss_halt_but_demo_tests_do_not_create_one(isolated):
    for index in range(4):
        record(isolated, -20 + index, "LOSS", -200)
    system = runtime.MultiAgentTradingSystem()
    assert system._check_consecutive_loss_halt()[0]
    # A demo test result cannot reset the strategy's loss streak.
    record(isolated, -1, "WIN", 170, test=True)
    restarted = runtime.MultiAgentTradingSystem()
    assert restarted._check_consecutive_loss_halt()[0]
    assert restarted._account_snapshot["strategy"]["settled_count"] == 4
    assert restarted._account_snapshot["settled_count"] == 5


def test_broker_flatness_releases_only_old_ftt_exposure_without_inventing_results(isolated):
    record(isolated, day_offset=-100)
    record(isolated, day_offset=-2, mode="forex")
    record(isolated, -1)
    original = Path(isolated.journal_path).read_bytes()
    before = isolated.daily_snapshot()
    after = isolated.confirm_flat_ftt_account(proof())
    assert after["pending_exposure"] == before["pending_exposure"] - 200
    assert after["historical_unconfirmed_ftt_count"] == 1
    assert after["settled_count"] == after["wins"] == 0
    assert after["net_pnl"] == 0
    assert Path(isolated.journal_path).read_bytes() == original
    # Proof does not survive restart: broker flatness must be checked again.
    restarted = RiskManager(max_daily_trades=20, max_daily_loss=2000)
    assert restarted.daily_snapshot()["pending_exposure"] == before["pending_exposure"]


@pytest.mark.parametrize("overrides", [
    {"demo": False}, {"no_active_ftt": False}, {"source": "guessed"},
    {"observed_at": 0}, {"observed_at": float("nan")},
])
def test_missing_or_stale_flatness_never_releases_exposure(isolated, overrides):
    record(isolated, day_offset=-100)
    with pytest.raises(ValueError):
        isolated.confirm_flat_ftt_account(proof(**overrides))
    assert isolated.daily_snapshot()["pending_exposure"] == 200


def test_interrupted_demo_check_recovers_without_becoming_strategy_trade(isolated, monkeypatch):
    import asyncio
    import json
    from unittest.mock import AsyncMock
    from scripts import verify_demo_order as smoke
    bot = SimpleNamespace(
        start=AsyncMock(), login=AsyncMock(return_value=True),
        select_asset=AsyncMock(return_value=True), is_demo_account=AsyncMock(return_value=True),
        get_flat_ftt_account_evidence=AsyncMock(return_value=None),
        place_ftt_trade=AsyncMock(return_value=True), stop=AsyncMock(),
        last_order={"id": "demo-check-id", "amount": 200.0, "duration": 60, "demo": True},
    )
    monkeypatch.setattr(smoke, "OlymtradeBot", lambda: bot)
    monkeypatch.setattr(smoke, "RiskManager", lambda **kwargs: isolated)
    def fail_write(*args):
        raise OSError("interrupted journal write")
    monkeypatch.setattr(isolated, "record_trade", fail_write)
    with pytest.raises(OSError, match="interrupted journal"):
        asyncio.run(smoke.verify("BUY", 200.0))
    state = json.loads((Path(settings.LOG_DIR) / "execution_state.json").read_text())
    assert state["order"]["state_key"] == "DEMO_EXECUTION_TEST"
    recovered = runtime.MultiAgentTradingSystem()
    snapshot = recovered.risk.daily_snapshot()
    assert snapshot["accepted_count"] == 1
    assert snapshot["pending_exposure"] == 200.0
    assert snapshot["strategy"]["accepted_count"] == 0
    assert recovered.active_trades[0]["state_key"] == "DEMO_EXECUTION_TEST"
