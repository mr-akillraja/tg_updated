"""
agents/agent_pipeline_supervisor.py
════════════════════════════════════════════════════════════════════
Agent 3 — Pipeline Supervisor & Automation Guard

Responsibilities:
  • Pre-flight health checks on browser, data feed and model.
  • Serializes one trade attempt with a timeout and acknowledgement guard.
  • Checks the selected asset and payout before execution.
  • Enforces circuit-breaker: stops trading if win-rate drops
    below the target threshold.
  • Recovers browser session on crash without restarting the loop.
════════════════════════════════════════════════════════════════════
"""

import asyncio
import logging
import math
from collections import deque
from datetime import datetime
import os
import time
from typing import Optional, Tuple

try:
    from notifications.telegram import send_telegram
except Exception:
    def send_telegram(msg):
        return False

log = logging.getLogger("agent3_supervisor")

# Assets tried in order when the current one is unavailable
from agents.agent_multi_timeframe import PREFERRED_ASSETS_ORDERED
from agents.agent_asset_selector import pretty_asset


class CircuitBreaker:
    """Pause on a low win rate over the last 20 decisive strategy outcomes."""

    def __init__(self, target_win_rate: float = 0.90,
                 min_trades: int = 5,
                 cooldown_s: float = 300.0):
        self.target = target_win_rate
        self.min_trades = min_trades
        self.cooldown_s = cooldown_s
        self._tripped_at: Optional[float] = None
        self._outcomes = deque(maxlen=20)
        self._wins = self._total = 0

    def _clear_history(self):
        self._outcomes.clear()
        self._wins = self._total = 0
        self._tripped_at = None

    def _expire_cooldown(self, timestamp: float):
        if (self._tripped_at is not None
                and timestamp - self._tripped_at >= self.cooldown_s):
            self._clear_history()
            return True
        return False

    def _record_at(self, win: bool, timestamp: float):
        self._expire_cooldown(timestamp)
        self._outcomes.append(bool(win))
        self._wins = sum(self._outcomes)
        self._total = len(self._outcomes)
        if (self._tripped_at is None and self._total >= self.min_trades
                and self.win_rate < self.target):
            # Settlement time is stable across a restart; polling time is not.
            self._tripped_at = timestamp

    def record(self, win: bool):
        self._record_at(win, time.time())

    @property
    def win_rate(self) -> float:
        return self._wins / self._total if self._total else 1.0

    @staticmethod
    def _outcome_timestamp(value):
        try:
            timestamp = (float(value) if isinstance(value, (int, float))
                         else datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
            return timestamp if math.isfinite(timestamp) else None
        except (TypeError, ValueError, OverflowError):
            return None

    def restore_from_outcomes(self, outcomes):
        """Replay journal settlements without starting a new cooldown on restart.

        Only WIN/LOSS rows enter the 20-outcome window. Old journals may lack a
        settlement timestamp, so their entry timestamp is the documented fallback.
        Cooldowns that elapsed before later settlements reset the earlier window.
        """
        now = time.time()
        ordered = []
        for row in outcomes:
            result = str(row.get("result", "")).upper()
            if result not in ("WIN", "LOSS"):
                continue
            timestamp = self._outcome_timestamp(row.get("settled_at"))
            if timestamp is None:
                timestamp = self._outcome_timestamp(row.get("timestamp"))
            if timestamp is None or timestamp > now:
                continue
            ordered.append((timestamp, result == "WIN"))
        self._clear_history()
        for timestamp, win in sorted(ordered, key=lambda item: item[0]):
            self._record_at(win, timestamp)
        self._expire_cooldown(now)

    def should_stop(self) -> Tuple[bool, str]:
        """Returns (True, reason) while the original cooldown is still active."""
        now = time.time()
        if self._expire_cooldown(now):
            log.info("[CB] Cooldown complete — resetting circuit breaker")
        if self._tripped_at is not None:
            remaining = max(0, math.ceil(self.cooldown_s - (now - self._tripped_at)))
            return True, f"Circuit breaker cooling down — {remaining}s left"
        return False, ""

    def state_summary(self, now=None):
        now = float(time.time() if now is None else now)
        self._expire_cooldown(now)
        wins = int(self._wins)
        total = int(self._total)
        losses = max(0, total - wins)
        remaining = 0
        if self._tripped_at is not None:
            remaining = max(0, math.ceil(self.cooldown_s - (now - self._tripped_at)))
        return {
            "window": 20,
            "wins": wins,
            "losses": losses,
            "total": total,
            "win_rate": self.win_rate,
            "target": self.target,
            "threshold_met": total >= self.min_trades and self.win_rate >= self.target,
            "tripped": self._tripped_at is not None,
            "remaining_s": remaining,
            "blocks_entries": self._tripped_at is not None and remaining > 0,
        }

    def log_state(self, logger=None, now=None):
        state = self.state_summary(now=now)
        logger = logger or log
        msg = (
            "[CB] state window=%s wins=%s losses=%s win_rate=%.4f target=%.2f blocks_entries=%s remaining_s=%s"
            % (state["window"], state["wins"], state["losses"], state["win_rate"],
               state["target"], state["blocks_entries"], state["remaining_s"])
        )
        logger.info(msg)
        logging.getLogger().info(msg)
        return state


class PipelineSupervisorAgent:
    """
    Pipeline Supervisor — Agent 3.

    Wraps trade execution and health monitoring.
    """

    MAX_TRADE_RETRIES        = 3
    TRADE_TIMEOUT_S          = 8.0
    MAX_CONSECUTIVE_FAILURES = 5

    def __init__(self, bot, risk_manager, agent1, agent2,
                 asset_selector=None, force_trades: bool = False,
                 disable_circuit_breaker: bool = False):
        self.bot   = bot
        self.risk  = risk_manager
        self.a1    = agent1
        self.a2    = agent2
        self.asset_selector = asset_selector
        self.force_trades = bool(force_trades)
        self.disable_circuit_breaker = bool(disable_circuit_breaker)
        self.cb    = CircuitBreaker(target_win_rate=0.90,
                                    min_trades=5,
                                    cooldown_s=300.0)
        self.consecutive_failures = 0
        self.max_consecutive_failures = self.MAX_CONSECUTIVE_FAILURES
        self._current_asset: Optional[str] = None
        self.execution_uncertain = False
        self.last_execution_reason = None
        self._trade_lock = asyncio.Lock()

    # ── Health Checks ────────────────────────────────────────────

    async def verify_pipeline_health(self) -> Tuple[bool, str, bool]:
        """Quick health pulse — browser alive, data feed flowing.

        Returns (healthy, reason, recoverable).
        recoverable=False means the failure is due to risk or circuit breaker
        conditions and should not trigger browser recovery attempts.
        """
        # 1. Browser alive?
        try:
            ok = await asyncio.wait_for(self.bot.is_alive(), timeout=3.0)
            if not ok:
                reason = "Browser not alive"
                log.warning(f"[Supervisor] {reason}")
                return False, reason, True
        except Exception as e:
            reason = f"Browser health check error: {e}"
            log.warning(f"[Supervisor] {reason}")
            return False, reason, True
        from config import settings

        # 2. Risk manager not halted?
        status = self.risk.status()
        if status.get("halted"):
            if self.force_trades or getattr(settings, 'TRAINING_MODE', False):
                log.warning("[Supervisor] Risk manager halted but force/training override active")
            else:
                reason = "Risk manager has halted trading"
                log.warning(f"[Supervisor] {reason}")
                return False, reason, False

        # 3. Circuit breaker?
        stop, reason = self.cb.should_stop()
        if stop:
            if self.force_trades or self.disable_circuit_breaker:
                log.warning(f"[Supervisor] Circuit breaker active but override enabled: {reason}")
            else:
                log.warning(f"[Supervisor] Circuit breaker active: {reason}")
                try:
                    send_telegram(f"[Supervisor] Circuit breaker active: {reason}")
                except Exception:
                    pass
                return False, reason, False

        return True, "", True

    def handle_tick_outcome(self, success: bool):
        if success:
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1

    async def recover_pipeline(self) -> bool:
        """Attempt to recover a broken browser session."""
        log.warning("[Supervisor] Attempting pipeline recovery...")
        try:
            await self.bot.stop()
        except Exception:
            pass
        await asyncio.sleep(3)
        try:
            await self.bot.start()
            ok = await self.bot.login()
            if ok:
                if not await self.bot.select_asset(self._current_asset or self.a1.asset):
                    return False
                if not await self.bot.get_current_quote(self._current_asset or self.a1.asset):
                    return False
                self.a1.invalidate("browser_recovery")
                self.consecutive_failures = 0
                log.info("[Supervisor] Pipeline recovered successfully.")
                return True
        except Exception as e:
            log.error(f"[Supervisor] Recovery failed: {e}")
        return False

    # ── Asset Management ─────────────────────────────────────────

    async def ensure_best_asset(self, current_asset: Optional[str] = None) -> str:
        """
        Keep the configured trading asset fixed unless the user explicitly changes it.
        The selector may recommend alternates, but this project is configured to trade
        EUR/USD OTC and should not rotate away from it automatically.
        """
        from config import settings

        current_asset = current_asset or self._current_asset or settings.ASSET
        current_asset = str(current_asset).strip() or settings.ASSET

        log.info(f"[Supervisor] Asset lock active — keeping {pretty_asset(current_asset)}")
        self._current_asset = current_asset
        return current_asset

    async def _score_asset(self, asset: str) -> Optional[float]:
        """Estimate the asset's current FTT momentum from 1m/5m chart patterns."""
        try:
            from data.fetcher import fetch_multi_timeframe
            dfs = await asyncio.to_thread(fetch_multi_timeframe, asset, ["M1", "M5"])
        except Exception:
            return None

        if not dfs:
            return None

        total = 0.0
        weight = 0.0
        for tf, df in dfs.items():
            if df.empty or len(df) < 8:
                continue
            last = df.iloc[-1]
            start = df.iloc[-5] if len(df) >= 5 else df.iloc[0]
            momentum = (last.close - start.close) / max(abs(start.close), 1e-9)
            trend = 0.0
            if last.macd > last.macd_signal:
                trend += 0.6
            elif last.macd < last.macd_signal:
                trend -= 0.6
            if last.rsi < 40:
                trend += 0.35
            elif last.rsi > 60:
                trend -= 0.35
            bb_mid = float(last.bb_mid)
            if last.close > bb_mid:
                trend += 0.15
            else:
                trend -= 0.15

            tf_weight = 1.0 if tf == "M1" else 1.3
            total += tf_weight * (momentum * 40 + trend)
            weight += tf_weight

        if weight == 0:
            return None

        score = total / weight
        return float(score)

    async def _get_payout(self, asset: str) -> Optional[int]:
        """Try to read the payout percentage for an asset from the UI."""
        try:
            payout = await asyncio.wait_for(
                self.bot.get_asset_payout(asset), timeout=5.0
            )
            return payout
        except Exception:
            return None

    # ── Trade Execution ──────────────────────────────────────────

    async def safe_execute_trade(self, signal, confidence: float, mode: str,
                                 *, entry_deadline: Optional[float] = None) -> bool:
        """Serialize one demo order attempt; never retry an uncertain click."""
        from config import settings
        async with self._trade_lock:
            self.last_execution_reason = None
            if self.execution_uncertain:
                self.last_execution_reason = "unresolved_order"
                log.error("[Supervisor] execution_blocked=unresolved_order")
                return False
            approved, reason = self.risk.approve(
                signal, confidence, mode, trade_amount=settings.TRADE_AMOUNT,
                allow_during_training=False)
            if not approved:
                self.last_execution_reason = f"risk_rejected: {reason}"
                log.info("[Supervisor] risk_rejected=%s", reason)
                return False
            if confidence < settings.CONFIDENCE_THRESHOLD:
                self.last_execution_reason = "confidence_below_threshold"
                return False
            stop, reason = self.cb.should_stop()
            if stop:
                self.last_execution_reason = f"circuit_breaker: {reason}"
                log.info("[Supervisor] circuit_breaker=%s", reason)
                return False
            if mode != "ftt":
                self.last_execution_reason = "unsupported_mode"
                log.error("[Supervisor] unsupported_mode=%s", mode)
                return False
            try:
                order_options = {"asset": self._current_asset}
                if entry_deadline is not None:
                    order_options["entry_deadline"] = entry_deadline
                accepted = bool(await asyncio.wait_for(
                    self.bot.place_ftt_trade(signal, settings.FTT_DURATION, **order_options),
                    timeout=45.0))
                self.last_execution_reason = "order_accepted" if accepted else "broker_order_not_accepted"
                return accepted
            except asyncio.CancelledError:
                # Cancellation does not prove that an in-flight click was rejected.
                # Preserve the unresolved state before releasing the trade lock.
                self.execution_uncertain = True
                self.last_execution_reason = "execution_uncertain: CancelledError"
                log.error("[Supervisor] execution_uncertain=true retry=false reason=CancelledError")
                raise
            except Exception as exc:
                # A cancellation/timeout can occur after the click reached the
                # broker. A second attempt could duplicate the order.
                self.execution_uncertain = True
                self.last_execution_reason = f"execution_uncertain: {type(exc).__name__}"
                log.error("[Supervisor] execution_uncertain=true retry=false reason=%s", exc)
                return False

    def record_outcome(self, win: bool):
        """Update circuit breaker after a trade resolves."""
        self.cb.record(win)
        wr = self.cb.win_rate
        log.info(f"[Supervisor] CB win-rate={wr:.0%} | total={self.cb._total}")
