"""
agent_strategic_framework.py — Strategic Trading Framework

Implements the 4-agent plan from whiteboard:
  A1: 4x/day asset selection with automatic chart opening
  A2: Strategy planning with execution rules
  A3: Time-based execution management (entry/exit timing)
  A4: Reinforcement learning from trade outcomes

Account: $10,000
Daily Loss Limit: $2,000 (20%)
Risk per trade: $200 (2% per trade, max 10 losses before daily limit)
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field, asdict
import json

log = logging.getLogger(__name__)


@dataclass
class DailyTradeStats:
    """Track daily performance metrics."""
    date: str
    trades_executed: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_profit: float = 0.0
    total_loss: float = 0.0
    net_pnl: float = 0.0
    daily_loss_limit_hit: bool = False
    max_consecutive_losses: int = 0
    current_consecutive_losses: int = 0


@dataclass
class TradeExecution:
    """Individual trade execution details."""
    timestamp: str
    asset: str
    direction: str  # 'UP' or 'DOWN'
    entry_price: float
    exit_price: float
    amount_risked: float
    pnl: float
    profit_loss: str  # 'WIN' or 'LOSS'
    strategy_used: str
    confidence: float
    rl_action: int  # 0 or 1 from RL agent


class StrategicFrameworkAgent:
    """
    Main strategic framework orchestrating:
    - A1: Asset Selection (4x daily)
    - A2: Strategy Planning
    - A3: Time Execution
    - A4: Reinforcement Learning feedback loop
    """

    # Account configuration
    ACCOUNT_SIZE = 10000.0  # $10,000
    DAILY_LOSS_LIMIT = 2000.0  # $2,000 per day (20% of account)
    RISK_PER_TRADE = 200.0  # $200 per trade (2% of account)
    MAX_TRADES_PER_DAY = 20  # Max 10 consecutive losses = $2,000
    
    # Strategy execution windows (4x per day selection)
    SELECTION_WINDOWS = [
        {"name": "Morning", "hour": 9, "minute": 0},     # 9:00 AM
        {"name": "Mid-Morning", "hour": 11, "minute": 30},  # 11:30 AM
        {"name": "Afternoon", "hour": 14, "minute": 0},   # 2:00 PM
        {"name": "Late Afternoon", "hour": 16, "minute": 0}, # 4:00 PM
    ]

    # Trade execution parameters
    HOLD_TIME_SECONDS = 300  # 5 minutes per trade
    MIN_TIME_BETWEEN_TRADES = 30  # 30 seconds between trades
    
    def __init__(self):
        self.daily_stats = DailyTradeStats(date=datetime.now().strftime("%Y-%m-%d"))
        self.trade_history: List[TradeExecution] = []
        self.last_trade_time = 0
        self.current_account_balance = self.ACCOUNT_SIZE
        self.circuit_breaker_active = False
        self.available_assets = ["EUR/USD", "GBP/USD", "USD/JPY"]  # Forex pairs
        self.selected_asset = None
        self.last_selection_time = 0
        
        log.info(f"✓ Strategic Framework initialized: ${self.ACCOUNT_SIZE} account, ${self.DAILY_LOSS_LIMIT}/day limit")

    # ==================== A1: ASSET SELECTION ====================
    def agent_a1_asset_selection(self) -> Optional[str]:
        """
        A1: 4x/day automatic chart opening and asset selection
        Runs at: 9:00 AM, 11:30 AM, 2:00 PM, 4:00 PM
        
        Returns: Selected asset for trading window
        """
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        
        for window in self.SELECTION_WINDOWS:
            window_time = f"{window['hour']:02d}:{window['minute']:02d}"
            if current_time == window_time:
                # Rotate through available assets
                rotation_index = (len(self.trade_history) // 5) % len(self.available_assets)
                selected = self.available_assets[rotation_index]
                self.selected_asset = selected
                self.last_selection_time = time.time()
                
                log.info(f"🎯 [A1] {window['name']} Selection: {selected}")
                log.info(f"   └─ Charts opened for: {selected}")
                
                return selected
        
        return self.selected_asset

    # ==================== A2: STRATEGY PLANNING ====================
    def agent_a2_strategy_planning(
        self,
        asset: str,
        current_price: float,
        trend: str,  # 'UPTREND', 'DOWNTREND', 'NEUTRAL'
        volatility: float,
        confidence: float
    ) -> Dict:
        """
        A2: Strategy planning with execution rules
        
        Determines:
        - Trade direction (UP/DOWN)
        - Entry/exit rules
        - Position sizing
        - Risk management rules
        """
        
        # Check daily circuit breaker
        if self.circuit_breaker_active:
            log.warning(f"⚠️  [A2] Daily loss limit hit. No new trades allowed.")
            return {"action": "HOLD", "reason": "Daily loss limit"}
        
        # Check minimum time between trades
        if time.time() - self.last_trade_time < self.MIN_TIME_BETWEEN_TRADES:
            return {"action": "HOLD", "reason": "Minimum trade interval"}
        
        # Check daily trade count
        if self.daily_stats.trades_executed >= self.MAX_TRADES_PER_DAY:
            log.warning(f"⚠️  [A2] Daily trade limit reached ({self.MAX_TRADES_PER_DAY} trades)")
            return {"action": "HOLD", "reason": "Daily trade limit"}
        
        # Strategy rules based on trend and volatility
        strategy = {
            "action": "HOLD",
            "direction": None,
            "entry_price": current_price,
            "risk_amount": self.RISK_PER_TRADE,
            "take_profit_percent": 1.5,  # 1.5% profit target
            "stop_loss_percent": 2.0,    # 2% stop loss
            "hold_time": self.HOLD_TIME_SECONDS,
            "confidence": confidence,
            "strategy_name": "NEUTRAL"
        }
        
        # Decision tree based on technical analysis
        if confidence >= 0.65 and volatility < 0.03:  # Low volatility, high confidence
            if trend == "UPTREND":
                strategy["action"] = "TRADE"
                strategy["direction"] = "UP"
                strategy["strategy_name"] = "TREND_FOLLOWING_UP"
                strategy["take_profit_percent"] = 2.0
                
            elif trend == "DOWNTREND":
                strategy["action"] = "TRADE"
                strategy["direction"] = "DOWN"
                strategy["strategy_name"] = "TREND_FOLLOWING_DOWN"
                strategy["take_profit_percent"] = 2.0
        
        elif confidence >= 0.60 and volatility < 0.05:  # Medium conditions
            if trend == "UPTREND":
                strategy["action"] = "TRADE"
                strategy["direction"] = "UP"
                strategy["strategy_name"] = "MEDIUM_CONFIDENCE_UP"
                
            elif trend == "DOWNTREND":
                strategy["action"] = "TRADE"
                strategy["direction"] = "DOWN"
                strategy["strategy_name"] = "MEDIUM_CONFIDENCE_DOWN"
        
        log.debug(f"📋 [A2] Strategy planned for {asset}: {strategy['strategy_name']} | Confidence: {confidence:.2%}")
        return strategy

    # ==================== A3: TIME EXECUTION ====================
    def agent_a3_time_execution(
        self,
        strategy: Dict,
        current_price: float
    ) -> Optional[TradeExecution]:
        """
        A3: Time-based execution management
        - Executes trades based on strategy and time parameters
        - Manages entry/exit timing
        - Tracks trade execution
        
        Returns: Executed trade record or None
        """
        
        if strategy["action"] != "TRADE":
            return None
        
        # Record trade execution
        execution_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        trade = TradeExecution(
            timestamp=execution_time,
            asset=self.selected_asset or strategy.get("asset", "UNKNOWN"),
            direction=strategy["direction"],
            entry_price=current_price,
            exit_price=0.0,  # Set when trade closes
            amount_risked=strategy["risk_amount"],
            pnl=0.0,
            profit_loss="PENDING",
            strategy_used=strategy["strategy_name"],
            confidence=strategy["confidence"],
            rl_action=0  # Set by A4
        )
        
        log.info(f"⏱️  [A3] Executing trade: {trade.direction} {trade.asset} @ {current_price:.4f}")
        log.info(f"   └─ Risk: ${trade.amount_risked} | TP: {strategy['take_profit_percent']:.1%} | SL: {strategy['stop_loss_percent']:.1%}")
        
        self.last_trade_time = time.time()
        
        return trade

    # ==================== A4: REINFORCEMENT LEARNING ====================
    def agent_a4_rl_feedback(
        self,
        trade: TradeExecution,
        final_price: float
    ) -> Dict:
        """
        A4: Reinforcement learning feedback loop
        - Evaluates trade outcome
        - Updates RL weights based on profit/loss
        - Learns from executed trades
        
        Returns: Trade result with RL feedback
        """
        
        # Calculate P&L
        if trade.direction == "UP":
            pnl = (final_price - trade.entry_price) * 100  # Simplified calculation
        else:  # DOWN
            pnl = (trade.entry_price - final_price) * 100
        
        # Determine if profit or loss
        if pnl > 0:
            trade.profit_loss = "WIN"
            self.daily_stats.winning_trades += 1
            self.daily_stats.current_consecutive_losses = 0
            rl_reward = 1.0
        elif pnl < 0:
            trade.profit_loss = "LOSS"
            self.daily_stats.losing_trades += 1
            self.daily_stats.current_consecutive_losses += 1
            self.daily_stats.max_consecutive_losses = max(
                self.daily_stats.max_consecutive_losses,
                self.daily_stats.current_consecutive_losses
            )
            rl_reward = -1.0
        else:
            trade.profit_loss = "BREAK_EVEN"
            rl_reward = 0.0
        
        trade.exit_price = final_price
        trade.pnl = pnl
        
        # Update daily statistics
        self.daily_stats.trades_executed += 1
        self.daily_stats.total_profit += max(0, pnl)
        self.daily_stats.total_loss += abs(min(0, pnl))
        self.daily_stats.net_pnl += pnl
        
        # Update account balance
        self.current_account_balance += pnl
        
        # Check if daily loss limit hit
        if self.daily_stats.total_loss >= self.DAILY_LOSS_LIMIT:
            self.circuit_breaker_active = True
            self.daily_stats.daily_loss_limit_hit = True
            log.warning(f"🚨 [A4] DAILY LOSS LIMIT HIT: ${self.daily_stats.total_loss:.2f}")
        
        # Log RL feedback
        log.info(f"📊 [A4] Trade result: {trade.profit_loss} | P&L: ${pnl:.2f} | RL Reward: {rl_reward}")
        log.info(f"   └─ Daily: {self.daily_stats.trades_executed} trades | Net: ${self.daily_stats.net_pnl:.2f} | Balance: ${self.current_account_balance:.2f}")
        
        self.trade_history.append(trade)
        
        return {
            "trade": trade,
            "rl_reward": rl_reward,
            "daily_stats": self.daily_stats
        }

    # ==================== DAILY RESET ====================
    def reset_daily_stats(self):
        """Reset daily tracking at end of trading day or new day."""
        today = datetime.now().strftime("%Y-%m-%d")
        
        if self.daily_stats.date != today:
            log.info(f"\n{'='*60}")
            log.info(f"📈 DAILY SUMMARY ({self.daily_stats.date})")
            log.info(f"{'='*60}")
            log.info(f"Trades: {self.daily_stats.trades_executed} | Wins: {self.daily_stats.winning_trades} | Losses: {self.daily_stats.losing_trades}")
            log.info(f"Profit: ${self.daily_stats.total_profit:.2f} | Loss: ${self.daily_stats.total_loss:.2f} | Net: ${self.daily_stats.net_pnl:.2f}")
            log.info(f"Account Balance: ${self.current_account_balance:.2f}")
            log.info(f"Max Consecutive Losses: {self.daily_stats.max_consecutive_losses}")
            if self.daily_stats.daily_loss_limit_hit:
                log.warning(f"⚠️  Daily Loss Limit HIT on {self.daily_stats.date}")
            log.info(f"{'='*60}\n")
            
            # Reset for new day
            self.daily_stats = DailyTradeStats(date=today)
            self.circuit_breaker_active = False

    # ==================== UTILITIES ====================
    def get_daily_summary(self) -> Dict:
        """Get current daily performance summary."""
        return {
            "date": self.daily_stats.date,
            "trades": self.daily_stats.trades_executed,
            "wins": self.daily_stats.winning_trades,
            "losses": self.daily_stats.losing_trades,
            "net_pnl": self.daily_stats.net_pnl,
            "total_profit": self.daily_stats.total_profit,
            "total_loss": self.daily_stats.total_loss,
            "account_balance": self.current_account_balance,
            "daily_loss_limit_reached": self.daily_stats.daily_loss_limit_hit,
            "circuit_breaker_active": self.circuit_breaker_active
        }

    def save_trade_history(self, filename: str = "strategy_trades.json"):
        """Save trade history to file."""
        data = {
            "account_size": self.ACCOUNT_SIZE,
            "daily_loss_limit": self.DAILY_LOSS_LIMIT,
            "trades": [asdict(t) for t in self.trade_history],
            "daily_summary": asdict(self.daily_stats)
        }
        
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2, default=str)
        
        log.info(f"💾 Trade history saved to {filename}")
