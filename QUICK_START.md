# QUICK START GUIDE — Strategic Trading System

**Status:** ✅ Ready to Use  
**Date:** May 27, 2026  
**Account:** $10,000 | Daily Loss Limit: $2,000

---

## 📋 WHAT YOU NOW HAVE

### **4-Agent Trading Framework (A1-A4)**

```
🎯 A1: ASSET SELECTION (4x/day)
├─ 9:00 AM   → EUR/USD
├─ 11:30 AM  → GBP/USD
├─ 2:00 PM   → USD/JPY
└─ 4:00 PM   → Rotation

📊 A2: STRATEGY PLANNING
├─ Confidence check: 60-65%
├─ Volatility check: <2-5%
├─ Trend analysis
└─ Position sizing: $200 per trade

⏱️  A3: TIME EXECUTION
├─ 5-minute hold per trade
├─ Entry/Exit management
├─ Stop loss: 2%
└─ Take profit: 1.5-2%

🤖 A4: REINFORCEMENT LEARNING
├─ Win/Loss tracking
├─ Strategy optimization
├─ Daily statistics
└─ Model improvement
```

---

## 🚀 QUICK START (5 MINUTES)

### **Step 1: Verify Installation**
```bash
cd /home/akill-sud/Desktop/tg_algo_enhanced./tg_algo

# Check files exist
ls agents/agent_strategic_framework.py
ls integration_strategic_trading.py
ls STRATEGY_GUIDE.md
```

### **Step 2: Test Framework**
```bash
python3 << 'EOF'
from agents.agent_strategic_framework import StrategicFrameworkAgent

agent = StrategicFrameworkAgent()
print(f"✓ Account: ${agent.ACCOUNT_SIZE}")
print(f"✓ Daily Limit: ${agent.DAILY_LOSS_LIMIT}")
print(f"✓ Risk/Trade: ${agent.RISK_PER_TRADE}")
EOF
```

**Expected:** See account configuration printed

### **Step 3: Run Demo**
```bash
python3 integration_strategic_trading.py
```

**Expected:** Trades execute in simulation, stats update in real-time

### **Step 4: Monitor Dashboard**
```bash
python3 monitor_strategy_dashboard.py
```

**Expected:** Live dashboard shows trades, P&L, metrics

---

## 🎮 RUNNING THE SYSTEM

### **Mode 1: Full Integration (with existing system)**
```bash
# Start main orchestrator (includes new framework)
python3 multi_agent_orchestrator.py
```

### **Mode 2: Framework Only (demo/testing)**
```bash
# Start just the strategic framework
python3 integration_strategic_trading.py
```

### **Mode 3: Monitor Only**
```bash
# View live dashboard (run in separate terminal)
python3 monitor_strategy_dashboard.py
```

---

## 💰 POSITION SIZING QUICK MATH

```
Account:       $10,000
Risk/Trade:    $200 (2% of account)
Daily Limit:   $2,000 (10 losses max)
Max Trades:    20/day
Hold Time:     5 minutes per trade
```

**Example Trade:**
- Entry: EUR/USD @ 1.0850
- Stop Loss: 2% = 1.0633
- Take Profit: 1.5% = 1.0866
- Risk: $200

---

## 📊 UNDERSTANDING THE DASHBOARD

```
╔══════════════════════════════════════╗
║     TRADING DASHBOARD               ║
╠══════════════════════════════════════╣
║ Account Balance:   $10,200           ║  ← Running balance
║ Daily P&L:        +$200              ║  ← Today's profit
║ Win Rate:         66.7% (2/3)        ║  ← Wins/Total
║ Circuit Breaker:  ✅ READY           ║  ← Trading allowed
╠══════════════════════════════════════╣
║ Trades Today:      3                 ║
║ Winning:           2                 ║
║ Losing:            1                 ║
║ Risk/Reward:       2.0:1             ║
╠══════════════════════════════════════╣
║ Status: ✅ OPTIMAL - Continue       ║
╚══════════════════════════════════════╝
```

### **Color Codes:**
- 🟢 **Green (✅)**: System ready, trading allowed
- 🟡 **Yellow (⚠️)**: Caution, review needed
- 🔴 **Red (🛑)**: Critical, trading stopped

---

## ⚠️ CIRCUIT BREAKER RULES

**Activates when:**
- Daily loss reaches $2,000
- 10 consecutive losses at $200 each
- Account balance drops to $8,000

**Effect:**
- No new trades allowed
- System goes into "LOCKED" state
- Wait until next trading day

**Example:**
```
Start:    $10,000
After 10 losses: $8,000
Status:   🛑 CIRCUIT BREAKER ACTIVE
Trades:   BLOCKED
Resume:   Next trading day (reset at 9:00 AM)
```

---

## 📈 WHAT TO MONITOR

### **Daily Checklist**

**Morning (before 9:00 AM)**
- [ ] Balance = expected amount
- [ ] Daily stats reset
- [ ] A1 asset selection ready

**Midday (11:00 AM - 3:00 PM)**
- [ ] Trades executing
- [ ] Win rate tracking
- [ ] P&L accumulating

**End of Day (after 5:00 PM)**
- [ ] Daily summary logged
- [ ] Balance updated
- [ ] Trade history saved

### **Key Metrics**

| Metric | Target | Alert |
|--------|--------|-------|
| Win Rate | 50-55% | <40% = STOP |
| Daily P&L | +$500+ | -$1,500 = REDUCE |
| Account | Growing | -20% = CRITICAL |
| Circuit Breaker | Ready | Active = PAUSE |

---

## 🔧 ADJUSTING SETTINGS

### **If Winning Too Much (>65% win rate)**
```python
# Make strategy harder
CONFIDENCE_THRESHOLD = 0.70  # Was 0.65
HOLD_TIME_SECONDS = 180      # Was 300 (shorter)
```

### **If Losing Too Much (<40% win rate)**
```python
# Make strategy easier
CONFIDENCE_THRESHOLD = 0.55  # Was 0.65
RISK_PER_TRADE = 150         # Was 200 (smaller)
```

### **If Account Drops Below $8,000**
```python
# Survival mode
RISK_PER_TRADE = 100         # Reduced 50%
ACCOUNT_SIZE = 8000          # Update reference
DAILY_LOSS_LIMIT = 1200      # 15% of account
```

---

## 🆘 EMERGENCY PROCEDURES

### **Loss Sequence (Approaching Daily Limit)**

**After 5 losses (-$1,000 lost so far):**
```
⚠️  WARNING: 50% of daily limit used
Action: Reduce next trade size to $150
        Increase hold time to 10 min
```

**After 8 losses (-$1,600 lost so far):**
```
🔴 CRITICAL: 80% of daily limit used
Action: Next loss triggers circuit breaker
        Last $400 daily buffer remaining
```

**After 10 losses (-$2,000 total):**
```
🛑 LOCKED: Daily circuit breaker activated
Action: NO MORE TRADES today
        Resume trading tomorrow at 9:00 AM
```

### **System Recovery**

If balance drops below $8,000:

1. **Stop live trading immediately**
   ```bash
   # Edit .env or settings
   TRADE_MODE=DEMO  # Switch to simulation
   ```

2. **Reduce position size**
   ```python
   RISK_PER_TRADE = 100  # From $200
   ```

3. **Back-test strategy**
   ```bash
   python3 integration_strategic_trading.py  # Demo mode
   ```

4. **Analyze what failed**
   - Check recent trades
   - Review win/loss patterns
   - Identify problematic signals

5. **Paper trade for 3-5 days**
   - Run in DEMO mode only
   - Verify >50% win rate
   - Then return to live with smaller size

---

## 📁 FILE REFERENCE

| File | Purpose |
|------|---------|
| `agents/agent_strategic_framework.py` | Main A1-A4 logic |
| `integration_strategic_trading.py` | Demo/testing runner |
| `monitor_strategy_dashboard.py` | Live performance dashboard |
| `STRATEGY_GUIDE.md` | Complete strategy documentation |
| `IMPLEMENTATION_PLAN.md` | Step-by-step setup guide |
| `config/settings.py` | Configuration parameters |

---

## 🎯 SUCCESS CRITERIA

### **Week 1: Baseline**
- [ ] >50% win rate
- [ ] No circuit breaker triggers
- [ ] P&L: Break-even to +$500

### **Week 2-3: Validation**
- [ ] >55% win rate
- [ ] Daily profits consistent
- [ ] P&L: +$500 to +$1,500 cumulative

### **Week 4+: Production**
- [ ] >55% sustained win rate
- [ ] 5%+ monthly return ($500+)
- [ ] Account growing steadily

---

## ❓ FAQ

**Q: How long before I see profits?**  
A: 3-5 trading days to establish baseline. First week should show pattern.

**Q: What if I lose all $2,000 in first trade?**  
A: Can't happen. Position size fixed at $200 max per trade.

**Q: Can I trade outside 9:00-4:00 PM?**  
A: No. A1 agent only selects assets at those 4 times.

**Q: What if circuit breaker triggers at 2:00 PM?**  
A: Trading stops. Wait until next day's 9:00 AM reset.

**Q: How do I increase to $50k account?**  
A: After 3+ months of 55%+ win rate. Scale gradually.

**Q: Is the 5-minute hold mandatory?**  
A: Yes. A3 exits all trades at 5 minutes or earlier at TP/SL.

**Q: How often should I check the dashboard?**  
A: Every 30-60 minutes during trading hours.

**Q: What if internet disconnects mid-trade?**  
A: Bot will auto-exit at market close or stop loss, whichever first.

---

## 🚨 GOLDEN RULES

1. **Never exceed $200 risk per trade** ← Most important
2. **Never ignore daily $2,000 loss limit** ← Hard stop
3. **Always verify circuit breaker status** ← Before trading
4. **Track every trade** ← Learning depends on it
5. **Let the system work** ← Don't manually override

---

## 📞 GETTING HELP

### **Common Issues:**

- **"Framework not initializing"**  
  → Check Python path, verify imports: `python3 -c "from agents.agent_strategic_framework import StrategicFrameworkAgent; print('OK')"`

- **"Dashboard not displaying"**  
  → Ensure terminal supports UTF-8: `export LANG=en_US.UTF-8`

- **"Trades not executing"**  
  → Check bot connection: `python3 bot/browser.py`

- **"Stats not updating"**  
  → Verify file permissions: `chmod -R 755 logs/`

---

**🎯 You're ready! Start with integration_strategic_trading.py and monitor_strategy_dashboard.py**

**Last Updated:** 2026-05-27  
**Version:** 1.0  
**Status:** ACTIVE
