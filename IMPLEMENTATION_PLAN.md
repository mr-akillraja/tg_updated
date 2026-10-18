# IMPLEMENTATION PLAN: Strategic Trading System

**Date:** May 27, 2026  
**Account:** $10,000  
**Daily Loss Limit:** $2,000  
**Status:** Ready for Implementation

---

## OVERVIEW

You now have a complete strategic trading system based on your whiteboard plan:

```
┌─────────────────────────────────────┐
│    A1: Asset Selection (4x/day)    │
│    9:00 AM, 11:30 AM, 2:00 PM,     │
│    4:00 PM                         │
└────────────┬──────────────────────┘
             │
             ↓
┌─────────────────────────────────────┐
│  A2: Strategy Planning (Confidence  │
│      + Volatility Rules)            │
│  A5: Execution                      │
└────────────┬──────────────────────┘
             │
             ↓
┌─────────────────────────────────────┐
│  A3: Time Execution                 │
│  5-minute holds                     │
│  Entry/Exit management              │
└────────────┬──────────────────────┘
             │
             ↓
┌─────────────────────────────────────┐
│  A4: Reinforcement Learning         │
│  Win/Loss feedback                  │
│  Model improvement                  │
└────────────┬──────────────────────┘
             │
             ↓
    [NEXT CYCLE - Every 5 seconds]
```

---

## FILES CREATED / MODIFIED

### **New Files:**

1. **agents/agent_strategic_framework.py** (Main Strategy Engine)
   - Implements A1-A4 agents
   - Manages daily statistics
   - Enforces risk limits
   - Circuit breaker logic

2. **integration_strategic_trading.py** (Integration Layer)
   - Connects framework with RL agent
   - Runs main trading loop
   - Simulates complete cycle
   - Saves trade history

3. **STRATEGY_GUIDE.md** (Reference Documentation)
   - Complete strategy rules
   - Position sizing formulas
   - Daily limits and circuit breaker
   - Performance tracking metrics

4. **IMPLEMENTATION_PLAN.md** (This file)
   - Step-by-step setup instructions
   - Testing procedures
   - Integration with existing system

---

## STEP-BY-STEP IMPLEMENTATION

### **Phase 1: Testing the Framework (2-4 hours)**

#### Step 1.1: Verify Framework Installation
```bash
cd /home/akill-sud/Desktop/tg_algo_enhanced./tg_algo

# Check if files are created
ls -la agents/agent_strategic_framework.py
ls -la integration_strategic_trading.py
ls -la STRATEGY_GUIDE.md
```

#### Step 1.2: Test Framework Initialization
```bash
python3 -c "
from agents.agent_strategic_framework import StrategicFrameworkAgent
agent = StrategicFrameworkAgent()
print(f'✓ Framework initialized')
print(f'  Account: \${agent.ACCOUNT_SIZE}')
print(f'  Daily limit: \${agent.DAILY_LOSS_LIMIT}')
print(f'  Risk per trade: \${agent.RISK_PER_TRADE}')
"
```

**Expected output:**
```
✓ Framework initialized
  Account: $10000.0
  Daily limit: $2000.0
  Risk per trade: $200.0
```

#### Step 1.3: Test A1 Asset Selection
```bash
python3 -c "
from agents.agent_strategic_framework import StrategicFrameworkAgent
from datetime import datetime
import logging
logging.basicConfig(level=logging.INFO)

agent = StrategicFrameworkAgent()

# Mock current time to 9:00 AM
print('[Test A1] Asset Selection at scheduled times:')
for window in agent.SELECTION_WINDOWS:
    print(f'  - {window[\"name\"]}: {window[\"hour\"]:02d}:{window[\"minute\"]:02d}')
"
```

#### Step 1.4: Test A2 Strategy Planning
```bash
python3 << 'EOF'
from agents.agent_strategic_framework import StrategicFrameworkAgent
import logging
logging.basicConfig(level=logging.INFO)

agent = StrategicFrameworkAgent()

# Test strategy generation
strategy = agent.agent_a2_strategy_planning(
    asset='EUR/USD',
    current_price=1.0850,
    trend='UPTREND',
    volatility=0.02,
    confidence=0.70
)

print(f"✓ Strategy Generated:")
print(f"  Action: {strategy['action']}")
print(f"  Direction: {strategy['direction']}")
print(f"  Risk Amount: \${strategy['risk_amount']}")
print(f"  Take Profit: {strategy['take_profit_percent']:.1%}")
print(f"  Stop Loss: {strategy['stop_loss_percent']:.1%}")
EOF
```

#### Step 1.5: Test A3-A4 Trade Cycle
```bash
python3 << 'EOF'
from agents.agent_strategic_framework import StrategicFrameworkAgent, TradeExecution
from datetime import datetime
import logging
logging.basicConfig(level=logging.INFO)

agent = StrategicFrameworkAgent()

# Create test strategy
strategy = {
    'action': 'TRADE',
    'direction': 'UP',
    'entry_price': 1.0850,
    'risk_amount': 200,
    'take_profit_percent': 1.5,
    'stop_loss_percent': 2.0,
    'hold_time': 300,
    'confidence': 0.70,
    'strategy_name': 'TEST'
}

# A3: Execute trade
trade = agent.agent_a3_time_execution(strategy, 1.0850)
print(f"✓ Trade Executed: {trade.direction} @ {trade.entry_price}")

# A4: Simulate exit and feedback
final_price = 1.0866  # Win scenario
result = agent.agent_a4_rl_feedback(trade, final_price)
print(f"✓ Trade Result: {trade.profit_loss} | P&L: \${trade.pnl:+.2f}")

# Check daily stats
summary = agent.get_daily_summary()
print(f"✓ Daily Summary:")
print(f"  Trades: {summary['trades']}")
print(f"  Net P&L: \${summary['net_pnl']:.2f}")
print(f"  Balance: \${summary['account_balance']:.2f}")
EOF
```

### **Phase 2: Integration with Existing System (4-6 hours)**

#### Step 2.1: Update settings.py
```python
# Add to config/settings.py

# Strategic Framework Settings
ACCOUNT_SIZE = float(os.getenv("ACCOUNT_SIZE", "10000.0"))
DAILY_LOSS_LIMIT = float(os.getenv("DAILY_LOSS_LIMIT", "2000.0"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "200.0"))
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "20"))

# Risk Management
HOLD_TIME_SECONDS = int(os.getenv("HOLD_TIME_SECONDS", "300"))  # 5 minutes
MIN_TIME_BETWEEN_TRADES = int(os.getenv("MIN_TIME_BETWEEN_TRADES", "30"))  # 30 seconds
```

#### Step 2.2: Connect to Multi-Agent Orchestrator
```python
# Update multi_agent_orchestrator.py to use framework

from agents.agent_strategic_framework import StrategicFrameworkAgent

class MultiAgentTradingSystem:
    def __init__(self):
        # ... existing code ...
        self.strategy_framework = StrategicFrameworkAgent()
        
    async def _evaluate_and_trade(self, current_price: float):
        # ... existing agent1/agent2 code ...
        
        # Add framework checks
        daily_summary = self.strategy_framework.get_daily_summary()
        
        if daily_summary['circuit_breaker_active']:
            log.warning("Circuit breaker active - no new trades")
            return
            
        # Verify position size
        if daily_summary['trades'] >= self.strategy_framework.MAX_TRADES_PER_DAY:
            log.warning("Max trades per day reached")
            return
```

#### Step 2.3: Test Integration
```bash
# Run both systems in parallel (in separate terminals)

# Terminal 1: Legacy system
python3 multi_agent_orchestrator.py

# Terminal 2: New framework (after 30 seconds)
python3 integration_strategic_trading.py
```

### **Phase 3: Live Trading Setup (2-3 hours)**

#### Step 3.1: Create Environment Variables
```bash
# Create .env file in project root
cat > .env << 'EOF'
ACCOUNT_SIZE=10000.0
DAILY_LOSS_LIMIT=2000.0
RISK_PER_TRADE=200.0
MAX_TRADES_PER_DAY=20
HOLD_TIME_SECONDS=300
MIN_TIME_BETWEEN_TRADES=30
TRADE_MODE=LIVE  # or DEMO for testing
EOF
```

#### Step 3.2: Update Bot Connection
```python
# In bot/browser.py or similar

class OlymtradeBot:
    async def execute_trade(self, direction, amount, duration):
        """Execute actual trade with proper position sizing."""
        # Enforce risk limits
        if amount > settings.RISK_PER_TRADE:
            log.warning(f"Position size {amount} exceeds limit {settings.RISK_PER_TRADE}")
            amount = settings.RISK_PER_TRADE
        
        # Execute with framework validation
        return await self._send_trade_request(direction, amount, duration)
```

#### Step 3.3: Add Daily Reset Logic
```python
# In multi_agent_orchestrator.py

async def _daily_reset_check(self):
    """Check if we need to reset daily stats."""
    while True:
        now = datetime.now()
        
        # Reset at 8:59 PM (before market close)
        if now.hour == 20 and now.minute == 59:
            self.strategy_framework.reset_daily_stats()
            log.info("Daily stats reset for new trading day")
            
        await asyncio.sleep(60)
```

---

## TESTING SCENARIOS

### **Test 1: Winning Trade Sequence**
```
Initial Balance: $10,000
Trade 1: BUY @ 1.0850 → SELL @ 1.0866 → WIN +$200 (Balance: $10,200)
Trade 2: SELL @ 1.0866 → BUY @ 1.0851 → WIN +$200 (Balance: $10,400)
Trade 3: BUY @ 1.0851 → SELL @ 1.0867 → WIN +$200 (Balance: $10,600)

Expected: Win rate 100%, daily P&L +$600
```

### **Test 2: Losing Trade Sequence**
```
Initial Balance: $10,000
Trade 1: BUY @ 1.0850 → SELL @ 1.0833 → LOSS -$200 (Balance: $9,800)
Trade 2: SELL @ 1.0833 → BUY @ 1.0850 → LOSS -$200 (Balance: $9,600)
...
Trade 10: [After 10 losses = -$2,000]

Expected: Circuit breaker triggers, trading stops for day
Final Balance: $8,000
```

### **Test 3: Mixed Results**
```
Initial Balance: $10,000
Trade 1: WIN +$200 (Balance: $10,200)
Trade 2: WIN +$200 (Balance: $10,400)
Trade 3: LOSS -$200 (Balance: $10,200)
Trade 4: WIN +$200 (Balance: $10,400)
Trade 5: LOSS -$200 (Balance: $10,200)

Expected: 60% win rate, daily P&L +$200
```

### **Test 4: Circuit Breaker Activation**
```
Initial Balance: $10,000
After 9 losses: Balance $8,200, Total Loss $1,800
Trade 10: LOSS -$200 → Total Loss $2,000

Expected: Circuit breaker active, no more trades
Final Balance: $8,000
Status: LOCKED until next day
```

---

## DAILY OPERATIONS CHECKLIST

### **Before Market Open (7:00 AM)**
- [ ] Check bot is connected
- [ ] Verify account balance = expected
- [ ] Check that daily stats are reset
- [ ] Verify settings loaded correctly

```bash
python3 << 'EOF'
from agents.agent_strategic_framework import StrategicFrameworkAgent
agent = StrategicFrameworkAgent()
print(f"✓ Daily Reset Check:")
print(f"  Date: {agent.daily_stats.date}")
print(f"  Trades: {agent.daily_stats.trades_executed}")
print(f"  Balance: ${agent.current_account_balance}")
EOF
```

### **During Trading (9:00 AM - 5:00 PM)**
- [ ] Monitor first A1 selection (9:00 AM)
- [ ] Verify A2 strategy signals
- [ ] Check A3 trade execution timing
- [ ] Monitor A4 learning updates
- [ ] Track daily P&L in real-time

```bash
# Monitor logs in real-time
tail -f logs/multi_agent_system.log | grep -E "\[A[1-4]\]|\[Circuit\]|\[Summary\]"
```

### **After Market Close (5:30 PM)**
- [ ] Review daily summary
- [ ] Check win rate and P&L
- [ ] Verify circuit breaker status
- [ ] Save trade history

```bash
python3 << 'EOF'
from agents.agent_strategic_framework import StrategicFrameworkAgent
import json

agent = StrategicFrameworkAgent()
agent.save_trade_history("logs/strategy_trades.json")

# Display summary
summary = agent.get_daily_summary()
print(f"Daily Summary:")
print(json.dumps(summary, indent=2, default=str))
EOF
```

---

## PERFORMANCE TARGETS

### **Week 1: Baseline Testing**
- Goal: 50% win rate, break-even or +$500 total
- Max daily loss: $2,000 (1 circuit breaker acceptable)
- Monitor: Signal quality, execution timing

### **Week 2-3: Optimization**
- Goal: 55% win rate, +$1,500 cumulative
- Adjust: Confidence thresholds if needed
- Monitor: Win rate by strategy type

### **Week 4+: Production**
- Goal: 55%+ win rate, +$2,000/week minimum
- Target: 5% monthly return ($500/month)
- Monitor: Drawdown, Sharpe ratio

---

## TROUBLESHOOTING

### **Issue: Circuit Breaker Triggered Too Early**

**Symptom:** Daily loss limit hit before end of day

**Solution:**
1. Reduce risk per trade: $200 → $150
2. Increase confidence threshold: 60% → 65%
3. Review recent loss trades - pattern analysis

```python
# Adjust in settings or environment
RISK_PER_TRADE = 150.0  # Reduced from 200
CONFIDENCE_THRESHOLD = 0.65  # Increased from 0.60
```

### **Issue: Too Few Trades Generated**

**Symptom:** Only 2-3 trades per day, expected 8-10

**Solution:**
1. Lower confidence requirement: 65% → 60%
2. Check A1 selection is working at scheduled times
3. Verify market data is flowing correctly

### **Issue: Win Rate < 40%**

**Symptom:** Losing more trades than winning

**Solution:**
1. Review A2 strategy rules (too loose?)
2. Increase hold time from 5 min to 10 min
3. Increase take profit from 1.5% to 2.5%

### **Issue: Balance Declining Below $8,000**

**Symptom:** Account underwater, need recovery

**Solution:**
1. **Reduce position size:** $200 → $100
2. **Pause live trading:** Switch to DEMO mode
3. **Back-test strategy:** Identify broken rules
4. **Paper trade:** Run simulation before returning to live

---

## NEXT STEPS

1. **Today:** Run Phase 1 tests (2-4 hours)
2. **Tomorrow:** Integrate with existing system (4-6 hours)
3. **This Week:** Paper trading in DEMO mode (4-5 days)
4. **Next Week:** Go live with reduced position size if > 50% win rate

---

## SUPPORT & REFERENCES

- Main Strategy: [STRATEGY_GUIDE.md](STRATEGY_GUIDE.md)
- Framework Code: [agents/agent_strategic_framework.py](agents/agent_strategic_framework.py)
- Integration Code: [integration_strategic_trading.py](integration_strategic_trading.py)
- RL Agent: [agents/agent_rl_learning.py](agents/agent_rl_learning.py)

---

**Status: READY FOR IMPLEMENTATION**

**Last Updated:** 2026-05-27
**Version:** 1.0
