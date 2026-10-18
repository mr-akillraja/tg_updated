"""Closed-candle multi-timeframe analysis with explicit market-data provenance."""

import logging
import math
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger()

TF_30S  = 30
TF_1M   = 60
TF_5M   = 300
ALL_TIMEFRAMES = [TF_30S, TF_1M, TF_5M]

MIN_CANDLES = {
    TF_30S: 5,
    TF_1M:  4,
    TF_5M:  3,
}
SR_HISTORY_SIZE = 60

PREFERRED_ASSETS_ORDERED = [
    "AUDUSD-OTC", "EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC",
    "EURGBP-OTC", "USDCAD-OTC", "NZDUSD-OTC",
    "BTCUSD", "ETHUSD", "LTCUSD",
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
]


# ─── Candle Builder ───────────────────────────────────────────────
class CandleBuilder:
    """Aligned OHLC of observed broker quotes, exposing only completed bars.

    The first partial bar and bars affected by quote outages are discarded;
    missing OHLC is never interpolated. This is sampled OHLC, not native broker
    history. ``max_tick_gap`` bounds sampling gaps, not market tick frequency.
    """

    def __init__(self, period: int, max_candles: int = 500,
                 max_tick_gap: float = 5.0):
        if period <= 0 or max_tick_gap <= 0:
            raise ValueError("Candle period and maximum quote gap must be positive")
        self.period = period
        self.max_tick_gap = max_tick_gap
        self.candles: deque = deque(maxlen=max_candles)
        self._current: Optional[dict] = None
        self._bar_end = 0.0
        self._last_tick_ts: Optional[float] = None
        self._current_complete = False
        # Native history and its live quote overlay have independent lifetimes.
        # An overlay must never finalize sampled OHLC into native closed history.
        self._forming: Optional[dict] = None
        self._forming_observed_at: Optional[float] = None
        self._forming_complete = False
        self._feed_suspended = False
        self._feed_gap_at: Optional[float] = None
        self.source = "broker_browser_sample"

    def invalidate(self, reason: str = "quote_gap"):
        if self.candles or self._current:
            log.warning("[Candles] timeframe=%ss source=%s invalidated=%s closed_count=%s",
                        self.period, self.source, reason, len(self.candles))
        self.candles.clear()
        self._current = None
        self._bar_end = 0.0
        self._last_tick_ts = None
        self._current_complete = False
        self._forming = None
        self._forming_observed_at = None
        self._forming_complete = False
        self._feed_suspended = False
        self._feed_gap_at = None
        self.source = "broker_browser_sample"

    def mark_feed_gap(self, now: Optional[float] = None):
        """Suspend live coverage while preserving real closed bars and extremes.

        A previously observed candle remains available as stale diagnostics. Its
        missing interval cannot be promoted into sampled closed history; native
        OHLC observed after the suspension may restore its coverage.
        """
        now = time.time() if now is None else now
        self._current_complete = False
        self._forming_complete = False
        self._feed_suspended = True
        self._feed_gap_at = now

    def add_tick(self, price: float, ts: float) -> bool:
        if not math.isfinite(price) or price <= 0 or not math.isfinite(ts):
            return False
        if self._last_tick_ts is not None and ts <= self._last_tick_ts:
            return False
        if self._feed_suspended and self._feed_gap_at is not None and ts < self._feed_gap_at:
            return False
        if self._last_tick_ts is not None and ts - self._last_tick_ts > self.max_tick_gap:
            self.invalidate("quote_gap")

        closed = False
        bar_start = math.floor(ts / self.period) * self.period
        if self._current is None:
            if self.candles and self.candles[-1]["ts"] + self.period < bar_start:
                self.candles.clear()
            self._current_complete = math.isclose(ts, bar_start, abs_tol=1e-6, rel_tol=0)
        elif ts >= self._bar_end:
            if self._current_complete:
                if self.candles and self._current["ts"] != self.candles[-1]["ts"] + self.period:
                    self.candles.clear()
                self.candles.append(self._current)
                closed = True
            else:
                # A discarded partial bar also breaks continuity with any seed.
                self.candles.clear()
            self._current_complete = bar_start == self._bar_end
            self._current = None

        if self._current is None:
            self._bar_end = bar_start + self.period
            self._current = {"open": price, "high": price, "low": price,
                             "close": price, "ts": bar_start,
                             "source": "broker_browser_sample"}
        else:
            self._current["high"] = max(self._current["high"], price)
            self._current["low"] = min(self._current["low"], price)
            self._current["close"] = price
        self._last_tick_ts = ts
        self._feed_suspended = False
        if self._current_complete:
            self._feed_gap_at = None
        return closed

    def update_forming(self, price: float, ts: float, native: Optional[dict] = None) -> bool:
        """Update a provisional quote overlay without altering closed history.

        ``native`` may supply genuine OHLC for this same active bucket. Without
        it, an observation starting mid-candle cannot claim a complete open/high/
        low. Quote gaps reset that completeness without clearing native history.
        A cached native baseline can restore completeness only when its original
        observation time bridges the current quote gap; rereading it is no update.
        """
        if not math.isfinite(price) or price <= 0 or not math.isfinite(ts):
            return False
        last_ts = self._forming_observed_at
        if last_ts is not None and ts <= last_ts:
            return False
        if self._feed_suspended and self._feed_gap_at is not None and ts < self._feed_gap_at:
            return False
        start = math.floor(ts / self.period) * self.period
        gap = last_ts is not None and ts - last_ts > self.max_tick_gap
        same_bucket = self._forming is not None and self._forming["ts"] == start
        continuous = same_bucket and self._forming_complete and not gap
        native_bar = None
        native_recent = False
        if native is not None:
            try:
                values = {key: float(native[key]) for key in ("open", "high", "low", "close")}
                valid = (float(native["ts"]) == start
                         and all(math.isfinite(v) and v > 0 for v in values.values())
                         and values["low"] <= min(values["open"], values["close"])
                         and values["high"] >= max(values["open"], values["close"]))
                if valid:
                    native_source = native.get("source", self.source)
                    try:
                        native_observed_at = float(native["observed_at"])
                    except (KeyError, TypeError, ValueError):
                        native_observed_at = None
                    if native_observed_at is not None and not math.isfinite(native_observed_at):
                        native_observed_at = None
                    native_recent = (native_observed_at is not None
                                     and start <= native_observed_at <= ts
                                     and ts - native_observed_at <= self.max_tick_gap
                                     and (self._feed_gap_at is None
                                          or native_observed_at >= self._feed_gap_at))
                    native_bar = dict(values, ts=start,
                                      source=f"{native_source}:sampled_overlay",
                                      native_source=native_source,
                                      native_observed_at=native_observed_at)
            except (KeyError, TypeError, ValueError):
                pass
        if native_bar is not None:
            # Preserve observed extremes if the native snapshot trails the quote.
            if same_bucket:
                native_bar["high"] = max(native_bar["high"], self._forming["high"])
                native_bar["low"] = min(native_bar["low"], self._forming["low"])
            self._forming = native_bar
            self._forming_complete = bool(continuous or native_recent)
        elif self._forming is None or self._forming["ts"] != start:
            self._forming = {"open": price, "high": price, "low": price,
                             "close": price, "ts": start,
                             "source": "broker_browser_sample"}
            self._forming_complete = math.isclose(ts, start, abs_tol=1e-6, rel_tol=0)
        elif gap:
            self._forming_complete = False
        self._forming["high"] = max(self._forming["high"], price)
        self._forming["low"] = min(self._forming["low"], price)
        self._forming["close"] = price
        self._forming_observed_at = ts
        self._feed_suspended = False
        if self._forming_complete:
            self._feed_gap_at = None
        return True

    def forming_snapshot(self, now: Optional[float] = None) -> Optional[dict]:
        """Return detached, explicitly provisional OHLC and observation metadata."""
        now = time.time() if now is None else now
        candidates = []
        if self._current is not None and self._last_tick_ts is not None:
            candidates.append((self._last_tick_ts, self._current, self._current_complete))
        if self._forming is not None and self._forming_observed_at is not None:
            candidates.append((self._forming_observed_at, self._forming, self._forming_complete))
        if not candidates:
            return None
        observed_at, current, complete = max(candidates, key=lambda item: item[0])
        result = dict(current)
        open_, close = result["open"], result["close"]
        age = max(0.0, now - observed_at)
        end = result["ts"] + self.period
        result.update(
            live_price=close, body=abs(close - open_),
            upper_wick=result["high"] - max(open_, close),
            lower_wick=min(open_, close) - result["low"],
            direction="bullish" if close > open_ else "bearish" if close < open_ else "doji",
            remaining_seconds=max(0.0, min(float(self.period), end - now)),
            observed_at=observed_at, age_seconds=age,
            stale=self._feed_suspended or age > self.max_tick_gap or now >= end or now < observed_at,
            provisional=True, complete_from_open=complete,
        )
        return result

    def closed_snapshot(self) -> List[dict]:
        return [dict(c) for c in self.candles]

    @property
    def latest_closed_ts(self) -> Optional[float]:
        return self.candles[-1]["ts"] if self.candles else None

    def get_opens(self) -> np.ndarray:
        return np.array([c["open"] for c in self.candles], dtype=float)

    def get_closes(self) -> np.ndarray:
        return np.array([c["close"] for c in self.candles], dtype=float)

    def get_highs(self) -> np.ndarray:
        return np.array([c["high"] for c in self.candles], dtype=float)

    def get_lows(self) -> np.ndarray:
        return np.array([c["low"] for c in self.candles], dtype=float)

    def __len__(self):
        return len(self.candles)


# ─── Indicators ───────────────────────────────────────────────────
def _ema(arr: np.ndarray, p: int) -> np.ndarray:
    out = np.full_like(arr, np.nan)
    k, v = 2.0 / (p + 1), arr[0]
    for i, x in enumerate(arr):
        v = x * k + v * (1 - k); out[i] = v
    return out

def _rsi(c: np.ndarray, p: int = 14) -> float:
    """Wilder RSI: initialize with all p changes, then apply 1/p smoothing."""
    if len(c) < p + 1:
        return float("nan")
    delta = np.diff(np.asarray(c, dtype=float))
    if not np.isfinite(delta).all():
        return float("nan")
    gains = np.maximum(delta, 0)
    losses = np.maximum(-delta, 0)
    gain, loss = float(gains[:p].mean()), float(losses[:p].mean())
    for up, down in zip(gains[p:], losses[p:]):
        gain = (gain * (p - 1) + up) / p
        loss = (loss * (p - 1) + down) / p
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    return float(100 - 100 / (1 + gain / loss))

def _macd(c: np.ndarray) -> Tuple[float, float, float]:
    if len(c) < 26:
        return 0.0, 0.0, 0.0
    m = _ema(c, 12) - _ema(c, 26)
    s = _ema(m[~np.isnan(m)], 9)
    macd_v = float(m[-1])
    macd_s = float(s[-1])
    hist = macd_v - macd_s
    return macd_v, macd_s, float(hist)

def _bollinger(c: np.ndarray, p: int = 20) -> Tuple[float, float, float]:
    if len(c) < p: m = c[-1]; return m, m, m
    w = c[-p:]; mid = w.mean(); std = w.std()
    return mid + 2 * std, mid, mid - 2 * std

def _stoch(h: np.ndarray, l: np.ndarray, c: np.ndarray, p: int = 14) -> float:
    if len(c) < p: return 50.0
    hi, lo = h[-p:].max(), l[-p:].min()
    return 50.0 if hi == lo else 100 * (c[-1] - lo) / (hi - lo)

def _ema_cross(c: np.ndarray) -> int:
    if len(c) < 21: return 0
    f, s = _ema(c, 9), _ema(c, 21)
    return 1 if f[-1] > s[-1] else (-1 if f[-1] < s[-1] else 0)


# ─── Support / Resistance ─────────────────────────────────────────
class SREngine:
    def __init__(self, tol: float = 0.0005):
        self.tol = tol
        self.supports: List[float]    = []
        self.resistances: List[float] = []

    def update(self, h: np.ndarray, l: np.ndarray, c: np.ndarray):
        if len(c) < 5: return
        ph, pl = [], []
        for i in range(2, len(c) - 2):
            if h[i] > h[i-1] and h[i] > h[i-2] and h[i] > h[i+1] and h[i] > h[i+2]:
                ph.append(h[i])
            if l[i] < l[i-1] and l[i] < l[i-2] and l[i] < l[i+1] and l[i] < l[i+2]:
                pl.append(l[i])
        self.resistances = self._cluster(ph)
        self.supports    = self._cluster(pl)

    def _cluster(self, pts: list) -> List[float]:
        if not pts: return []
        pts = sorted(pts); clusters, grp = [], [pts[0]]
        for p in pts[1:]:
            if abs(p - grp[-1]) / max(grp[-1], 1e-9) < self.tol:
                grp.append(p)
            else:
                clusters.append(float(np.mean(grp))); grp = [p]
        clusters.append(float(np.mean(grp)))
        return clusters

    def zone_bias(self, price: float) -> str:
        all_z = [(s, "support") for s in self.supports] + \
                [(r, "resistance") for r in self.resistances]
        if not all_z: return "neutral"
        closest = min(all_z, key=lambda z: abs(z[0] - price))
        if abs(closest[0] - price) / max(closest[0], 1e-9) > 0.003:
            return "neutral"
        return "bullish" if closest[1] == "support" else "bearish"


# ─── Main Agent ───────────────────────────────────────────────────
class MultiTimeframeAgent:
    def __init__(self, asset: str, max_tick_gap: float = 5.0):
        self.asset = asset
        self.max_tick_gap = max_tick_gap
        self.reset(asset)

    def reset(self, asset: str = None):
        if asset is not None:
            self.asset = asset
        self.builders: Dict[int, CandleBuilder] = {
            TF_30S: CandleBuilder(TF_30S, 200, self.max_tick_gap),
            TF_1M:  CandleBuilder(TF_1M,  70, self.max_tick_gap),
            TF_5M:  CandleBuilder(TF_5M,  40, self.max_tick_gap),
        }
        self.sr_engine  = SREngine()
        self._last_price = 0.0
        self._tick_count = 0
        self._last_tf_state: Dict = {}

    def log_seed_sources(self):
        for period, builder in self.builders.items():
            log.info("[Agent1Seed] timeframe=%ss source=%s closed_count=%s native_history=%s",
                     period, builder.source, len(builder),
                     "unavailable" if builder.source == "broker_browser_sample" else "provided")

    def seed_from_dataframe(self, df, timeframe: int = TF_1M,
                            source: str = "unspecified", now: Optional[float] = None, log_seed: bool = True):
        """Seed real timestamped OHLC; never invent shorter-timeframe candles.

        Native 30s history must declare its resolution in DataFrame metadata.
        Minute history may additionally seed complete aligned 5m bars.
        """
        import pandas as pd

        if timeframe not in self.builders:
            raise ValueError(f"Unsupported seed timeframe: {timeframe}")
        if df is None or df.empty:
            self.log_seed_sources()
            return
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("OHLC history must carry its original DatetimeIndex")
        if source == "unspecified":
            source = df.attrs.get("source", source)
        declared = df.attrs.get("timeframe_seconds")
        if declared is not None and declared != timeframe:
            raise ValueError("History resolution does not match requested seed timeframe")
        if timeframe == TF_30S and declared != TF_30S:
            raise ValueError("Native 30s history requires timeframe_seconds=30 metadata; M1 cannot seed 30s")
        required = {"open", "high", "low", "close"}
        if not required.issubset(df.columns) or df.index.has_duplicates:
            raise ValueError("OHLC history has missing columns or duplicate timestamps")
        now = time.time() if now is None else now
        bars = []
        for timestamp, row in df.sort_index().iterrows():
            timestamp = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp
            ts = timestamp.timestamp()
            if ts % timeframe:
                raise ValueError("OHLC timestamps must be aligned candle opening times")
            if ts + timeframe > now:
                continue
            candle = {key: float(row[key]) for key in required}
            if (not all(math.isfinite(v) and v > 0 for v in candle.values())
                    or candle["low"] > min(candle["open"], candle["close"])
                    or candle["high"] < max(candle["open"], candle["close"])):
                raise ValueError("Invalid historical OHLC values")
            if bars and ts != bars[-1]["ts"] + timeframe:
                bars.clear()
            bars.append(dict(candle, ts=ts, source=source))
        builder = self.builders[timeframe]
        # History refreshes should not erase observations from the active candle.
        forming = builder.forming_snapshot(now)
        active_start = math.floor(now / timeframe) * timeframe
        if forming is not None and forming["ts"] == active_start:
            builder._forming = {key: forming[key] for key in
                                ("open", "high", "low", "close", "ts", "source",
                                 "native_source", "native_observed_at") if key in forming}
            builder._forming_observed_at = forming["observed_at"]
            builder._forming_complete = forming["complete_from_open"]
        else:
            builder._forming = None
            builder._forming_observed_at = None
            builder._forming_complete = False
        builder.candles.clear()
        builder._current = None
        builder._bar_end = 0.0
        builder._last_tick_ts = None
        builder._current_complete = False
        builder.candles.extend(bars)
        builder.source = source
        if timeframe == TF_1M:
            # Use the full supplied history before the 70-minute deque truncates it.
            self.aggregate_five_minute(bars, reset=True)
        if log_seed:
            self.log_seed_sources()
        self._update_sr()

    def aggregate_five_minute(self, candles: Optional[List[dict]] = None,
                              *, reset: bool = False):
        """Maintain 5m closed history from complete, contiguous closed 1m bars.

        Incremental aggregation retains the 40-bar trend history even though the
        active minute builder retains only 70 minutes. Missing minutes break the
        retained continuity; partial five-minute groups are never published.
        """
        minutes = self.builders[TF_1M].closed_snapshot() if candles is None else candles
        b5m = self.builders[TF_5M]
        source = f"{self.builders[TF_1M].source}:complete_aligned_M1_aggregation"
        tail = []
        for candle in minutes:
            if tail and candle["ts"] != tail[-1]["ts"] + TF_1M:
                tail.clear()
            tail.append(candle)
        if (reset or not tail
                or (b5m.candles and tail[0]["ts"] > b5m.candles[-1]["ts"] + TF_5M)):
            b5m.candles.clear()
        b5m._current = None
        b5m._bar_end = 0.0
        b5m._last_tick_ts = None
        b5m._current_complete = False
        grouped = {}
        for candle in tail:
            start = math.floor(candle["ts"] / TF_5M) * TF_5M
            grouped.setdefault(start, []).append(candle)
        merged = {candle["ts"]: dict(candle) for candle in b5m.candles}
        for start, group in sorted(grouped.items()):
            if [bar["ts"] for bar in group] != [start + i * TF_1M for i in range(5)]:
                continue
            merged[start] = {"open": group[0]["open"], "close": group[-1]["close"],
                             "high": max(bar["high"] for bar in group),
                             "low": min(bar["low"] for bar in group), "ts": start,
                             "source": source}
        b5m.candles.clear()
        for start, candle in sorted(merged.items()):
            if b5m.candles and start != b5m.candles[-1]["ts"] + TF_5M:
                b5m.candles.clear()
            b5m.candles.append(candle)
        b5m.source = source

    def bootstrap(self, base_price: float, n_ticks: int = 200):
        # Compatibility: one observed price supplies no historical OHLC.
        if math.isfinite(base_price) and base_price > 0:
            self._last_price = base_price
        log.warning("[Agent1] Historical bootstrap unavailable; awaiting real closed candles")
        self.log_seed_sources()

    def invalidate(self, reason: str = "quote_gap"):
        for builder in self.builders.values():
            builder.invalidate(reason)
        self.sr_engine = SREngine()
        self._last_tf_state = {}
        self._last_price = 0.0

    def suspend_live_observations(self, reason: str = "quote_unavailable",
                                  now: Optional[float] = None):
        """Mark an unavailable feed without discarding confirmed market history."""
        now = time.time() if now is None else now
        for builder in self.builders.values():
            builder.mark_feed_gap(now)
        log.warning("[Agent1] live_observations=suspended reason=%s closed_history=retained", reason)

    def _update_sr(self):
        b1m = self.builders[TF_1M]
        if len(b1m) >= 5:
            self.sr_engine.update(b1m.get_highs(), b1m.get_lows(), b1m.get_closes())
        else:
            self.sr_engine = SREngine()

    def add_tick(self, price: float, ts: Optional[float] = None) -> set:
        ts = time.time() if ts is None else ts
        if not math.isfinite(price) or price <= 0 or not math.isfinite(ts):
            return set()
        last_ts = self.builders[TF_30S]._last_tick_ts
        if last_ts is not None and ts <= last_ts:
            return set()
        if last_ts is not None and ts - last_ts > self.max_tick_gap:
            self.invalidate("quote_gap")
        self._last_price = price
        self._tick_count += 1
        closed = {period for period, builder in self.builders.items() if builder.add_tick(price, ts)}
        self._update_sr()
        return closed

    def analyze(self) -> Tuple["Signal", float]:
        from models.predictor import Signal

        if self._last_price <= 0:
            return Signal.HOLD, 0.0

        votes_buy = votes_sell = 0
        total_conf = 0.0
        tf_details = {}
        active_tfs = 0

        for tf in ALL_TIMEFRAMES:
            b = self.builders[tf]
            if len(b) < MIN_CANDLES[tf]:
                log.debug(f"[Agent1] TF={tf}s: {len(b)}/{MIN_CANDLES[tf]} candles")
                continue
            active_tfs += 1
            vote, conf = self._analyze_tf(b.get_closes(), b.get_highs(), b.get_lows(), tf)
            tf_details[tf] = {"vote": vote, "confidence": conf}
            if vote == "BUY":
                votes_buy  += 1; total_conf += conf
            elif vote == "SELL":
                votes_sell += 1; total_conf += conf

        self._last_tf_state = tf_details

        def _emit(direction: str, votes: int, avg_raw: float) -> Tuple["Signal", float]:
            bias = self.sr_engine.zone_bias(self._last_price)
            if direction == "BUY":
                sr_adj = +0.04 if bias == "bullish" else (-0.05 if bias == "bearish" else 0.0)
            else:
                sr_adj = +0.04 if bias == "bearish" else (-0.05 if bias == "bullish" else 0.0)
            if votes >= 3:
                floor = 0.50
            elif votes == 2:
                floor = 0.45
            else:
                floor = 0.40
            conf = max(floor, min(0.97, avg_raw + sr_adj))
            sig  = Signal.BUY if direction == "BUY" else Signal.SELL
            log.info(f"[Agent1] {direction} {votes}/{active_tfs} active TFs | raw={avg_raw:.2%} "
                     f"sr={sr_adj:+.2%} floor={floor:.2%} → conf={conf:.2%} | bias={bias}")
            return sig, conf

        if votes_buy >= 2:
            return _emit("BUY",  votes_buy,  total_conf / votes_buy)
        if votes_sell >= 2:
            return _emit("SELL", votes_sell, total_conf / votes_sell)

        if active_tfs >= 1 and (votes_buy >= 1 or votes_sell >= 1):
            if votes_buy >= 1:
                return _emit("BUY",  votes_buy,  total_conf / votes_buy)
            else:
                return _emit("SELL", votes_sell, total_conf / votes_sell)

        log.debug(f"[Agent1] No consensus BUY={votes_buy} SELL={votes_sell} from {active_tfs} active TFs")
        return Signal.HOLD, 0.0

    def get_latest_multi_tf_state(self) -> Dict:
        return self._last_tf_state

    def evaluate_chart_strength(self) -> float:
        if not self._last_tf_state:
            return 0.0
        score = 0.0
        weight = 0.0
        for tf, data in self._last_tf_state.items():
            conf = data.get("confidence", 0.0)
            vote = data.get("vote")
            tf_weight = 1.0
            if tf >= TF_1M:
                tf_weight = 1.25
            elif tf >= TF_30S:
                tf_weight = 1.1
            if vote == "BUY":
                score += conf * tf_weight
            elif vote == "SELL":
                score -= conf * tf_weight
            weight += tf_weight

        if weight == 0:
            return 0.0

        bias = self.sr_engine.zone_bias(self._last_price)
        if bias == "bullish":
            score += 0.1
        elif bias == "bearish":
            score -= 0.1

        return float(score / max(weight, 1.0))

    def _analyze_tf(self, c: np.ndarray, h: np.ndarray,
                    l: np.ndarray, tf: int) -> Tuple[str, float]:
        scores = []

        rsi = _rsi(c)
        if   rsi < 30:  scores.append(+1.0)
        elif rsi > 70:  scores.append(-1.0)
        elif rsi <= 45: scores.append(+0.5)
        elif rsi >= 55: scores.append(-0.5)
        else:           scores.append(0.0)

        ml, sl, _ = _macd(c)
        if   ml > sl and ml > 0: scores.append(+1.0)
        elif ml < sl and ml < 0: scores.append(-1.0)
        elif ml > sl:            scores.append(+0.5)
        elif ml < sl:            scores.append(-0.5)
        else:                    scores.append(0.0)

        up, mid, lo = _bollinger(c)
        p = c[-1]
        if   p <= lo:  scores.append(+1.0)
        elif p >= up:  scores.append(-1.0)
        elif p < mid:  scores.append(+0.3)
        elif p > mid:  scores.append(-0.3)
        else:          scores.append(0.0)

        scores.append(_ema_cross(c) * 0.8)

        sk = _stoch(h, l, c)
        if   sk < 20: scores.append(+1.0)
        elif sk > 80: scores.append(-1.0)
        elif sk < 40: scores.append(+0.3)
        elif sk > 60: scores.append(-0.3)
        else:         scores.append(0.0)

        norm  = sum(scores) / 5.0
        abs_n = abs(norm)
        conf  = 0.45 + abs_n * 0.47

        if   norm >=  0.20: return "BUY",  conf
        elif norm <= -0.20: return "SELL", conf
        return "NEUTRAL", conf
