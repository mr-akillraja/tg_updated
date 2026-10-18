"""Market freshness and native forming OHLC remain independent of polling."""
import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import multi_agent_orchestrator as runtime
from bot.browser import OlymtradeBot


def test_unchanged_title_does_not_become_fresh_when_read_again(monkeypatch):
    bot = OlymtradeBot()
    bot.is_alive = AsyncMock(return_value=True)
    bot._page = SimpleNamespace(title=AsyncMock(return_value="1.09556 EUR/USD | Trading platform"))
    monkeypatch.setattr("bot.browser.time.time", lambda: 100.0)
    first = asyncio.run(bot.get_current_quote("EUR/USD"))
    assert first["observed_at"] == 100.0
    assert first["stale"]  # Reading an already-open page proves no source update.
    assert not bot._quote_is_fresh(first)
    assert first["freshness"] == "initial_observation_unverified"

    monkeypatch.setattr("bot.browser.time.time", lambda: 101.0)
    unchanged = asyncio.run(bot.get_current_quote("EUR/USD"))
    assert unchanged["stale"]
    assert unchanged["freshness"] == "initial_observation_unverified"

    monkeypatch.setattr("bot.browser.time.time", lambda: 103.0)
    frozen = asyncio.run(bot.get_current_quote("EUR/USD"))
    assert frozen["observed_at"] == 100.0
    assert frozen["ts"] == 100.0
    assert frozen["read_at"] == 103.0
    assert frozen["age_seconds"] == 3.0
    assert frozen["stale"]
    assert not bot._quote_is_fresh(frozen)

    bot._page.title.return_value = "1.09557 EUR/USD | Trading platform"
    changed = asyncio.run(bot.get_current_quote("EUR/USD"))
    assert changed["observed_at"] == 103.0
    assert not changed["stale"]
    assert bot._quote_is_fresh(changed)


def test_tick_used_when_title_path_is_none_and_identity_is_verified(monkeypatch):
    bot = OlymtradeBot()
    bot.is_alive = AsyncMock(return_value=True)
    bot._page = SimpleNamespace(title=AsyncMock(return_value="Price unavailable"))
    monkeypatch.setattr("bot.browser.time.time", lambda: 100.0)
    bot.live_feed._clock = lambda: 100.0
    bot._identity_verified_at["EURUSD"] = 100.0
    bot.live_feed._last["EURUSD"] = {"price": 1.1234, "ts": 99.0, "recv": 99.5}

    async def title_fail(_):
        return None

    bot._get_current_quote_title = title_fail
    quote = asyncio.run(bot.get_current_quote("EUR/USD"))
    assert quote["price"] == 1.1234
    assert quote["source"] == "broker_ws_tick"
    assert quote["observed_at"] == 99.5


def test_no_tick_when_identity_was_never_verified(monkeypatch):
    bot = OlymtradeBot()
    bot.is_alive = AsyncMock(return_value=True)
    bot._page = SimpleNamespace(title=AsyncMock(return_value="Price unavailable"))
    monkeypatch.setattr("bot.browser.time.time", lambda: 100.0)
    bot.live_feed._clock = lambda: 100.0
    bot.live_feed._last["EURUSD"] = {"price": 1.1234, "ts": 99.0, "recv": 99.5}
    bot._get_current_quote_title = AsyncMock(return_value=None)
    assert asyncio.run(bot.get_current_quote("EUR/USD")) is None


@pytest.mark.parametrize("live", [True, False])
def test_native_refresh_loop_skips_repeated_history_while_live_feed_active(monkeypatch, live):
    system = runtime.MultiAgentTradingSystem.__new__(runtime.MultiAgentTradingSystem)
    system.bot = SimpleNamespace(
        live_feed=SimpleNamespace(health=lambda asset: {"live": live}),
        get_native_candles=AsyncMock(return_value=None),
    )
    system._current_asset = "EUR/USD OTC"
    system._seed_sources = {30: "broker_native_ws", 60: "broker_native_ws"}
    system._update_closed_candles = AsyncMock(return_value=set())
    monkeypatch.setattr(runtime.settings, "ONE_INDICATOR_ENABLED", True)
    monkeypatch.setattr(runtime.time, "monotonic", lambda: 10.0)

    async def stop_after_first_sleep(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(runtime.asyncio, "sleep", stop_after_first_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(system._native_refresh_loop())
    if live:
        system.bot.get_native_candles.assert_not_awaited()
        system._update_closed_candles.assert_not_awaited()
    else:
        system._update_closed_candles.assert_awaited()


@pytest.mark.parametrize("quote", [
    {"price": 1.1, "observed_at": 101},
    {"price": 1.1, "observed_at": 97.9},
    {"price": 1.1, "observed_at": 100, "stale": True},
    {"price": 1.1, "observed_at": float("nan")},
    {"price": 1.1},
])
def test_unverifiable_and_stale_quotes_cannot_pass_execution(quote, monkeypatch):
    monkeypatch.setattr("bot.browser.time.time", lambda: 100.0)
    assert not OlymtradeBot._quote_is_fresh(quote)


def test_native_forming_snapshot_keeps_receipt_time_and_is_detached(monkeypatch):
    bot = OlymtradeBot()
    monkeypatch.setattr("bot.browser.time.time", lambda: 125.0)
    bot._receive_market_frame(json.dumps([{"d": [
        {"p": "EURUSD", "tf": 30, "candles": [
            {"t": 90, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15},
            {"t": 120, "open": 1.15, "high": 1.25, "low": 1.05, "close": 1.2},
        ]},
        {"p": "EURUSD", "tf": 60, "candles": [
            {"t": 120, "open": 1.16, "high": 1.26, "low": 1.06, "close": 1.21},
        ]},
    ]}]))
    monkeypatch.setattr("bot.browser.time.time", lambda: 126.0)
    snapshot = bot.get_native_forming_candle("EUR/USD", 30)
    assert snapshot["ts"] == 120
    assert snapshot["observed_at"] == 125
    assert snapshot["source"] == "broker_native_ws"
    assert snapshot["provisional"]
    snapshot["high"] = 99
    assert bot.get_native_forming_candle("EUR/USD", 30)["high"] == 1.25
    assert bot.get_native_forming_candle("EUR/USD", 60)["high"] == 1.26
    assert bot.get_native_forming_candle("EUR/USD OTC", 30) is None
    assert bot.get_native_forming_candle("EUR/USD", 300) is None

    monkeypatch.setattr("bot.browser.time.time", lambda: 150.0)
    assert bot.get_native_forming_candle("EUR/USD", 30) is None
    assert bot.get_native_forming_candle("EUR/USD", 60)["ts"] == 120


def market_packet(*, asset="EURUSD", period=30, start=120, close=1.15, event=10, request_id=None):
    packet = {"e": event, "d": [{"p": asset, "tf": period, "candles": [
        {"t": start, "open": 1.1, "high": 1.2, "low": 1.0, "close": close},
    ]}]}
    if request_id is not None:
        packet["uuid"] = request_id
    return json.dumps([packet])


def title_bot():
    bot = OlymtradeBot()
    bot.is_alive = AsyncMock(return_value=True)
    bot._page = SimpleNamespace(title=AsyncMock(return_value="1.15 EUR/USD | Trading platform"))
    return bot


def test_same_price_native_updates_refresh_freshness_but_title_polls_do_not(monkeypatch):
    bot = title_bot()
    clock = SimpleNamespace(now=125.0)
    monkeypatch.setattr("bot.browser.time.time", lambda: clock.now)
    bot._receive_market_frame(market_packet())
    clock.now = 125.2
    first = asyncio.run(bot.get_current_quote("EUR/USD"))
    assert not first["stale"]
    assert first["observed_at"] == 125.0
    assert first["timestamp_kind"] == "broker_receipt_time"
    assert first["freshness"] == "native_forming_candle_received"
    assert first["freshness_source"] == "broker_native_ws"

    clock.now = 126.0
    poll = asyncio.run(bot.get_current_quote("EUR/USD"))
    assert poll["observed_at"] == 125.0
    clock.now = 127.0
    bot._receive_market_frame(market_packet())
    clock.now = 127.1
    refreshed = asyncio.run(bot.get_current_quote("EUR/USD"))
    assert refreshed["price"] == first["price"]
    assert refreshed["observed_at"] == 127.0
    assert not refreshed["stale"]

    # A new closed-history or unrelated account frame cannot refresh the quote.
    clock.now = 130.0
    bot._receive_market_frame(market_packet(start=90))
    bot._receive_market_frame(json.dumps([{"e": 42, "d": [{"balance": 1000}]}]))
    stale = asyncio.run(bot.get_current_quote("EUR/USD"))
    assert stale["stale"]
    assert stale["observed_at"] == 127.0
    assert stale["timestamp_kind"] == "broker_receipt_time"
    assert bot.get_native_forming_candle("EUR/USD", 30)["observed_at"] == 127.0


@pytest.mark.parametrize("override", [
    {"asset": "EURUSD_OTC"}, {"period": 15}, {"start": 90},
    {"start": 150}, {"close": 1.16}, {"event": 42}, {"close": 99},
])
def test_nonmatching_or_noncurrent_source_frames_cannot_refresh_title(override, monkeypatch):
    bot = title_bot()
    monkeypatch.setattr("bot.browser.time.time", lambda: 125.0)
    bot._receive_market_frame(market_packet(**override))
    quote = asyncio.run(bot.get_current_quote("EUR/USD"))
    assert quote["stale"]
    assert quote["freshness"] == "initial_observation_unverified"


def test_old_and_future_receipts_cannot_support_a_fresh_quote(monkeypatch):
    bot = title_bot()
    clock = SimpleNamespace(now=125.0)
    monkeypatch.setattr("bot.browser.time.time", lambda: clock.now)
    bot._receive_market_frame(market_packet())
    clock.now = 128.0
    assert asyncio.run(bot.get_current_quote("EUR/USD"))["stale"]
    clock.now = 124.0
    assert asyncio.run(bot.get_current_quote("EUR/USD"))["stale"]


@pytest.mark.parametrize("mismatch", ["request", "asset", "period"])
def test_native_history_requires_its_own_matching_response(mismatch, monkeypatch):
    bot = OlymtradeBot()
    bot.get_current_quote = AsyncMock(return_value={"price": 1.15})
    monkeypatch.setattr("bot.browser.time.time", lambda: 125.0)

    async def evaluate(script, request):
        bot._receive_market_frame(market_packet(
            request_id="unrelated" if mismatch == "request" else request["uuid"],
            asset="GBPUSD" if mismatch == "asset" else "EURUSD",
            period=60 if mismatch == "period" else 30, start=60))
        return True

    bot._page = SimpleNamespace(evaluate=evaluate)
    assert asyncio.run(bot.get_native_candles("EUR/USD", 30, response_timeout=.001)) is None
    assert not bot._native_requests
    assert not bot._native_responses


def test_overlapping_small_and_bulk_requests_keep_their_own_response_rows(monkeypatch):
    bot = OlymtradeBot()
    bot.get_current_quote = AsyncMock(return_value={"price": 1.15})
    monkeypatch.setattr("bot.browser.time.time", lambda: 125.0)

    async def run():
        both_sent = asyncio.Event()
        requests = []

        async def evaluate(script, request):
            requests.append(request)
            if len(requests) == 2:
                # Write bulk first, then replace the shared cache with the small
                # response before either caller has consumed its own reply.
                for packet in sorted(requests, key=lambda item: item["d"][0]["limit"], reverse=True):
                    count = packet["d"][0]["limit"] - 2
                    close = 1.15 if count == 4 else 1.16
                    starts = (0, 30, 60, 90, 120) if count == 4 else (60, 90, 120)
                    bot._receive_market_frame(json.dumps([{"e": 10, "uuid": packet["uuid"], "d": [
                        {"p": "EURUSD", "tf": 30, "candles": [
                            {"t": start, "open": 1.1, "high": 1.2, "low": 1.0, "close": close}
                            for start in starts]}]}]))
                both_sent.set()
            await both_sent.wait()
            return True

        bot._page = SimpleNamespace(evaluate=evaluate)
        return await asyncio.gather(bot.get_native_candles("EUR/USD", 30, count=4),
                                    bot.get_native_candles("EUR/USD", 30, count=2))

    bulk, small = asyncio.run(run())
    assert len(bulk) == 4
    assert len(small) == 2
    assert bulk["close"].tolist() == [1.15] * 4
    assert small["close"].tolist() == [1.16] * 2
    assert bot.get_native_forming_candle("EUR/USD", 30)["close"] == 1.16
    assert not bot._native_requests
    assert not bot._native_responses


def test_lightweight_refresh_requests_both_periods_and_bounds_blocked_transport(monkeypatch):
    bot = OlymtradeBot()
    bot.get_current_quote = AsyncMock(return_value={"price": 1.15})
    monkeypatch.setattr("bot.browser.time.time", lambda: 125.0)
    requests = []

    async def evaluate(script, packet):
        requests.append(packet)
        item = packet["d"][0]
        bot._receive_market_frame(market_packet(period=item["size"], request_id=packet["uuid"]))
        return True

    bot._page = SimpleNamespace(evaluate=evaluate)
    assert asyncio.run(bot.refresh_native_market_data("EUR/USD", response_timeout=.01)) == {30: True, 60: True}
    assert {request["d"][0]["size"] for request in requests} == {30, 60}
    assert all(request["d"][0]["limit"] == 4 for request in requests)

    async def blocked(script, packet):
        await asyncio.Event().wait()

    bot._page.evaluate = blocked
    assert asyncio.run(bot.refresh_native_market_data("EUR/USD", response_timeout=.01)) == {30: False, 60: False}
    assert not bot._native_requests
    assert not bot._native_responses


@pytest.mark.parametrize("phase", ["quote", "send"])
@pytest.mark.parametrize("purpose,level", [("closed_history", logging.WARNING),
                                          ("live_quote", logging.DEBUG)])
def test_native_history_bounds_quote_and_send_waits(phase, purpose, level, caplog):
    bot = OlymtradeBot()

    async def blocked(*args):
        await asyncio.Event().wait()

    bot.get_current_quote = (blocked if phase == "quote"
                             else AsyncMock(return_value={"price": 1.15}))
    bot._page = SimpleNamespace(evaluate=blocked)

    async def run():
        # This guard makes a regression fail instead of hanging the test suite.
        return await asyncio.wait_for(bot.get_native_candles(
            "EUR/USD", 30, response_timeout=.01, purpose=purpose), timeout=1)

    with caplog.at_level(logging.DEBUG, logger="browser"):
        assert asyncio.run(run()) is None
    assert not bot._native_requests
    assert not bot._native_responses
    records = [record for record in caplog.records
               if "[NativeHistory] request=unavailable" in record.message]
    assert len(records) == 1
    assert records[0].levelno == level


@pytest.mark.parametrize("phase", ["quote", "send"])
def test_native_history_caller_cancellation_propagates_and_cleans_requests(phase):
    bot = OlymtradeBot()

    async def run():
        blocked_at_phase = asyncio.Event()

        async def blocked(*args):
            blocked_at_phase.set()
            await asyncio.Event().wait()

        bot.get_current_quote = (blocked if phase == "quote"
                                 else AsyncMock(return_value={"price": 1.15}))
        bot._page = SimpleNamespace(evaluate=blocked)
        task = asyncio.create_task(bot.get_native_candles("EUR/USD", 30))
        try:
            await asyncio.wait_for(blocked_at_phase.wait(), timeout=1)
            assert bool(bot._native_requests) == (phase == "send")
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())
    assert not bot._native_requests
    assert not bot._native_responses


@pytest.mark.parametrize("requested_at,expected_starts", [
    (149.9, [90]),
    (150.0, [90, 120]),
])
def test_native_history_uses_request_time_to_confirm_closed_bars(
        requested_at, expected_starts, monkeypatch):
    bot = OlymtradeBot()
    bot.get_current_quote = AsyncMock(return_value={"price": 1.15})
    clock = SimpleNamespace(now=requested_at)
    monkeypatch.setattr("bot.browser.time.time", lambda: clock.now)

    async def evaluate(script, request):
        packet = json.loads(market_packet(start=120, request_id=request["uuid"]))
        packet[0]["d"][0]["candles"].insert(0, {
            "t": 90, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15})
        # The reply crosses a close, but may still contain the pre-close OHLC.
        clock.now = 150.2
        bot._receive_market_frame(json.dumps(packet))
        return True

    bot._page = SimpleNamespace(evaluate=evaluate)
    frame = asyncio.run(bot.get_native_candles("EUR/USD", 30))
    assert [timestamp.timestamp() for timestamp in frame.index] == expected_starts
    assert frame.attrs["closed_only"]
    assert not bot._native_requests
    assert not bot._native_responses
