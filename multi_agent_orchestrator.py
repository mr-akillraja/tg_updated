"""BUY/SELL demo strategy with one-second provisional market monitoring.

Completed 30s candles drive entry decisions by default (60s is optional).
Existing directional entry rules and matching 5m trend must confirm each 60s order.
"""

import asyncio
import hashlib
import inspect
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import shutil
from pathlib import Path
import numpy as np
import argparse
import json
import math
from contextlib import nullcontext
from bot.process_lock import ProcessLock
from strategies.closed_candle import evaluate_both, evaluate_demo_continuation
from strategies.live_monitor import analyze_forming
from strategies.ema_pattern import evaluate_one_indicator
from bot.live_feed import _norm as _norm_asset, candle_momentum
from strategies.trend_filter import evaluate_five_minute_trend

sys.path.insert(0, os.path.dirname(__file__))

from config import settings
from bot.risk import RiskManager, TradeRecord
from bot.browser import OlymtradeBot
from bot.chart_indicators import configure_strategy_chart, reconcile_selected_strategy_chart
from models.predictor import Signal
from agents.agent_multi_timeframe import MultiTimeframeAgent
from agents.agent_rl_learning import RLFeedbackAgent
from agents.agent_pipeline_supervisor import PipelineSupervisorAgent
from agents.agent_regime_detector import RegimeDetector
from agents.agent_asset_selector import AssetSelectorAgent
from agents.chart_analyzer import ChartAnalyzer, AnalysisResult
from agents.multi_timeframe_consensus import MultiTimeframeConsensusAnalyzer
from utils.log_filters import MarketDataChangeFilter

BEAR_REGIMES = {"BEAR_TREND", "HIGH_VOL_DOWN", "BREAKOUT_DOWN"}
BULL_REGIMES = {"BULL_TREND",  "HIGH_VOL_UP",  "BREAKOUT_UP"}

# ── Risk constants (from your strategy plan) ──────────────────────────────────
TRADE_AMOUNT        = 200.0    # $200 fixed per trade
DAILY_LOSS_LIMIT    = 2000.0   # $2,000 circuit breaker
MIN_CONFIDENCE      = 0.60     # 60% minimum (strategy doc: 60–65%)
MIN_CONFLUENCES     = 5        # require 5/9 indicators to agree
MAX_TRADES_PER_DAY  = 20       # hard cap
PAYOUT_MINIMUM      = 0.80     # skip assets below 80% payout
FTT_DURATION_S      = 60       # 1 minute trades


def resolve_daily_limits(cli_max_daily_trades=None, cli_max_daily_loss=None):
    def pick(cli_value, env_name, default_value, *, integer):
        if cli_value is not None:
            value = cli_value
            source = "cli"
        else:
            env_value = os.getenv(env_name)
            if env_value is not None:
                value = env_value
                source = "env"
            elif hasattr(settings, env_name):
                value = getattr(settings, env_name)
                source = "env"
            else:
                value = default_value
                source = "default"
        try:
            if integer:
                return int(value), source
            return float(value), source
        except (TypeError, ValueError):
            fallback = int(default_value) if integer else float(default_value)
            return fallback, "default"

    trade_limit, trade_source = pick(cli_max_daily_trades, "MAX_DAILY_TRADES", MAX_TRADES_PER_DAY, integer=True)
    loss_limit, loss_source = pick(cli_max_daily_loss, "MAX_DAILY_LOSS", DAILY_LOSS_LIMIT, integer=False)
    if loss_limit is None or loss_limit <= 0:
        loss_limit = DAILY_LOSS_LIMIT
        loss_source = "default"
    if trade_limit is None or trade_limit <= 0:
        trade_limit = MAX_TRADES_PER_DAY
        trade_source = "default"
    return trade_limit, loss_limit, trade_source, loss_source


def _rotate_daily_log(log_path: str):
    if not os.path.exists(log_path):
        return
    archive_dir = settings.LOG_ARCHIVE_DIR
    os.makedirs(archive_dir, exist_ok=True)
    today     = datetime.now().date()
    file_date = datetime.fromtimestamp(os.path.getmtime(log_path)).date()
    if file_date < today:
        name = (f"multi_agent_system_"
                f"{file_date.strftime(settings.LOG_DATE_FORMAT)}.log")
        path = os.path.join(archive_dir, name)
        if os.path.exists(path):
            name = (f"multi_agent_system_"
                    f"{file_date.strftime(settings.LOG_DATE_FORMAT)}"
                    f"_{int(time.time())}.log")
            path = os.path.join(archive_dir, name)
        os.rename(log_path, path)



log = logging.getLogger("multi_agent_orchestrator")
log.setLevel(logging.INFO)
log.propagate = True


def select_entry_evaluator():
    if getattr(settings, "DEMO_STRATEGY_ENABLED", False):
        return evaluate_demo_continuation
    return evaluate_both


class MonitorConsoleFormatter(logging.Formatter):
    """Keep the terminal readable; file handlers retain the full JSON evidence."""

    def format(self, record):
        message = record.getMessage()
        if message.startswith("[Decision] {"):
            try:
                data = json.loads(message[len("[Decision] "):])
                gate = data.get("entry_evaluation", data.get("buy_evaluation", {}))
                entry_seconds = data.get("entry_timeframe_seconds", gate.get("entry_timeframe_seconds", 60))
                entry_label = "30s" if entry_seconds == 30 else "1m"
                live_entry = data.get("entry_candle", data.get("candles", {}).get(str(entry_seconds), {}))
                live_rsi = live_entry.get("indicators", {}).get("rsi14")
                number = lambda value: f"{value:.5f}" if isinstance(value, (int, float)) else "?"
                seconds = lambda value: f"{value:.3f}" if isinstance(value, (int, float)) else "?"
                timing = data.get("entry_timing", {})
                window = {True: "open", False: "closed"}.get(timing.get("within_window"), "unknown")
                stale_timeframes = ",".join(f"{period}s" for period in data.get("stale_closed_timeframes", [])) or "none"
                message = (
                    f"[Decision] {data['decision']} stage={data['blocking_stage']} "
                    f"direction={data.get('direction') or 'none'} "
                    f"evaluating={data.get('diagnostic_direction', 'BUY')} "
                    f"reason={data['reason']} quote={number(data.get('live_price'))} "
                    f"quote_status={data.get('quote_status', 'unknown')} "
                    f"quote_age_seconds={seconds(data.get('quote_age_seconds'))} "
                    f"closed_history={data.get('closed_candle_status', 'unknown')} "
                    f"stale_timeframes={stale_timeframes} "
                    f"trend.30s.closed={number(gate.get('trend_close'))} "
                    f"entry.{entry_label}.closed={number(gate.get('entry_close'))} "
                    f"entry_seconds={entry_seconds} "
                    f"entry_age_seconds={seconds(timing.get('age_seconds'))} entry_window={window} "
                    f"{entry_label}.RSI.closed={number(gate.get('rsi'))} "
                    f"{entry_label}.RSI.live={number(live_rsi)} "
                    f"5m={data.get('trend_filter_5m', {}).get('reason', 'unavailable')} "
                    f"failed_count={len(gate.get('failed_rules', []))} "
                    f"accepted={data.get('accepted_orders_this_run', 0)}")
            except (ValueError, TypeError, KeyError):
                pass
        # Do not mutate the shared record: the file handlers need the original.
        copy = logging.makeLogRecord(record.__dict__)
        copy.msg, copy.args = message, ()
        return super().format(copy)


def log_build_fingerprint():
    if getattr(log, "_build_fingerprint_logged", False):
        return
    project_dir = Path(__file__).resolve().parent
    targets = [
        ("multi_agent_orchestrator.py", project_dir / "multi_agent_orchestrator.py"),
        ("bot/governor_plugin.py", project_dir / "bot" / "governor_plugin.py"),
        ("strategies/ema_pattern.py", project_dir / "strategies" / "ema_pattern.py"),
        ("bot/live_feed.py", project_dir / "bot" / "live_feed.py"),
        ("bot/browser.py", project_dir / "bot" / "browser.py"),
    ]
    pieces = []
    for label, path in targets:
        try:
            digest = hashlib.sha1(path.read_bytes()).hexdigest()[:10]
        except OSError:
            digest = "missing"
        pieces.append(f"sha1({label})={digest}")
    log.info("[Build] project_dir=%s python=%s conda_env=%s %s",
             str(project_dir.resolve()),
             sys.executable,
             os.getenv("CONDA_DEFAULT_ENV", ""),
             " ".join(pieces))
    log._build_fingerprint_logged = True


def configure_logging():
    os.makedirs(settings.LOG_DIR, exist_ok=True)
    log_file = os.path.join(settings.LOG_DIR, "multi_agent_system.log")
    _rotate_daily_log(log_file)
    run_dir = Path(settings.LOG_DIR) / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    run_log = run_dir / ("unified_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".log")
    format_string = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(MonitorConsoleFormatter(format_string))
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    root.addFilter(MarketDataChangeFilter())
    root.addHandler(console)
    root.addHandler(logging.FileHandler(log_file, encoding="utf-8"))
    root.addHandler(logging.FileHandler(run_log, encoding="utf-8"))
    log.info("[RunLog] path=%s", run_log.resolve())
    log_build_fingerprint()


# ── OHLC helper (fixes RSI=2.9 seed noise bug) ───────────────────────────────

def _get_ohlc_from_builder(builder):
    """Read actual closed OHLC only; never invent highs, lows, or opens."""
    return (builder.get_opens(), builder.get_highs(),
            builder.get_lows(), builder.get_closes())


# ── Session window helpers ────────────────────────────────────────────────────

# ── Main system ───────────────────────────────────────────────────────────────

class MultiAgentTradingSystem:

    TICK_INTERVAL     = 1.0
    DECISION_INTERVAL = 1.0
    CONFIRMATION_WINDOW = 5.0  # Existing allowed delay after the entry candle closes.
    ASSET_CHECK_EVERY = settings.ASSET_SCAN_INTERVAL

    def __init__(self, force_trades: bool = False,
                 training_mode: bool = False,
                 disable_circuit_breaker: bool = False,
                 min_confidence: float = MIN_CONFIDENCE,
                 trade_amount: float = TRADE_AMOUNT,
                 payout_minimum: float = PAYOUT_MINIMUM,
                 max_trades_per_day: int = MAX_TRADES_PER_DAY,
                 daily_loss_limit: float = DAILY_LOSS_LIMIT,
                 observe_only: bool = False, max_candle_closes: int = 0,
                 max_runtime: float = 0, chart_view: str = "entry",
                 entry_seconds: Optional[int] = None):
        self.entry_seconds = settings.STRATEGY_ENTRY_SECONDS if entry_seconds is None else entry_seconds
        if self.entry_seconds not in (30, 60):
            raise ValueError("Entry candles must be 30 or 60 seconds")
        if settings.RESET_TODAY_LOGS_ON_STARTUP:
            log.warning("[Risk] startup_reset_ignored=true reason=preserve_trade_accounting")
            settings.RESET_TODAY_LOGS_ON_STARTUP = False
        if not math.isfinite(float(trade_amount)) or float(trade_amount) <= 0:
            raise ValueError("Trade amount must be finite and positive")
        if chart_view not in {"entry", "trend", "filter5m"}:
            raise ValueError("Chart view must be entry, trend or filter5m")
        self.chart_view = chart_view
        self.bot            = OlymtradeBot()
        self.risk           = RiskManager(max_daily_trades=max_trades_per_day,
                                          max_daily_loss=daily_loss_limit)
        self.asset_selector = AssetSelectorAgent()
        self.agent1         = MultiTimeframeAgent(settings.ASSET)
        self.agent2         = RLFeedbackAgent(settings.MODEL_DIR)
        self.regime         = RegimeDetector()
        self.chart          = ChartAnalyzer()
        self.consensus      = MultiTimeframeConsensusAnalyzer(self.chart)
        # Debug / test overrides (set before supervisor creation)
        self.force_trades: bool = bool(force_trades)
        self.training_mode: bool = bool(training_mode)
        self.disable_circuit_breaker: bool = bool(disable_circuit_breaker)
        self.min_confidence: float = float(min_confidence)
        self.trade_amount: float = float(trade_amount)
        settings.TRADE_AMOUNT = self.trade_amount
        self.payout_minimum: float = float(payout_minimum)
        self.max_trades_per_day = self.risk.max_daily_trades
        self.daily_loss_limit = self.risk.max_daily_loss

        self.supervisor     = PipelineSupervisorAgent(
            self.bot, self.risk, self.agent1, self.agent2,
            asset_selector=self.asset_selector,
            force_trades=self.force_trades,
            disable_circuit_breaker=self.disable_circuit_breaker,
        )

        self.observe_only = observe_only
        self.max_candle_closes = max_candle_closes
        self.max_runtime = max_runtime
        self.closed_events = 0
        self._last_evaluation_key = None
        self._last_attempted_entry = None
        self._last_attempted_entry_seconds = None
        self._attempted_entries = {}
        self._execution_lock = asyncio.Lock()
        self._feed_missing = False
        self._process_lock_held = False
        self._execution_uncertain = False
        self._last_pause_log_ts = 0.0
        self._quote = None
        self.latest_decision = None
        self._chart_sync_active = False
        self._last_chart_report = None
        self._decision_tasks = set()
        self._accepted_count = 0
        self.filter_stats = {"decisions": 0, "by_reason": {}, "by_filter": {}}
        self._seed_sources = {}
        self.active_trades: List[Dict] = []
        self._order_state_path = Path(settings.LOG_DIR) / "execution_state.json"
        self._attempted_setup_path = Path(settings.LOG_DIR) / "last_entry_setup.json"
        if self._attempted_setup_path.exists():
            try:
                attempted = json.loads(self._attempted_setup_path.read_text())
                if not isinstance(attempted, dict):
                    raise ValueError("Invalid attempted setup state")
                if attempted.get("asset") == settings.ASSET:
                    entry_ts = float(attempted["entry_ts"])
                    if not math.isfinite(entry_ts):
                        raise ValueError("Invalid setup timestamp")
                    entry_seconds = attempted.get("entry_timeframe_seconds", 60)
                    if entry_seconds not in (30, 60):
                        raise ValueError("Invalid setup timeframe")
                    entries = attempted.get("entries_by_timeframe", {str(entry_seconds): entry_ts})
                    if (not isinstance(entries, dict) or
                            any(period not in {"30", "60"} or not math.isfinite(float(timestamp))
                                for period, timestamp in entries.items())):
                        raise ValueError("Invalid attempted setups")
                    self._attempted_entries = {int(period): float(timestamp) for period, timestamp in entries.items()}
                    self._attempted_entries[entry_seconds] = entry_ts
                    self._last_attempted_entry = entry_ts
                    self._last_attempted_entry_seconds = entry_seconds
            except (ValueError, KeyError, TypeError, OSError):
                self._execution_uncertain = True
        if self._order_state_path.exists():
            try:
                state = json.loads(self._order_state_path.read_text())
                if state and state.get("status") == "accepted":
                    self.active_trades = [state["order"]]
                    order = state["order"]
                    self.risk.record_trade(TradeRecord(
                        timestamp=order["timestamp"], asset=order.get("asset", settings.ASSET),
                        mode="ftt", signal=order["signal"], confidence=0.95,
                        amount=order["amount"], state_key=order.get("state_key", "")))
                elif state:
                    self._execution_uncertain = True
            except (ValueError, KeyError, OSError):
                self._execution_uncertain = True
            if self.active_trades or self._execution_uncertain:
                log.warning("[Execution] pending_state_restored=true entries=paused_until_reconciled")
                self._log_execution_pause()
        self.running = False
        self._last_decision_ts:    float = 0.0
        self._last_asset_check_ts: float = 0.0
        self._current_asset: str   = settings.ASSET
        self._bg_tasks: List[asyncio.Task] = []

        self._loss_halt_duration_sec = 300.0
        snapshot = self._sync_daily_accounting()
        self.supervisor.cb.restore_from_outcomes(snapshot["strategy"]["recent_outcomes"])
        self._log_daily_accounting("restored")

    def _record_filter_stats(self, result: Dict[str, Any]) -> None:
        if not hasattr(self, "filter_stats") or not isinstance(self.filter_stats, dict):
            self.filter_stats = {"decisions": 0, "by_reason": {}, "by_filter": {}, "ema_signals": 0, "pattern_confirmed": 0}
        signal = result.get("signal") or result.get("direction")
        reason = result.get("reason") or "unknown"
        first_blocker = result.get("blocked_by") or reason
        if signal in {"BUY", "SELL"}:
            self.filter_stats["ema_signals"] = self.filter_stats.get("ema_signals", 0) + 1
        if str(reason).startswith("pattern_confirmed_"):
            self.filter_stats["pattern_confirmed"] = self.filter_stats.get("pattern_confirmed", 0) + 1
        self.filter_stats["decisions"] += 1
        if str(reason).startswith("pattern_confirmed_"):
            self.filter_stats["by_reason"]["pattern_confirmed"] = self.filter_stats["by_reason"].get("pattern_confirmed", 0) + 1
        elif first_blocker not in {"none", "", None}:
            self.filter_stats["by_reason"][first_blocker] = self.filter_stats["by_reason"].get(first_blocker, 0) + 1
        else:
            self.filter_stats["by_reason"][reason] = self.filter_stats["by_reason"].get(reason, 0) + 1
        filter_name = first_blocker if first_blocker not in {"none", "", None} and not str(reason).startswith("pattern_confirmed_") else "pattern_confirmed" if str(reason).startswith("pattern_confirmed_") else reason
        self.filter_stats["by_filter"][filter_name] = self.filter_stats["by_filter"].get(filter_name, 0) + 1
        if self.filter_stats["decisions"] % int(getattr(settings, "ONE_INDICATOR_FILTER_STATS_EVERY", 50)) == 0:
            total = max(self.filter_stats["decisions"], 1)
            summary = {
                "decisions": self.filter_stats["decisions"],
                "ema_signals": self.filter_stats.get("ema_signals", 0),
                "pattern_confirmed": self.filter_stats.get("pattern_confirmed", 0),
                "by_reason": {k: v for k, v in sorted(self.filter_stats["by_reason"].items())},
                "by_filter": {k: v for k, v in sorted(self.filter_stats["by_filter"].items())},
                "percent_by_reason": {k: round(v / total * 100.0, 2) for k, v in sorted(self.filter_stats["by_reason"].items())},
                "percent_by_filter": {k: round(v / total * 100.0, 2) for k, v in sorted(self.filter_stats["by_filter"].items())},
                "top_filter": max(self.filter_stats["by_filter"].items(), key=lambda item: item[1])[0] if self.filter_stats["by_filter"] else None,
            }
            log.info("[FilterStats] %s", json.dumps(summary, sort_keys=True))

    def _sync_daily_accounting(self):
        """Daily controls derive from durable records, including pending orders."""
        snapshot = self.risk.daily_snapshot()
        self._daily_loss = snapshot["gross_loss"]
        self._daily_profit = snapshot["gross_profit"]
        self._trades_today = snapshot["accepted_count"]
        self._settled_today = snapshot["settled_count"]
        self._wins_today = snapshot["wins"]
        self._consecutive_losses = snapshot["strategy"]["consecutive_losses"]
        last_loss = snapshot["strategy"]["last_loss_at"]
        self._loss_halt_time = datetime.fromisoformat(last_loss).timestamp() if last_loss else 0.0
        self._account_snapshot = snapshot
        return snapshot

    def _log_daily_accounting(self, event):
        snapshot = self._sync_daily_accounting()
        strategy = snapshot["strategy"]
        log.info("[DailyRisk] %s", json.dumps({
            "event": event, "date": snapshot["date"], "source": "trade_journal",
            "max_daily_trades": self.max_trades_per_day,
            "max_daily_loss": self.daily_loss_limit, "loss_basis": "gross_loss_plus_pending",
            "accepted": snapshot["accepted_count"], "settled": snapshot["settled_count"],
            "gross_loss": snapshot["gross_loss"], "net_pnl": snapshot["net_pnl"],
            "pending_exposure": snapshot["pending_exposure"],
            "historical_unconfirmed_ftt_count": snapshot["historical_unconfirmed_ftt_count"],
            "strategy_settled": strategy["settled_count"],
            "strategy_wins": strategy["wins"], "demo_tests_in_strategy_stats": False,
        }, sort_keys=True))

    def _check_consecutive_loss_halt(self) -> Tuple[bool, str]:
        self._sync_daily_accounting()
        remaining = self._loss_halt_duration_sec - (time.time() - self._loss_halt_time)
        if self._consecutive_losses >= 4 and remaining > 0:
            reason = f"Loss halt active ({self._consecutive_losses} losses), {remaining:.0f}s left"
            log.warning("[DailyRisk] %s", reason)
            return True, reason
        return False, ""

    def _trade_capacity(self) -> Tuple[int, int, float]:
        snapshot = self._sync_daily_accounting()
        loss_budget = snapshot["loss_budget_remaining"]
        return snapshot["trades_left"], int(loss_budget // self.trade_amount), loss_budget

    def _log_execution_pause(self):
        if self.active_trades:
            reason = "active_trades"
            order_id = self.active_trades[0].get("id", "unknown")
        elif self._execution_uncertain:
            reason = "uncertain"
            order_id = getattr(self, "_uncertain_order_id", "unknown")
        else:
            return
        now = time.monotonic()
        last = getattr(self, "_last_pause_log_ts", 0.0)
        if now - last < 60.0:
            return
        self._last_pause_log_ts = now
        log.warning("[Execution] entries_paused reason=%s order_id=%s", reason, order_id)

    def _clear_execution_state(self, reason: str, order_id: Optional[str] = None):
        self.active_trades = []
        self._execution_uncertain = False
        self._uncertain_order_id = None
        if hasattr(self, "supervisor") and hasattr(self.supervisor, "execution_uncertain"):
            try:
                self.supervisor.execution_uncertain = False
            except Exception:
                pass
        self._persist_order_state(None)
        log.warning("[Execution] state_cleared reason=%s order_id=%s", reason, order_id or "unknown")

    async def _fetch_order_rows(self, include_history: bool = False, wanted_id: Optional[str] = None):
        bot = getattr(self, "bot", None)
        if bot is None:
            return []
        fetch = getattr(bot, "_order_rows", None)
        if fetch is None:
            return []
        try:
            return await fetch(include_history=include_history, wanted_id=wanted_id)
        except TypeError:
            if wanted_id is not None:
                rows = await fetch(include_history=include_history)
                return [row for row in rows if str(row.get("id")) == str(wanted_id)]
            return await fetch(include_history=include_history)

    async def _reconcile_execution_state(self):
        path = getattr(self, "_order_state_path", None)
        if path is None or not path.exists():
            return
        try:
            state = json.loads(path.read_text())
        except (OSError, ValueError):
            return
        if not isinstance(state, dict) or not state:
            self._persist_order_state(None)
            return
        if state.get("status") == "accepted":
            order = state.get("order")
            if not isinstance(order, dict):
                self._clear_execution_state("order_not_found", None)
                return
            order_id = str(order.get("id", "")).strip() or None
            active_ids = {str(row.get("id")) for row in await self._fetch_order_rows(include_history=False) if row.get("id") is not None}
            if order_id and order_id in active_ids:
                self.active_trades = [order]
                self._log_execution_pause()
                return
            found = None
            for attempt in range(3):
                if attempt:
                    await asyncio.sleep(0.25)
                rows = await self._fetch_order_rows(include_history=True, wanted_id=order_id)
                if rows:
                    found = rows[0]
                    break
            if found is not None:
                result = await self.bot.get_order_result(order)
                if result is not None:
                    pnl = float(result.get("pnl", 0.0))
                    self.risk.update_pnl_and_journal(order.get("timestamp"), result["result"], pnl)
                    self.active_trades = []
                    self._persist_order_state(None)
                    log.info("[Settlement] startup_reconciled status=%s order_id=%s", result.get("result"), order_id)
                    return
                self.active_trades = [order]
                return
            self._clear_execution_state("order_not_found", order_id)
            return
        if state.get("status") == "submitting":
            submitted_at = state.get("submitted_at")
            if submitted_at is None:
                self._execution_uncertain = False
                self._persist_order_state(None)
                log.warning("[Execution] state_cleared reason=uncertain_order_missing order_id=unknown")
                return
            try:
                submitted_ts = datetime.fromisoformat(submitted_at).timestamp()
            except ValueError:
                submitted_ts = None
            created_after = False
            for row in await self._fetch_order_rows(include_history=True):
                row_id = row.get("id")
                if row_id is None:
                    continue
                accepted = row.get("accepted_at") or row.get("timestamp") or row.get("created_at") or row.get("open_time")
                if accepted is None:
                    continue
                try:
                    accepted_ts = datetime.fromisoformat(str(accepted)).timestamp()
                except ValueError:
                    continue
                if submitted_ts is not None and accepted_ts >= submitted_ts:
                    created_after = True
                    break
                if submitted_ts is None:
                    created_after = True
                    break
            if created_after:
                self._execution_uncertain = True
                self._uncertain_order_id = state.get("order", {}).get("id") if isinstance(state.get("order"), dict) else None
                self._log_execution_pause()
                return
            self._execution_uncertain = False
            self._uncertain_order_id = None
            if hasattr(self, "supervisor") and hasattr(self.supervisor, "execution_uncertain"):
                try:
                    self.supervisor.execution_uncertain = False
                except Exception:
                    pass
            self._persist_order_state(None)
            log.warning("[Execution] state_cleared reason=uncertain_order_missing order_id=%s",
                        state.get("order", {}).get("id") if isinstance(state.get("order"), dict) else "unknown")
            return

    def _check_circuit_breaker(self) -> bool:
        snapshot = self._sync_daily_accounting()
        blocked = snapshot["halted"] or snapshot["trades_left"] <= 0
        if blocked:
            log.warning("[DailyRisk] status=blocked accepted=%s gross_loss=%s pending_exposure=%s",
                        snapshot["accepted_count"], snapshot["gross_loss"], snapshot["pending_exposure"])
        return blocked

    def _reset_daily_counters(self):
        # RiskManager detects rollover; never discard a pending order or reset the journal.
        snapshot = self._sync_daily_accounting()
        self.supervisor.cb.restore_from_outcomes(snapshot["strategy"]["recent_outcomes"])
        self._log_daily_accounting("rollover")

    def _record_trade_outcome(self, won: bool, pnl: float):
        # Settlement was already committed by RiskManager. Do not count it twice.
        self._log_daily_accounting("settled")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _calculate_consensus_score(self, tf_state: dict,
                                   direction: str) -> float:
        if not tf_state:
            return 0.0
        sup   = sum(1 for d in tf_state.values()
                    if isinstance(d, dict) and d.get("vote") == direction)
        total = sum(1 for d in tf_state.values() if isinstance(d, dict))
        return sup / total if total > 0 else 0.0

    def _combine_confidences(self, a1: float, rl: float,
                              experienced: bool, consensus: float,
                              chart: float) -> float:
        # Chart is now the PRIMARY signal (EMA+Aroon strategy)
        # Agent1 MTF is supporting evidence
        if experienced:
            fused = 0.40 * a1 + 0.20 * rl + 0.40 * chart
        else:
            fused = 0.45 * a1 + 0.55 * chart
        fused *= (consensus * 0.4 + 0.6)   # softer consensus discount
        return float(min(0.97, max(0.45, fused)))

    async def _get_price(self):
        """Return only a current observation of the selected broker instrument."""
        try:
            self._quote = await asyncio.wait_for(
                self.bot.get_current_quote(self._current_asset), timeout=0.8)
        except (asyncio.TimeoutError, ConnectionError):
            self._quote = None
        if self._quote is None:
            return None
        price = self._quote.get("price")
        if not self._quote_is_fresh(self._quote):
            return None
        return float(price)

    @staticmethod
    def _quote_is_fresh(quote):
        try:
            price, observed = float(quote["price"]), float(quote["observed_at"])
            return (not quote.get("stale", False) and math.isfinite(price) and price > 0
                    and math.isfinite(observed)
                    and 0 <= time.time() - observed <= settings.QUOTE_MAX_AGE_SECONDS)
        except (TypeError, ValueError, KeyError):
            return False

    async def setup(self) -> bool:
        if settings.FTT_DURATION != FTT_DURATION_S:
            log.error("[Setup] status=blocked reason=duration_mismatch configured=%s required=%s", settings.FTT_DURATION, FTT_DURATION_S)
            return False
        if settings.POLL_INTERVAL != self.TICK_INTERVAL:
            log.error("[Setup] status=blocked reason=monitor_interval_must_be_one_second")
            return False
        await self.bot.start()
        if not await self.bot.login():
            log.error("[Setup] status=blocked reason=login_failed")
            return False
        self.supervisor._current_asset = self._current_asset
        # Avoid repeated asset switches when the active title already proves identity.
        quote = await self.bot.get_current_quote(self._current_asset)
        if quote is None:
            if not await self.bot.select_asset(self._current_asset):
                log.error("[Setup] status=blocked reason=asset_selection_failed")
                return False
            quote = await self.bot.get_current_quote(self._current_asset)
        if quote is None:
            log.error("[Setup] status=blocked reason=selected_asset_quote_unavailable")
            return False
        if not self.observe_only and not await self.bot.is_demo_account():
            log.error("[Setup] status=blocked reason=demo_account_not_verified")
            return False
        flat_proof = await self.bot.get_flat_ftt_account_evidence()
        if flat_proof is not None:
            self.risk.confirm_flat_ftt_account(flat_proof)
            self._log_daily_accounting("broker_reconciled")
        await self._reconcile_execution_state()
        display = await configure_strategy_chart(self.bot._page, self._entry_chart_view())
        log.info("[ChartDisplay] %s", json.dumps(display, sort_keys=True))
        log.info("[StrategyData] source=broker_native_ws trend_seconds=30 entry_seconds=%s "
                 "display_view=%s trend_structure_required=false", self.entry_seconds, self._entry_chart_view())
        native_periods = set()
        # No Yahoo OTC mapping, fabricated 30s bars, or randomized bootstrap.
        for period in (30, 60):
            history = await self.bot.get_native_candles(self._current_asset, period, 180)
            if history is not None and not history.empty:
                native_periods.add(period)
                source = history.attrs.get("source", "broker_native")
                self.agent1.seed_from_dataframe(history, timeframe=period, source=source, log_seed=False)
                self._seed_sources[period] = source
            else:
                self._seed_sources[period] = "broker_browser_sample"
            log.info("[Seed] %s", json.dumps({
                "asset": self._current_asset, "timeframe_seconds": period,
                "source": self._seed_sources[period],
                "native_history_available": history is not None and not history.empty,
                "received_closed_candles": len(history) if history is not None else 0,
                "retained_closed_candles": len(self.agent1.builders[period]),
                "minimum_history_closed": 53 if period == 30 else 120,
                "required_trend_closed": 53 if period == 30 else 0,
                "required_entry_closed": 21 if period == self.entry_seconds else 0,
                "entry_timeframe_seconds": self.entry_seconds,
                "filter5m_closed": len(self.agent1.builders[settings.TREND_FILTER_SECONDS]),
                "filter5m_required_closed": 24,
                "filter5m_requires_complete_aligned_groups": True,
                "m1_upsampling": False,
            }, sort_keys=True))
        if native_periods != {30, 60}:
            log.info("[DataConstraint] native_history_missing_for=%s; live browser "
                     "samples are observed quotes, not broker-native tick-complete OHLC; "
                     "warmup requires 53 closed 30s bars and 24 closed 5m bars "
                     "aggregated from complete aligned 1m groups; entry_seconds=%s",
                     sorted({30, 60} - native_periods), self.entry_seconds)
        self._last_evaluation_key = (self.agent1.builders[30].latest_closed_ts,
                                     self.agent1.builders[self.entry_seconds].latest_closed_ts)
        self._bg_tasks.append(asyncio.create_task(self._daily_reset_monitor()))
        self._bg_tasks.append(asyncio.create_task(self._settlement_loop()))
        self._bg_tasks.append(asyncio.create_task(self._native_refresh_loop()))
        self._bg_tasks.append(asyncio.create_task(self._market_refresh_loop()))
        self._bg_tasks.append(asyncio.create_task(self._chart_display_loop()))
        if getattr(settings, "ONE_INDICATOR_ENABLED", False) and getattr(self.bot, "live_feed", None) is not None:
            self._start_one_indicator()
        return True

    # ── Background tasks ──────────────────────────────────────────────────────

    async def _periodic_qtable_backup(self, interval_s: int = 3600):
        while True:
            try:
                await asyncio.sleep(interval_s)
                src = Path(self.agent2.qtable_path)
                if src.exists():
                    bdir = Path(settings.MODEL_DIR) / "backups"
                    bdir.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(
                        src,
                        bdir / f"rl_qtable_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning(f"[Backup] {e}")

    async def _daily_replay_and_save(self):
        await asyncio.sleep(30)
        while True:
            try:
                self.agent2.learn_from_logs(self.risk.journal_path)
                self.agent2.q.save(self.agent2.qtable_path)
                log.info("[DailyTrain] RL replayed and Q-table saved")
                await asyncio.sleep(24 * 3600)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning(f"[DailyTrain] {e}")
                await asyncio.sleep(600)

    async def _daily_reset_monitor(self):
        """Reset circuit breaker and daily counters at midnight."""
        while True:
            try:
                now   = datetime.now()
                # Sleep until next midnight
                secs  = (86400 - (now.hour*3600 + now.minute*60 + now.second))
                await asyncio.sleep(secs)
                self._reset_daily_counters()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning(f"[DailyReset] {e}")
                await asyncio.sleep(3600)

    # ── Shared data and decision loop ────────────────────────────────────────

    async def run(self, lock_acquired=False):
        with (nullcontext() if lock_acquired else ProcessLock()):
            self._process_lock_held = True
            try:
                if not await self.setup():
                    return
                self.running = True
                started = time.monotonic()
                log.info("[Run] poll_seconds=%s decisions=every_second signals=provisional_until_confirmed pid=%s",
                         self.TICK_INTERVAL, os.getpid())
                while self.running:
                    tick_start = time.monotonic()
                    if self.max_runtime and tick_start - started >= self.max_runtime:
                        log.info("[Run] status=finished reason=max_runtime closed_events=%s", self.closed_events)
                        break
                    price = await self._get_price()
                    if price is None:
                        live_status = bool(getattr(getattr(self.bot, "live_feed", None), "health", lambda *_: {"live": False})(self._current_asset).get("live"))
                        if live_status != getattr(self, "_live_feed_state", None):
                            log.warning("[Feed] status=%s entries=paused closed_history=preserved", "live" if live_status else "unavailable")
                            self._live_feed_state = live_status
                        self._feed_missing = True
                    else:
                        live_status = bool(getattr(getattr(self.bot, "live_feed", None), "health", lambda *_: {"live": False})(self._current_asset).get("live"))
                        if live_status != getattr(self, "_live_feed_state", None):
                            log.info("[Feed] status=%s closed_history=preserved forming_coverage=rechecked", "live" if live_status else "stale")
                            self._live_feed_state = live_status
                        self._feed_missing = False
                    if price is None:
                        self.agent1.suspend_live_observations("stale_quote", now=time.time())
                    else:
                        self._update_forming_candles(price)
                    key = tuple(self.agent1.builders[p].latest_closed_ts for p in (30, self.entry_seconds))
                    if key != self._last_evaluation_key and key != (None, None):
                        self.closed_events += 1
                    task = asyncio.create_task(self._evaluate_and_trade(price))
                    self._decision_tasks.add(task)
                    task.add_done_callback(self._decision_finished)
                    if self.max_candle_closes and self.closed_events >= self.max_candle_closes:
                        log.info("[Run] status=finished reason=candle_close_target closed_events=%s", self.closed_events)
                        break
                    await asyncio.sleep(max(0.01, self.TICK_INTERVAL - (time.monotonic() - tick_start)))
            finally:
                await self._shutdown()
                self._process_lock_held = False

    def _update_forming_candles(self, price):
        """Observe both active bars without promoting native samples to history."""
        observed = self._quote.get("read_at", self._quote["observed_at"])
        for period in settings.ACTIVE_CANDLE_SECONDS:
            builder = self.agent1.builders[period]
            if self._seed_sources.get(period) == "broker_native_ws":
                native = self.bot.get_native_forming_candle(self._current_asset, period)
                builder.update_forming(price, observed, native=native)
            elif builder.add_tick(price, observed) and period == 60:
                self.agent1.aggregate_five_minute()

    async def _native_refresh_loop(self):
        """Slow broker history responses cannot suspend the one-second monitor.

        While the websocket live_feed is active for the one-indicator flow, the
        monitor should not spam 1s native-history polls. A single 60s reconcile
        is enough to merge confirmed closed bars into the live feed state.
        """
        last_reconcile = 0.0
        while True:
            started = time.monotonic()
            try:
                live_feed = getattr(self.bot, "live_feed", None)
                if getattr(settings, "ONE_INDICATOR_ENABLED", False) and live_feed is not None:
                    health = live_feed.health(self._current_asset) if hasattr(live_feed, "health") else {"live": False}
                    if health.get("live"):
                        if time.monotonic() - last_reconcile >= 60.0:
                            history = await self.bot.get_native_candles(self._current_asset, 60, 180,
                                                                        response_timeout=1.5, purpose="live_feed_reconcile")
                            if history is not None and not history.empty:
                                merged = live_feed.merge_history(self._current_asset, 60, history.to_dict("records"))
                                if merged:
                                    log.debug("[Feed] live_history_reconciled rows=%s", merged)
                            last_reconcile = time.monotonic()
                        await asyncio.sleep(max(0.01, self.TICK_INTERVAL - (time.monotonic() - started)))
                        continue
                await self._update_closed_candles(None, native_only=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("[NativeHistory] status=unavailable reason=%s", exc)
            await asyncio.sleep(max(0.01, self.TICK_INTERVAL - (time.monotonic() - started)))

    def _entry_chart_view(self):
        view = getattr(self, "chart_view", "entry")
        return "entry30s" if view == "entry" and self.entry_seconds == 30 else view

    async def _sync_chart_display(self):
        # Indicator dialogs share the order page. Reserve the close/entry window
        # for orders. Leave room for the 8s update plus bounded cleanup/readback.
        second = time.time() % self.entry_seconds
        if (not 10 <= second <= min(40, self.entry_seconds - 14) or self.active_trades or self._execution_uncertain
                or self._execution_lock.locked()):
            return
        async with self._execution_lock:
            self._chart_sync_active = True
            try:
                result = await reconcile_selected_strategy_chart(
                    self.bot._page, timeout_seconds=8, preferred_view=self._entry_chart_view())
                report = json.dumps(result, sort_keys=True)
                if report != getattr(self, "_last_chart_report", None):
                    log.info("[ChartDisplay] %s", report)
                    self._last_chart_report = report
                return result
            finally:
                self._chart_sync_active = False

    async def _chart_display_loop(self):
        while True:
            try:
                await self._sync_chart_display()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("[ChartDisplay] verified=false reason=%s", exc)
            await asyncio.sleep(3)

    async def _market_refresh_loop(self):
        """Request source updates even when the displayed price stays unchanged."""
        previous = None
        last_live_state = None
        state_change_started = None
        last_summary_ts = 0.0
        while True:
            started = time.monotonic()
            try:
                refreshed = await self.bot.refresh_native_market_data(self._current_asset)
                live_status = None
                live_feed = getattr(self.bot, "live_feed", None)
                if live_feed is not None and hasattr(live_feed, "health"):
                    live_status = live_feed.health(self._current_asset).get("live")
                state_changed = last_live_state is None or last_live_state != live_status
                if refreshed is not None:
                    if last_live_state is None:
                        log.info("[MarketData] %s", json.dumps({
                            "native_forming_fresh_by_timeframe": refreshed,
                            "scope": "native_forming_refresh",
                            "closed_history_managed_separately": True}, sort_keys=True))
                        last_live_state = live_status
                    elif state_changed:
                        if state_change_started is None:
                            state_change_started = time.monotonic()
                        if time.monotonic() - state_change_started >= 2.0:
                            log.info("[MarketData] %s", json.dumps({
                                "native_forming_fresh_by_timeframe": refreshed,
                                "scope": "native_forming_refresh",
                                "closed_history_managed_separately": True}, sort_keys=True))
                            last_live_state = live_status
                            state_change_started = None
                    elif time.monotonic() - last_summary_ts >= 300.0:
                        log.info("[MarketData] %s", json.dumps({
                            "native_forming_fresh_by_timeframe": refreshed,
                            "scope": "native_forming_refresh",
                            "closed_history_managed_separately": True}, sort_keys=True))
                        last_summary_ts = time.monotonic()
                previous = refreshed
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("[MarketData] refresh=unavailable reason=%s", exc)
            await asyncio.sleep(max(0.01, self.TICK_INTERVAL - (time.monotonic() - started)))

    async def _update_closed_candles(self, price, native_only=False):
        """Refresh each native interval after its close, independently of monitoring."""
        closed = set()
        now = time.time()

        async def refresh(period):
            builder = self.agent1.builders[period]
            if self._seed_sources.get(period) == "broker_native_ws":
                expected = math.floor(now / period) * period - period
                previous = builder.latest_closed_ts
                if previous is not None and previous >= expected:
                    return
                history = await self.bot.get_native_candles(self._current_asset, period, 180)
                if history is None or history.empty:
                    log.warning("[NativeHistory] status=unavailable timeframe=%s entries=waiting", period)
                    return
                self.agent1.seed_from_dataframe(history, timeframe=period,
                                                source="broker_native_ws", log_seed=False)
                if builder.latest_closed_ts is not None and builder.latest_closed_ts != previous:
                    closed.add(period)
            elif not native_only and builder.add_tick(price, self._quote["observed_at"]):
                closed.add(period)
                if period == 60:
                    self.agent1.aggregate_five_minute()
        await asyncio.gather(*(refresh(period) for period in (30, 60)))
        return closed

    def _decision_finished(self, task):
        self._decision_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("[Decision] status=error reason=%s", task.exception())
            self._execution_uncertain = True
            self.running = False

    def _five_minute_filter(self, direction="BUY"):
        """Use completed 5m EMA direction rather than the legacy reversal vote."""
        builder = self.agent1.builders.get(settings.TREND_FILTER_SECONDS)
        return evaluate_five_minute_trend(
            builder.closed_snapshot() if builder is not None else [], time.time(), direction)

    def _monitor_context(self, trend, entry, current_price, direction="BUY"):
        now = time.time()
        quote = getattr(self, "_quote", None)
        fresh = current_price is not None and self._quote_is_fresh(quote)
        observed_at = quote.get("observed_at") if quote else None
        if not isinstance(observed_at, (int, float)) or not math.isfinite(observed_at):
            observed_at = None
        quote_age = now - observed_at if observed_at is not None else None
        if quote_age is not None and not math.isfinite(quote_age):
            quote_age = None
        candles, closed_status, forming_bars = {}, {}, {}
        monitored = {30: trend, 60: (entry if self.entry_seconds == 60 else
                                    self.agent1.builders[60].closed_snapshot())}
        for period, closed in monitored.items():
            expected = math.floor(now / period) * period - period
            opening = closed[-1]["ts"] if closed else None
            contiguous = all(right["ts"] - left["ts"] == period
                             for left, right in zip(closed, closed[1:]))
            closed_status[str(period)] = {
                "expected_open_ts": expected, "open_ts": opening,
                "close_at": opening + period if opening is not None else None,
                "fresh": opening == expected and contiguous,
                "contiguous": contiguous, "count": len(closed),
            }
            builder = self.agent1.builders[period]
            forming = builder.forming_snapshot(now) if hasattr(builder, "forming_snapshot") else None
            if forming and quote:
                # The forming snapshot must reflect the newest successful poll, not
                # an older native sample that was already known to be stale.
                observed = quote.get("observed_at")
                if isinstance(observed, (int, float)) and math.isfinite(observed):
                    forming["sampled_at"] = max(float(forming.get("sampled_at", observed)), float(observed))
                    forming["observed_at"] = observed
                forming["stale"] = forming.get("stale", False) or not fresh
            forming_bars[period] = forming
            candles[str(period)] = analyze_forming(closed, forming, period, now)
        clearance = support_clearance = None
        live_trend = candles["30"]
        live_entry = analyze_forming(entry, forming_bars[self.entry_seconds],
                                     self.entry_seconds, now, role="entry")
        if (live_trend["support_resistance"] and live_entry["candle"]
                and not live_entry["candle"]["partial"]):
            distance = live_trend["support_resistance"]["resistance_distance"]
            bar = live_entry["candle"]
            clearance = distance is None or distance > 1.5 * (bar["high"] - bar["low"])
            support_distance = live_trend["support_resistance"]["support_distance"]
            support_clearance = support_distance is None or support_distance > bar["high"] - bar["low"]
        history_fresh = all(item["fresh"] for item in closed_status.values())
        entry_close_at = closed_status[str(self.entry_seconds)]["close_at"]
        age = now - entry_close_at if entry_close_at is not None else None
        return {"evaluated_at": now, "asset": self._current_asset,
                "monitor_interval_seconds": self.DECISION_INTERVAL,
                "active_candle_seconds": list(settings.ACTIVE_CANDLE_SECONDS),
                "trend_timeframe_seconds": 30, "entry_timeframe_seconds": self.entry_seconds,
                "trade_expiry_seconds": FTT_DURATION_S, "expiry_basis": "actual_broker_entry",
                "data_status": "fresh" if fresh and history_fresh else "stale_or_unavailable",
                "quote_status": "fresh" if fresh else "stale_or_unavailable",
                "closed_candle_status": "fresh" if history_fresh else "stale_or_unavailable",
                "closed_candles": closed_status,
                "stale_closed_timeframes": [int(period) for period, status in closed_status.items()
                                            if not status["fresh"]],
                "confirmation_window_seconds": self.CONFIRMATION_WINDOW,
                "entry_timing": {"timeframe_seconds": self.entry_seconds,
                                 "entry_close_at": entry_close_at, "age_seconds": age,
                                 "within_window": age is not None and 0 <= age <= self.CONFIRMATION_WINDOW},
                "live_price": current_price if fresh else None,
                "quote_observed_at": observed_at,
                "quote_age_seconds": quote_age,
                "quote_freshness": quote.get("freshness") if quote else None,
                "quote_freshness_source": quote.get("freshness_source") if quote else None,
                "quote_timestamp_kind": quote.get("timestamp_kind") if quote else None,
                "provisional_trend_resistance_vs_entry_range": clearance,
                "provisional_trend_support_vs_entry_range": support_clearance,
                "entry_candle": live_entry,
                "candles": candles, "trend_filter_5m": self._five_minute_filter(direction)}

    def _publish_decision(self, context, decision, reason):
        if reason in {"stale_or_unavailable_quote", "stale_candle_snapshot", "quote_expired_during_preflight"}:
            stage = "data"
        elif reason in {"awaiting_next_closed_confirmation", "stale_confirmation"}:
            stage = "timing"
        elif reason in {"risk_limit_or_loss_halt", "capacity", "payout_unavailable_or_below_minimum"}:
            stage = "risk"
        elif reason.startswith("5m_") or reason in {"stale_5m_candles", "invalid_5m_candles"}:
            stage = "trend_filter"
        elif reason in {"order_unresolved", "chart_update_in_progress", "checking_demo_order_conditions",
                        "execution_rejected", "execution_uncertain", "order_accepted", "setup_already_attempted"}:
            stage = "execution"
        elif decision in {"BUY", "SELL"}:
            stage = "confirmation"
        else:
            stage = "entry_rules"
        payload = dict(context, decision=decision, reason=reason, blocking_stage=stage,
                       accepted_orders_this_run=self._accepted_count)
        self.latest_decision = payload
        log.info("[Decision] %s", json.dumps(payload, sort_keys=True, allow_nan=False))
        return payload

    async def _evaluate_and_trade(self, current_price: Optional[float]):
        if getattr(self, "_one_indicator_active", False):
            return  # the one-indicator loop owns entries; legacy gates stay available but idle
        trend = self.agent1.builders[30].closed_snapshot()
        entry = trend if self.entry_seconds == 30 else self.agent1.builders[60].closed_snapshot()
        key = (trend[-1]["ts"] if trend else None,
               entry[-1]["ts"] if entry else None)
        changed = key != self._last_evaluation_key and key != (None, None)
        self._last_evaluation_key = key
        evaluator = select_entry_evaluator()
        param_count = len(inspect.signature(evaluator).parameters)
        if param_count >= 4:
            results = evaluator(trend, entry, self.entry_seconds,
                               getattr(settings, "DEMO_EMA_TOUCH_TOLERANCE_MULTIPLIER", 0.15))
        else:
            results = evaluator(trend, entry, self.entry_seconds)
        if getattr(settings, "DEMO_STRATEGY_ENABLED", False):
            legacy = evaluate_both(trend, entry, self.entry_seconds)
            for side in ("BUY", "SELL"):
                results[side]["legacy_gate_diagnostics"] = legacy[side]
        confirmed = [side for side in ("BUY", "SELL") if results[side]["passed"]]
        direction = confirmed[0] if len(confirmed) == 1 else None
        # A trend-side diagnostic explains WAIT without authorizing an entry.
        ema20, ema50 = results["BUY"].get("ema20"), results["BUY"].get("ema50")
        diagnostic_direction = direction or (
            "SELL" if ema20 is not None and ema50 is not None and ema20 < ema50 else "BUY")
        gate = results[diagnostic_direction]

        def decision_context(price):
            context = self._monitor_context(trend, entry, price, diagnostic_direction)
            context.update(buy_evaluation=results["BUY"], sell_evaluation=results["SELL"],
                           entry_evaluation=gate, direction=direction,
                           diagnostic_direction=diagnostic_direction)
            return context

        context = decision_context(current_price)
        for gate_direction, evaluated_gate in (results.items() if changed else []):
            payload = dict(evaluated_gate, asset=self._current_asset,
                           trend_closed_ts=key[0], entry_closed_ts=key[1],
                           source=self._seed_sources.get(30, "broker_browser_sample"),
                           entry_source=self._seed_sources.get(self.entry_seconds, "broker_browser_sample"),
                           closed_only=True, candle_event=self.closed_events,
                           execution_enabled=True,
                           trend_structure_required=False,
                           live_price=current_price)
            log.info("[%sOnlyGate] %s", gate_direction.title(),
                     json.dumps(payload, sort_keys=True, allow_nan=False))
        # Logging a snapshot does not consume an entry. Revisit delayed history
        # within the confirmation window; only an attempted setup is consumed.
        if context["quote_status"] != "fresh":
            return self._publish_decision(context, "NO TRADE", "stale_or_unavailable_quote")
        if context["closed_candle_status"] != "fresh":
            return self._publish_decision(context, "NO TRADE", "stale_candle_snapshot")
        chart_busy = getattr(self, "_chart_sync_active", False)
        if (self.active_trades or self._execution_uncertain
                or (self._execution_lock.locked() and not chart_busy)):
            self._log_execution_pause()
            return self._publish_decision(context, "WAIT", "order_unresolved")
        if len(confirmed) > 1:
            return self._publish_decision(context, "NO TRADE", "conflicting_directional_setups")
        if direction is None or not entry:
            return self._publish_decision(context, "WAIT", gate.get("reason", "awaiting_closed_confirmation"))
        now = time.time()
        if any(not candles or candles[-1]["ts"] != math.floor(now / period) * period - period
               for candles, period in ((trend, 30), (entry, self.entry_seconds))):
            return self._publish_decision(context, "NO TRADE", "stale_candle_snapshot")
        if not context["trend_filter_5m"]["passed"]:
            return self._publish_decision(context, "NO TRADE", context["trend_filter_5m"]["reason"])
        if self._setup_already_attempted(key[1]):
            return self._publish_decision(context, "WAIT", "setup_already_attempted")
        if not 0 <= time.time() - (key[1] + self.entry_seconds) <= self.CONFIRMATION_WINDOW:
            log.info("[Execution] status=blocked reason=stale_confirmation entry_ts=%s", key[1])
            return self._publish_decision(context, "WAIT", "awaiting_next_closed_confirmation")
        if chart_busy:
            return self._publish_decision(context, "WAIT", "chart_update_in_progress")
        if self.observe_only:
            log.info("[Execution] status=observe_only direction=%s entry_ts=%s", direction, key[1])
            return self._publish_decision(context, direction, f"confirmed_{direction.lower()}_observe_only")
        if not self._process_lock_held:
            raise RuntimeError("Execution requires the shared process lock")
        async with self._execution_lock:
            if self._setup_already_attempted(key[1]):
                return self._publish_decision(context, "WAIT", "setup_already_attempted")
            if self.active_trades or self._execution_uncertain:
                self._log_execution_pause()
                log.info("[Execution] status=blocked reason=order_unresolved")
                return self._publish_decision(context, "WAIT", "order_unresolved")
            now = time.time()
            if any(candles[-1]["ts"] != math.floor(now / period) * period - period
                   for candles, period in ((trend, 30), (entry, self.entry_seconds))):
                log.info("[Execution] status=blocked reason=stale_candle_snapshot")
                return self._publish_decision(context, "NO TRADE", "stale_candle_snapshot")
            if self._check_circuit_breaker() or self._check_consecutive_loss_halt()[0]:
                return self._publish_decision(context, "NO TRADE", "risk_limit_or_loss_halt")
            remaining, budget, _ = self._trade_capacity()
            if remaining <= 0 or budget <= 0:
                log.info("[Execution] status=blocked reason=capacity")
                return self._publish_decision(context, "NO TRADE", "capacity")
            self._publish_decision(context, "WAIT", "checking_demo_order_conditions")
            payout = await self.bot.get_asset_payout(self._current_asset)
            context = decision_context((self._quote or {}).get("price"))
            if payout is None or not self.payout_minimum <= payout <= 1.0:
                log.info("[Execution] status=blocked reason=payout payout=%s minimum=%s", payout, self.payout_minimum)
                return self._publish_decision(context, "NO TRADE", "payout_unavailable_or_below_minimum")
            if not 0 <= time.time() - (key[1] + self.entry_seconds) <= self.CONFIRMATION_WINDOW:
                log.info("[Execution] status=blocked reason=stale_confirmation entry_ts=%s", key[1])
                return self._publish_decision(context, "WAIT", "awaiting_next_closed_confirmation")
            if not self._quote_is_fresh(getattr(self, "_quote", None)):
                return self._publish_decision(context, "NO TRADE", "quote_expired_during_preflight")
            if context["closed_candle_status"] != "fresh":
                return self._publish_decision(context, "NO TRADE", "stale_candle_snapshot")
            if not context["trend_filter_5m"]["passed"]:
                return self._publish_decision(context, "NO TRADE", context["trend_filter_5m"]["reason"])
            self._last_attempted_entry = key[1]
            self._last_attempted_entry_seconds = self.entry_seconds
            self._persist_attempted_entry(key[1])
            signal = Signal.BUY if direction == "BUY" else Signal.SELL
            self._publish_decision(context, direction, f"closed_{direction.lower()}_and_5m_filter_confirmed")
            # Preserve the existing gate score; this is not a calibrated win probability.
            self._persist_order_state({"status": "submitting", "asset": self._current_asset,
                                       "direction": direction, "entry_ts": key[1],
                                       "entry_timeframe_seconds": self.entry_seconds,
                                       "amount": self.trade_amount, "duration": FTT_DURATION_S,
                                       "submitted_at": datetime.now().isoformat(timespec="microseconds")})
            submission_evaluated_at = context["evaluated_at"]
            try:
                success = await self.supervisor.safe_execute_trade(
                    signal, 0.95, "ftt",
                    entry_deadline=key[1] + self.entry_seconds + self.CONFIRMATION_WINDOW)
            except asyncio.CancelledError:
                # Keep the durable submitting record and block later setups even
                # when this decision task is canceled while the process continues.
                self._execution_uncertain = True
                self.supervisor.execution_uncertain = True
                self.supervisor.last_execution_reason = "execution_uncertain: CancelledError"
                log.error("[Execution] status=uncertain retry=false reason=CancelledError direction=%s entry_ts=%s",
                          direction, key[1])
                context = decision_context((self._quote or {}).get("price"))
                context.update(submission_evaluated_at=submission_evaluated_at,
                               execution_detail=self.supervisor.last_execution_reason)
                self._publish_decision(context, "NO TRADE", "execution_uncertain")
                raise
            self._execution_uncertain = getattr(self.supervisor, "execution_uncertain", False)
            context = decision_context((self._quote or {}).get("price"))
            context.update(submission_evaluated_at=submission_evaluated_at)
            if not success:
                if not self._execution_uncertain:
                    self._persist_order_state(None)
                log.warning("[Execution] status=%s direction=%s entry_ts=%s",
                            "uncertain" if self._execution_uncertain else "rejected", direction, key[1])
                context["execution_detail"] = getattr(self.supervisor, "last_execution_reason", None)
                return self._publish_decision(context, "NO TRADE",
                                              "execution_uncertain" if self._execution_uncertain else "execution_rejected")
            order = dict(self.bot.last_order or {})
            timestamp = datetime.now().isoformat(timespec="microseconds")
            order.update(timestamp=timestamp, signal=direction, asset=self._current_asset,
                         state_key="CLOSED_CANDLE_GATE", entry_ts=key[1],
                         entry_timeframe_seconds=self.entry_seconds)
            self._persist_order_state({"status": "accepted", "order": order})
            self.risk.record_trade(TradeRecord(
                timestamp=timestamp, asset=self._current_asset, mode="ftt",
                signal=direction, confidence=0.95, amount=order.get("amount", self.trade_amount),
                state_key=order["state_key"]))
            self.active_trades.append(order)
            self._accepted_count += 1
            log.info("[Execution] %s", json.dumps(dict(order, status="accepted", direction=direction), default=str))
            # Do not manufacture outcomes from a later quote. Until the platform
            # settlement adapter confirms this order, further entries stay blocked.
            log.info("[Settlement] status=pending source=platform required_order_id=%s", order.get("id"))
            context["order_id"] = order.get("id")
            return self._publish_decision(context, direction, "order_accepted")


    # ── One-indicator strategy (additive) ─────────────────────────────────────

    def _start_one_indicator(self):
        from bot.campaign import CampaignTracker
        from bot.governor_plugin import install as install_governor
        log_build_fingerprint()
        governor = install_governor(self)
        if not getattr(self, "_setup_logged", False):
            log.info("[Setup] active_strategy=%s indicator=EMA9 timeframe=%ss entry=open_of_new_candle "
                     "window=%ss price_source=broker_ws_ticks governor=%s",
                     settings.ACTIVE_STRATEGY, self.entry_seconds,
                     settings.ONE_INDICATOR_ENTRY_WINDOW_SECONDS,
                     getattr(settings, "GOVERNOR_ENABLED", True))
            self._setup_logged = True
        self._one_indicator_active = True
        self._rollover_queue = asyncio.Queue(maxsize=4)
        self._payout_cache = None
        self.bot.live_feed.add_listener(self._on_candle_rollover)
        campaign = getattr(self, "_campaign_tracker", None)
        if campaign is None:
            archive_dir = getattr(settings, "LOG_ARCHIVE_DIR", os.path.join(settings.LOG_DIR, "archive"))
            extra_dirs = []
            extra_env = getattr(settings, "CAMPAIGN_EXTRA_JOURNAL_DIRS", "")
            if extra_env:
                extra_dirs = [p.strip() for p in extra_env.split(os.pathsep) if p.strip()]
            campaign = CampaignTracker(
                os.path.join(settings.LOG_DIR, "trade_journal.csv"),
                archive_dir,
                state_key="ONE_INDICATOR_EMA",
                target=int(getattr(settings, "CAMPAIGN_TARGET_TRADES", 1000)),
                since=getattr(settings, "CAMPAIGN_SINCE", ""),
                payout=float(getattr(settings, "CAMPAIGN_PAYOUT", 0.85)),
                extra_dirs=extra_dirs,
            )
            self._campaign_tracker = campaign
            campaign.stats()
        if getattr(settings, "TICK_TAPE_ENABLED", True):
            self.tick_tape = __import__("bot.tick_tape", fromlist=["TickTape"]).TickTape(
                self.bot.live_feed,
                lambda: self._current_asset,
                period=self.entry_seconds,
                ema_period=settings.ONE_INDICATOR_EMA_PERIOD,
                log_dir=getattr(settings, "LOG_DIR", "logs"),
                console=getattr(settings, "TICK_CONSOLE", False),
            )
            self.tick_tape.install()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            self._bg_tasks.append(loop.create_task(self._one_indicator_loop()))
            self._bg_tasks.append(loop.create_task(self._payout_cache_loop()))
            self._bg_tasks.append(loop.create_task(self._ftt_prearm_loop()))

    def _on_candle_rollover(self, code, period, closed, opening):
        """Called synchronously from the websocket frame handler at candle open."""
        if period != self.entry_seconds or code != _norm_asset(self._current_asset):
            return
        if self._rollover_queue.full():
            self._rollover_queue.get_nowait()
        self._rollover_queue.put_nowait((closed, opening))

    async def _payout_cache_loop(self):
        """Keep payout off the critical path: the entry reads a recent value."""
        while True:
            try:
                payout = await self.bot.get_asset_payout(self._current_asset)
                if payout is not None:
                    self._payout_cache = (payout, time.time())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("[Payout] cache_refresh_failed reason=%s", exc)
            await asyncio.sleep(10)

    async def _ftt_prearm_loop(self):
        """Pre-arm the FTT form 4-5s before the current candle closes when idle."""
        last_bucket = None
        while True:
            try:
                if self.observe_only or self.active_trades or self._execution_uncertain:
                    await asyncio.sleep(0.5)
                    continue
                if not self._current_asset:
                    await asyncio.sleep(0.5)
                    continue
                now = time.time()
                bucket = math.floor(now / self.entry_seconds) * self.entry_seconds
                seconds_left = bucket + self.entry_seconds - now
                if 4.0 <= seconds_left <= 6.0 and bucket != last_bucket:
                    await self.bot.prearm_ftt(self._current_asset, amount=self.trade_amount,
                                             duration_seconds=FTT_DURATION_S)
                    last_bucket = bucket
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug("[PreArm] skipped reason=%s", exc)
            await asyncio.sleep(0.5)

    async def _one_indicator_loop(self):
        while True:
            closed, opening = await self._rollover_queue.get()
            try:
                await self._one_indicator_trade(closed, opening)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("[OneIndicator] status=error reason=%s", exc)

    def _append_trade_meta(self, record):
        if record is None:
            return None
        log_path = getattr(self, "_trade_meta_path", None) or getattr(self, "_timing_log_file", None)
        if log_path is None:
            log_path = Path(getattr(settings, "LOG_DIR", "logs")) / "trade_meta.jsonl"
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        self._trade_meta_path = path
        self._timing_log_file = path
        return path

    async def _one_indicator_trade(self, closed, opening):
        period, asset, feed = self.entry_seconds, self._current_asset, self.bot.live_feed
        open_ts = closed["ts"] + period
        t0 = opening["recv"]
        window = float(settings.ONE_INDICATOR_ENTRY_WINDOW_SECONDS)

        def note(decision, reason, **extra):
            governor = getattr(self, "_governor_instance", None)
            payload = dict(extra, decision=decision, reason=reason, asset=asset,
                           candle_open_ts=open_ts, open_price=opening["price"],
                           processing_delay_ms=round((time.time() - t0) * 1000, 1),
                           accepted_orders_this_run=self._accepted_count)
            if payload.get("signal") is None:
                payload["signal"] = extra.get("signal")
            if payload.get("pattern") is None:
                payload["pattern"] = extra.get("pattern")
            if payload.get("strength_bp") is None:
                payload["strength_bp"] = extra.get("strength_bp")
            if payload.get("ema_side") is None:
                ema = payload.get("ema")
                ema_prev = payload.get("ema_prev")
                close = payload.get("closed_candle", {}).get("close") if isinstance(payload.get("closed_candle"), dict) else None
                if ema is None or ema_prev is None or close is None or close == 0:
                    payload["ema_side"] = None
                else:
                    ema = float(ema)
                    ema_prev = float(ema_prev)
                    close = float(close)
                    if close > ema and ema > ema_prev:
                        payload["ema_side"] = "BUY"
                    elif close < ema and ema < ema_prev:
                        payload["ema_side"] = "SELL"
                    else:
                        payload["ema_side"] = None
            if payload.get("ema_strength_bp") is None:
                ema = payload.get("ema")
                ema_prev = payload.get("ema_prev")
                close = payload.get("closed_candle", {}).get("close") if isinstance(payload.get("closed_candle"), dict) else None
                if ema is None or ema_prev is None or close in (None, 0):
                    payload["ema_strength_bp"] = None
                else:
                    payload["ema_strength_bp"] = round(abs(float(ema) - float(ema_prev)) / float(close) * 10000, 4)
            if payload.get("blocked_by") is None:
                payload["blocked_by"] = extra.get("blocked_by") or "none"
            if payload.get("governor_threshold") is None:
                payload["governor_threshold"] = extra.get("governor_threshold")
            if governor is not None:
                payload["history_size"] = len(getattr(governor, "strengths", []))
                payload["window"] = getattr(governor, "window", None)
            timing = {
                "entry_seconds": period,
                "open_ts": open_ts,
                "opening_recv": t0,
                "time_to_decide_ms": round((time.time() - t0) * 1000, 1),
                "boundary_lag_ms": round((t0 - open_ts) * 1000, 1),
                "window_seconds": window,
            }
            payload["timing"] = timing
            log.info("[Timing] %s", json.dumps({"decision": decision, "reason": reason, "timing": timing}, sort_keys=True, default=str))
            log.info("[OneIndicator] %s", json.dumps(payload, sort_keys=True, default=str))
            decisions_path = Path(settings.LOG_DIR) / "decisions.jsonl"
            decisions_path.parent.mkdir(parents=True, exist_ok=True)
            with decisions_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
            self.latest_decision = payload
            self._append_trade_meta({k: v for k, v in payload.items() if k not in {"timing", "closed_candle", "closed_momentum", "momentum"}})
            return payload

        candles = feed.closed(asset, period)
        latest_closed = candles[0] if candles else None
        if not candles or latest_closed["ts"] != closed["ts"]:
            return note("NO TRADE", "closed_candle_not_in_history")
        canonical_closed = latest_closed if latest_closed is not None else closed
        if hasattr(self.supervisor, "cb"):
            breaker_stop, breaker_reason = self.supervisor.cb.should_stop()
            if breaker_stop:
                try:
                    seconds_left = int(breaker_reason.split("—")[-1].split("s")[0].strip())
                except Exception:
                    seconds_left = 0
                return note("WAIT", "circuit_breaker_cooldown", signal=None,
                            seconds_left=seconds_left, breaker_reason=breaker_reason,
                            closed_candle={k: canonical_closed.get(k) for k in ("open", "high", "low", "close", "ticks", "up_ticks", "down_ticks")},
                            closed_momentum=candle_momentum(canonical_closed), momentum=None)
        tick = feed.last_tick(asset, max_age=settings.ONE_INDICATOR_STALE_MAX_AGE_SECONDS)
        forming = getattr(feed, "forming", None)
        forming_state = forming(asset, period) if callable(forming) else None
        trend_history = feed.closed(asset, 300)
        recent_outcomes = None
        risk = getattr(self, "risk", None)
        if risk is not None and hasattr(risk, "_snapshot") and isinstance(risk._snapshot, dict):
            recent_outcomes = risk._snapshot.get("strategy", {}).get("recent_outcomes")
        result = evaluate_one_indicator(
            list(reversed(candles)), period,
            settings.ONE_INDICATOR_EMA_PERIOD,
            settings.ONE_INDICATOR_SLOPE_BARS,
            trend_candles=trend_history,
            recent_outcomes=recent_outcomes,
            feed_stale=tick is None,
            forming_fresh=forming_state is not None,
            tick_bias=(candle_momentum(canonical_closed) or {}).get("tick_bias"),
        )
        signal = result.get("signal") or result.get("direction")
        pattern = result.get("pattern")
        strength_bp = result.get("strength_bp")
        blocked_by = result.get("blocked_by") or ("governor" if result.get("reason", "").startswith("governor_") else "pattern" if result.get("reason") == "pattern_no_confirmation" else "none")
        governor_threshold = result.get("governor_threshold")
        info = {k: result.get(k) for k in ("indicator", "ema", "ema_prev", "close", "candles")}
        info.update({"signal": signal, "pattern": pattern, "strength_bp": strength_bp, "blocked_by": blocked_by, "governor_threshold": governor_threshold})
        info["closed_candle"] = {k: canonical_closed.get(k) for k in ("open", "high", "low", "close", "ticks", "up_ticks", "down_ticks")}
        info["closed_momentum"] = candle_momentum(canonical_closed)
        forming = getattr(feed, "forming", None)
        if callable(forming):
            forming = forming(asset, period)
        else:
            forming = None
        info["momentum"] = None
        if forming and forming.get("ticks", 0) >= 3:
            momentum_fn = getattr(feed, "momentum", None)
            if callable(momentum_fn):
                info["momentum"] = momentum_fn(asset, period)
        direction = result["direction"]
        self._record_filter_stats(result)
        if direction is None:
            if result.get("diagnostics"):
                diag = result["diagnostics"][-1]
                d = {"filter": diag.get("filter"), "slope": result.get("slope"), "atr14": result.get("atr14"),
                     "slope_to_atr_ratio": result.get("slope_to_atr_ratio"), "threshold": result.get("slope_threshold"),
                     "reason": result.get("reason")}
                log.info("[DecisionDiag] %s", json.dumps(d, sort_keys=True, default=str))
            return note("WAIT", result["reason"], **info)
        if time.time() - t0 > window:
            return note("NO TRADE", "late_open_window_missed", signal=direction, **info)
        if feed.last_tick(asset, max_age=settings.QUOTE_MAX_AGE_SECONDS) is None:
            return note("NO TRADE", "stale_or_unavailable_quote", signal=direction, **info)
        if self.active_trades or self._execution_uncertain or self._execution_lock.locked():
            return note("WAIT", "order_unresolved", signal=direction, **info)
        breaker_stop, breaker_reason = self.supervisor.cb.should_stop() if hasattr(self.supervisor, "cb") else (False, "")
        if breaker_stop:
            try:
                seconds_left = int(breaker_reason.split("—")[-1].split("s")[0].strip())
            except Exception:
                seconds_left = 0
            return note("WAIT", "circuit_breaker_cooldown", signal=direction,
                        seconds_left=seconds_left, breaker_reason=breaker_reason, **info)
        if self._setup_already_attempted(open_ts):
            return note("WAIT", "setup_already_attempted", signal=direction, **info)
        if self.observe_only:
            return note(direction, f"{direction.lower()}_observe_only", **info)
        if not self._process_lock_held:
            raise RuntimeError("Execution requires the shared process lock")
        async with self._execution_lock:
            if self._setup_already_attempted(open_ts) or self.active_trades or self._execution_uncertain:
                return note("WAIT", "order_unresolved", signal=direction, **info)
            if self._check_consecutive_loss_halt()[0]:
                return note("NO TRADE", "loss_halt_active", signal=direction, **info)
            if self._check_circuit_breaker():
                return note("NO TRADE", "daily_loss_limit_reached", signal=direction, **info)
            breaker_stop, breaker_reason = self.supervisor.cb.should_stop() if hasattr(self.supervisor, "cb") else (False, "")
            if breaker_stop:
                try:
                    seconds_left = int(breaker_reason.split("—")[-1].split("s")[0].strip())
                except Exception:
                    seconds_left = 0
                return note("WAIT", "circuit_breaker_cooldown", signal=direction,
                            seconds_left=seconds_left, breaker_reason=breaker_reason, **info)
            approved, risk_reason = self.risk.approve(
                Signal.BUY if direction == "BUY" else Signal.SELL,
                0.95, "ftt", trade_amount=self.trade_amount, allow_during_training=False)
            if not approved:
                return note("WAIT", "risk_rejected", signal=direction,
                            risk_reason=risk_reason, **info)
            remaining, budget, _ = self._trade_capacity()
            if remaining <= 0 or budget <= 0:
                return note("NO TRADE", "capacity", signal=direction, **info)
            cached = self._payout_cache
            payout = cached[0] if cached and time.time() - cached[1] <= 45 else \
                await self.bot.get_asset_payout(asset)
            if payout is None or not self.payout_minimum <= payout <= 1.0:
                return note("NO TRADE", "payout_unavailable_or_below_minimum",
                            signal=direction, payout=payout, **info)
            if time.time() - t0 > window:
                return note("NO TRADE", "late_open_window_missed", signal=direction, **info)
            self._last_attempted_entry = open_ts
            self._last_attempted_entry_seconds = self.entry_seconds
            self._persist_attempted_entry(open_ts)
            signal = Signal.BUY if direction == "BUY" else Signal.SELL
            note(direction, "sending_order", payout=payout, **info)
            self._persist_order_state({"status": "submitting", "asset": asset,
                                       "direction": direction, "entry_ts": open_ts,
                                       "entry_timeframe_seconds": self.entry_seconds,
                                       "amount": self.trade_amount, "duration": FTT_DURATION_S,
                                       "submitted_at": datetime.now().isoformat(timespec="microseconds")})
            try:
                success = await self.supervisor.safe_execute_trade(
                    signal, 0.95, "ftt", entry_deadline=t0 + window)
            except asyncio.CancelledError:
                self._execution_uncertain = True
                self.supervisor.execution_uncertain = True
                self.supervisor.last_execution_reason = "execution_uncertain: CancelledError"
                log.error("[Execution] status=uncertain retry=false reason=CancelledError direction=%s", direction)
                raise
            self._execution_uncertain = getattr(self.supervisor, "execution_uncertain", False)
            if not success:
                if not self._execution_uncertain:
                    self._persist_order_state(None)
                return note("NO TRADE", "execution_uncertain" if self._execution_uncertain
                            else "execution_rejected", signal=direction,
                            detail=getattr(self.supervisor, "last_execution_reason", None), **info)
            order = dict(self.bot.last_order or {})
            timestamp = datetime.now().isoformat(timespec="microseconds")
            order.update(timestamp=timestamp, signal=direction, asset=asset,
                         state_key="ONE_INDICATOR_EMA", entry_ts=open_ts,
                         entry_timeframe_seconds=self.entry_seconds)
            self._persist_order_state({"status": "accepted", "order": order})
            self.risk.record_trade(TradeRecord(
                timestamp=timestamp, asset=asset, mode="ftt", signal=direction, confidence=0.95,
                amount=order.get("amount", self.trade_amount), state_key=order["state_key"]))
            self.active_trades.append(order)
            self._accepted_count += 1
            log.info("[Execution] %s", json.dumps(dict(order, status="accepted", direction=direction), default=str))
            log.info("[Settlement] status=pending source=platform required_order_id=%s", order.get("id"))
            return note(direction, "order_accepted", order_id=order.get("id"),
                        fill_delay_ms=round((order.get("accepted_at", time.time()) - t0) * 1000, 1), **info)

    def _setup_already_attempted(self, entry_ts):
        return (getattr(self, "_attempted_entries", {}).get(self.entry_seconds) == entry_ts or
                (self._last_attempted_entry == entry_ts and
                 getattr(self, "_last_attempted_entry_seconds", 60) == self.entry_seconds))

    def _persist_attempted_entry(self, entry_ts):
        """Keep rejected or settled setups consumed across process restarts too."""
        if not hasattr(self, "_attempted_entries"):
            self._attempted_entries = {}
        self._attempted_entries[self.entry_seconds] = entry_ts
        path = getattr(self, "_attempted_setup_path", None)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with temp.open("w") as handle:
            json.dump({"asset": self._current_asset, "entry_ts": entry_ts,
                       "entry_timeframe_seconds": self.entry_seconds,
                       "entries_by_timeframe": self._attempted_entries}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)

    def _persist_order_state(self, state):
        """Persist before clicking so restart never silently resubmits an order."""
        path = getattr(self, "_order_state_path", None)
        if path is None:  # Dependency-injected unit tests have no execution store.
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with temp.open("w") as handle:
            json.dump(state, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)

    async def _settlement_loop(self):
        while True:
            try:
                for order in list(self.active_trades):
                    result = await self.bot.get_order_result(order)
                    if result is None:
                        continue
                    if result.get("result") not in {"WIN", "LOSS", "DRAW"}:
                        continue
                    pnl = float(result["pnl"])
                    if not math.isfinite(pnl):
                        continue
                    committed = self.risk.update_pnl_and_journal(order["timestamp"], result["result"], pnl)
                    if committed is False:
                        self.active_trades.remove(order)
                        self._persist_order_state(None)
                        log.info("[Settlement] status=already_recorded order_id=%s", order.get("id"))
                        continue
                    won = result["result"] == "WIN"
                    self._record_trade_outcome(won, pnl)
                    if (result["result"] != "DRAW"
                            and order.get("state_key") != "DEMO_EXECUTION_TEST"
                            and order["timestamp"].startswith(self._account_snapshot["date"])):
                        self.supervisor.record_outcome(won)
                    self.active_trades.remove(order)
                    self._persist_order_state(None)
                    log.info("[Settlement] %s", json.dumps(dict(result, order_id=order.get("id"))))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("[Settlement] status=unconfirmed reason=%s", exc)
            await asyncio.sleep(5)

    # ── Shutdown ───────────────────────────────────────────────────────────────

    async def _shutdown(self):
        self.running = False
        if self._decision_tasks:
            await asyncio.gather(*list(self._decision_tasks), return_exceptions=True)
        for t in list(self._bg_tasks):
            t.cancel()
        await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        await self.bot.stop()
        snapshot = self._sync_daily_accounting()
        strategy = snapshot["strategy"]
        log.info("[Session] accepted_this_run=%s account_accepted_today=%s "
                 "account_settled_today=%s account_net_pnl=%s strategy_settled_today=%s "
                 "strategy_wins_today=%s",
                 self._accepted_count, snapshot["accepted_count"], snapshot["settled_count"],
                 snapshot["net_pnl"], strategy["settled_count"], strategy["wins"])



if __name__ == "__main__":
    configure_logging()
    parser = argparse.ArgumentParser(description="BUY/SELL demo bot with one-second monitoring and confirmed entries")
    parser.add_argument("--chart-view", choices=("entry", "trend", "filter5m"), default="entry",
                        help="Initial chart: selected entry period EMA9/21, 30s trend EMA20/50, or 5m EMA9/21")
    parser.add_argument("--entry-seconds", type=int, choices=(30, 60), default=settings.STRATEGY_ENTRY_SECONDS,
                        help="Completed entry candle period (default: 30; 60 keeps legacy minute entries)")
    parser.add_argument("--observe-only", action="store_true", help="Collect actual quotes and evaluate without clicks")
    parser.add_argument("--max-candle-closes", type=int, default=0, help="Stop after this many real close events; 0 runs continuously")
    parser.add_argument("--max-runtime", type=float, default=0, help="Bound runtime after login in seconds")
    parser.add_argument('--force-trades', action='store_true',
                        help='Force trades by bypassing the confluence gate (testing only)')
    parser.add_argument('--training-mode', action='store_true',
                        help='Enable training mode so the risk engine uses training overrides')
    parser.add_argument('--disable-circuit-breaker', action='store_true',
                        help='Ignore local circuit-breaker limits for testing')
    parser.add_argument('--min-confidence', type=float, default=MIN_CONFIDENCE,
                        help='Minimum final confidence required to execute a trade')
    parser.add_argument('--trade-amount', type=float, default=TRADE_AMOUNT,
                        help='Trade amount to use for this run')
    parser.add_argument('--max-daily-trades', type=int, default=None,
                        help='Override maximum daily trades')
    parser.add_argument('--max-daily-loss', type=float, default=None,
                        help='Override the daily loss limit for the local circuit breaker')
    parser.add_argument('--confidence-threshold', type=float, default=None,
                        help='Override the risk confidence threshold for the risk manager')
    parser.add_argument('--payout-minimum', type=float, default=PAYOUT_MINIMUM,
                        help='Minimum payout percentage required to place a trade')

    args = parser.parse_args()
    if args.force_trades:
        args.training_mode = True
        args.disable_circuit_breaker = True
    if args.training_mode:
        settings.TRAINING_MODE = True
    effective_trades, effective_loss, trade_source, loss_source = resolve_daily_limits(args.max_daily_trades, args.max_daily_loss)
    settings.MAX_DAILY_TRADES = effective_trades
    settings.MAX_DAILY_LOSS_USD = effective_loss
    settings.MAX_DAILY_LOSS = effective_loss
    governor_max_per_day = int(os.getenv("GOVERNOR_MAX_PER_DAY", "110"))
    if effective_trades < governor_max_per_day:
        log.warning("[Startup] max_trades_per_day=%s < GOVERNOR_MAX_PER_DAY=%s; governor will block additional entries earlier than the daily cap.", effective_trades, governor_max_per_day)
    if args.confidence_threshold is not None:
        settings.CONFIDENCE_THRESHOLD = args.confidence_threshold
    if args.trade_amount is not None:
        settings.TRADE_AMOUNT = args.trade_amount
    log.info("[Startup] effective_daily_limits max_daily_trades=%s source=%s max_daily_loss=%s source=%s",
             effective_trades, trade_source, effective_loss, loss_source)

    try:
        with ProcessLock():
            asyncio.run(
                MultiAgentTradingSystem(
                    observe_only=args.observe_only,
                    chart_view=args.chart_view,
                    entry_seconds=args.entry_seconds,
                    max_candle_closes=args.max_candle_closes,
                    max_runtime=args.max_runtime,
                    force_trades=args.force_trades,
                    training_mode=args.training_mode,
                    disable_circuit_breaker=args.disable_circuit_breaker,
                    min_confidence=args.min_confidence,
                    trade_amount=args.trade_amount,
                    payout_minimum=args.payout_minimum,
                    max_trades_per_day=effective_trades,
                    daily_loss_limit=effective_loss,
                ).run(lock_acquired=True)
            )
    except KeyboardInterrupt:
        pass
