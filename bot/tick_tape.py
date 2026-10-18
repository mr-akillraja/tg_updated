import json
import logging
import math
import os
import time
from datetime import datetime
from typing import Callable, Optional

log = logging.getLogger("tick_tape")


def _norm_asset(asset):
    return "".join(ch for ch in str(asset).upper() if ch.isalnum())


class TickTape:
    """Record tick-level tape data for the current asset without slowing market data."""

    def __init__(self, feed, asset_getter, period=30, ema_period=9, log_dir=None,
                 console=False, clock=time.time):
        self.feed = feed
        self.asset_getter = asset_getter
        self.period = int(period)
        self.ema_period = int(ema_period)
        self.log_dir = log_dir or os.getenv("LOG_DIR", "logs")
        self.console = bool(console)
        self.clock = clock
        self._last_tick = None
        self._last_closed_ema = None
        self._last_closed_ts = None
        self._file_handle = None
        self._file_path = None
        self._asset_code = None

    def install(self):
        original = getattr(self.feed, "ingest_tick", None)
        if original is None:
            return self
        def wrapped(pair, price, broker_ts=None, recv=None):
            try:
                result = original(pair, price, broker_ts=broker_ts, recv=recv)
            except Exception:
                return False
            try:
                self._record(pair, price, broker_ts=broker_ts, recv=recv)
            except Exception:
                pass
            return result
        self.feed.ingest_tick = wrapped
        return self

    def _open_file(self):
        day = datetime.utcfromtimestamp(self.clock()).strftime("%Y%m%d")
        path = os.path.join(self.log_dir, f"tick_tape_{day}.jsonl")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        if self._file_path != path:
            if self._file_handle is not None:
                self._file_handle.flush()
                self._file_handle.close()
            self._file_handle = open(path, "a", encoding="utf-8")
            self._file_path = path
        return self._file_handle

    def _write_row(self, row):
        handle = self._open_file()
        line = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        handle.write(line)
        handle.flush()
        if self.console:
            ts = datetime.utcfromtimestamp(row["recv_ms"] / 1000.0).strftime("%H:%M:%S.%f")[:-3]
            print(f"{ts} {row['asset']} {row['price']:.5f} {row['move'] if row['move'] in ('up','down') else 'flat'} {row['delta']:+.5f} | candle pos {row['pos_in_range']*100:.0f}% open{row['from_open']:+.5f} | EMA {row['side']} {row['vs_ema']:+.5f} | gap {row['gap_ms']:.0f}ms lat {row['latency_ms']:.0f}ms")

    def _ema_for_closed(self, asset, period):
        closed = self.feed.closed(asset, period)
        if not closed:
            return 0.0, None, None
        latest_ts = float(closed[0]["ts"])
        if self._last_closed_ts is not None and latest_ts == self._last_closed_ts:
            return self._last_closed_ema, self._last_closed_ts, closed[0]
        closes = [float(c["close"]) for c in closed]
        alpha = 2.0 / (self.ema_period + 1.0)
        ema_value = closes[0]
        for close in closes[1:]:
            ema_value = alpha * close + (1.0 - alpha) * ema_value
        self._last_closed_ts = latest_ts
        self._last_closed_ema = ema_value
        return ema_value, latest_ts, closed[0]

    def _record(self, pair, price, broker_ts=None, recv=None):
        asset = self.asset_getter() if callable(self.asset_getter) else self.asset_getter
        if not asset:
            return
        if _norm_asset(pair) != _norm_asset(asset):
            return
        recv = self.clock() if recv is None else float(recv)
        broker_ms = None
        if broker_ts is not None:
            try:
                broker_ms = float(broker_ts)
                if broker_ms > 1e11:
                    broker_ms /= 1000.0
            except (TypeError, ValueError):
                broker_ms = None
        if broker_ms is None:
            broker_ms = recv
        try:
            price = float(price)
        except (TypeError, ValueError):
            return
        if not math.isfinite(price) or price <= 0:
            return
        prev = self._last_tick
        delta = 0.0 if prev is None else price - prev["price"]
        move = "flat" if prev is None or math.isclose(delta, 0.0, abs_tol=1e-12) else "up" if delta > 0 else "down"
        gap_ms = 0.0 if prev is None else (recv - prev["recv_ms"]) * 1000.0
        if broker_ms is not None:
            diff = (recv - broker_ms)
            latency_ms = diff * 1000.0 if abs(diff) <= 10.0 else None
        else:
            latency_ms = None
        forming = self.feed.forming(asset, self.period)
        candle_open = candle_high = candle_low = None
        from_open = None
        pos_in_range = None
        if forming:
            candle_open = float(forming.get("open", price))
            candle_high = float(forming.get("high", price))
            candle_low = float(forming.get("low", price))
            baseline_open = candle_open
            if self._last_tick and forming.get("ticks", 0) <= 1:
                baseline_open = float(self._last_tick["price"])
            from_open = price - baseline_open
            if candle_high > candle_low:
                pos_in_range = (price - candle_low) / (candle_high - candle_low)
            elif abs(from_open) > 1e-12:
                pos_in_range = 0.7 if from_open > 0 else 0.3
            else:
                pos_in_range = 0.5
        ema, _, closed = self._ema_for_closed(asset, self.period)
        vs_ema = float(price) - float(ema)
        if vs_ema > 0:
            side = "above"
        elif vs_ema < 0:
            side = "below"
        else:
            side = "on"
        row = {
            "asset": asset,
            "recv_ms": recv * 1000.0,
            "broker_ms": broker_ms * 1000.0,
            "latency_ms": None if latency_ms is None else float(latency_ms),
            "price": float(price),
            "delta": float(delta),
            "move": move,
            "gap_ms": float(gap_ms),
            "candle_open": candle_open,
            "candle_high": candle_high,
            "candle_low": candle_low,
            "from_open": None if from_open is None else float(from_open),
            "pos_in_range": None if pos_in_range is None else float(pos_in_range),
            "seconds_in": None if forming is None else float(max(0.0, recv - forming.get("ts", recv))),
            "candle_ticks": None if forming is None else int(forming.get("ticks", 0)),
            "ema": float(ema),
            "ema_closed": float(ema) if closed is not None else None,
            "vs_ema": float(vs_ema),
            "side": side,
            "recv": recv,
            "broker_ts": broker_ms,
        }
        self._last_tick = {"price": float(price), "recv_ms": recv}
        self._write_row(row)
