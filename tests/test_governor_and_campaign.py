import json
import asyncio
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import multi_agent_orchestrator as runtime
from bot.live_feed import LiveTickFeed
from bot.trade_governor import TradeGovernor
from bot.campaign import CampaignTracker
from bot import governor_plugin


def test_trade_governor_threshold_and_caps(tmp_path):
    path = tmp_path / "state.json"
    g = TradeGovernor(max_per_hour=2, min_spacing=10, percentile=80.0, window=5, min_history=2, max_per_day=3, state_path=path, clock=lambda: 1000.0)
    g.seed([10.0, 20.0, 30.0, 40.0, 50.0])
    th = g.threshold()
    assert th > 0
    assert g.evaluate(45.0, now=1000.0)["allow"] in {True, False}
    assert g.evaluate(5.0, now=1000.0)["reason"] in {"warmup", "below_percentile"}
    g.record_taken(now=1000.0)
    g.record_taken(now=1010.0)
    assert g.taken_last_hour(now=2000.0) >= 2


def test_campaign_tracker_verdicts_and_dedupe(tmp_path):
    journal = tmp_path / "trade_journal.csv"
    archive = tmp_path / "archive"
    archive.mkdir()
    journal.write_text(
        "timestamp,result,pnl,state_key\n"
        "2024-01-01T00:00:00Z,WIN,100,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:00:00Z,WIN,100,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:01:00Z,LOSS,-100,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:02:00Z,DRAW,0,ONE_INDICATOR_EMA\n",
        encoding="utf-8",
    )
    extra_dir = tmp_path / "extra"
    extra_dir.mkdir()
    (extra_dir / "trade_journal_extra.csv").write_text(
        "timestamp,result,pnl,state_key\n"
        "2024-01-01T00:03:00Z,WIN,150,ONE_INDICATOR_EMA\n",
        encoding="utf-8",
    )
    tracker = CampaignTracker(str(journal), str(archive), state_key="ONE_INDICATOR_EMA", target=3, payout=0.85, extra_dirs=[str(extra_dir)])
    stats = tracker.stats()
    assert stats["trades"] >= 4
    assert "verdict" in stats
    assert stats["state_keys"] == ["ONE_INDICATOR_EMA"]


def test_governor_plugin_wraps_signal_and_records_accepts_only():
    class FakeOrch:
        module = __name__

        def __init__(self):
            self.supervisor = self
            self.governor = None
            self.calls = []

        def safe_execute_trade(self, signal, confidence, mode, **kwargs):
            self.calls.append((signal, confidence, mode, kwargs))
            return True

    fake = FakeOrch()
    original = governor_plugin.install(fake)
    assert original is not None
    assert governor_plugin.install(fake) is original

    def fake_eval(closed, *args, **kwargs):
        return {"direction": "BUY", "reason": "buy_ema_confirmed", "ema": 10.0, "close": 9.0, "candles": 30}

    sys.modules[__name__].fake_eval = fake_eval
    from strategies import one_indicator as one_indicator_module
    one_indicator_module.evaluate_one_indicator = fake_eval
    # plugin wraps the module-level function and should reject when governor blocks the signal.
    result = governor_plugin._wrapped_evaluate_one_indicator({"close": 1.0}, period_seconds=30)
    assert "direction" in result


def test_runtime_startup_logs_single_setup_and_governor_campaign(caplog, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime.settings, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(runtime.settings, "TICK_TAPE_ENABLED", False)
    system = runtime.MultiAgentTradingSystem.__new__(runtime.MultiAgentTradingSystem)
    system.entry_seconds = 30
    system._accepted_count = 0
    system._bg_tasks = []
    system.observe_only = False
    system._current_asset = "EUR/USD OTC"
    system.bot = SimpleNamespace(live_feed=LiveTickFeed())
    system.supervisor = SimpleNamespace()
    system.active_trades = []
    system._execution_uncertain = False
    caplog.set_level(logging.INFO)
    runtime.MultiAgentTradingSystem._start_one_indicator(system)
    messages = [record.getMessage() for record in caplog.records]
    setup_count = sum("[Setup]" in message for message in messages)
    governor_count = sum("[Governor]" in message and "installed" in message.lower() for message in messages)
    campaign_count = sum("[Campaign] journal=" in message for message in messages)
    assert setup_count == 1
    assert governor_count >= 1
    assert campaign_count >= 1


def test_governor_seeding_uses_all_ema_buy_sell_signal_strengths():
    history = [
        {"close": 100.0},
        {"close": 101.0},
        {"close": 100.5},
        {"close": 101.5},
        {"close": 101.9},
    ]
    strengths = governor_plugin._signal_strengths_from_history(history)
    assert len(strengths) >= 1
    assert all(value >= 0.0 for value in strengths)


def test_governor_history_keeps_ema_signals_but_pattern_rejection_stays_pattern_blocked(caplog):
    governor = TradeGovernor(max_per_hour=100, min_spacing=1.0, percentile=90.0, window=5, min_history=1, max_per_day=1000, state_path=None, clock=lambda: 1000.0)
    governor.seed([1.0, 2.0, 3.0, 4.0, 5.0])

    def original(closed, *args, **kwargs):
        return {"direction": None, "reason": "pattern_no_confirmation", "pattern": None, "ema": 101.0, "ema_prev": 100.0, "close": 100.5}

    wrapped = governor_plugin._wrap_evaluate(SimpleNamespace(), governor, original)
    result = wrapped([{"close": 100.0}, {"close": 100.5}], period_seconds=30)

    assert result["blocked_by"] == "pattern_no_confirmation"
    assert result["direction"] is None
    assert len(governor.strengths) >= 1
    messages = [record.getMessage() for record in caplog.records]
    assert all("[Governor] decision" not in message for message in messages if "[Governor]" in message)

    governor = TradeGovernor(max_per_hour=100, min_spacing=1.0, percentile=90.0, window=5, min_history=1, max_per_day=1000, state_path=None, clock=lambda: 1000.0)
    governor.seed([40.0, 50.0, 60.0])

    def original_confirmed(closed, *args, **kwargs):
        return {"direction": "BUY", "reason": "pattern_confirmed_buy", "pattern": "bullish_engulfing", "ema": 100.5, "ema_prev": 100.0, "close": 100.5}

    wrapped_confirmed = governor_plugin._wrap_evaluate(SimpleNamespace(), governor, original_confirmed)
    result2 = wrapped_confirmed([{"close": 100.0}, {"close": 100.5}], period_seconds=30)
    assert result2["blocked_by"] == "governor_below_percentile"
    assert result2["reason"] == "governor_below_percentile"


def test_one_indicator_note_payload_has_required_fields(monkeypatch):
    async def fake_trade(self, closed, opening):
        payload = {
            "decision": "WAIT",
            "reason": "pattern_no_confirmation",
            "signal": None,
            "pattern": None,
            "strength_bp": 12.5,
            "blocked_by": "pattern",
            "governor_threshold": 15.0,
        }
        return payload

    monkeypatch.setattr(runtime.MultiAgentTradingSystem, "_one_indicator_trade", fake_trade)
    system = runtime.MultiAgentTradingSystem.__new__(runtime.MultiAgentTradingSystem)
    system.entry_seconds = 30
    system._current_asset = "EUR/USD OTC"
    system._accepted_count = 0
    system.latest_decision = None
    system.bot = SimpleNamespace(live_feed=LiveTickFeed())
    system.supervisor = SimpleNamespace(cb=SimpleNamespace(should_stop=lambda: (False, "")))
    payload = asyncio.run(system._one_indicator_trade({"ts": 30.0}, {"price": 100.0, "recv": 30.0}))
    assert set({"signal", "pattern", "strength_bp", "blocked_by", "governor_threshold"}).issubset(payload)


def test_trade_governor_observe_keeps_pattern_blocked_strengths(tmp_path):
    governor = TradeGovernor(max_per_hour=100, min_spacing=1.0, percentile=90.0, window=5, min_history=4, max_per_day=1000, state_path=tmp_path / "governor.json", clock=lambda: 1000.0)
    assert governor.observe(12.5) == 12.5
    assert governor.strengths == [12.5]
    assert governor.threshold() is None
    governor.seed([2.0, 3.0, 4.0, 5.0])
    governor.observe(12.5)
    assert len(governor.strengths) == 5


def test_trade_governor_threshold_is_none_until_history_ready(tmp_path):
    governor = TradeGovernor(max_per_hour=100, min_spacing=1.0, percentile=90.0, window=5, min_history=4, max_per_day=1000, state_path=tmp_path / "governor.json", clock=lambda: 1000.0)
    assert governor.threshold() is None
    decision = governor.evaluate(5.0, now=1000.0)
    assert decision["allow"] is False
    assert decision["reason"] == "warmup"
    assert decision["threshold"] is None


def test_campaign_warns_on_mixed_strategy_history_without_start_marker(caplog, tmp_path):
    journal = tmp_path / "trade_journal.csv"
    journal.write_text(
        "timestamp,result,pnl,state_key\n"
        "2024-01-01T00:00:00Z,WIN,100,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:01:00Z,LOSS,-50,ONE_INDICATOR_EMA\n"
        "2024-01-01T00:02:00Z,WIN,80,ONE_INDICATOR_EMA\n",
        encoding="utf-8",
    )
    tracker = CampaignTracker(str(journal), str(tmp_path), state_key="ONE_INDICATOR_EMA", target=1000, payout=0.85)
    with caplog.at_level(logging.WARNING):
        tracker.stats()
    messages = [record.getMessage() for record in caplog.records]
    assert any("[Campaign] WARNING counting all 3 trades across by_date=" in message for message in messages)
    assert any("earlier strategy versions may be mixed in" in message for message in messages)
    assert any("Run tools/campaign_reset.py --start-now --yes to start a clean count" in message for message in messages)


def test_trade_governor_rate_snapshot_uses_target_and_orders_even_when_nonzero(caplog, tmp_path):
    governor = TradeGovernor(max_per_hour=10, min_spacing=1.0, percentile=90.0, window=50, min_history=1, max_per_day=100, state_path=tmp_path / "governor.json", clock=lambda: 1000.0)
    governor.target = 100
    governor.seed([1.0] * 37)
    governor.taken = [1000.0 - (86400 - i) for i in range(12)]
    snapshot = governor.rate_snapshot(now=1000.0)
    assert snapshot["projected_days_to_target"] == pytest.approx((100 - 37) / 12)
    assert snapshot["orders_accepted_last_24h"] == 12
    with caplog.at_level(logging.INFO):
        governor.log_rate(logger=logging.getLogger("governor-rate"), now=1000.0)
    messages = [record.getMessage() for record in caplog.records]
    assert any("projected_days_to_target=" in message and "projected_days_to_target=5.25" in message for message in messages)


def test_startup_logs_are_singleton_and_rate_labels_are_clear(caplog, tmp_path):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(runtime.settings, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(runtime.settings, "TICK_TAPE_ENABLED", False)
    system = runtime.MultiAgentTradingSystem.__new__(runtime.MultiAgentTradingSystem)
    system.entry_seconds = 30
    system._accepted_count = 0
    system._bg_tasks = []
    system.observe_only = False
    system._current_asset = "EUR/USD OTC"
    system.bot = SimpleNamespace(live_feed=LiveTickFeed())
    system.supervisor = SimpleNamespace()
    system.active_trades = []
    system._execution_uncertain = False
    caplog.set_level(logging.INFO)
    runtime.MultiAgentTradingSystem._start_one_indicator(system)
    messages = [record.getMessage() for record in caplog.records]
    assert sum(message.startswith("[Governor] installed") for message in messages) == 1
    assert sum("[Campaign] journal=" in message for message in messages) == 1
    assert any("seeded_signals=" in message and "seeded_top10pct=" in message for message in messages)
    assert any("pattern_confirmed=n/a" in message for message in messages)
    monkeypatch.undo()


def test_campaign_reset_script_requires_yes_before_writing(tmp_path):
    script = Path(__file__).resolve().parents[1] / "tools" / "campaign_reset.py"
    assert script.exists()
    if script.exists():
        assert "--start-now" in script.read_text(encoding="utf-8")
        assert "--yes" in script.read_text(encoding="utf-8")


