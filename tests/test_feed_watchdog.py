import asyncio
import json
import logging
import os
import time
from types import SimpleNamespace

import pytest

import multi_agent_orchestrator as runtime
from bot.feed_watchdog import FeedWatchdog


class FakePage:
    def __init__(self, *, url="https://example.test", title="1.1234 EUR/USD | Trading platform", visible=True, online=True, banner=""):
        self.url = url
        self.title_text = title
        self.visibilityState = "visible" if visible else "hidden"
        self.onLine = online
        self.banner = banner

    async def evaluate(self, script, *args):
        if "navigator.onLine" in str(script):
            return self.onLine
        if "document.visibilityState" in str(script):
            return self.visibilityState
        if "location.href" in str(script):
            return self.url
        if "document.title" in str(script):
            return self.title_text
        if "visible banner" in str(script):
            return self.banner
        return ""


class FakeFeed:
    def __init__(self):
        self.frames_seen = 0
        self.ticks_seen = 0
        self._last_tick = 0.0
        self._now = 0.0

    def last_tick(self, asset, max_age=None):
        age = self._now - self._last_tick
        if max_age is not None and age > max_age:
            return None
        return {"price": 1.12, "recv": self._last_tick, "age_seconds": age}

    def health(self, asset):
        return {"live": self.ticks_seen > 0 and (self._now - self._last_tick) <= 1.0}


def test_feed_watchdog_detects_ticks_stalled_and_ws_silent(caplog):
    feed = FakeFeed()
    bot = SimpleNamespace(live_feed=feed, _selected_asset="EUR/USD", _page=FakePage())
    system = SimpleNamespace(
        bot=bot,
        _feed_watchdog=None,
        _shutdown=lambda: None,
    )
    watcher = FeedWatchdog(system=system, poll_seconds=1.0, logger=logging.getLogger("watchdog-test"))
    watcher._last_frames = 0
    watcher._last_ticks = 0
    feed.frames_seen = 100
    feed.ticks_seen = 10
    feed._now = 10.0
    feed._last_tick = 9.0
    state = watcher.classify(
        is_alive=True,
        page_url="https://example.test",
        title="1.1234 EUR/USD | Trading platform",
        visibility="visible",
        online=True,
        banner="",
        last_tick_age=0.2,
        frames_seen=feed.frames_seen,
        ticks_seen=feed.ticks_seen,
        native_history_age=1.0,
        last_successful_native_history=5.0,
    )
    assert state == "HEALTHY"

    feed.frames_seen = 120
    feed.ticks_seen = 10
    feed._now = 20.0
    feed._last_tick = 19.0
    state = watcher.classify(
        is_alive=True,
        page_url="https://example.test",
        title="1.1234 EUR/USD | Trading platform",
        visibility="visible",
        online=True,
        banner="",
        last_tick_age=0.5,
        frames_seen=feed.frames_seen,
        ticks_seen=feed.ticks_seen,
        native_history_age=2.0,
        last_successful_native_history=5.0,
    )
    assert state == "TICKS_STALLED"

    feed.frames_seen = 20
    feed.ticks_seen = 20
    feed._now = 30.0
    feed._last_tick = 29.0
    state = watcher.classify(
        is_alive=True,
        page_url="https://example.test",
        title="1.1234 EUR/USD | Trading platform",
        visibility="visible",
        online=True,
        banner="",
        last_tick_age=10.0,
        frames_seen=feed.frames_seen,
        ticks_seen=feed.ticks_seen,
        native_history_age=3.0,
        last_successful_native_history=5.0,
    )
    assert state == "WS_SILENT"


def test_log_flood_control_summarises_repeated_failures(caplog):
    logger = logging.getLogger("flood-test")
    logger.setLevel(logging.WARNING)
    caplog.set_level(logging.WARNING, logger="flood-test")
    from bot.log_flood_control import MessageCollapseFilter

    filt = MessageCollapseFilter(window_seconds=30.0, max_emits=3)
    logger.addFilter(filt)
    try:
        for i in range(100):
            logger.warning("[NativeHistory] status=unavailable timeframe=30 entries=waiting")
        messages = [r.getMessage() for r in caplog.records if "[NativeHistory]" in r.getMessage()]
        assert len(messages) <= 3
        assert messages[0].startswith("[NativeHistory]")
        assert messages[-1].startswith("[NativeHistory]")
    finally:
        logger.removeFilter(filt)


def test_feed_recovery_exit_is_blocked_only_when_orders_are_active(monkeypatch):
    system = runtime.MultiAgentTradingSystem.__new__(runtime.MultiAgentTradingSystem)
    system.active_trades = []
    system._execution_lock = asyncio.Lock()
    system._pending_order_ids = []
    system.running = True
    system.bot = SimpleNamespace()
    system._feed_watchdog = None

    monkeypatch.setenv("FEED_RECOVERY", "exit")
    monkeypatch.setenv("FEED_RECOVERY_AFTER_SECONDS", "5")
    watcher = FeedWatchdog(system=system, poll_seconds=1.0)
    watcher._last_state = "WS_SILENT"
    watcher._state_since = time.monotonic() - 20

    assert watcher.should_exit() is True

    system.active_trades = [{"order_id": "abc"}]
    assert watcher.should_exit() is False

    system.active_trades = []
    system._pending_order_ids = ["abc"]
    assert watcher.should_exit() is False
