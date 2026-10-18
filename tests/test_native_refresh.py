"""Native history refresh integration: no polling ticks masquerade as new bars."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd

import multi_agent_orchestrator as runtime
from agents.agent_multi_timeframe import MultiTimeframeAgent
from bot.browser import OlymtradeBot


def frame(period, last):
    ts = list(range(last - period * 59, last + period, period))
    df = pd.DataFrame({"open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15},
                      index=pd.to_datetime(ts, unit="s", utc=True))
    df.attrs.update(source="broker_native_ws", timeframe_seconds=period)
    return df


def make_system():
    s = runtime.MultiAgentTradingSystem.__new__(runtime.MultiAgentTradingSystem)
    s.agent1 = MultiTimeframeAgent("EUR/USD OTC")
    s._current_asset = "EUR/USD OTC"
    s._seed_sources = {30: "broker_native_ws", 60: "broker_native_ws"}
    s._quote = {"observed_at": 3600.0}
    return s


def test_refresh_native_periods_only_when_their_candles_close(monkeypatch):
    s = make_system()
    s.agent1.seed_from_dataframe(frame(30, 3510), timeframe=30, now=3600)
    s.agent1.seed_from_dataframe(frame(60, 3480), timeframe=60, now=3600)
    monkeypatch.setattr(runtime.time, "time", lambda: 3541.0)
    s.bot = SimpleNamespace(get_native_candles=AsyncMock())
    assert asyncio.run(s._update_closed_candles(99.0)) == set()
    s.bot.get_native_candles.assert_not_awaited()
    monkeypatch.setattr(runtime.time, "time", lambda: 3601.0)
    s.bot.get_native_candles.side_effect = [frame(30, 3570), frame(60, 3540)]
    assert asyncio.run(s._update_closed_candles(99.0)) == {30, 60}
    assert s.agent1.builders[30].get_closes()[-1] == 1.15
    assert s.agent1.builders[60].get_closes()[-1] == 1.15
    assert s.agent1.builders[30].latest_closed_ts == 3570
    assert s.agent1.builders[60].latest_closed_ts == 3540
    s.bot.get_native_candles.reset_mock()
    assert asyncio.run(s._update_closed_candles(999.0)) == set()
    s.bot.get_native_candles.assert_not_awaited()


def test_failed_native_refresh_does_not_create_candle_from_title(monkeypatch):
    s = make_system()
    s.agent1.seed_from_dataframe(frame(30, 3510), timeframe=30, now=3600)
    s.agent1.seed_from_dataframe(frame(60, 3480), timeframe=60, now=3600)
    monkeypatch.setattr(runtime.time, "time", lambda: 3601.0)
    s.bot = SimpleNamespace(get_native_candles=AsyncMock(return_value=None))
    assert asyncio.run(s._update_closed_candles(99.0)) == set()
    assert s.agent1.builders[30].latest_closed_ts == 3510
    assert s.agent1.builders[60].latest_closed_ts == 3480


def test_stalled_timeframe_preserves_history_while_other_refreshes_and_later_recovers(monkeypatch):
    s = make_system()
    s.agent1.seed_from_dataframe(frame(30, 3510), timeframe=30, now=3600)
    s.agent1.seed_from_dataframe(frame(60, 3480), timeframe=60, now=3600)
    old_minutes = s.agent1.builders[60].closed_snapshot()
    monkeypatch.setattr(runtime.time, "time", lambda: 3601.0)
    s.bot = OlymtradeBot()
    s.bot.get_current_quote = AsyncMock(return_value={"price": 1.15})
    native_request = s.bot.get_native_candles
    requests, canceled = [], []
    stall_minute = True

    async def bounded_request(asset, period, count):
        return await native_request(asset, period, count, response_timeout=.01)

    async def evaluate(script, packet):
        period = packet["d"][0]["size"]
        requests.append(period)
        if period == 60 and stall_minute:
            try:
                await asyncio.Event().wait()
            finally:
                canceled.append(period)
        last = 3600 - period
        rows = [dict(t=start, open=1.1, high=1.2, low=1.0, close=1.15)
                for start in range(last - 59 * period, last + period, period)]
        s.bot._receive_market_frame(json.dumps([{
            "e": 10, "uuid": packet["uuid"],
            "d": [{"p": "EURUSD_OTC", "tf": period, "candles": rows}],
        }]))
        return True

    s.bot.get_native_candles = bounded_request
    s.bot._page = SimpleNamespace(evaluate=evaluate)

    async def run():
        nonlocal stall_minute
        # The wrapper's one-second bound catches a regression without hanging pytest.
        closed = await asyncio.wait_for(s._update_closed_candles(None, native_only=True), 1)
        assert closed == {30}
        assert s.agent1.builders[30].latest_closed_ts == 3570
        assert s.agent1.builders[60].closed_snapshot() == old_minutes
        assert canceled == [60]
        assert not s.bot._native_requests and not s.bot._native_responses
        stall_minute = False
        closed = await asyncio.wait_for(s._update_closed_candles(None, native_only=True), 1)
        assert closed == {60}
        assert s.agent1.builders[60].latest_closed_ts == 3540
        assert requests.count(30) == 1 and requests.count(60) == 2
        assert not s.bot._native_requests and not s.bot._native_responses

    asyncio.run(run())
