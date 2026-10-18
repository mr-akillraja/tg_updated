import asyncio
import json
import logging
import os
import re
import time
from typing import Optional


class FeedWatchdog:
    """Read-only diagnostics for feed outages and login/session failures."""

    def __init__(self, system, *, poll_seconds: float = 5.0, logger: Optional[logging.Logger] = None):
        self.system = system
        self.poll_seconds = float(poll_seconds)
        self.logger = logger or logging.getLogger("feed_watchdog")
        self._last_state = "HEALTHY"
        self._state_since = time.monotonic()
        self._last_summary_ts = 0.0
        self._last_frames = 0
        self._last_ticks = 0
        self._last_outage_start = None
        self._last_recovery_blocked_log = 0.0

    @staticmethod
    def _normalise(text):
        return (text or "").strip()

    @staticmethod
    def _banner_from_text(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text or "")
        if len(cleaned) > 200:
            cleaned = cleaned[:200]
        return cleaned

    def _state_url_text(self, url: str, title: str, banner: str) -> Optional[str]:
        pool = " ".join(part for part in (url, title, banner) if part)
        pool = pool.lower()
        if any(token in pool for token in ("log in", "login", "sign in", "session expired", "maintenance", "reconnect", "connect")):
            return "login_or_session_banner"
        return None

    def classify(self, *, is_alive: bool, page_url: str, title: str, visibility: str,
                 online: bool, banner: str, last_tick_age: float, frames_seen: int,
                 ticks_seen: int, native_history_age: float, last_successful_native_history: float) -> str:
        previous_frames = getattr(self, "_last_frames", frames_seen)
        previous_ticks = getattr(self, "_last_ticks", ticks_seen)

        if not is_alive:
            self._last_frames = frames_seen
            self._last_ticks = ticks_seen
            return "PAGE_DEAD"
        if not online:
            self._last_frames = frames_seen
            self._last_ticks = ticks_seen
            return "OFFLINE"
        if self._state_url_text(page_url, title, banner) is not None:
            self._last_frames = frames_seen
            self._last_ticks = ticks_seen
            return "LOGGED_OUT_SUSPECTED"

        if frames_seen > previous_frames and ticks_seen <= previous_ticks and last_tick_age < 4.0:
            self._last_frames = frames_seen
            self._last_ticks = ticks_seen
            return "TICKS_STALLED"

        self._last_frames = frames_seen
        self._last_ticks = ticks_seen
        if frames_seen <= 0 and last_tick_age > 3.0:
            return "WS_SILENT"
        if time.monotonic() - last_successful_native_history > 60.0 and native_history_age > 60.0:
            return "WS_SILENT"
        if last_tick_age > 4.0:
            return "WS_SILENT"
        return "HEALTHY"

    async def _page_snapshot(self):
        bot = getattr(self.system, "bot", None)
        page = getattr(bot, "_page", None)
        if page is None:
            return {"url": "", "title": "", "visibility": "unknown", "banner": "", "online": True}
        try:
            url = await page.evaluate("() => window.location.href")
            title = await page.evaluate("() => document.title")
            visibility = await page.evaluate("() => document.visibilityState")
            online = await page.evaluate("() => navigator.onLine")
            banner = await page.evaluate("() => { const nodes = [...document.body.querySelectorAll('*')]; const hits = []; for (const node of nodes) { const t = (node.textContent || '').replace(/\\s+/g, ' ').trim(); if (/login|log in|sign in|session|reconnect|maintenance|connection/i.test(t)) hits.push(t); } return hits.slice(0, 5).join(' | '); }")
        except Exception:
            return {"url": "", "title": "", "visibility": "unknown", "banner": "", "online": True}
        return {"url": str(url or ""), "title": str(title or ""), "visibility": str(visibility or "unknown"), "banner": self._banner_from_text(str(banner or "")), "online": bool(online)}

    async def observe_once(self):
        bot = getattr(self.system, "bot", None)
        live_feed = getattr(bot, "live_feed", None)
        asset = getattr(self.system, "_current_asset", getattr(getattr(bot, "_selected_asset", None), "__str__", lambda: "")())
        last_tick = None
        if live_feed is not None and hasattr(live_feed, "last_tick"):
            last_tick = live_feed.last_tick(asset, max_age=600)
        last_tick_age = 9999.0 if last_tick is None else float(last_tick.get("age_seconds", 9999.0))
        frames_seen = getattr(live_feed, "frames_seen", 0)
        ticks_seen = getattr(live_feed, "ticks_seen", 0)
        page_state = await self._page_snapshot()
        is_alive = True
        if bot is not None and hasattr(bot, "is_alive"):
            try:
                is_alive = bool(await bot.is_alive())
            except Exception:
                is_alive = False
        native_history_age = 9999.0
        last_successful_native_history = getattr(self.system, "_last_successful_native_history", 0.0)
        if last_successful_native_history:
            native_history_age = time.monotonic() - float(last_successful_native_history)
        state = self.classify(
            is_alive=is_alive,
            page_url=page_state["url"],
            title=page_state["title"],
            visibility=page_state["visibility"],
            online=page_state["online"],
            banner=page_state["banner"],
            last_tick_age=last_tick_age,
            frames_seen=frames_seen,
            ticks_seen=ticks_seen,
            native_history_age=native_history_age,
            last_successful_native_history=last_successful_native_history,
        )
        return {
            "state": state,
            "since": self._state_since,
            "silent_seconds": time.monotonic() - self._state_since,
            "frames": frames_seen,
            "ticks": ticks_seen,
            "page_alive": is_alive,
            "visibility": page_state["visibility"],
            "online": page_state["online"],
            "url": page_state["url"],
            "title": page_state["title"],
            "banner": page_state["banner"],
            "last_tick_age": last_tick_age,
            "native_history_age": native_history_age,
        }

    def should_exit(self):
        mode = os.getenv("FEED_RECOVERY", "off").strip().lower()
        if mode != "exit":
            return False
        threshold = float(os.getenv("FEED_RECOVERY_AFTER_SECONDS", "180"))
        if time.monotonic() - self._state_since < threshold:
            return False
        if self.system.active_trades or getattr(self.system, "_pending_order_ids", None):
            if time.monotonic() - self._last_recovery_blocked_log >= 60.0:
                self.logger.warning("[FeedWatchdog] recovery_blocked active_trade=%s order_id=%s", bool(self.system.active_trades), (self.system.active_trades[0].get('id') if self.system.active_trades else None))
                self._last_recovery_blocked_log = time.monotonic()
            return False
        if os.path.exists(getattr(self.system, "_order_state_path", "execution_state.json")):
            return False
        return self._last_state in {"WS_SILENT", "PAGE_DEAD", "OFFLINE", "LOGGED_OUT_SUSPECTED"}

    async def run(self):
        while True:
            try:
                state = (await self.observe_once())["state"]
                if state != self._last_state:
                    self._last_state = state
                    self._state_since = time.monotonic()
                    self.logger.warning("[FeedWatchdog] state=%s since=%s silent_seconds=%s frames=%s ticks=%s page_alive=%s visibility=%s online=%s url=%s title=%s banner=%s",
                                        state, self._state_since, 0, self._last_frames, self._last_ticks, True, "unknown", True, "", "", "")
                    if state in {"WS_SILENT", "PAGE_DEAD", "OFFLINE", "LOGGED_OUT_SUSPECTED"} and self._last_outage_start is None:
                        self._last_outage_start = time.monotonic()
                        self.logger.warning("[Feed] outage_start reason=%s", state)
                    elif self._last_outage_start is not None and state == "HEALTHY":
                        duration = time.monotonic() - self._last_outage_start
                        self.logger.warning("[Feed] outage_end duration=%s", round(duration, 2))
                        self._last_outage_start = None
                if self._last_state != "HEALTHY" and time.monotonic() - self._last_summary_ts >= 60.0:
                    snapshot = await self.observe_once()
                    self.logger.warning("[FeedWatchdog] state=%s since=%s silent_seconds=%s frames=%s ticks=%s page_alive=%s visibility=%s online=%s url=%s title=%s banner=%s",
                                        snapshot["state"], snapshot["since"], snapshot["silent_seconds"], snapshot["frames"], snapshot["ticks"], snapshot["page_alive"], snapshot["visibility"], snapshot["online"], snapshot["url"], snapshot["title"], snapshot["banner"])
                    self._last_summary_ts = time.monotonic()
                if self.should_exit():
                    self.logger.warning("[FeedWatchdog] recovery=exit code=75 reason=%s", self._last_state)
                    system = self.system
                    if hasattr(system, "running"):
                        system.running = False
                    if hasattr(system, "_shutdown"):
                        try:
                            await system._shutdown()
                        except Exception:
                            pass
                    raise SystemExit(75)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.warning("[FeedWatchdog] poll_failed reason=%s", exc)
            await asyncio.sleep(self.poll_seconds)
