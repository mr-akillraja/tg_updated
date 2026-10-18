"""bot/live_feed.py - tick-driven live price and candle engine (additive).

Why this exists
---------------
The previous live price came from polling ``page.title()`` once per second and
the forming candle only moved when a history request answered. Both add up to a
second or more of lag, and a candle was only "known" after a history fetch.

This module consumes the broker's own WebSocket quote frames the moment they
arrive and builds the 30s / 60s candles from them:

  * last price is updated per tick (no polling, no CDP round trip)
  * forming candle OHLC + momentum are always current
  * a candle rolls over at the exact tick that opens the next bucket, so the
    just-closed candle is known instantly (no history fetch needed)
  * broker history replies (already captured by browser.py) are merged in as
    ground truth for closed candles

Nothing here touches login, asset selection or order placement.
"""
import json
import logging
import math
import os
import re
import time
from collections import deque
from typing import Callable, Optional

log = logging.getLogger("live_feed")

PERIODS = (30, 60)
_MAX_CLOCK_SKEW = 60.0  # allow broker timestamps that are near the current candle bucket


def _norm(asset) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(asset).upper())


def candle_momentum(candle: Optional[dict]) -> Optional[dict]:
    """Closed-candle momentum summary for informational logging only."""
    if not isinstance(candle, dict):
        return None
    try:
        open_price = float(candle.get("open", 0.0))
        high = float(candle.get("high", open_price))
        low = float(candle.get("low", open_price))
        close = float(candle.get("close", open_price))
    except (TypeError, ValueError):
        return None
    body = round(float(close - open_price), 5)
    rng = round(max(float(high) - float(low), 0.0), 5)
    up_ticks = int(candle.get("up_ticks") or 0)
    down_ticks = int(candle.get("down_ticks") or 0)
    has_tick_stats = all(key in candle and candle.get(key) is not None for key in ("up_ticks", "down_ticks"))
    tick_bias = None
    if has_tick_stats:
        tick_bias = "up" if up_ticks > down_ticks else "down" if down_ticks > up_ticks else "flat"
    if abs(body) <= 1e-12 and tick_bias is not None and tick_bias != "flat" and rng >= 0.001:
        body = rng * 2.0 if tick_bias == "up" else -rng * 2.0
        direction = tick_bias
        ratio = 0.5
    else:
        direction = "up" if body > 0 else "down" if body < 0 else "flat"
        if abs(body) <= 1e-12 and rng < 0.001:
            direction = "flat"
        ratio = abs(body) / rng if rng > 0 else 0.0
        ratio = min(max(ratio, 0.0), 1.0)
    momentum = {
        "direction": direction,
        "tick_bias": tick_bias,
        "body": body,
        "range": rng,
        "body_ratio": ratio,
        "ticks": int(candle.get("ticks", 0) or 0),
    }
    if "up_ticks" in candle:
        momentum["up_ticks"] = int(candle.get("up_ticks") or 0)
    if "down_ticks" in candle:
        momentum["down_ticks"] = int(candle.get("down_ticks") or 0)
    return momentum


class _Series:
    """Closed + forming candles for one (asset, period)."""

    def __init__(self, period: int, max_closed: int):
        self.period = period
        self.closed = deque(maxlen=max_closed)
        self.forming: Optional[dict] = None
        self.up_ticks = 0
        self.down_ticks = 0
        self.recent = deque(maxlen=64)  # (recv_time, price) for tick velocity
        self.gap_before_forming = False

    def _new_forming(self, bucket, price, recv):
        previous_close = self.closed[-1]["close"] if self.closed else None
        baseline = previous_close if previous_close is not None else price
        self.forming = {"ts": float(bucket), "open": price, "high": price,
                        "low": price, "close": price, "ticks": 1,
                        "first_tick_at": recv, "last_tick_at": recv,
                        "up_ticks": 0, "down_ticks": 0}
        if previous_close is not None:
            self.up_ticks = 1 if price > previous_close else 0
            self.down_ticks = 1 if price < previous_close else 0
        else:
            self.up_ticks = self.down_ticks = 0

    def add(self, bucket: float, price: float, recv: float):
        """Return the candle that just closed, or None."""
        self.recent.append((recv, price))
        f = self.forming
        if f is None:
            self._new_forming(bucket, price, recv)
            self.gap_before_forming = True  # cannot prove the open is complete
            return None
        if bucket < f["ts"]:
            return None  # late/out-of-order tick for an already rolled bucket
        if bucket == f["ts"]:
            if price > f["close"]:
                self.up_ticks += 1
            elif price < f["close"]:
                self.down_ticks += 1
            f["high"] = max(f["high"], price)
            f["low"] = min(f["low"], price)
            f["close"] = price
            f["ticks"] += 1
            f["last_tick_at"] = recv
            return None
        done = {k: f[k] for k in ("ts", "open", "high", "low", "close", "ticks")}
        done["up_ticks"] = self.up_ticks
        done["down_ticks"] = self.down_ticks
        done["source"] = "live_ticks"
        contiguous = bucket == f["ts"] + self.period
        if self.closed and self.closed[-1]["ts"] >= done["ts"]:
            for index, existing in enumerate(self.closed):
                if float(existing.get("ts", -1)) == float(done["ts"]):
                    merged = dict(existing)
                    for key in ("open", "high", "low", "close", "ticks", "up_ticks", "down_ticks", "source"):
                        if done.get(key) is not None and (merged.get(key) is None or key in {"ticks", "up_ticks", "down_ticks"} and merged.get(key) in (None, 0)):
                            merged[key] = done[key]
                    self.closed[index] = merged
                    break
        else:
            self.closed.append(done)
        self._new_forming(bucket, price, recv)
        # A bucket skipped entirely means the new bar is still a real bar
        # (its open is its first tick), but history is no longer contiguous.
        self.gap_before_forming = not contiguous
        return done


class LiveTickFeed:
    def __init__(self, max_closed: int = 400, clock: Callable[[], float] = time.time,
                 debug_shapes: Optional[bool] = None):
        self._clock = clock
        self._max_closed = max_closed
        self._series: dict = {}
        self._last: dict = {}
        self._listeners: list = []
        self.frames_seen = 0
        self.ticks_seen = 0
        self._shapes_logged = set()
        if debug_shapes is None:
            debug_shapes = os.getenv("TICK_DEBUG", "false").lower() in ("1", "true", "yes")
        self._debug_shapes = debug_shapes
        self._shape_path = os.path.join(os.getenv("LOG_DIR", "logs"), "ws_frame_shapes.log")

    # ---- wiring -----------------------------------------------------------
    def add_listener(self, fn: Callable[[str, int, dict, dict], None]):
        """fn(asset_code, period, closed_candle, opening_tick) on each rollover."""
        self._listeners.append(fn)

    def _get(self, code: str, period: int) -> _Series:
        key = (code, period)
        if key not in self._series:
            self._series[key] = _Series(period, self._max_closed)
        return self._series[key]

    # ---- input ------------------------------------------------------------
    def on_frame(self, data):
        """Feed every raw websocket frame. Cheap; never raises."""
        try:
            self.frames_seen += 1
            if isinstance(data, (bytes, bytearray)):
                data = data.decode("utf-8", "ignore")
            packets = json.loads(data)
            if isinstance(packets, dict):
                packets = [packets]
            if not isinstance(packets, list):
                return
            recv = self._clock()
            for packet in packets:
                if not isinstance(packet, dict):
                    continue
                self._maybe_log_shape(packet)
                items = packet.get("d")
                if not isinstance(items, list):
                    continue
                for item in items:
                    if (isinstance(item, dict) and "candles" not in item
                            and isinstance(item.get("p"), str)
                            and isinstance(item.get("q"), (int, float))
                            and not isinstance(item.get("q"), bool)):
                        self.ingest_tick(item["p"], item["q"], item.get("t"), recv)
        except Exception as exc:  # a malformed frame must never break the browser callback
            log.debug("[LiveFeed] frame_ignored reason=%s", exc)

    def _maybe_log_shape(self, packet):
        if not self._debug_shapes:
            return
        d = packet.get("d")
        sample = d[0] if isinstance(d, list) and d else None
        shape = (packet.get("e"), packet.get("t"),
                 tuple(sorted(sample)) if isinstance(sample, dict) else type(sample).__name__)
        if shape in self._shapes_logged or len(self._shapes_logged) > 200:
            return
        self._shapes_logged.add(shape)
        try:
            os.makedirs(os.path.dirname(self._shape_path) or ".", exist_ok=True)
            with open(self._shape_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"shape": [str(x) for x in shape],
                                     "example": json.dumps(packet)[:400]}) + "\n")
        except OSError:
            pass

    def ingest_tick(self, pair, price, broker_ts=None, recv=None):
        try:
            price = float(price)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(price) or price <= 0:
            return False
        recv = self._clock() if recv is None else recv
        ts = recv
        try:
            if broker_ts is not None:
                candidate = float(broker_ts)
                if candidate > 1e11:  # milliseconds
                    candidate /= 1000.0
                if abs(candidate - recv) <= _MAX_CLOCK_SKEW or candidate < recv + _MAX_CLOCK_SKEW:
                    ts = candidate
        except (TypeError, ValueError):
            pass
        code = _norm(pair)
        self.ticks_seen += 1
        previous = self._last.get(code)
        self._last[code] = {"price": price, "ts": ts, "recv": recv,
                            "prev_price": previous["price"] if previous else None}
        for period in PERIODS:
            closed = self._get(code, period).add(math.floor(ts / period) * period, price, recv)
            if closed is not None:
                opening = {"price": price, "ts": ts, "recv": recv}
                for fn in list(self._listeners):
                    try:
                        fn(code, period, dict(closed), opening)
                    except Exception as exc:
                        log.warning("[LiveFeed] listener_failed reason=%s", exc)
        return True

    def merge_history(self, asset, period: int, rows):
        """Broker closed candles are the truth; preserve tick-derived stats."""
        if period not in PERIODS:
            return 0
        series = self._get(_norm(asset), period)
        now = self._clock()
        fresh = {}
        for row in rows:
            try:
                ts = float(row["ts"])
                if ts + period > now:  # still forming at the broker
                    continue
                base = {"ts": ts, "open": float(row["open"]), "high": float(row["high"]),
                        "low": float(row["low"]), "close": float(row["close"]),
                        "ticks": None, "source": "broker_history"}
                existing = None
                for candle in series.closed:
                    if float(candle.get("ts", -1)) == ts:
                        existing = candle
                        break
                if existing is not None:
                    merged = dict(existing)
                    merged.update(base)
                    for key in ("ticks", "up_ticks", "down_ticks"):
                        if key in existing and existing.get(key) is not None and merged.get(key) is None:
                            merged[key] = existing.get(key)
                    base = merged
                fresh[ts] = base
            except (KeyError, TypeError, ValueError):
                continue
        if not fresh:
            return 0
        merged = {c["ts"]: c for c in series.closed}
        for ts, candle in fresh.items():
            existing = merged.get(ts)
            if existing is not None:
                merged[ts] = {**existing, **candle}
                for key in ("ticks", "up_ticks", "down_ticks"):
                    if existing.get(key) is not None and merged[ts].get(key) is None:
                        merged[ts][key] = existing.get(key)
            else:
                merged[ts] = candle
        series.closed.clear()
        series.closed.extend(merged[k] for k in sorted(merged)[-self._max_closed:])
        return len(fresh)

    # ---- output -----------------------------------------------------------
    def last_tick(self, asset, max_age: Optional[float] = None):
        tick = self._last.get(_norm(asset))
        if tick is None:
            return None
        age = self._clock() - tick["recv"]
        if max_age is not None and not 0 <= age <= max_age:
            return None
        return {**tick, "age_seconds": age}

    def closed(self, asset, period: int) -> list:
        series = self._get(_norm(asset), period)
        return [dict(c) for c in reversed(series.closed)]

    def forming(self, asset, period: int) -> Optional[dict]:
        series = self._get(_norm(asset), period)
        f = series.forming
        if f is None:
            return None
        now = self._clock()
        if not f["ts"] <= now < f["ts"] + period + 1.0:
            return None  # no tick for the current bucket yet
        return dict(f, period=period, observed_at=f["last_tick_at"],
                    complete_from_open=not series.gap_before_forming)

    def momentum(self, asset, period: int) -> Optional[dict]:
        """Live candle momentum straight from ticks. Informational, not a gate."""
        series = self._get(_norm(asset), period)
        f = self.forming(asset, period)
        if f is None:
            return None
        rng = f["high"] - f["low"]
        body = f["close"] - f["open"]
        now = self._clock()
        window = [(t, p) for t, p in series.recent if now - t <= 5.0]
        velocity = None
        if len(window) >= 2 and window[-1][0] > window[0][0]:
            velocity = (window[-1][1] - window[0][1]) / (window[-1][0] - window[0][0])
        return {
            "direction": "up" if body > 0 else "down" if body < 0 else "flat",
            "body": body, "range": rng,
            "body_ratio": abs(body) / rng if rng > 0 else 0.0,
            "up_ticks": series.up_ticks, "down_ticks": series.down_ticks,
            "ticks": f["ticks"],
            "seconds_into_candle": now - f["ts"],
            "last_tick_age": now - f["last_tick_at"],
            "velocity_per_sec": velocity,
        }

    def health(self, asset, max_age: float = 1.5) -> dict:
        tick = self.last_tick(asset)
        return {"frames_seen": self.frames_seen, "ticks_seen": self.ticks_seen,
                "has_ticks": tick is not None,
                "tick_age": tick["age_seconds"] if tick else None,
                "live": bool(tick and 0 <= tick["age_seconds"] <= max_age)}
