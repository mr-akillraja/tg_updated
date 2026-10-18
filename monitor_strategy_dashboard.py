"""
monitor_strategy_dashboard.py — Real-time Performance Dashboard

Displays live trading metrics for the strategic framework.

Usage:
    python monitor_strategy_dashboard.py
"""

import logging
import time
import json
from datetime import datetime
from typing import Dict
from pathlib import Path
import sys

sys.path.insert(0, '.')

from agents.agent_strategic_framework import StrategicFrameworkAgent


class StrategyMonitorDashboard:
    """Real-time monitoring dashboard for trading strategy."""

    def __init__(self, update_interval: int = 5):
        self.strategy = StrategicFrameworkAgent()
        self.update_interval = update_interval
        self.running = False

    def clear_screen(self):
        """Clear console screen."""
        print("\033[2J\033[H", end="")

    def print_header(self):
        """Print dashboard header."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print("╔" + "═" * 78 + "╗")
        print(f"║  STRATEGIC TRADING DASHBOARD  |  {now}  │")
        print("╚" + "═" * 78 + "╝")
        print()

    def print_account_status(self, summary: Dict):
        """Print account status section."""
        print("┌─ ACCOUNT STATUS " + "─" * 61 + "┐")
        
        # Account info
        print(f"│ Account Size:        ${self.strategy.ACCOUNT_SIZE:,.2f}                              │")
        print(f"│ Current Balance:     ${summary['account_balance']:,.2f}                              │")
        print(f"│ Daily Profit:        ${summary['net_pnl']:+,.2f}                              │")
        
        # Equity curve
        profit_pct = (summary['account_balance'] - self.strategy.ACCOUNT_SIZE) / self.strategy.ACCOUNT_SIZE * 100
        print(f"│ Return:              {profit_pct:+.2f}%                                 │")
        
        # Risk metrics
        daily_loss_used_pct = (summary['total_loss'] / self.strategy.DAILY_LOSS_LIMIT * 100)
        print(f"│ Daily Loss Used:     ${summary['total_loss']:.2f} / ${self.strategy.DAILY_LOSS_LIMIT:.2f} ({daily_loss_used_pct:.0f}%)          │")
        
        # Circuit breaker status
        breaker_status = "🔴 ACTIVE" if summary['circuit_breaker_active'] else "🟢 READY"
        print(f"│ Circuit Breaker:     {breaker_status:<30} │")
        
        print("└" + "─" * 78 + "┘")
        print()

    def print_trading_stats(self, summary: Dict):
        """Print trading statistics section."""
        print("┌─ TRADING STATISTICS " + "─" * 57 + "┐")
        
        trades = summary['trades']
        wins = summary['wins']
        losses = summary['losses']
        
        # Win rate calculation
        if trades > 0:
            win_rate = (wins / trades * 100)
        else:
            win_rate = 0.0
        
        # Trades summary
        print(f"│ Total Trades:        {trades:<30} │")
        print(f"│ Winning Trades:      {wins:<30} │")
        print(f"│ Losing Trades:       {losses:<30} │")
        print(f"│ Win Rate:            {win_rate:.1f}%                                  │")
        
        # P&L summary
        print(f"│ Total Profit:        ${summary['total_profit']:,.2f}                              │")
        print(f"│ Total Loss:          ${summary['total_loss']:,.2f}                              │")
        
        # Risk-reward
        if trades > 0:
            avg_profit = summary['total_profit'] / max(wins, 1)
            avg_loss = summary['total_loss'] / max(losses, 1)
            if avg_loss > 0:
                risk_reward_ratio = avg_profit / avg_loss
            else:
                risk_reward_ratio = 0.0
        else:
            avg_profit = 0.0
            avg_loss = 0.0
            risk_reward_ratio = 0.0
        
        print(f"│ Avg Profit/Trade:    ${avg_profit:,.2f}                              │")
        print(f"│ Avg Loss/Trade:      ${avg_loss:,.2f}                              │")
        print(f"│ Risk/Reward Ratio:   {risk_reward_ratio:.2f}                                 │")
        
        print("└" + "─" * 78 + "┘")
        print()

    def print_risk_metrics(self, summary: Dict):
        """Print risk management metrics."""
        print("┌─ RISK MANAGEMENT " + "─" * 60 + "┐")
        
        # Daily limits
        trades_remaining = self.strategy.MAX_TRADES_PER_DAY - summary['trades']
        losses_remaining = max(0, int(self.strategy.DAILY_LOSS_LIMIT / self.strategy.RISK_PER_TRADE) - 
                               max(summary['losses'], 0))
        
        print(f"│ Trades Remaining:    {trades_remaining} / {self.strategy.MAX_TRADES_PER_DAY}                                 │")
        print(f"│ Losses Allowed:      {losses_remaining} before circuit breaker                   │")
        print(f"│ Risk Per Trade:      ${self.strategy.RISK_PER_TRADE:.2f}                                │")
        print(f"│ Daily Loss Limit:    ${self.strategy.DAILY_LOSS_LIMIT:.2f}                              │")
        
        # Current metrics
        if summary['trades'] > 0:
            drawdown = (summary['total_loss'] / self.strategy.ACCOUNT_SIZE) * 100
        else:
            drawdown = 0.0
        
        print(f"│ Current Drawdown:    {drawdown:.2f}%                                 │")
        
        print("└" + "─" * 78 + "┘")
        print()

    def print_status_indicators(self, summary: Dict):
        """Print colored status indicators."""
        print("┌─ STATUS INDICATORS " + "─" * 58 + "┐")
        
        # Determine status colors
        win_rate = (summary['wins'] / max(summary['trades'], 1) * 100) if summary['trades'] > 0 else 0
        
        # Win rate indicator
        if win_rate >= 60:
            rate_indicator = "✅ EXCELLENT (60%+)"
        elif win_rate >= 50:
            rate_indicator = "🟡 GOOD (50-60%)"
        elif win_rate >= 40:
            rate_indicator = "🔴 POOR (40-50%)"
        else:
            rate_indicator = "❌ CRITICAL (<40%)"
        
        # Account health
        balance = summary['account_balance']
        if balance >= self.strategy.ACCOUNT_SIZE:
            health_indicator = "✅ GROWING"
        elif balance >= self.strategy.ACCOUNT_SIZE * 0.95:
            health_indicator = "🟡 STABLE"
        elif balance >= self.strategy.ACCOUNT_SIZE * 0.80:
            health_indicator = "🔴 DECLINING"
        else:
            health_indicator = "❌ CRITICAL"
        
        # Circuit breaker
        breaker_indicator = "🔴 ACTIVE" if summary['circuit_breaker_active'] else "✅ READY"
        
        print(f"│ Win Rate Status:     {rate_indicator:<32} │")
        print(f"│ Account Health:      {health_indicator:<32} │")
        print(f"│ Circuit Breaker:     {breaker_indicator:<32} │")
        
        # Recommendation
        if summary['circuit_breaker_active']:
            recommendation = "⏹️  NO TRADING - Wait for next day"
        elif win_rate < 40 and summary['trades'] > 5:
            recommendation = "⚠️  REVIEW STRATEGY - Win rate low"
        elif balance < self.strategy.ACCOUNT_SIZE * 0.80:
            recommendation = "⚠️  REDUCE SIZE - Preserve capital"
        elif win_rate >= 55:
            recommendation = "✅ OPTIMAL - Continue strategy"
        else:
            recommendation = "🟡 MONITOR - Track performance"
        
        print(f"│ Recommendation:      {recommendation:<32} │")
        
        print("└" + "─" * 78 + "┘")
        print()

    def print_alerts(self, summary: Dict):
        """Print any active alerts."""
        alerts = []
        
        # Check for critical conditions
        if summary['circuit_breaker_active']:
            alerts.append("🛑 CIRCUIT BREAKER ACTIVE - Daily loss limit reached")
        
        if summary['account_balance'] < self.strategy.ACCOUNT_SIZE * 0.80:
            alerts.append("⚠️  ACCOUNT CRITICAL - Balance below 80% of starting")
        
        if summary['trades'] > 0:
            win_rate = summary['wins'] / summary['trades']
            if win_rate < 0.40 and summary['trades'] >= 5:
                alerts.append("⚠️  LOW WIN RATE - Below 40% threshold")
        
        if alerts:
            print("┌─ ALERTS " + "─" * 68 + "┐")
            for alert in alerts:
                print(f"│ {alert:<76} │")
            print("└" + "─" * 78 + "┘")
            print()

    def print_footer(self):
        """Print dashboard footer."""
        print("┌" + "─" * 78 + "┐")
        print(f"│ Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S'):<60} │")
        print(f"│ Refresh Interval: {self.update_interval}s | Press Ctrl+C to exit                         │")
        print("└" + "─" * 78 + "┘")

    def run(self):
        """Run dashboard in refresh loop."""
        try:
            print("Starting monitoring dashboard...")
            time.sleep(2)
            
            while True:
                self.clear_screen()
                
                # Get current summary
                summary = self.strategy.get_daily_summary()
                
                # Print sections
                self.print_header()
                self.print_account_status(summary)
                self.print_trading_stats(summary)
                self.print_risk_metrics(summary)
                self.print_status_indicators(summary)
                self.print_alerts(summary)
                self.print_footer()
                
                # Wait for next update
                time.sleep(self.update_interval)
                
        except KeyboardInterrupt:
            print("\n\nDashboard stopped by user.")
            
            # Print final summary
            print("\n" + "=" * 80)
            print("FINAL SUMMARY")
            print("=" * 80)
            
            summary = self.strategy.get_daily_summary()
            print(f"Final Balance: ${summary['account_balance']:,.2f}")
            print(f"Total P&L: ${summary['net_pnl']:+,.2f}")
            print(f"Win Rate: {summary['wins']}/{summary['trades']} = "
                  f"{100*summary['wins']/max(summary['trades'],1):.1f}%")
            
            # Save trade history
            self.strategy.save_trade_history()
            print("\nTrade history saved.")


def main():
    """Entry point."""
    dashboard = StrategyMonitorDashboard(update_interval=5)
    dashboard.run()


if __name__ == "__main__":
    main()
