import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Iterable, List, Optional


class TradeGovernor:
    """Gate the 1-indicator strategy by signal strength and pacing."""

    def __init__(self, max_per_hour=5, min_spacing=480.0, percentile=90.0, window=300,
                 min_history=40, max_per_day=110, state_path=None, clock=time.time, target=None):
        self.max_per_hour = int(max_per_hour)
        self.min_spacing = float(min_spacing)
        self.percentile = float(percentile)
        self.window = int(window)
        self.min_history = int(min_history)
        self.max_per_day = int(max_per_day)
        self.target = int(target) if target not in (None, "", 0) else None
        self.clock = clock
        self.strengths: List[float] = []
        self.taken: List[float] = []
        self._lock = threading.RLock()
        self._state_log_ts = None
        self._rate_log_ts = None
        self.state_path = Path(state_path) if state_path is not None else Path("logs") / "governor_state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_state()

    def seed(self, strengths: Iterable[float]):
        values = [float(v) for v in strengths if v is not None and float(v) == float(v)]
        if self.window > 0:
            values = values[-self.window:]
        self.strengths = values
        return self.strengths

    def observe(self, strength, now=None):
        try:
            value = float(strength)
        except (TypeError, ValueError):
            return None
        if not math_isfinite(value):
            return None
        with self._lock:
            self.strengths.append(value)
            if self.window > 0 and len(self.strengths) > self.window:
                self.strengths = self.strengths[-self.window:]
            self._persist()
            return value

    def _history_snapshot(self, now=None):
        now = float(self.clock() if now is None else now)
        with self._lock:
            return {
                "history_size": len(self.strengths),
                "min_history": self.min_history,
                "threshold": self.threshold(),
                "taken_last_hour": self.taken_last_hour(now),
                "taken_last_24h": self.taken_last_24h(now),
            }

    def log_state(self, logger=None, now=None):
        now = float(self.clock() if now is None else now)
        with self._lock:
            snapshot = {
                "history_size": len(self.strengths),
                "window": self.window,
                "min_history": self.min_history,
                "threshold": self.threshold(),
                "taken_last_hour": self.taken_last_hour(now),
                "taken_last_24h": self.taken_last_24h(now),
            }
            logger = logger or logging.getLogger("governor")
            logger.info("[Governor] state history=%s window=%s min_history=%s threshold=%s taken_last_hour=%s taken_last_24h=%s",
                        snapshot["history_size"], snapshot["window"], snapshot["min_history"], snapshot["threshold"],
                        snapshot["taken_last_hour"], snapshot["taken_last_24h"])
            self._state_log_ts = now
            return snapshot

    def maybe_log_state(self, logger=None, now=None, *, interval_seconds=3600.0):
        now = float(self.clock() if now is None else now)
        with self._lock:
            if self._state_log_ts is None or (now - self._state_log_ts) >= interval_seconds:
                snapshot = {
                    "history_size": len(self.strengths),
                    "window": self.window,
                    "min_history": self.min_history,
                    "threshold": self.threshold(),
                    "taken_last_hour": self.taken_last_hour(now),
                    "taken_last_24h": self.taken_last_24h(now),
                }
                logger = logger or logging.getLogger("governor")
                if logger.level == logging.WARNING or logger.level == logging.NOTSET:
                    logger.setLevel(logging.INFO)
                logger.propagate = True
                logger.info("[Governor] state history=%s window=%s min_history=%s threshold=%s taken_last_hour=%s taken_last_24h=%s",
                            snapshot["history_size"], snapshot["window"], snapshot["min_history"], snapshot["threshold"],
                            snapshot["taken_last_hour"], snapshot["taken_last_24h"])
                self._state_log_ts = now
                return snapshot
            return None

    @staticmethod
    def _rate_value(value):
        if value in (None, "", "None"):
            return "n/a"
        return value

    def log_rate(self, logger=None, now=None):
        now = float(self.clock() if now is None else now)
        with self._lock:
            snapshot = self.rate_snapshot(now)
            logger = logger or logging.getLogger("governor")
            if logger.level == logging.WARNING or logger.level == logging.NOTSET:
                logger.setLevel(logging.INFO)
            logger.propagate = True
            logger.info("[Rate] history=%s threshold=%s taken_last_hour=%s taken_last_24h=%s max_per_hour=%s max_per_day=%s seeded_signals=%s seeded_top10pct=%s pattern_confirmed=%s governor_selected=%s orders_accepted_last_hour=%s orders_accepted_last_24h=%s projected_days_to_target=%s",
                        len(self.strengths), snapshot.get("threshold"), snapshot.get("hourly_taken"),
                        snapshot.get("daily_taken"), self.max_per_hour, self.max_per_day,
                        snapshot.get("seeded_signals"), snapshot.get("seeded_top10pct"),
                        self._rate_value(snapshot.get("pattern_confirmed")),
                        snapshot.get("governor_selected"),
                        snapshot.get("orders_accepted_last_hour"), snapshot.get("orders_accepted_last_24h"),
                        self._rate_value(snapshot.get("projected_days_to_target")))
            self._rate_log_ts = now
            return snapshot

    def maybe_log_rate(self, logger=None, now=None, *, interval_seconds=3600.0):
        now = float(self.clock() if now is None else now)
        with self._lock:
            if self._rate_log_ts is None or (now - self._rate_log_ts) >= interval_seconds:
                snapshot = self.rate_snapshot(now)
                logger = logger or logging.getLogger("governor")
                if logger.level == logging.WARNING or logger.level == logging.NOTSET:
                    logger.setLevel(logging.INFO)
                logger.propagate = True
                logger.info("[Rate] history=%s threshold=%s taken_last_hour=%s taken_last_24h=%s max_per_hour=%s max_per_day=%s seeded_signals=%s seeded_top10pct=%s pattern_confirmed=%s governor_selected=%s orders_accepted_last_hour=%s orders_accepted_last_24h=%s projected_days_to_target=%s",
                            len(self.strengths), snapshot.get("threshold"), snapshot.get("hourly_taken"),
                            snapshot.get("daily_taken"), self.max_per_hour, self.max_per_day,
                            snapshot.get("seeded_signals"), snapshot.get("seeded_top10pct"),
                            self._rate_value(snapshot.get("pattern_confirmed")),
                            snapshot.get("governor_selected"),
                            snapshot.get("orders_accepted_last_hour"), snapshot.get("orders_accepted_last_24h"),
                            self._rate_value(snapshot.get("projected_days_to_target")))
                self._rate_log_ts = now
                return snapshot
            return None

    def _load_state(self):
        if not self.state_path.exists():
            self.taken = []
            return []
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            self.taken = []
            return []
        if isinstance(payload, dict):
            timestamps = payload.get("taken") if isinstance(payload.get("taken"), list) else payload.get("timestamps")
            if isinstance(timestamps, list):
                self.taken = [float(t) for t in timestamps if t is not None]
            else:
                self.taken = []
            if isinstance(payload.get("strengths"), list):
                self.strengths = [float(x) for x in payload["strengths"] if x is not None]
            return self.taken
        if isinstance(payload, list):
            self.taken = [float(x) for x in payload if x is not None]
            return self.taken
        self.taken = []
        return []

    def _persist(self):
        payload = {"taken": [float(ts) for ts in self.taken], "strengths": [float(v) for v in self.strengths]}
        tmp = self.state_path.with_suffix(".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            return
        try:
            os.replace(tmp, self.state_path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _percentile(self, values: Iterable[float], percentile: float) -> float:
        items = sorted(float(v) for v in values if v is not None)
        if not items:
            return 0.0
        if len(items) == 1:
            return items[0]
        if percentile <= 0.0:
            return items[0]
        if percentile >= 100.0:
            return items[-1]
        rank = (len(items) - 1) * (percentile / 100.0)
        lower = int(rank)
        upper = min(lower + 1, len(items) - 1)
        fraction = rank - lower
        return items[lower] + (items[upper] - items[lower]) * fraction

    def threshold(self):
        history = list(self.strengths)
        if not history or (self.min_history and len(history) < self.min_history):
            return None
        window = history[-self.window:] if self.window > 0 else history
        return self._percentile(window, self.percentile)

    def taken_last_hour(self, now=None):
        now = float(self.clock() if now is None else now)
        return sum(1 for ts in self.taken if now - ts <= 3600.0)

    def taken_last_24h(self, now=None):
        now = float(self.clock() if now is None else now)
        return sum(1 for ts in self.taken if now - ts <= 86400.0)

    def rate_snapshot(self, now=None):
        now = float(self.clock() if now is None else now)
        with self._lock:
            hourly_taken = self.taken_last_hour(now)
            daily_taken = self.taken_last_24h(now)
            last_taken = max(self.taken) if self.taken else None
            seconds_since_last = None if last_taken is None or not math_isfinite(last_taken) or last_taken > now else float(now - last_taken)
            threshold = self.threshold()
            achievable = hourly_taken < self.max_per_hour and (seconds_since_last is None or seconds_since_last >= self.min_spacing) and daily_taken < self.max_per_day
            decision_target = getattr(self, "target", None)
            orders_last_hour = hourly_taken
            orders_last_24h = daily_taken
            seeded_signals = len(self.strengths)
            seeded_top10pct = max(1, min(seeded_signals, int(round(seeded_signals * 0.10)))) if seeded_signals > 0 else 0
            pattern_confirmed = getattr(self, "pattern_confirmed", None)
            decided = len(self.strengths)
            projected_days_to_target = None
            if decision_target is not None and orders_last_24h == 0:
                projected_days_to_target = None
            elif decision_target is not None and orders_last_24h > 0:
                remaining = max(0, int(decision_target) - int(decided))
                projected_days_to_target = remaining / max(orders_last_24h, 1)
            return {
                "hourly_taken": hourly_taken,
                "daily_taken": daily_taken,
                "hourly_cap": self.max_per_hour,
                "daily_cap": self.max_per_day,
                "threshold": threshold,
                "seconds_since_last": seconds_since_last,
                "achievable": achievable,
                "ema_signals": seeded_signals,
                "seeded_signals": seeded_signals,
                "seeded_top10pct": seeded_top10pct,
                "pattern_confirmed": pattern_confirmed,
                "governor_selected": len(self.taken),
                "orders_accepted_last_hour": hourly_taken,
                "orders_accepted_last_24h": daily_taken,
                "projected_days_to_target": projected_days_to_target,
            }

    def evaluate(self, strength, now=None):
        now = float(self.clock() if now is None else now)
        with self._lock:
            threshold = self.threshold()
            history = list(self.strengths)
            last_taken = max(self.taken) if self.taken else None
            if last_taken is None or not math_isfinite(last_taken) or last_taken > now:
                seconds_since_last = None
            else:
                seconds_since_last = float(now - last_taken)
            taken_last_hour = self.taken_last_hour(now)
            taken_last_24h = self.taken_last_24h(now)
            if len(history) < self.min_history:
                return {
                    "allow": False,
                    "reason": "warmup",
                    "strength": float(strength),
                    "threshold": None,
                    "taken_last_hour": taken_last_hour,
                    "taken_last_24h": taken_last_24h,
                    "seconds_since_last": seconds_since_last,
                }
            if threshold is None:
                return {
                    "allow": False,
                    "reason": "warmup",
                    "strength": float(strength),
                    "threshold": None,
                    "taken_last_hour": taken_last_hour,
                    "taken_last_24h": taken_last_24h,
                    "seconds_since_last": seconds_since_last,
                }
            if float(strength) < threshold:
                return {
                    "allow": False,
                    "reason": "below_percentile",
                    "strength": float(strength),
                    "threshold": threshold,
                    "taken_last_hour": taken_last_hour,
                    "taken_last_24h": taken_last_24h,
                    "seconds_since_last": seconds_since_last,
                }
            if taken_last_hour >= self.max_per_hour:
                return {
                    "allow": False,
                    "reason": "hourly_cap",
                    "strength": float(strength),
                    "threshold": threshold,
                    "taken_last_hour": taken_last_hour,
                    "taken_last_24h": taken_last_24h,
                    "seconds_since_last": seconds_since_last,
                }
            if seconds_since_last is not None and seconds_since_last < self.min_spacing:
                return {
                    "allow": False,
                    "reason": "spacing",
                    "strength": float(strength),
                    "threshold": threshold,
                    "taken_last_hour": taken_last_hour,
                    "taken_last_24h": taken_last_24h,
                    "seconds_since_last": seconds_since_last,
                }
            if taken_last_24h >= self.max_per_day:
                return {
                    "allow": False,
                    "reason": "daily_cap",
                    "strength": float(strength),
                    "threshold": threshold,
                    "taken_last_hour": taken_last_hour,
                    "taken_last_24h": taken_last_24h,
                    "seconds_since_last": seconds_since_last,
                }
            return {
                "allow": True,
                "reason": "selected",
                "strength": float(strength),
                "threshold": threshold,
                "taken_last_hour": taken_last_hour,
                "taken_last_24h": taken_last_24h,
                "seconds_since_last": seconds_since_last,
            }

    def record_taken(self, now=None):
        now = float(self.clock() if now is None else now)
        with self._lock:
            self.taken.append(now)
            self._persist()
            return now


def math_isfinite(value):
    try:
        import math
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
