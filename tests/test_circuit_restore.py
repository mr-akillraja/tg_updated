"""Circuit history and cooldowns survive restarts without changing thresholds."""
from datetime import datetime

import pytest

from agents import agent_pipeline_supervisor as module
from agents.agent_pipeline_supervisor import CircuitBreaker


@pytest.fixture
def clock(monkeypatch):
    current = [10_000.0]
    monkeypatch.setattr(module.time, "time", lambda: current[0])
    return current


def outcome(timestamp, result, *, entry_timestamp=None, settled=True):
    return {"timestamp": datetime.fromtimestamp(
                timestamp if entry_timestamp is None else entry_timestamp).isoformat(),
            "settled_at": datetime.fromtimestamp(timestamp).isoformat() if settled else "",
            "result": result, "pnl": 170 if result == "WIN" else -200}


def test_window_evicts_old_wins_and_losses_and_never_exceeds_twenty(clock):
    breaker = CircuitBreaker(target_win_rate=0)
    for won in [True] * 25 + [False] * 10:
        breaker.record(won)
    assert breaker._total == 20
    assert breaker._wins == 10
    assert breaker.win_rate == 0.5
    for _ in range(15):
        breaker.record(False)
    assert breaker._total == 20
    assert breaker._wins == 0
    assert breaker.win_rate == 0


def test_restore_preserves_remaining_cooldown_from_settlement(clock):
    rows = [outcome(9900 + index * 5, "LOSS", entry_timestamp=9000 + index)
            for index in range(5)]
    breaker = CircuitBreaker()
    breaker.restore_from_outcomes(list(reversed(rows)))
    assert breaker._tripped_at == 9920
    assert breaker.should_stop() == (True, "Circuit breaker cooling down — 220s left")
    clock[0] += 30
    restarted = CircuitBreaker()
    restarted.restore_from_outcomes(rows)
    assert restarted._tripped_at == 9920
    assert restarted.should_stop() == (True, "Circuit breaker cooling down — 190s left")


def test_expired_restored_cooldown_does_not_start_again(clock):
    breaker = CircuitBreaker()
    breaker.restore_from_outcomes([outcome(9600 + index, "LOSS") for index in range(5)])
    assert breaker.should_stop() == (False, "")
    assert breaker._total == breaker._wins == 0
    assert breaker._tripped_at is None


def test_later_outcomes_start_new_window_after_historic_cooldown(clock):
    rows = [outcome(9600 + index, "LOSS") for index in range(5)]
    rows += [outcome(9950, "WIN"), outcome(9960, "LOSS")]
    breaker = CircuitBreaker()
    breaker.restore_from_outcomes(rows)
    assert breaker._total == 2
    assert breaker._wins == 1
    assert breaker.win_rate == 0.5
    assert breaker.should_stop() == (False, "")


def test_restore_falls_back_to_entry_time_and_ignores_nondirectional_results(clock):
    rows = [outcome(9900 + index, "LOSS", settled=False) for index in range(5)]
    rows += [outcome(9990, "DRAW"), outcome(9991, "FAILED"), outcome(9992, "PENDING")]
    breaker = CircuitBreaker()
    breaker.restore_from_outcomes(rows)
    assert breaker._total == 5
    assert breaker._tripped_at == 9904
    assert breaker.should_stop()[0] is True


def test_live_and_restored_state_agree_after_cooldown_reset(clock):
    rows = [outcome(9500 + index, "LOSS") for index in range(5)]
    rows += [outcome(9850 + index, result)
             for index, result in enumerate(["WIN", "WIN", "WIN", "WIN", "LOSS"])]
    live = CircuitBreaker()
    for row in rows:
        clock[0] = datetime.fromisoformat(row["settled_at"]).timestamp()
        live.record(row["result"] == "WIN")
    clock[0] = 10_000
    restored = CircuitBreaker()
    restored.restore_from_outcomes(rows)
    assert (restored._wins, restored._total, restored._tripped_at) == (
        live._wins, live._total, live._tripped_at)
    assert restored.should_stop() == live.should_stop()
    assert restored._tripped_at == 9854
    clock[0] = 10_154
    assert live.should_stop() == restored.should_stop() == (False, "")
    assert live._total == restored._total == 0


def test_restore_empty_history_clears_previous_day_state(clock):
    breaker = CircuitBreaker()
    for _ in range(5):
        breaker.record(False)
    assert breaker.should_stop()[0] is True
    breaker.restore_from_outcomes([])
    assert breaker.should_stop() == (False, "")
    assert breaker._total == 0
