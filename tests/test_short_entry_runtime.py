"""Closed 30s entries exercise the actual gate, filter, risk and order route."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import multi_agent_orchestrator as runtime
from agents.agent_pipeline_supervisor import PipelineSupervisorAgent
from strategies.closed_candle import evaluate_both
from test_closed_strategy import buy_fixture, mirror, short_entry_fixture
from test_execution_state import make_system, restore_system
from test_monitoring_runtime import monitoring_system


def short_system(monkeypatch, tmp_path, entry_seconds=30, bullish_filter=None, direction="BUY"):
    system, bars, clock = make_system(monkeypatch, tmp_path, entry_seconds)
    # The short candle closes at 2430, halfway through the still-open minute.
    bars[30][:] = [dict(bar, ts=bar["ts"] + 30) for bar in short_entry_fixture()]
    bars[60][:] = buy_fixture()[1]  # Last minute still closed at 2400.
    if direction == "SELL":
        bars[30][:], bars[60][:] = mirror(bars[30]), mirror(bars[60])
    if bullish_filter is None:
        bullish_filter = direction == "BUY"
    slope = .05 if bullish_filter else -.05
    five_minute = [dict(open=100 + i * slope, close=100 + i * slope,
                        high=101 + i * slope, low=99 + i * slope, ts=-4800 + i * 300)
                   for i in range(24)]
    system.agent1.builders[300] = SimpleNamespace(closed_snapshot=lambda: list(five_minute))
    del system._five_minute_filter
    clock.now = 2431.0
    system._quote.update(price=bars[30][-1]["close"], observed_at=clock.now)
    monkeypatch.setattr(runtime, "evaluate_both", evaluate_both)
    monkeypatch.setattr(runtime.settings, "TRADE_AMOUNT", 200.0)
    monkeypatch.setattr(runtime.settings, "FTT_DURATION", 60)
    monkeypatch.setattr(runtime.settings, "TRAINING_MODE", False)
    system.bot.last_order.update(amount=200.0)
    system.bot.place_ftt_trade = AsyncMock(return_value=True)
    system.supervisor = PipelineSupervisorAgent(system.bot, system.risk, system.agent1, None)
    system.supervisor._current_asset = system._current_asset
    return system, bars, clock


@pytest.mark.parametrize("entry_seconds", [30, 60])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_half_minute_close_reaches_actual_order_route_only_in_short_mode(monkeypatch, tmp_path, entry_seconds, direction):
    system, _, _ = short_system(monkeypatch, tmp_path, entry_seconds, direction=direction)

    async def run():
        result = await system._evaluate_and_trade(system._quote["price"])
        assert result[f"{direction.lower()}_evaluation"]["passed"]
        assert result["entry_evaluation"] == result[f"{direction.lower()}_evaluation"]
        assert result["direction"] == direction
        assert result["trend_filter_5m"]["passed"]
        assert result["entry_timeframe_seconds"] == entry_seconds
        await system._evaluate_and_trade(system._quote["price"])
        return result

    result = asyncio.run(run())
    if entry_seconds == 30:
        assert result["reason"] == "order_accepted"
        system.bot.place_ftt_trade.assert_awaited_once_with(
            runtime.Signal[direction], 60, asset=system._current_asset, entry_deadline=2435.0)
        assert system.risk.daily_snapshot()["accepted_count"] == 1
        assert system.active_trades[0]["entry_timeframe_seconds"] == 30
        assert system.active_trades[0]["duration"] == 60
        assert result["entry_timing"]["age_seconds"] == 1
        stored = json.loads(system._order_state_path.read_text())
        assert stored["status"] == "accepted"
        assert stored["order"]["entry_ts"] == 2400
        assert stored["order"]["signal"] == direction
    else:
        assert result["reason"] == "awaiting_next_closed_confirmation"
        system.bot.place_ftt_trade.assert_not_awaited()
        assert system.risk.daily_snapshot()["accepted_count"] == 0


@pytest.mark.parametrize("blocker, expected", [
    ("five_minute", "opposite_filter"),
    ("stale_trend", "stale_candle_snapshot"),
    ("stale_minute", "stale_candle_snapshot"),
    ("stale_quote", "stale_or_unavailable_quote"),
    ("expired_confirmation", "awaiting_next_closed_confirmation"),
    ("risk", "risk_limit_or_loss_halt"),
    ("loss_halt", "risk_limit_or_loss_halt"),
    ("capacity", "capacity"),
    ("payout", "payout_unavailable_or_below_minimum"),
    ("pending", "order_unresolved"),
    ("unresolved", "order_unresolved"),
    ("observe_only", "observe_only"),
])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_short_entry_keeps_execution_safeguards(monkeypatch, tmp_path, blocker, expected, direction):
    filter_is_bullish = (direction == "BUY") != (blocker == "five_minute")
    system, bars, clock = short_system(monkeypatch, tmp_path, bullish_filter=filter_is_bullish,
                                      direction=direction)
    if blocker == "stale_trend":
        # Preserve the actual passing fixture while making its timestamp stale.
        for candle in bars[30]:
            candle["ts"] -= 30
    elif blocker == "stale_minute":
        bars[60].pop()
    elif blocker == "stale_quote":
        system._quote["observed_at"] -= 3
    elif blocker == "expired_confirmation":
        clock.now = 2436
        system._quote["observed_at"] = clock.now
    elif blocker == "risk":
        system._check_circuit_breaker = lambda: True
    elif blocker == "loss_halt":
        system._check_consecutive_loss_halt = lambda: (True, "loss halt")
    elif blocker == "capacity":
        system._trade_capacity = lambda: (0, 0, 0)
    elif blocker == "payout":
        system.bot.get_asset_payout.return_value = .79
    elif blocker == "pending":
        system.active_trades = [{"id": "pending"}]
    elif blocker == "unresolved":
        system._execution_uncertain = True
    elif blocker == "observe_only":
        system.observe_only = True
    result = asyncio.run(system._evaluate_and_trade(system._quote["price"]))
    assert result[f"{direction.lower()}_evaluation"]["passed"]
    if expected == "opposite_filter":
        expected = "5m_bearish" if direction == "BUY" else "5m_bullish"
    elif expected == "observe_only":
        expected = f"confirmed_{direction.lower()}_observe_only"
        assert result["decision"] == direction
        assert result["blocking_stage"] == "confirmation"
    assert result["reason"] == expected
    system.bot.place_ftt_trade.assert_not_awaited()
    assert not system._order_state_path.exists()


@pytest.mark.parametrize("delay", [3, 6])
@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_delayed_short_close_is_reconsidered_only_within_confirmation_window(monkeypatch, tmp_path, delay, direction):
    system, bars, clock = short_system(monkeypatch, tmp_path, direction=direction)
    final = bars[30].pop()

    async def run():
        result = await system._evaluate_and_trade(system._quote["price"])
        assert result["reason"] == "stale_candle_snapshot"
        system.bot.place_ftt_trade.assert_not_awaited()
        bars[30].append(final)
        clock.now = 2430 + delay
        system._quote["observed_at"] = clock.now
        return await system._evaluate_and_trade(system._quote["price"])

    result = asyncio.run(run())
    assert result["reason"] == ("order_accepted" if delay <= 5 else "awaiting_next_closed_confirmation")
    assert system.bot.place_ftt_trade.await_count == (1 if delay <= 5 else 0)


@pytest.mark.parametrize("direction", ["BUY", "SELL"])
def test_short_confirmation_expiring_during_payout_cannot_submit(monkeypatch, tmp_path, direction):
    system, _, clock = short_system(monkeypatch, tmp_path, direction=direction)

    async def payout(asset):
        clock.now = 2436
        system._quote["observed_at"] = clock.now
        return .85

    system.bot.get_asset_payout.side_effect = payout
    result = asyncio.run(system._evaluate_and_trade(system._quote["price"]))
    assert result["reason"] == "awaiting_next_closed_confirmation"
    system.bot.place_ftt_trade.assert_not_awaited()


def test_real_short_sell_confirmation_requires_matching_bearish_filter(monkeypatch, tmp_path):
    system, bars, _ = short_system(monkeypatch, tmp_path, direction="SELL", bullish_filter=True)
    assert evaluate_both(bars[30], bars[30], 30)["SELL"]["passed"]
    result = asyncio.run(system._evaluate_and_trade(system._quote["price"]))
    assert not result["buy_evaluation"]["passed"]
    assert result["sell_evaluation"]["passed"]
    assert result["direction"] == "SELL"
    assert result["reason"] == "5m_bullish"
    assert result["blocking_stage"] == "trend_filter"
    system.bot.place_ftt_trade.assert_not_awaited()


def test_restart_preserves_attempts_for_both_periods_even_after_switching_back(monkeypatch, tmp_path):
    system, _, _ = short_system(monkeypatch, tmp_path)
    system.bot.place_ftt_trade.return_value = False
    asyncio.run(system._evaluate_and_trade(system._quote["price"]))
    restored = restore_system(monkeypatch, tmp_path, None, entry_seconds=30)
    assert restored._setup_already_attempted(2400)
    restored.entry_seconds = 60
    assert not restored._setup_already_attempted(2400)
    restored._persist_attempted_entry(2340)
    again = restore_system(monkeypatch, tmp_path, None, entry_seconds=30)
    assert again._setup_already_attempted(2400)
    again.entry_seconds = 60
    assert again._setup_already_attempted(2340)
    assert not again._execution_uncertain


def test_legacy_attempt_file_is_interpreted_as_minute_entry(monkeypatch, tmp_path):
    (tmp_path / "last_entry_setup.json").write_text(json.dumps({
        "asset": runtime.settings.ASSET, "entry_ts": 2340}))
    system = restore_system(monkeypatch, tmp_path, None, entry_seconds=30)
    assert not system._setup_already_attempted(2340)
    system.entry_seconds = 60
    assert system._setup_already_attempted(2340)


@pytest.mark.parametrize("entries", [{"15": 2400}, {"30": None}, {"30": "nan"}, []])
def test_invalid_durable_attempts_keep_execution_blocked(monkeypatch, tmp_path, entries):
    (tmp_path / "last_entry_setup.json").write_text(json.dumps({
        "asset": runtime.settings.ASSET, "entry_ts": 2400,
        "entry_timeframe_seconds": 30, "entries_by_timeframe": entries}))
    system = restore_system(monkeypatch, tmp_path, None, entry_seconds=30)
    assert system._execution_uncertain


def test_default_entry_uses_short_period_and_invalid_period_fails_before_services(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime.settings, "STRATEGY_ENTRY_SECONDS", 30)
    system = restore_system(monkeypatch, tmp_path, None, entry_seconds=None)
    assert system.entry_seconds == 30
    assert system._entry_chart_view() == "entry30s"
    with pytest.raises(ValueError, match="30 or 60"):
        runtime.MultiAgentTradingSystem(entry_seconds=15)


def test_short_live_entry_diagnostics_use_entry_indicators_without_confirming(monkeypatch, tmp_path):
    system, clock, _, _ = monitoring_system(monkeypatch, tmp_path)
    system.entry_seconds = 30
    history = short_entry_fixture()
    for period in (30, 60):
        system.agent1.builders[period].candles.clear()
    system.agent1.builders[30].candles.extend(history)
    system.agent1.builders[60].candles.extend(buy_fixture()[1])
    clock.now = 2401
    system._quote.update(observed_at=clock.now, read_at=clock.now, price=104)
    system._update_forming_candles(104)
    system.observe_only = True
    result = asyncio.run(system._evaluate_and_trade(104))
    entry = result["entry_candle"]
    trend = result["candles"]["30"]
    assert entry["timeframe_seconds"] == trend["timeframe_seconds"] == 30
    assert {"ema9", "ema21"} <= entry["indicators"].keys()
    assert {"ema20", "ema50"} <= trend["indicators"].keys()
    assert entry["provisional"] and not entry["can_confirm_entry"]
    assert result["entry_timing"]["entry_close_at"] == 2400
    system.supervisor.safe_execute_trade.assert_not_awaited()


@pytest.mark.parametrize("second", [0, 5, 9, 17, 20, 29, 30, 35, 39, 47, 50, 59])
def test_short_chart_updates_reserve_both_entry_windows(monkeypatch, tmp_path, second):
    system, clock, _, _ = monitoring_system(monkeypatch, tmp_path)
    system.entry_seconds = 30
    clock.now = 120 + second
    reconcile = AsyncMock()
    monkeypatch.setattr(runtime, "reconcile_selected_strategy_chart", reconcile)
    asyncio.run(system._sync_chart_display())
    reconcile.assert_not_awaited()


@pytest.mark.parametrize("second", [10, 16, 40, 46])
def test_short_chart_updates_use_entry_profile_between_windows(monkeypatch, tmp_path, second):
    system, clock, _, _ = monitoring_system(monkeypatch, tmp_path)
    system.entry_seconds = 30
    system.bot._page = object()
    clock.now = 120 + second
    reconcile = AsyncMock(return_value={"verified": True, "view": "entry30s"})
    monkeypatch.setattr(runtime, "reconcile_selected_strategy_chart", reconcile)
    asyncio.run(system._sync_chart_display())
    reconcile.assert_awaited_once_with(system.bot._page, timeout_seconds=8, preferred_view="entry30s")
    assert not system._execution_lock.locked()
