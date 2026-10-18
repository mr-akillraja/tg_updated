"""Durable, restart-safe trade accounting without duplicate P&L."""
import csv
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot import risk as risk_module
from bot.risk import RiskManager, TradeRecord
from config import settings


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "JOURNAL_ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(settings, "RESET_TODAY_LOGS_ON_STARTUP", False)
    monkeypatch.setattr(settings, "TRAINING_MODE", False)
    monkeypatch.setattr(settings, "MAX_TRADE_LOSS_USD", 300)
    return RiskManager()


def trade(timestamp=None):
    return TradeRecord(timestamp=timestamp or datetime.now().isoformat(), asset="EUR/USD OTC",
                       mode="ftt", signal="BUY", confidence=0.8, amount=100.0)


def journal(manager):
    with open(manager.journal_path, newline="") as handle:
        return list(csv.DictReader(handle))


def test_pending_settlement_updates_pnl_only_once(manager):
    record = trade()
    assert manager.record_trade(record) is True
    assert manager.update_pnl_and_journal(record.timestamp, "WIN", 85) is True
    assert manager.update_pnl_and_journal(record.timestamp, "WIN", 85) is False
    assert manager.status()["daily_pnl"] == 85
    assert manager.status()["trade_count"] == 1
    assert journal(manager)[0]["result"] == "WIN"


def test_restart_does_not_record_or_settle_same_trade_twice(manager):
    record = trade()
    manager.record_trade(record)
    manager.update_pnl_and_journal(record.timestamp, "WIN", 85)
    restarted = RiskManager()
    assert restarted.record_trade(record) is False
    assert restarted.update_pnl_and_journal(record.timestamp, "WIN", 85) is False
    assert restarted.status()["daily_pnl"] == 85
    assert restarted.status()["trade_count"] == 1
    assert len(journal(restarted)) == 1


def test_missing_settlement_raises_without_changing_pnl(manager):
    original = Path(manager.journal_path).read_bytes()
    with pytest.raises(LookupError, match="missing journal trade"):
        manager.update_pnl_and_journal(trade().timestamp, "WIN", 85)
    assert manager.status()["daily_pnl"] == 0
    assert Path(manager.journal_path).read_bytes() == original


def test_failed_atomic_settlement_preserves_pending_record_and_pnl(manager, monkeypatch):
    record = trade()
    manager.record_trade(record)
    original = Path(manager.journal_path).read_bytes()
    def fail_replace(*args, **kwargs):
        raise OSError("test disk failure")
    monkeypatch.setattr(risk_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="test disk failure"):
        manager.update_pnl_and_journal(record.timestamp, "WIN", 85)
    assert manager.status()["daily_pnl"] == 0
    assert Path(manager.journal_path).read_bytes() == original
    assert not list(Path(manager.journal_path).parent.glob(".trade_journal-*.tmp"))


def test_failed_record_write_does_not_increment_daily_count(manager, monkeypatch):
    original = Path(manager.journal_path).read_bytes()
    def fail_write(*args, **kwargs):
        raise OSError("test disk failure")
    monkeypatch.setattr(manager, "_atomic_write_journal", fail_write)
    with pytest.raises(OSError):
        manager.record_trade(trade())
    assert manager.status()["trade_count"] == 0
    assert Path(manager.journal_path).read_bytes() == original


def test_duplicate_pending_record_is_not_appended_or_recounted(manager):
    record = trade()
    assert manager.record_trade(record) is True
    assert manager.record_trade(record) is False
    assert manager.status()["trade_count"] == 1
    assert len(journal(manager)) == 1


def test_conflicting_identity_or_outcome_raises(manager):
    record = trade()
    manager.record_trade(record)
    with pytest.raises(RuntimeError, match="Conflicting journal identity"):
        manager.record_trade(replace(record, amount=999))
    manager.update_pnl_and_journal(record.timestamp, "WIN", 85)
    with pytest.raises(RuntimeError, match="Conflicting settled outcome"):
        manager.update_pnl_and_journal(record.timestamp, "LOSS", -100)
    assert manager.status()["daily_pnl"] == 85


def test_previous_day_pending_survives_startup_archive(manager):
    record = trade((datetime.now() - timedelta(days=1)).isoformat())
    manager.record_trade(record)
    restarted = RiskManager()
    assert len(journal(restarted)) == 1
    assert restarted.record_trade(record) is False
    assert restarted.update_pnl_and_journal(record.timestamp, "WIN", 85) is True
    assert restarted.status()["daily_pnl"] == 0  # journal accounts by entry day


def test_archived_settlement_is_still_idempotent_on_recovery(manager):
    record = trade((datetime.now() - timedelta(days=1)).isoformat())
    manager.record_trade(record)
    manager.update_pnl_and_journal(record.timestamp, "WIN", 85)
    restarted = RiskManager()  # completed prior-day journal is now archived
    assert journal(restarted) == []
    assert restarted.record_trade(record) is False
    assert restarted.update_pnl_and_journal(record.timestamp, "WIN", 85) is False
    assert restarted.status()["trade_count"] == 0
    assert restarted.status()["daily_pnl"] == 0
    assert journal(restarted) == []


def test_explicit_reset_preserves_pending_orders(manager):
    pending, final = trade(), trade()
    manager.record_trade(pending)
    manager.record_trade(final)
    manager.update_pnl_and_journal(final.timestamp, "WIN", 85)
    manager.reset_today()
    assert [row["timestamp"] for row in journal(manager)] == [pending.timestamp]
    assert manager.status()["trade_count"] == 1



def approve(manager, amount=100, **kwargs):
    return manager.approve(SimpleNamespace(value="BUY"), 1.0, "ftt", trade_amount=amount, **kwargs)


def test_snapshot_restores_pending_gross_results_and_demo_separation(manager):
    demo = replace(trade(), state_key="DEMO_EXECUTION_TEST")
    loss, pending = trade(), trade()
    manager.record_trade(demo)
    manager.update_pnl_and_journal(demo.timestamp, "WIN", 85)
    manager.record_trade(loss)
    manager.update_pnl_and_journal(loss.timestamp, "LOSS", -100)
    manager.record_trade(pending)
    expected = manager.daily_snapshot()
    restarted = RiskManager(max_daily_trades=20, max_daily_loss=2000)
    actual = restarted.daily_snapshot()
    assert {key: actual[key] for key in expected if key not in {
        "max_trades", "max_daily_trades", "max_daily_loss", "trades_left", "loss_budget_remaining"
    }} == {key: expected[key] for key in expected if key not in {
        "max_trades", "max_daily_trades", "max_daily_loss", "trades_left", "loss_budget_remaining"
    }}
    assert actual["accepted_count"] == 3
    assert actual["settled_count"] == 2
    assert actual["gross_profit"] == 85
    assert actual["gross_loss"] == 100
    assert actual["net_pnl"] == -15
    assert actual["pending_count"] == 1
    assert actual["pending_exposure"] == 100
    strategy = actual["strategy"]
    assert strategy["accepted_count"] == 2
    assert strategy["settled_count"] == 1
    assert strategy["wins"] == 0
    assert strategy["gross_profit"] == 0
    assert strategy["net_pnl"] == -100
    assert strategy["consecutive_losses"] == 1
    assert strategy["last_loss_at"] == journal(manager)[1]["settled_at"]
    assert restarted.asset_stats["EUR/USD OTC"] == {"wins": 0, "losses": 1, "total": 1}


def test_default_limits_and_explicit_overrides_are_reported(manager, monkeypatch):
    monkeypatch.setattr(settings, "MAX_DAILY_TRADES", 91)
    monkeypatch.setattr(settings, "MAX_DAILY_LOSS_USD", 456)
    default = RiskManager()
    assert default.status()["max_trades"] == 91
    assert default.status()["max_daily_loss"] == 456
    overridden = RiskManager(max_daily_trades=2, max_daily_loss=200)
    assert overridden.status()["max_trades"] == 2
    assert overridden.status()["max_daily_loss"] == 200
    assert settings.MAX_DAILY_TRADES == 91  # instance overrides do not mutate settings


@pytest.mark.parametrize("kwargs", [
    {"max_daily_trades": 0}, {"max_daily_trades": -1}, {"max_daily_trades": 2.5},
    {"max_daily_trades": True}, {"max_daily_loss": 0}, {"max_daily_loss": -100},
    {"max_daily_loss": float("nan")}, {"max_daily_loss": float("inf")},
])
def test_invalid_effective_limits_are_rejected(manager, kwargs):
    before = Path(manager.journal_path).read_bytes()
    with pytest.raises(ValueError):
        RiskManager(**kwargs)
    assert Path(manager.journal_path).read_bytes() == before


def test_startup_reset_setting_never_erases_spent_risk_budget(manager, monkeypatch):
    record = trade()
    manager.record_trade(record)
    manager.update_pnl_and_journal(record.timestamp, "LOSS", -100)
    before = Path(manager.journal_path).read_bytes()
    monkeypatch.setattr(settings, "RESET_TODAY_LOGS_ON_STARTUP", True)
    restarted = RiskManager(max_daily_loss=100)
    assert restarted.status()["halted"] is True
    assert restarted.status()["gross_loss"] == 100
    assert Path(manager.journal_path).read_bytes() == before


def test_gross_loss_cap_is_not_refilled_by_wins_and_exact_remaining_risk_allowed(manager):
    manager = RiskManager(max_daily_trades=10, max_daily_loss=200)
    win, loss = trade(), trade()
    manager.record_trade(win)
    manager.update_pnl_and_journal(win.timestamp, "WIN", 85)
    manager.record_trade(loss)
    manager.update_pnl_and_journal(loss.timestamp, "LOSS", -100)
    assert approve(manager, 101)[0] is False
    assert approve(manager, 100)[0] is True
    pending = trade()
    manager.record_trade(pending)
    assert manager.status()["halted"] is True
    assert approve(manager, 1)[0] is False
    manager.update_pnl_and_journal(pending.timestamp, "WIN", 85)
    assert manager.status()["halted"] is False
    assert approve(manager, 100)[0] is True


def test_rejected_large_trade_does_not_latch_halt_for_smaller_order(manager):
    manager = RiskManager(max_daily_loss=100)
    assert approve(manager, 101)[0] is False
    assert manager.status()["halted"] is False
    assert approve(manager, 100)[0] is True


def test_per_trade_max_loss_is_enforced_in_normal_and_training_modes(manager):
    assert approve(manager, 301)[0] is False
    assert approve(manager, 301, allow_during_training=True)[0] is False
    assert approve(manager, 300)[0] is True


@pytest.mark.parametrize("amount", [0, -1, float("nan"), float("inf")])
def test_invalid_proposed_stakes_fail_closed(manager, amount):
    assert approve(manager, amount)[0] is False


def test_pending_orders_consume_trade_cap_after_restart(manager):
    manager = RiskManager(max_daily_trades=1, max_daily_loss=2000)
    manager.record_trade(trade())
    restarted = RiskManager(max_daily_trades=1, max_daily_loss=2000)
    assert restarted.status()["trades_left"] == 0
    assert approve(restarted)[0] is False


def test_status_rolls_over_without_an_approval_and_retains_prior_pending_exposure(manager, monkeypatch):
    today = datetime.now().replace(hour=23, minute=59, second=0, microsecond=0)
    class Clock(datetime):
        current = today
        @classmethod
        def now(cls, tz=None):
            return cls.current
    monkeypatch.setattr(risk_module, "datetime", Clock)
    old_pending = trade(today.isoformat())
    old_loss = trade((today - timedelta(minutes=1)).isoformat())
    manager.record_trade(old_pending)
    manager.record_trade(old_loss)
    manager.update_pnl_and_journal(old_loss.timestamp, "LOSS", -100)
    Clock.current = today + timedelta(minutes=2)
    snapshot = manager.status()
    assert snapshot["date"] == Clock.current.date().isoformat()
    assert snapshot["trade_count"] == 0
    assert snapshot["gross_loss"] == 0
    assert snapshot["pending_count"] == 1
    assert snapshot["pending_exposure"] == 100
    restarted = RiskManager(max_daily_loss=150)
    assert approve(restarted, 51)[0] is False
    assert approve(restarted, 50)[0] is True
    restarted.update_pnl_and_journal(old_pending.timestamp, "LOSS", -100)
    assert restarted.status()["gross_loss"] == 0
    assert restarted.status()["settled_count"] == 0
    assert restarted.status()["pending_count"] == 0
    assert approve(restarted, 150)[0] is True


def test_daily_snapshot_does_not_expose_mutable_internal_state(manager):
    record = trade()
    manager.record_trade(record)
    manager.update_pnl_and_journal(record.timestamp, "LOSS", -100)
    snapshot = manager.daily_snapshot()
    snapshot["strategy"]["recent_outcomes"][0]["result"] = "WIN"
    assert manager.daily_snapshot()["strategy"]["recent_outcomes"][0]["result"] == "LOSS"


def test_loss_streak_restored_and_previous_cooldown_not_counted_twice(manager):
    base = datetime.now().replace(hour=1, minute=0, second=0, microsecond=0)
    records = []
    for minute in [0, 1, 2, 3, 9]:
        record = replace(trade((base + timedelta(minutes=minute)).isoformat()),
                         result="LOSS", pnl=-100,
                         settled_at=(base + timedelta(minutes=minute, seconds=59)).isoformat())
        records.append(record)
        manager.record_trade(record)
    restarted = RiskManager()
    strategy = restarted.daily_snapshot()["strategy"]
    assert strategy["consecutive_losses"] == 1
    assert strategy["last_loss_at"] == records[-1].settled_at
    assert [item["timestamp"] for item in strategy["recent_outcomes"]] == [item.timestamp for item in records]


def test_old_csv_without_settled_at_remains_readable_and_can_settle(manager):
    record = trade()
    fields = [field for field in manager._JOURNAL_FIELDS if field != "settled_at"]
    with open(manager.journal_path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow({key: getattr(record, key) for key in fields})
    restarted = RiskManager()
    restarted.update_pnl_and_journal(record.timestamp, "WIN", 85)
    assert journal(restarted)[0]["settled_at"]
    assert restarted.status()["daily_pnl"] == 85
