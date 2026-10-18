"""
bot/risk.py
════════════════════════════════════════════════════════════════════
Risk Manager & Trade Journal — Enhanced Edition

Same public API as the original; key additions:
  • `TradeRecord` now has an optional `state_key` field (needed by
    the multi-agent orchestrator).
  • `status()` now includes a `halted` flag (used by Supervisor).
  • `update_pnl_and_journal()` updates both in-memory stats and
    the CSV journal atomically.
  • Stricter daily-loss cap to protect capital.
════════════════════════════════════════════════════════════════════
"""

import csv
import logging
import math
import os
import time
import tempfile
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

log = logging.getLogger("risk")


@dataclass
class TradeRecord:
    timestamp:  str
    asset:      str
    mode:       str
    signal:     str
    confidence: float
    amount:     float
    result:     str = "PENDING"  # PENDING | WIN | LOSS | FAILED
    pnl:        float = 0.0
    state_key:  str = ""     # set by multi-agent orchestrator
    settled_at: str = ""


class RiskManager:
    """
    Tracks daily trade counts, P&L, and enforces hard caps.
    Thread-safe enough for single-process async use.
    """

    def __init__(self, *, max_daily_trades=None, max_daily_loss=None):
        from config import settings
        self.settings      = settings
        configured_trades = settings.MAX_DAILY_TRADES if max_daily_trades is None else max_daily_trades
        configured_loss = settings.MAX_DAILY_LOSS_USD if max_daily_loss is None else max_daily_loss
        self.max_daily_trades = self._positive_limit(configured_trades, "max_daily_trades", integer=True)
        self.max_daily_loss = self._positive_limit(configured_loss, "max_daily_loss")
        log.info("[Risk] Effective limits max_daily_trades=%d max_daily_loss=%.2f source=%s",
                 self.max_daily_trades, self.max_daily_loss,
                 "overrides" if max_daily_trades is not None or max_daily_loss is not None else "settings")
        self.journal_path  = os.path.join(settings.LOG_DIR, "trade_journal.csv")
        os.makedirs(settings.LOG_DIR, exist_ok=True)
        os.makedirs(settings.JOURNAL_ARCHIVE_DIR, exist_ok=True)
        self._archive_previous_journal_if_needed()
        self._ensure_journal()

        # Restarting cannot erase accepted orders or restore a spent daily budget.
        if getattr(self.settings, 'RESET_TODAY_LOGS_ON_STARTUP', False):
            log.warning("[Risk] Ignoring RESET_TODAY_LOGS_ON_STARTUP; durable risk accounting is retained")

        # Daily counters (reset when date changes)
        self._day:        str   = datetime.now().date().isoformat()
        self._trade_count: int  = 0
        self._daily_pnl:  float = 0.0
        self._halted:     bool  = False
        self.asset_stats: dict = {}

        # Load today's trades and historical asset stats from journal
        self._load_today()
        self._load_asset_history()

    # ── Public Interface ─────────────────────────────────────────

    @staticmethod
    def _positive_limit(value, name, integer=False):
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a positive {'integer' if integer else 'finite number'}")
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be a positive finite number") from None
        if not math.isfinite(numeric) or numeric <= 0 or (integer and not numeric.is_integer()):
            raise ValueError(f"{name} must be a positive {'integer' if integer else 'finite number'}")
        return int(numeric) if integer else numeric

    def approve(self, signal, confidence: float, mode: str,
                trade_amount: float = None, allow_during_training: bool = False) -> Tuple[bool, str]:
        """Reserve gross daily loss budget for accepted pending orders and the proposed stake.

        Training keeps its explicit daily-cap override, but the per-trade maximum
        applies to both training and normal execution.
        """
        settings = self.settings
        snapshot = self.daily_snapshot()
        if signal.value == "HOLD":
            return False, "Signal is HOLD"
        if not math.isfinite(confidence) or confidence < settings.CONFIDENCE_THRESHOLD:
            return False, (f"Confidence {confidence:.2%} < "
                           f"threshold {settings.CONFIDENCE_THRESHOLD:.2%}")
        amount = settings.TRADE_AMOUNT if trade_amount is None else trade_amount
        try:
            amount = self._positive_limit(amount, "trade_amount")
        except ValueError as exc:
            return False, str(exc)
        est_loss = self._estimate_trade_loss(amount, mode)
        if not math.isfinite(est_loss) or est_loss <= 0:
            return False, "Estimated trade loss must be positive and finite"
        if est_loss > settings.MAX_TRADE_LOSS_USD:
            return False, (f"Estimated trade loss ${est_loss:.2f} exceeds "
                           f"MAX_TRADE_LOSS_USD ${settings.MAX_TRADE_LOSS_USD:.2f}")
        if allow_during_training or getattr(settings, "TRAINING_MODE", False):
            return True, "Approved (training override)"
        if snapshot["trade_count"] >= self.max_daily_trades:
            return False, f"Max daily trades reached ({self.max_daily_trades})"
        if self._halted:
            return False, "Daily loss budget exhausted"
        projected_loss = snapshot["gross_loss"] + snapshot["pending_exposure"] + est_loss
        if projected_loss > self.max_daily_loss + 1e-9:
            return False, (f"Trade would breach daily loss cap: gross_loss=${snapshot['gross_loss']:.2f} "
                           f"+ pending_exposure=${snapshot['pending_exposure']:.2f} "
                           f"+ est_loss=${est_loss:.2f} > ${self.max_daily_loss:.2f}")
        return True, "Approved"

    def record_trade(self, record: TradeRecord) -> bool:
        """Persist an accepted order once; its timestamp is the journal identity."""
        self._check_day_rollover()
        found = self._find_journal_trade(record.timestamp)
        if found:
            _, _, _, existing = found
            if (any(existing.get(key) != getattr(record, key) for key in ("asset", "mode", "signal"))
                    or not math.isclose(float(existing["amount"]), float(record.amount), abs_tol=1e-9)):
                raise RuntimeError(f"Conflicting journal identity: {record.timestamp}")
            return False

        row = {
            "timestamp": record.timestamp, "asset": record.asset,
            "mode": record.mode, "signal": record.signal,
            "confidence": f"{record.confidence:.4f}", "amount": record.amount,
            "result": record.result, "pnl": record.pnl, "state_key": record.state_key,
            "settled_at": record.settled_at,
        }
        # Counters must not change until durable persistence succeeds.
        self._append_row(row)
        self._load_today()
        log.info(f"Trade recorded: {record.signal} | result={record.result} | pnl=${record.pnl:+.2f}")
        return True

    def update_pnl_and_journal(self, timestamp: str, result: str, pnl: float) -> bool:
        """Commit PENDING -> final exactly once, or report a missing/conflicting order.

        False means the same outcome is already durable. Persistence failures and
        missing identities propagate so the executor retains its unresolved order.
        """
        self._check_day_rollover()
        result = result.upper()
        pnl = float(pnl)
        if result not in {"WIN", "LOSS", "DRAW", "FAILED"} or not math.isfinite(pnl):
            raise ValueError("Settlement requires a final result and finite P&L")
        if not self._rewrite_journal_result(timestamp, result, pnl):
            return False
        self._load_today()
        self._load_asset_history()
        if self._halted:
            log.warning("[Risk] Daily budget exhausted; new orders are blocked")
        return True

    def confirm_flat_ftt_account(self, proof):
        """Release previous-day FTT exposure only after explicit broker flatness.

        Legacy archived PENDING rows retain unknown results and untouched P&L.
        The proof is session-local: every restart must verify the demo account
        again. Today's unconfirmed orders and Forex exposure are never released.
        """
        observed = float(proof.get("observed_at", 0))
        if (proof.get("source") != "platform_active_order_list"
                or proof.get("demo") is not True or proof.get("no_active_ftt") is not True
                or not math.isfinite(observed) or not 0 <= time.time() - observed <= 5):
            raise ValueError("Fresh explicit demo-account broker flatness is required")
        self._ftt_flat_as_of = observed
        snapshot = self.daily_snapshot()
        log.info("[RiskReconciliation] historical_ftt_results_unknown=%s "
                 "active_ftt=0 source=platform_active_order_list journal_unchanged=true",
                 snapshot["historical_unconfirmed_ftt_count"])
        return snapshot

    def status(self) -> dict:
        """Backwards-compatible alias for the durable account-risk snapshot."""
        return self.daily_snapshot()

    def daily_snapshot(self) -> dict:
        """Return durable statistics, with pending risk reserved across midnight.

        Realized P&L belongs to the order's entry date. Mechanical demo checks use
        account risk budget but are excluded from nested strategy statistics.
        """
        from copy import deepcopy
        self._check_day_rollover()
        self._load_today()
        return deepcopy(self._snapshot)

    # ── Internals ────────────────────────────────────────────────

    _JOURNAL_FIELDS = [
        "timestamp", "asset", "mode", "signal", "confidence",
        "amount", "result", "pnl", "state_key", "settled_at",
    ]

    def _ensure_journal(self):
        os.makedirs(os.path.dirname(self.journal_path), exist_ok=True)
        if not os.path.exists(self.journal_path):
            self._atomic_write_journal([], self._JOURNAL_FIELDS)

    def _read_journal(self, path=None):
        with open(path or self.journal_path, newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise RuntimeError("Trade journal has no CSV header")
            return list(reader), reader.fieldnames

    def _find_journal_trade(self, timestamp):
        paths = [self.journal_path]
        with os.scandir(self.settings.JOURNAL_ARCHIVE_DIR) as entries:
            paths.extend(entry.path for entry in entries
                         if entry.is_file() and entry.name.startswith("trade_journal_")
                         and entry.name.endswith(".csv"))
        found = None
        for path in paths:
            rows, fieldnames = self._read_journal(path)
            for row in rows:
                if row.get("timestamp") == timestamp:
                    if found is not None:
                        raise RuntimeError(f"Duplicate journal identity: {timestamp}")
                    found = (path, rows, fieldnames, row)
        return found

    def _atomic_write_journal(self, rows, fieldnames, path=None):
        """Replace the CSV only after its full replacement is flushed to disk."""
        path = path or self.journal_path
        directory = os.path.dirname(os.path.abspath(path))
        fd, temporary = tempfile.mkstemp(prefix=".trade_journal-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _journal_has_pending(self):
        rows, _ = self._read_journal()
        return any(row.get("result") == "PENDING" for row in rows)

    def _archive_previous_journal_if_needed(self):
        if not os.path.exists(self.journal_path):
            return

        today = datetime.now().date().isoformat()
        if self._journal_has_pending() or self._journal_contains_today_rows(today):
            return

        if not self._journal_has_rows():
            return

        last_day = self._journal_last_entry_day() or datetime.fromtimestamp(
            os.path.getmtime(self.journal_path)
        ).date().isoformat()

        archive_path = self._make_archive_path(last_day)
        if os.path.exists(archive_path):
            archive_path = self._make_archive_path(last_day, suffix=int(time.time()))

        try:
            os.rename(self.journal_path, archive_path)
            log.info(f"[Risk] Archived previous journal to {archive_path}")
        except OSError as e:
            log.warning(f"[Risk] Failed to archive previous journal: {e}")

    def _journal_has_rows(self) -> bool:
        try:
            with open(self.journal_path, newline="") as f:
                reader = csv.DictReader(f)
                return any(True for _ in reader)
        except Exception:
            return False

    def _journal_last_entry_day(self) -> Optional[str]:
        last_date = None
        try:
            with open(self.journal_path, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ts = row.get("timestamp", "")
                    if len(ts) >= 10:
                        last_date = ts[:10]
        except Exception:
            return None
        return last_date

    def _make_archive_path(self, day: str, suffix: Optional[int] = None) -> str:
        base_name = f"trade_journal_{self._format_archive_day(day)}"
        if suffix is not None:
            base_name = f"{base_name}_{suffix}"
        os.makedirs(self.settings.JOURNAL_ARCHIVE_DIR, exist_ok=True)
        return os.path.join(self.settings.JOURNAL_ARCHIVE_DIR, f"{base_name}.csv")

    def _format_archive_day(self, day: str) -> str:
        try:
            day_dt = datetime.fromisoformat(day)
            return day_dt.strftime(self.settings.LOG_DATE_FORMAT)
        except ValueError:
            return day

    def _journal_contains_today_rows(self, today: str) -> bool:
        try:
            with open(self.journal_path, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("timestamp", "").startswith(today):
                        return True
        except Exception:
            pass
        return False

    def reset_today(self):
        today = datetime.now().date().isoformat()
        rows = []
        try:
            with open(self.journal_path, newline="") as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames
                for row in reader:
                    if (not row.get("timestamp", "").startswith(today)
                            or row.get("result") == "PENDING"):
                        rows.append(row)

            self._atomic_write_journal(rows, fieldnames)

            self._day = today
            self._load_today()
            self._load_asset_history()
            log.info(f"[Risk] Cleared today's journal rows and reset risk state")
        except Exception as e:
            log.warning(f"[Risk] Failed to reset today's journal: {e}")

    def _append_row(self, row: dict):
        rows, fieldnames = self._read_journal()
        fieldnames = list(dict.fromkeys([*fieldnames, *row.keys()]))
        rows.append(row)
        self._atomic_write_journal(rows, fieldnames)

    def _rewrite_journal_result(self, timestamp: str, result: str, pnl: float) -> bool:
        found = self._find_journal_trade(timestamp)
        if found is None:
            raise LookupError(f"Cannot settle missing journal trade: {timestamp}")
        path, rows, fieldnames, row = found
        if row.get("result") != "PENDING":
            if (row.get("result") == result
                    and math.isclose(float(row.get("pnl", 0)), pnl, abs_tol=0.00005, rel_tol=0)):
                return False
            raise RuntimeError(f"Conflicting settled outcome for trade: {timestamp}")
        row["result"] = result
        row["pnl"] = f"{pnl:.4f}"
        row["settled_at"] = datetime.now().isoformat()
        fieldnames = list(dict.fromkeys([*fieldnames, "settled_at"]))
        self._atomic_write_journal(rows, fieldnames, path=path)
        return True

    def _check_day_rollover(self):
        today = datetime.now().date().isoformat()
        if today != self._day:
            self._day = today
            self._archive_previous_journal_if_needed()
            self._ensure_journal()
            self._load_today()
            log.info("New day %s — restored daily counters from the journal", today)

    def _archive_current_journal(self, day: str):
        if not os.path.exists(self.journal_path):
            return
        # An unresolved order must remain recoverable when the calendar rolls over.
        if self._journal_has_pending():
            return

        archive_path = self._make_archive_path(day)
        if os.path.exists(archive_path):
            archive_path = self._make_archive_path(day, suffix=int(time.time()))

        try:
            os.rename(self.journal_path, archive_path)
            log.info(f"[Risk] Rotated journal to {archive_path}")
        except OSError as e:
            log.warning(f"[Risk] Failed to rotate journal: {e}")

    def _estimate_trade_loss(self, trade_amount: float, mode: str, payout: Optional[float] = None) -> float:
        """Estimate the USD loss if the trade loses.

        - For FTT (binary) trades: losing the stake = full trade_amount.
        - For forex trades: estimate using `STOP_LOSS_PCT` of the stake.

        Returns positive USD value representing potential loss.
        """
        try:
            amt = float(trade_amount or 0.0)
        except Exception:
            amt = 0.0

        from config import settings

        if mode == "ftt":
            return amt

        # Forex-style: use configured stop-loss percentage
        try:
            sl = float(getattr(settings, 'STOP_LOSS_PCT', 0.02))
            return abs(amt * sl)
        except Exception:
            return amt

    def _all_journal_rows(self):
        paths = [self.journal_path]
        with os.scandir(self.settings.JOURNAL_ARCHIVE_DIR) as entries:
            paths.extend(sorted(entry.path for entry in entries
                                if entry.is_file() and entry.name.startswith("trade_journal_")
                                and entry.name.endswith(".csv")))
        rows, seen = [], set()
        for path in paths:
            source, _ = self._read_journal(path)
            for row in source:
                identity = row.get("timestamp")
                if not identity or identity in seen:
                    raise RuntimeError(f"Missing or duplicate journal identity: {identity}")
                seen.add(identity)
                rows.append(row)
        return rows

    def _summarize_rows(self, rows):
        daily = [row for row in rows if row["timestamp"].startswith(self._day)]
        final = [row for row in daily if row.get("result", "").upper() in {"WIN", "LOSS", "DRAW"}]
        pending = [row for row in rows if row.get("result", "").upper() == "PENDING"]
        unconfirmed_history = []
        flat_as_of = getattr(self, "_ftt_flat_as_of", None)
        if flat_as_of is not None:
            unconfirmed_history = [
                row for row in pending if row.get("mode") == "ftt"
                and row["timestamp"][:10] < self._day
                and datetime.fromisoformat(row["timestamp"]).timestamp() <= flat_as_of
            ]
            historical_ids = {row["timestamp"] for row in unconfirmed_history}
            pending = [row for row in pending if row["timestamp"] not in historical_ids]
        pnl = [float(row.get("pnl") or 0) for row in final]
        exposure = [self._estimate_trade_loss(float(row["amount"]), row["mode"]) for row in pending]
        if not all(math.isfinite(value) for value in [*pnl, *exposure]) or any(value <= 0 for value in exposure):
            raise RuntimeError("Invalid journal P&L or pending exposure; risk accounting cannot be restored")
        outcomes = [{
            "timestamp": row["timestamp"],
            "settled_at": row.get("settled_at") or row["timestamp"],
            "result": row["result"].upper(),
            "pnl": float(row.get("pnl") or 0),
        } for row in final]
        outcomes.sort(key=lambda item: (item["settled_at"], item["timestamp"]))
        consecutive_losses = 0
        last_loss_at = None
        for item in outcomes:
            if (consecutive_losses >= 4 and last_loss_at
                    and (datetime.fromisoformat(item["settled_at"]) -
                         datetime.fromisoformat(last_loss_at)).total_seconds() >= 300):
                consecutive_losses = 0
                last_loss_at = None
            if item["result"] == "LOSS":
                consecutive_losses += 1
                last_loss_at = item["settled_at"]
            elif item["result"] == "WIN":
                consecutive_losses = 0
                last_loss_at = None
        gross_profit = sum(value for value in pnl if value > 0)
        gross_loss = -sum(value for value in pnl if value < 0)
        return {
            "date": self._day,
            "accepted_count": len(daily), "trade_count": len(daily),
            "settled_count": len(final),
            "wins": sum(row["result"].upper() == "WIN" for row in final),
            "losses": sum(row["result"].upper() == "LOSS" for row in final),
            "draws": sum(row["result"].upper() == "DRAW" for row in final),
            "gross_profit": gross_profit, "gross_loss": gross_loss,
            "net_pnl": gross_profit - gross_loss, "daily_pnl": gross_profit - gross_loss,
            "pending_count": len(pending), "pending_exposure": sum(exposure),
            "historical_unconfirmed_ftt_count": len(unconfirmed_history),
            "consecutive_losses": consecutive_losses, "last_loss_at": last_loss_at,
            "recent_outcomes": outcomes[-20:],
        }

    def _load_today(self):
        """Recompute, never increment, so retries and restarts produce identical totals."""
        rows = self._all_journal_rows()
        snapshot = self._summarize_rows(rows)
        snapshot["strategy"] = self._summarize_rows([
            row for row in rows if row.get("state_key") != "DEMO_EXECUTION_TEST"
        ])
        self._trade_count = snapshot["trade_count"]
        self._daily_pnl = snapshot["daily_pnl"]
        self._halted = (self._trade_count >= self.max_daily_trades or
                        snapshot["gross_loss"] + snapshot["pending_exposure"] >= self.max_daily_loss - 1e-9)
        snapshot.update({
            "max_trades": self.max_daily_trades, "max_daily_trades": self.max_daily_trades,
            "max_daily_loss": self.max_daily_loss,
            "trades_left": max(0, self.max_daily_trades - self._trade_count),
            "loss_budget_remaining": max(0.0, self.max_daily_loss - snapshot["gross_loss"] - snapshot["pending_exposure"]),
            "halted": self._halted,
        })
        self._snapshot = snapshot

    def _load_asset_history(self):
        """Historical strategy-only WIN/LOSS counts; mechanical demo checks are excluded."""
        stats = {}
        for row in self._all_journal_rows():
            if row.get("state_key") == "DEMO_EXECUTION_TEST":
                continue
            asset, result = row.get("asset"), row.get("result", "").upper()
            if not asset or result not in {"WIN", "LOSS"}:
                continue
            entry = stats.setdefault(asset, {"wins": 0, "losses": 0, "total": 0})
            entry["wins" if result == "WIN" else "losses"] += 1
            entry["total"] += 1
        self.asset_stats = stats
