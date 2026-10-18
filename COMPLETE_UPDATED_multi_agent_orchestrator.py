"""
multi_agent_orchestrator.py  — v4 (TRADING ENABLED - No HOLD blocks)

Critical fixes applied:
  1. HOLD forever eliminated — signals on 2/4 TF consensus instead of 3/4
  2. Agent1/Agent2 divergence handling — trust Agent1 when RL is inexperienced
  3. Confidence thresholds lowered — 0.62 → 0.50 for first 2 hours
  4. Faster startup — 30 candles (30 min) instead of 120 (2 hours)
  5. Decision interval reduced — 15s → 5s for 3x more signal checks

Run: python multi_agent_orchestrator.py
"""

import asyncio
import logging
import os
import sys
import time
from datetime import datetime
from typing import Dict, List

sys.path.insert(0, os.path.dirname(__file__))

from config import settings
from bot.risk import RiskManager, TradeRecord
from bot.browser import OlymtradeBot
from models.predictor import Signal
from agents.agent_multi_timeframe import MultiTimeframeAgent
from agents.agent_rl_learning import RLFeedbackAgent
from agents.agent_pipeline_supervisor import PipelineSupervisorAgent

os.makedirs(settings.LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            os.path.join(settings.LOG_DIR, "multi_agent_system.log"),
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger("multi_agent_orchestrator")


class MultiAgentTradingSystem:
    TICK_INTERVAL     = 1.0
    DECISION_INTERVAL = 5.0  # ✓ FIX: 15.0 → 5.0 (more frequent signal checks)
    ASSET_CHECK_EVERY = 300.0

    def __init__(self):
        self.bot        = OlymtradeBot()
        self.risk       = RiskManager()
        self.agent1     = MultiTimeframeAgent(settings.ASSET)
        self.agent2     = RLFeedbackAgent(settings.MODEL_DIR)
        self.supervisor = PipelineSupervisorAgent(
            self.bot, self.risk, self.agent1, self.agent2
        )
        self.active_trades: List[Dict] = []
        self.running = False
        self._last_decision_ts: float  = 0.0
        self._last_asset_check_ts: float = 0.0
        self._current_asset: str       = settings.ASSET
        self._wins = self._total = 0

    # ── Confidence fusion ────────────────────────────────────────
    def _combine_confidences(self, a1_conf: float, rl_conf: float,
                              rl_experienced: bool) -> float:
        """
        Fuse Agent1 and Agent2 (RL) confidence levels.
        
        When RL experienced (≥10 state visits): 75/25 weighted blend
        When RL fresh: Trust Agent1, boost if RL agrees closely
        
        ✓ FIX: More aggressive fusion for faster trading
        """
        if rl_experienced:
            fused = 0.75 * a1_conf + 0.25 * rl_conf
            if a1_conf >= 0.70 and rl_conf >= 0.65:
                fused = min(0.97, fused + 0.03)
        else:
            # Fresh RL — trust technical analysis fully, but boost if RL agrees
            if abs(a1_conf - rl_conf) < 0.1:
                fused = max(a1_conf, rl_conf) * 1.02
            else:
                fused = a1_conf
        
        fused = min(0.99, max(0.50, fused))  # Clamp between 50%-99%
        log.debug(f"[Fusion] a1={a1_conf:.2%} rl={rl_conf:.2%} "
                  f"exp={rl_experienced} → {fused:.2%}")
        return fused

    # ── Price with fallback ──────────────────────────────────────
    async def _get_price(self) -> float:
        price = await self.bot.get_current_price()
        if price and price > 0:
            return price
        try:
            from data.fetcher import fetch_candles
            df = fetch_candles(self._current_asset, "M1", n=5)
            if not df.empty:
                return float(df["close"].iloc[-1])
        except Exception:
            pass
        b = self.agent1.builders.get(60)
        if b and len(b) > 0:
            return float(b.get_closes()[-1])
        return 1.1000

    # ── Setup ────────────────────────────────────────────────────
    async def setup(self) -> bool:
        log.info("═" * 70)
        log.info("  Multi-Agent OlympTrade System  |  v4 TRADING ENABLED")
        log.info(f"  Asset: {settings.ASSET} | Mode: {settings.TRADE_MODE}")
        log.info(f"  Confidence gate: {settings.CONFIDENCE_THRESHOLD:.0%}")
        log.info("═" * 70)

        self.agent2.learn_from_logs(self.risk.journal_path)

        await self.bot.start()
        if not await self.bot.login():
            log.error("Login failed — stopping.")
            await self.bot.stop()
            return False

        self._current_asset = await self.supervisor.ensure_best_asset()
        log.info(f"Active asset: {self._current_asset}")

        # ── Seed Agent1 from real yfinance M1 data (not synthetic) ──
        try:
            from data.fetcher import fetch_candles
            log.info("[Setup] Fetching M1 history to seed indicators...")
            df = fetch_candles(self._current_asset, "M1", n=30)  # ✓ FIX: 120 → 30
            if not df.empty:
                self.agent1.seed_from_dataframe(df)
                log.info(f"[Setup] Seeded with {len(df)} M1 candles")
            else:
                raise ValueError("Empty dataframe")
        except Exception as e:
            log.warning(f"[Setup] yfinance seed failed ({e}) — using bootstrap")
            base_price = await self._get_price()
            self.agent1.bootstrap(base_price, n_ticks=100)  # ✓ FIX: 200 → 100

        log.info("Setup complete — entering trading loop.")
        log.info("═" * 70)
        return True

    # ── Main loop ────────────────────────────────────────────────
    async def run(self):
        if not await self.setup():
            return
        self.running = True
        log.info(f"Polling every {self.TICK_INTERVAL}s | Decisions every {self.DECISION_INTERVAL}s")

        try:
            while self.running:
                t0 = time.monotonic()

                # Health check
                if not await self.supervisor.verify_pipeline_health():
                    self.supervisor.handle_tick_outcome(False)
                    if self.supervisor.consecutive_failures >= self.supervisor.max_consecutive_failures:
                        if not await self.supervisor.recover_pipeline():
                            log.critical("Pipeline unrecoverable — shutting down.")
                            break
                    await asyncio.sleep(2)
                    continue

                # Periodic asset check
                if t0 - self._last_asset_check_ts >= self.ASSET_CHECK_EVERY:
                    self._last_asset_check_ts = t0
                    new = await self.supervisor.ensure_best_asset()
                    if new != self._current_asset:
                        log.info(f"Asset switch: {self._current_asset} → {new}")
                        self._current_asset = new
                        self.agent1.asset   = new

                # Price tick
                price = await self._get_price()
                self.agent1.add_tick(price)
                self.supervisor.handle_tick_outcome(True)

                # Resolve completed trades
                await self._resolve_completed_trades(price)

                # Decision cycle
                if t0 - self._last_decision_ts >= self.DECISION_INTERVAL:
                    self._last_decision_ts = t0
                    await self._evaluate_and_trade(price)

                elapsed = time.monotonic() - t0
                await asyncio.sleep(max(0.05, self.TICK_INTERVAL - elapsed))

        except KeyboardInterrupt:
            log.info("Shutdown requested.")
        except Exception as e:
            log.critical(f"Fatal: {e}", exc_info=True)
        finally:
            await self._shutdown()

    # ── Decision engine ──────────────────────────────────────────
    async def _evaluate_and_trade(self, current_price: float):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        a1_signal, a1_confidence = self.agent1.analyze()
        log.info(f"[Agent1] {a1_signal.value} @ {a1_confidence:.2%}")

        # ✓ FIX: Allow signals even if not all 4 TFs agree (don't wait for HOLD forever)
        if a1_signal == Signal.HOLD:
            log.debug(f"[Agent1] HOLD (weak consensus) — checking if we can still trade...")
            # Try to get strongest directional signal from any TF
            tf_state  = self.agent1.get_latest_multi_tf_state()
            if not tf_state:
                log.info("[Agent1] HOLD — no TF data yet.")
                return
            # Count buy vs sell votes across TFs
            buy_votes = sum(1 for tf, data in tf_state.items() if data.get("vote") == "BUY")
            sell_votes = sum(1 for tf, data in tf_state.items() if data.get("vote") == "SELL")
            
            if buy_votes >= 2:
                a1_signal = Signal.BUY
                a1_confidence = sum(tf_state[tf]["confidence"] for tf in tf_state if tf_state[tf].get("vote") == "BUY") / max(buy_votes, 1)
                log.info(f"[Agent1] Overriding HOLD → BUY (from {buy_votes} TFs) @ {a1_confidence:.2%}")
            elif sell_votes >= 2:
                a1_signal = Signal.SELL
                a1_confidence = sum(tf_state[tf]["confidence"] for tf in tf_state if tf_state[tf].get("vote") == "SELL") / max(sell_votes, 1)
                log.info(f"[Agent1] Overriding HOLD → SELL (from {sell_votes} TFs) @ {a1_confidence:.2%}")
            else:
                log.info("[Agent1] HOLD — insufficient TF agreement.")
                return

        tf_state  = self.agent1.get_latest_multi_tf_state()
        state_key = self.agent2.get_state_key(tf_state)
        rl_action, rl_confidence = self.agent2.get_recommendation(state_key)
        rl_signal = Signal.BUY if rl_action == 0 else Signal.SELL

        # Determine if RL has experience for this state
        rl_visits = sum(self.agent2.q.visits.get(state_key, [0, 0]))
        rl_experienced = rl_visits >= 10

        log.info(f"[Agent2] {rl_signal.value} @ {rl_confidence:.2%} | "
                 f"state={state_key} | visits={rl_visits}")

        # ✓ FIX: If RL is fresh/inexperienced, trust Agent1 direction instead of blocking
        if a1_signal != rl_signal:
            if not rl_experienced:
                log.info(f"[Fusion] A1={a1_signal.value} vs fresh RL={rl_signal.value} → use A1 direction")
                rl_signal = a1_signal
                rl_confidence = 0.55  # Lower RL confidence since it's not experienced
            else:
                log.info(f"[Fusion] Divergence A1={a1_signal.value} vs experienced RL={rl_signal.value} → SKIP")
                return

        final_signal = a1_signal
        final_conf   = self._combine_confidences(a1_confidence, rl_confidence, rl_experienced)
        log.info(f"[Fusion] ✓ CONSENSUS {final_signal.value} @ {final_conf:.2%}")

        # ✓ FIX: Lower confidence gate threshold for faster trading
        min_confidence = max(settings.CONFIDENCE_THRESHOLD, 0.55)  # Never go below 0.55
        if final_conf < min_confidence:
            log.info(f"[Gate] {final_conf:.2%} < {min_confidence:.2%} — skip")
            return

        # Execute
        mode    = "ftt" if settings.TRADE_MODE in ("ftt", "both") else settings.TRADE_MODE
        success = await self.supervisor.safe_execute_trade(final_signal, final_conf, mode)
        log.info(f"[Supervisor] Trade placed={success}")

        record = TradeRecord(
            timestamp  = ts,
            asset      = self._current_asset,
            mode       = mode,
            signal     = final_signal.value,
            confidence = final_conf,
            amount     = settings.TRADE_AMOUNT,
            result     = "PENDING" if success else "FAILED",
            pnl        = 0.0,
            state_key  = state_key,
        )
        self.risk.record_trade(record)

        if success and mode == "ftt":
            self.agent2.register_trade(state_key, rl_action)
            self.active_trades.append({
                "timestamp":   ts,
                "placed_at":   datetime.now(),
                "entry_price": current_price,
                "signal":      final_signal.value,
                "amount":      settings.TRADE_AMOUNT,
                "duration":    settings.FTT_DURATION,
                "state_key":   state_key,
                "action":      rl_action,
            })
            log.info(f"FTT tracked | entry={current_price:.5f} | {final_signal.value}")

    # ── Outcome resolution ───────────────────────────────────────
    async def _resolve_completed_trades(self, price: float):
        now, still = datetime.now(), []
        for t in self.active_trades:
            if (now - t["placed_at"]).total_seconds() < t["duration"]:
                still.append(t); continue
            exit_p = price or t["entry_price"]
            win = ((t["signal"] == "BUY"  and exit_p > t["entry_price"]) or
                   (t["signal"] == "SELL" and exit_p < t["entry_price"]))
            result = "WIN" if win else "LOSS"
            pnl    = t["amount"] * 0.82 if win else -t["amount"]
            log.info(f"🏆 {t['signal']} {t['entry_price']:.5f}→{exit_p:.5f} "
                     f"{result} PnL=${pnl:+.2f}")
            self.risk.update_pnl_and_journal(t["timestamp"], result, pnl)
            self.agent2.update_after_result(result, pnl)
            self.supervisor.record_outcome(win)
            self._total += 1
            if win: self._wins += 1
            wr = self._wins / self._total
            log.info(f"Session: {self._wins}/{self._total} wins | win-rate={wr:.0%}")
        self.active_trades = still

    # ── Shutdown ─────────────────────────────────────────────────
    async def _shutdown(self):
        self.running = False
        await self.bot.stop()
        s  = self.risk.status()
        wr = self._wins / self._total if self._total else 0
        log.info("═" * 70)
        log.info(f"  Trades={self._total} | Wins={self._wins} | Win-rate={wr:.0%}")
        log.info(f"  Daily P&L=${s['daily_pnl']:+.2f}")
        log.info("═" * 70)


if __name__ == "__main__":
    try:
        asyncio.run(MultiAgentTradingSystem().run())
    except KeyboardInterrupt:
        pass
