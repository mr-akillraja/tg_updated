import csv
import glob
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable, List

log = logging.getLogger("campaign")
log.setLevel(logging.INFO)
log.propagate = True


class CampaignTracker:
    def __init__(self, journal_path, archive_dir, state_key="ONE_INDICATOR_EMA", target=1000,
                 since="", payout=0.85, extra_dirs=None, clock=time.time):
        self.journal_path = str(journal_path)
        self.archive_dir = str(archive_dir)
        self.state_key = state_key
        self.target = int(target)
        self.since = since
        self.payout = float(payout)
        self.extra_dirs = list(dict.fromkeys((extra_dirs or []) if isinstance(extra_dirs, (list, tuple, set)) else []))
        self.clock = clock
        self.last_logged_count = None
        self.last_verdict = None
        self._startup_logged = False
        self._mixed_version_warning_logged = False
        self._target_reached = False
        self._last_results_log_ts = 0.0
        self._last_results_count = None
        self._stop_event = threading.Event()
        self._thread = None

    def _as_iso(self, value):
        if value in (None, ""):
            return None
        value = str(value).strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return None

    def _journal_files(self):
        files = []
        if os.path.exists(self.journal_path):
            files.append(self.journal_path)
        archive_path = Path(self.archive_dir)
        if archive_path.exists():
            files.extend(sorted(glob.glob(str(archive_path / "trade_journal_*.csv"))))
        for extra in self.extra_dirs:
            extra_path = Path(extra)
            if extra_path.exists():
                files.extend(sorted(glob.glob(str(extra_path / "trade_journal_*.csv"))))
        deduped = []
        seen = set()
        for path in files:
            real = os.path.abspath(path)
            if real not in seen:
                deduped.append(real)
                seen.add(real)
        return deduped

    def _read_rows(self, since=None):
        rows = []
        files = self._journal_files()
        since_ts = self._as_iso(since or self.since)
        for path in files:
            if not os.path.exists(path):
                continue
            try:
                with open(path, newline="", encoding="utf-8") as handle:
                    reader = csv.DictReader(handle)
                    for row in reader:
                        if not row:
                            continue
                        ts = row.get("timestamp")
                        result = str(row.get("result", "")).upper()
                        state_key = str(row.get("state_key", ""))
                        if ts in (None, "") or result not in {"WIN", "LOSS", "DRAW"}:
                            continue
                        if self.state_key and state_key and state_key != self.state_key:
                            continue
                        ts_val = self._as_iso(ts)
                        if since_ts is not None and ts_val is not None and ts_val < since_ts:
                            continue
                        try:
                            pnl = float(row.get("pnl", 0.0))
                        except (TypeError, ValueError):
                            pnl = 0.0
                        rows.append({"timestamp": ts, "result": result, "pnl": pnl, "state_key": state_key})
            except OSError:
                continue
        deduped = {}
        for row in rows:
            deduped[row["timestamp"]] = row
        return list(deduped.values())

    def _get_start_cutoff(self):
        if self.since:
            return self._as_iso(self.since)
        start_path = Path(self.archive_dir) / "campaign_start.json"
        if start_path.exists():
            try:
                payload = json.loads(start_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and payload.get("start"):
                    value = self._as_iso(payload.get("start"))
                    if value is not None:
                        return value
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                pass
        return None

    def stats(self):
        cutoff = self._get_start_cutoff()
        all_rows = self._read_rows()
        rows = self._read_rows(cutoff) if cutoff is not None else all_rows
        excluded_before_start = max(0, len(all_rows) - len(rows)) if cutoff is not None else 0
        wins = sum(1 for row in rows if row["result"] == "WIN")
        losses = sum(1 for row in rows if row["result"] == "LOSS")
        draws = sum(1 for row in rows if row["result"] == "DRAW")
        decided = wins + losses
        total = wins + losses + draws
        net_pnl = sum(float(r["pnl"]) for r in rows)
        win_rate = (wins / decided) if decided else 0.0
        z_score = 1.96
        phat = win_rate
        denom = 1.0 + (z_score ** 2) / decided if decided else 1.0
        center = (phat + (z_score ** 2) / (2 * decided)) / denom if decided else 0.0
        margin = (z_score * ((phat * (1.0 - phat) / decided + (z_score ** 2) / (4 * decided ** 2)) ** 0.5)) / denom if decided else 0.0
        lower_bound = max(0.0, center - margin)
        upper_bound = min(1.0, center + margin)
        break_even = 1.0 / (1.0 + self.payout)
        if decided == 0:
            verdict = "NOT PROVEN"
        elif upper_bound < break_even:
            verdict = "LOSES"
        elif lower_bound > break_even and decided >= self.target:
            verdict = "PASS"
        elif lower_bound > break_even and decided < self.target:
            verdict = "PROMISING"
        else:
            verdict = "NOT PROVEN"
        state_keys = sorted({row["state_key"] for row in rows if row.get("state_key")})
        by_date = {}
        for row in rows:
            ts = self._as_iso(row["timestamp"])
            if ts is None:
                continue
            try:
                key = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            except (OverflowError, OSError, ValueError):
                key = "unknown"
            by_date[key] = by_date.get(key, 0) + 1
        stats = {
            "trades": total,
            "trades_total": total,
            "decided": decided,
            "draws": draws,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 4),
            "wilson_lower": round(lower_bound, 4),
            "wilson_upper": round(upper_bound, 4),
            "net_pnl": round(net_pnl, 2),
            "break_even": round(break_even, 4),
            "verdict": verdict,
            "target": self.target,
            "state_keys": state_keys,
            "excluded_before_start": excluded_before_start,
            "by_date": by_date,
            "start": cutoff,
        }
        if not self._startup_logged:
            if not self.since and cutoff is None and len(rows) > 0 and not self._mixed_version_warning_logged:
                log.warning(
                    "[Campaign] WARNING counting all %s trades across by_date=%s; earlier strategy versions may be mixed in. Run tools/campaign_reset.py --start-now --yes to start a clean count",
                    len(rows),
                    json.dumps(by_date, sort_keys=True),
                )
                self._mixed_version_warning_logged = True
            journal_abs = os.path.abspath(self.journal_path)
            archive_abs = os.path.abspath(self.archive_dir)
            files = self._journal_files()
            log.info("[Campaign] breakdown by_date=%s counted=%d excluded_before_start=%d start=%s",
                     json.dumps(by_date, sort_keys=True), len(rows), excluded_before_start, cutoff)
            log.info("[Campaign] journal=%s exists=%s archive_dir=%s files_scanned=%d rows_matching=%d since=%s state_keys=%s",
                     journal_abs,
                     os.path.exists(self.journal_path),
                     archive_abs,
                     len(files),
                     len(rows),
                     self.since,
                     state_keys,
            )
            self._startup_logged = True
        changed = self.last_logged_count != total
        if changed or self._last_results_log_ts == 0.0 or (self.clock() - self._last_results_log_ts) >= 3600.0:
            log.info("[Results] trades_total=%s decided=%s draws=%s wins=%s losses=%s win_rate=%.4f net_pnl=%.2f wilson_95=[%.4f, %.4f] break_even=%.4f verdict=%s",
                     total, decided, draws, wins, losses, win_rate, net_pnl, lower_bound, upper_bound, break_even, verdict)
            self._last_results_log_ts = self.clock()
        if self.last_logged_count != total:
            log.info("[Campaign] %s", json.dumps(stats, sort_keys=True, default=str))
            self.last_logged_count = total
        if total >= self.target and not self._target_reached:
            self._target_reached = True
            log.info("[Campaign] verdict=%s reason=campaign_target_reached target=%s trades=%s", verdict, self.target, total)
        if verdict != self.last_verdict:
            self.last_verdict = verdict
        return stats

    def start(self):
        if self._thread and self._thread.is_alive():
            return self._thread
        def worker():
            while not self._stop_event.wait(30):
                self.stats()
        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()
        return self._thread

    def stop(self):
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)


__all__ = ["CampaignTracker"]
