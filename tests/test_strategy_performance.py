"""Mechanical broker checks remain journalled but never become strategy evidence."""
import csv

import pandas as pd
import pytest

from agents.agent_asset_selector import _load_win_rates_from_journal
from agents.agent_rl_learning import RLFeedbackAgent


def write_journal(path, rows, *, state_column=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["asset", "signal", "result", "pnl"]
    if state_column:
        fields.append("state_key")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def row(result, state_key, *, asset="EUR/USD OTC", signal="BUY"):
    return {"asset": asset, "signal": signal, "result": result,
            "pnl": 170 if result == "WIN" else -200,
            "state_key": state_key}


@pytest.mark.parametrize("relative_path", ["trade_journal.csv", "archive/trade_journal_2026-09-29.csv"])
def test_asset_rates_exclude_test_orders_and_leave_journal_intact(tmp_path, relative_path):
    path = write_journal(tmp_path / relative_path, [
        row("WIN", "DEMO_EXECUTION_TEST"),
        row("LOSS", "30SH|60SH"),
        row("WIN", "30BH|60BH"),
        row("WIN", "DEMO_EXECUTION_TEST", asset="GBP/USD OTC"),
        row("PENDING", "30BH|60BH"),
    ])
    original = path.read_bytes()
    assert _load_win_rates_from_journal(path) == {
        "EUR/USD OTC": {"wins": 1, "losses": 1, "total": 2, "win_rate": 0.5}}
    assert path.read_bytes() == original


def test_asset_rates_keep_legacy_rows_without_test_marker(tmp_path):
    path = write_journal(tmp_path / "legacy.csv", [
        {"asset": "EUR/USD OTC", "signal": "BUY", "result": "WIN", "pnl": 170}
    ], state_column=False)
    assert _load_win_rates_from_journal(path)["EUR/USD OTC"]["total"] == 1


@pytest.mark.parametrize("relative_path", ["trade_journal.csv", "archive/trade_journal_2026-09-29.csv"])
def test_replay_skips_test_rewards_and_transitions(tmp_path, relative_path):
    path = write_journal(tmp_path / relative_path, [
        row("WIN", "DEMO_EXECUTION_TEST"),
        row("LOSS", "30SH|60SH", signal="SELL"),
        row("WIN", "DEMO_EXECUTION_TEST"),
        row("WIN", "30BH|60BH"),
        row("LOSS", "DEMO_EXECUTION_TEST"),
    ])
    original = path.read_bytes()
    agent = RLFeedbackAgent(str(tmp_path / "models"))
    agent.learn_from_logs(str(path))
    assert agent.perf.outcomes == [0, 1]
    assert agent.perf.win_rate == 0.5
    assert dict(agent.q.visits) == {"30SH|60SH": [0, 1]}
    assert "DEMO_EXECUTION_TEST" not in agent.q.table
    assert path.read_bytes() == original


def test_only_test_orders_provide_no_performance_or_learning_history(tmp_path):
    path = write_journal(tmp_path / "trade_journal.csv", [
        row("WIN", "DEMO_EXECUTION_TEST"), row("WIN", "DEMO_EXECUTION_TEST", signal="SELL")])
    assert _load_win_rates_from_journal(path) == {}
    agent = RLFeedbackAgent(str(tmp_path / "models"))
    agent.learn_from_logs(str(path))
    assert agent.perf.outcomes == []
    assert agent.q.table == {}
    assert agent.q.visits == {}


def test_backtest_filters_mechanical_checks_before_sampling_limit(monkeypatch, capsys):
    from scripts import strategy_and_analysis as analysis

    journal = pd.DataFrame([
        dict(row("WIN", "DEMO_EXECUTION_TEST"), timestamp="2026-09-29T03:01:00"),
        dict(row("LOSS", "30SH|60SH", signal="SELL"), timestamp="2026-09-29T03:00:00"),
    ])
    candles = pd.DataFrame(
        {"close": [1.1] * 20},
        index=pd.date_range("2026-09-29T02:30:00", periods=20, freq="min"))
    calls = []
    monkeypatch.setattr(analysis.pd, "read_csv", lambda _: journal.copy())
    monkeypatch.setattr(analysis, "fetch_candles",
                        lambda *args, **kwargs: calls.append(args[0]) or candles)
    monkeypatch.setattr(analysis, "strategy_signal", lambda _: ("SELL", {}))
    analysis.backtest_against_journal(limit=1)
    assert calls == ["EUR/USD OTC"]
    assert "checked=1, matched_direction=1, wins_when_matched=0" in capsys.readouterr().out
