"""
integration_strategic_trading.py — Integration Layer

Connects the strategic framework (A1-A4) with the multi-agent orchestrator.
Manages daily loss limits, position sizing, and execution scheduling.

Usage:
    python integration_strategic_trading.py
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, Optional

sys.path.insert(0, os.path.dirname(__file__))

from agents.agent_strategic_framework import StrategicFrameworkAgent
from agents.agent_multi_timeframe import MultiTimeframeAgent
from agents.agent_rl_learning import RLFeedbackAgent
from config import settings

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s"
)
log = logging.getLogger("strategic_integration")


class StrategicTradingIntegrator:
    """
    Orchestrates the complete trading strategy:
    
    A1: Asset Selection (4x daily at scheduled times)
    A2: Strategy Planning (confidence + volatility checks)
    A3: Time Execution (5-minute holds with entry/exit)
    A4: Reinforcement Learning (feedback loop for improvements)
    
    Risk Management:
    - Account: $10,000
    - Risk per trade: $200
    - Daily loss limit: $2,000
    - Max trades: 20/day
    """

    def __init__(self):
        self.strategy_agent = StrategicFrameworkAgent()
        self.ta_agent = MultiTimeframeAgent(settings.ASSET)
        self.rl_agent = RLFeedbackAgent(settings.MODEL_DIR)
        self.running = False

    async def run_strategy_loop(self):
        """Main trading loop implementing A1-A4 cycle."""
        
        log.info("=" * 80)
        log.info("STRATEGIC TRADING SYSTEM STARTING")
        log.info("=" * 80)
        log.info(f"Account Size: ${self.strategy_agent.ACCOUNT_SIZE}")
        log.info(f"Daily Loss Limit: ${self.strategy_agent.DAILY_LOSS_LIMIT}")
        log.info(f"Risk Per Trade: ${self.strategy_agent.RISK_PER_TRADE}")
        log.info(f"Max Trades/Day: {self.strategy_agent.MAX_TRADES_PER_DAY}")
        log.info("=" * 80)

        self.running = True
        tick_count = 0

        try:
            while self.running:
                tick_count += 1
                current_time = datetime.now()

                # =============== A1: ASSET SELECTION (4x/day) ===============
                selected_asset = self.strategy_agent.agent_a1_asset_selection()

                # =============== A2: STRATEGY PLANNING ===============
                if selected_asset:
                    # Get technical analysis signals
                    signal, confidence = self.ta_agent.analyze()
                    
                    # Get market data
                    trend = self._estimate_trend()
                    volatility = self._estimate_volatility()
                    current_price = await self._get_current_price()

                    # A2: Plan strategy
                    strategy = self.strategy_agent.agent_a2_strategy_planning(
                        asset=selected_asset,
                        current_price=current_price,
                        trend=trend,
                        volatility=volatility,
                        confidence=confidence
                    )

                    # =============== A3: TIME EXECUTION ===============
                    if strategy["action"] == "TRADE":
                        trade_record = self.strategy_agent.agent_a3_time_execution(
                            strategy=strategy,
                            current_price=current_price
                        )

                        if trade_record:
                            # Simulate trade execution (in real system, connect to broker)
                            await asyncio.sleep(strategy["hold_time"])
                            final_price = await self._simulate_trade_exit(
                                current_price,
                                trade_record.direction
                            )

                            # =============== A4: REINFORCEMENT LEARNING ===============
                            rl_result = self.strategy_agent.agent_a4_rl_feedback(
                                trade=trade_record,
                                final_price=final_price
                            )

                            # Update RL model with result
                            self._update_rl_model(rl_result)

                # Check if daily reset needed
                self.strategy_agent.reset_daily_stats()

                # Log status every 100 ticks
                if tick_count % 100 == 0:
                    summary = self.strategy_agent.get_daily_summary()
                    log.info(
                        f"[Status] Tick {tick_count} | Trades: {summary['trades']} | "
                        f"W/L: {summary['wins']}/{summary['losses']} | "
                        f"P&L: ${summary['net_pnl']:+.2f} | Balance: ${summary['account_balance']:.2f}"
                    )

                    # Circuit breaker check
                    if summary['circuit_breaker_active']:
                        log.warning("🛑 CIRCUIT BREAKER ACTIVE - Waiting for next trading day")
                        await asyncio.sleep(60)
                        continue

                await asyncio.sleep(5)  # 5-second decision interval

        except KeyboardInterrupt:
            log.info("\n⏹️  Shutdown requested by user")
        except Exception as e:
            log.error(f"Fatal error: {e}", exc_info=True)
        finally:
            await self._shutdown()

    def _estimate_trend(self) -> str:
        """Estimate market trend from technical analysis."""
        # In real implementation, calculate from candles
        signal, _ = self.ta_agent.analyze()
        if signal.value == "BUY":
            return "UPTREND"
        elif signal.value == "SELL":
            return "DOWNTREND"
        else:
            return "NEUTRAL"

    def _estimate_volatility(self) -> float:
        """Estimate market volatility."""
        # In real implementation, calculate from ATR or standard deviation
        # For demo: return simulated value
        return 0.02  # 2% volatility

    async def _get_current_price(self) -> float:
        """Get current market price."""
        # In real implementation, fetch from broker API
        # For demo: return baseline
        return 1.0850

    async def _simulate_trade_exit(self, entry_price: float, direction: str) -> float:
        """Simulate trade exit price."""
        # In real implementation: actual trade execution
        # For demo: random exit
        import random
        
        if direction == "UP":
            # 60% chance of winning trade
            if random.random() < 0.60:
                # Win: 1.5% profit
                return entry_price * 1.015
            else:
                # Loss: 2% stop loss
                return entry_price * 0.98
        else:  # DOWN
            if random.random() < 0.60:
                # Win: 1.5% profit
                return entry_price * 0.985
            else:
                # Loss: 2% stop loss
                return entry_price * 1.02

    def _update_rl_model(self, rl_result: Dict):
        """Update RL model with trade feedback."""
        rl_reward = rl_result["rl_reward"]
        daily_stats = rl_result["daily_stats"]

        # Log RL update
        log.debug(f"[RL Update] Reward: {rl_reward:+.1f} | "
                  f"Daily P&L: ${daily_stats.net_pnl:+.2f}")

    async def _shutdown(self):
        """Cleanup and save state."""
        log.info("\n" + "=" * 80)
        log.info("SHUTDOWN - SAVING FINAL STATE")
        log.info("=" * 80)

        # Save daily summary
        summary = self.strategy_agent.get_daily_summary()
        log.info(f"Final Account Balance: ${summary['account_balance']:.2f}")
        log.info(f"Total P&L: ${summary['net_pnl']:+.2f}")
        log.info(f"Win Rate: {summary['wins']}/{summary['trades']} = "
                 f"{100*summary['wins']/max(summary['trades'],1):.1f}%")

        # Save trade history
        self.strategy_agent.save_trade_history("logs/strategy_trades.json")

        log.info("=" * 80)
        self.running = False


async def main():
    """Entry point."""
    integrator = StrategicTradingIntegrator()
    await integrator.run_strategy_loop()


if __name__ == "__main__":
    asyncio.run(main())
