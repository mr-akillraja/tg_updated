"""Real populated-history recovery and bounded background-task regressions."""
import asyncio
from unittest.mock import AsyncMock

import pytest

import multi_agent_orchestrator as runtime
from strategies.closed_candle import evaluate_both
from test_candle_data import history
from test_monitoring_runtime import monitoring_system


def populate_native_history(system, now=10801):
    # 179 aligned minutes beginning at minute one contain 35 complete 5m bars.
    system.agent1.seed_from_dataframe(history(179, start=60), source="broker_native_ws", now=now)
    system.agent1.seed_from_dataframe(history(180, period=30, start=5400), timeframe=30,
                                      source="broker_native_ws", now=now)


def test_quote_age_2_002_seconds_preserves_native_history_through_recovery(monkeypatch, tmp_path):
    system, clock, decisions, sleeps = monitoring_system(monkeypatch, tmp_path)
    clock.now = 10801.0
    system.observe_only = True
    populate_native_history(system)
    monkeypatch.setattr(runtime, "evaluate_both", evaluate_both)
    system._five_minute_filter = runtime.MultiAgentTradingSystem._five_minute_filter.__get__(system)
    before = {period: builder.closed_snapshot() for period, builder in system.agent1.builders.items()}
    recorded_counts = []
    publish = system._publish_decision

    def record_counts(context, decision, reason):
        recorded_counts.append([len(system.agent1.builders[p]) for p in (30, 60, 300)])
        return publish(context, decision, reason)

    async def quote(asset):
        return {"price": 1.105, "observed_at": clock.now - (2.002 if clock.now == 10803 else 0),
                "read_at": clock.now}

    def cached_native(asset, period):
        return {"ts": 10800, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.105,
                "observed_at": 10801 if clock.now < 10805 else 10805,
                "source": "broker_native_ws"}

    system._publish_decision = record_counts
    system.bot.get_current_quote.side_effect = quote
    system.bot.get_native_forming_candle.side_effect = cached_native
    asyncio.run(system.run(lock_acquired=True))

    assert [d["evaluated_at"] for d in decisions] == list(range(10801, 10806))
    assert sleeps == [1.0] * 5
    assert recorded_counts == [[180, 70, 35]] * 5
    assert {p: b.closed_snapshot() for p, b in system.agent1.builders.items()} == before
    assert all(d["buy_evaluation"]["reason"] != "insufficient_closed_candles" for d in decisions)
    assert all(d["trend_filter_5m"]["closed_ts"] == 10500 for d in decisions)

    stale = decisions[2]
    assert stale["evaluated_at"] - stale["quote_observed_at"] == pytest.approx(2.002)
    assert stale["decision"] == "NO TRADE" and stale["reason"] == "stale_or_unavailable_quote"
    assert stale["live_price"] is None
    for period in ("30", "60"):
        assert not stale["candles"][period]["current"]
        assert stale["candles"][period]["live_price"] is None
        assert stale["candles"][period]["status"] == "stale"
        recovered = decisions[3]["candles"][period]
        assert recovered["current"] and recovered["candle"]["partial"]
        assert recovered["candle"]["native_observed_at"] == 10801
        assert recovered["candle"]["high"] == 1.2
        assert not decisions[4]["candles"][period]["candle"]["partial"]
        assert decisions[4]["candles"][period]["candle"]["native_observed_at"] == 10805
    system.supervisor.safe_execute_trade.assert_not_awaited()


def test_actual_background_helpers_wait_without_interrupting_monitor_cadence(monkeypatch, tmp_path):
    real_sleep = asyncio.sleep
    system, clock, decisions, _ = monitoring_system(monkeypatch, tmp_path)
    clock.now = 10801.0
    system.observe_only = True
    system.active_trades = [{"id": "pending-demo-order"}]
    system._bg_tasks = []
    system.chart_view = "entry"
    system.bot._page = object()
    system.bot.start = AsyncMock()
    system.bot.login = AsyncMock(return_value=True)
    system.bot.get_flat_ftt_account_evidence = AsyncMock(return_value=None)
    monkeypatch.setattr(runtime, "configure_strategy_chart", AsyncMock(return_value={"verified": True}))
    system.setup = runtime.MultiAgentTradingSystem.setup.__get__(system)
    # Supply history one bar behind so the actual native refresh worker requests
    # the missing closed bar, then simulate a slow asynchronous broker response.
    seeded = {30: history(180, period=30, start=5370), 60: history(179, start=0)}
    for frame in seeded.values():
        frame.attrs["source"] = "broker_native_ws"
    starts = []
    monitor_sleeps = []

    async def scenario():
        monitor_task = asyncio.current_task()
        unavailable = asyncio.Event()
        requests = {30: 0, 60: 0}

        async def bounded_sleep(seconds):
            if asyncio.current_task() is monitor_task:
                await real_sleep(0)
                monitor_sleeps.append(seconds)
                clock.now += seconds
                await real_sleep(0)
            else:
                await unavailable.wait()

        async def native_history(asset, period, count):
            requests[period] += 1
            if requests[period] == 1:
                return seeded[period]
            starts.append(f"native_{period}")
            await unavailable.wait()

        async def market_refresh(asset):
            starts.append("market_refresh")
            await unavailable.wait()

        async def order_result(order):
            starts.append("settlement")
            await unavailable.wait()

        async def finish():
            if system._decision_tasks:
                await asyncio.gather(*system._decision_tasks)
            for task in system._bg_tasks:
                task.cancel()
            await asyncio.gather(*system._bg_tasks, return_exceptions=True)

        monkeypatch.setattr(runtime.asyncio, "sleep", bounded_sleep)
        system.bot.get_native_candles = AsyncMock(side_effect=native_history)
        system.bot.refresh_native_market_data = AsyncMock(side_effect=market_refresh)
        system.bot.get_order_result = AsyncMock(side_effect=order_result)
        system._shutdown = AsyncMock(side_effect=finish)
        # The execution is bounded by five virtual monitor seconds. Broker work
        # remains pending throughout and is cancelled without performing I/O.
        await system.run(lock_acquired=True)

    asyncio.run(scenario())
    assert [d["evaluated_at"] for d in decisions] == list(range(10801, 10806))
    assert monitor_sleeps == [1.0] * 5
    assert system.bot.get_current_quote.await_count == 6  # Setup plus five ticks.
    assert set(starts) == {"native_30", "native_60", "market_refresh", "settlement"}
    assert {task.get_coro().__name__ for task in system._bg_tasks} == {
        "_daily_reset_monitor", "_settlement_loop", "_native_refresh_loop", "_market_refresh_loop",
        "_chart_display_loop"}
    assert all(task.done() for task in system._bg_tasks)
    assert all(d["reason"] == "stale_candle_snapshot" for d in decisions)
    assert all(d["quote_status"] == "fresh" and d["closed_candle_status"] == "stale_or_unavailable"
               for d in decisions)
    system.supervisor.safe_execute_trade.assert_not_awaited()
