# TRADING STRATEGY GUIDE — $10,000 Account with $2,000 Daily Loss Management

## EXECUTIVE SUMMARY

**Account Setup:**
- Starting Capital: $10,000
- Daily Loss Limit: $2,000 (20% of account)
- Risk Per Trade: $200 (2% of account)
- Max Trades Per Day: 20 trades
- Max Consecutive Losses Before Limit: 10 losses = $2,000

**Key Rule:** Once daily loss hits $2,000, trading stops for the day (circuit breaker).

---

## STRATEGIC FRAMEWORK (Whiteboard Plan)

```
┌─────────────────────────────────────────────────────────────┐
│                    4-AGENT TRADING PLAN                    │
└─────────────────────────────────────────────────────────────┘

         A1: ASSET SELECTION          
         4x Per Day Opening
         ↓
         9:00 AM    → EUR/USD
         11:30 AM   → GBP/USD  
         2:00 PM    → USD/JPY
         4:00 PM    → (Rotate)
         ↓
         
    ┌────────────────────────────────────────┐
    │  A2: STRATEGY PLANNING    A5: EXECUTION│
    │  • Trend Analysis                       │
    │  • Volatility Check                     │
    │  • Confidence Scoring                   │
    │  • Entry/Exit Rules                     │
    │  • Risk Sizing                          │
    │  • Position Management                  │
    └────────────────────────────────────────┘
         ↓
    A3: TIME EXECUTION
    • 5-minute hold time
    • Entry at signal
    • Exit on TP/SL
    • Track execution
         ↓
    A4: REINFORCEMENT LEARNING
    • Trade outcome evaluation
    • Win/Loss feedback
    • Model improvement
    • Daily limit enforcement
         ↓
    NEXT CYCLE (Every 5-60 seconds)
```

---

## DETAILED AGENT DESCRIPTIONS

### **A1: ASSET SELECTION (4x Daily)**

**Schedule:**
```
9:00 AM    → Morning Session   (EUR/USD)
11:30 AM   → Mid-Morning       (GBP/USD)
2:00 PM    → Afternoon         (USD/JPY)
4:00 PM    → Late Afternoon    (Rotate)
```

**What happens:**
- Charts automatically open
- Asset selected based on rotation
- 30-min analysis window per session
- New trading pairs prevent over-focusing on one asset

---

### **A2: STRATEGY PLANNING**

**Entry Signal Decision Tree:**

```
IF (Confidence >= 65%) AND (Volatility < 3%) THEN
   IF Trend = UPTREND → Trade UP (2% profit target)
   IF Trend = DOWNTREND → Trade DOWN (2% profit target)

ELSE IF (Confidence >= 60%) AND (Volatility < 5%) THEN
   IF Trend = UPTREND → Trade UP (1.5% profit target)
   IF Trend = DOWNTREND → Trade DOWN (1.5% profit target)

ELSE
   → HOLD (Wait for next signal)
```

**Risk Management Rules:**
```
Position Size: $200 per trade (Fixed)
Take Profit: 1.5% - 2.0% from entry
Stop Loss: 2.0% from entry
Hold Time: 5 minutes maximum
Time Between Trades: 30 seconds minimum
```

**Daily Circuit Breaker:**
```
IF Daily Loss >= $2,000 THEN
   Close all open trades
   Block new entries
   Log event
   Wait for next trading day
```

---

### **A3: TIME EXECUTION**

**Execution Flow:**

1. **Entry Phase (0-30 seconds after signal):**
   - Confirm price at entry level
   - Execute trade immediately
   - Log entry time and price

2. **Holding Phase (30 seconds - 5 minutes):**
   - Monitor price movement
   - Check profit targets every 30 seconds
   - Check stop loss every 30 seconds
   - Track time elapsed

3. **Exit Phase (5 minutes or earlier if TP/SL hit):**
   - Automatic exit at take profit
   - Automatic exit at stop loss
   - Force exit if 5 minutes elapsed
   - Record final price and P&L

**Exit Priority:**
1. Stop Loss (most important)
2. Take Profit
3. Time-based exit (5 minutes)

---

### **A4: REINFORCEMENT LEARNING**

**Learning Loop:**

1. **Trade Outcome Evaluation:**
   ```
   IF Exit Price > Entry Price THEN
      Reward = +1 (WIN)
      Daily Profit += Amount
   ELSE IF Exit Price < Entry Price THEN
      Reward = -1 (LOSS)
      Daily Loss += Amount
   ELSE
      Reward = 0 (BREAK EVEN)
   ```

2. **Model Updates:**
   - Track winning strategy combinations
   - Increase weight for high-confidence signals that win
   - Reduce weight for high-confidence signals that lose
   - Adjust volatility thresholds based on outcomes

3. **Daily Statistics:**
   ```
   Trades Executed: Count
   Winning Trades: Count
   Losing Trades: Count
   Total Profit: Sum
   Total Loss: Sum
   Net P&L: Profit - Loss
   Win Rate: Wins / Total Trades
   Max Consecutive Losses: Track for streaks
   ```

4. **Consecutive Loss Tracking:**
   ```
   After 1st loss:  -$200  (9 losses remaining)
   After 5th loss:  -$1000 (5 losses remaining)
   After 10th loss: -$2000 (CIRCUIT BREAKER TRIGGERED)
   ```

---

## TRADING DAY EXAMPLE

**Scenario: $10,000 account, $2,000 daily limit, $200 per trade**

```
TIME         | ACTION                      | P&L       | BALANCE   | STATUS
─────────────┼─────────────────────────────┼───────────┼───────────┼──────────
9:00 AM      | A1 Selection: EUR/USD       | -         | $10,000   | Ready
9:05 AM      | A2 Plans entry (UP signal)  | -         | $10,000   | 
9:06 AM      | A3 Executes BUY @ 1.0800    | -         | $10,000   | 
9:11 AM      | A4 Closes @ 1.0816 (+1.5%) | +$200     | $10,200   | WIN ✓
────────────┼─────────────────────────────┼───────────┼───────────┼──────────
9:12 AM      | A2 Plans entry (DOWN signal)| -         | $10,200   | 
9:13 AM      | A3 Executes SELL @ 1.0816  | -         | $10,200   | 
9:18 AM      | A4 Closes @ 1.0796 (+1.5%) | +$200     | $10,400   | WIN ✓
────────────┼─────────────────────────────┼───────────┼───────────┼──────────
9:20 AM      | A2 Plans entry (HIGH VOL)  | -         | $10,400   | 
9:21 AM      | A3 Executes SELL @ 1.0810  | -         | $10,400   | 
9:26 AM      | A4 Closes @ 1.0850 (-2.0%) | -$200     | $10,200   | LOSS ✗
────────────┼─────────────────────────────┼───────────┼───────────┼──────────
11:30 AM     | A1 Selection: GBP/USD       | -         | $10,200   | Ready
...          | Continue trading cycle      | -         | -         | -
────────────┼─────────────────────────────┼───────────┼───────────┼──────────
AFTER 10     | Daily Loss = $2,000         | -$2,000   | $8,000    | 
LOSSES       | CIRCUIT BREAKER ACTIVE      | -         | LOCKED    | ⛔ NO MORE TRADES
```

---

## RISK MANAGEMENT RULES

### Position Sizing Formula:
```
Risk Per Trade = Account Size × Risk Percentage
                = $10,000 × 2%
                = $200

Position Size = Risk Amount / Stop Loss %
              = $200 / 2%
              = $10,000 units (contracts)
```

### Daily Limits:
```
Maximum Daily Loss: $2,000 (20% of account)
Minimum Daily Loss Before Stopping: 10 consecutive losses × $200

Daily Loss Tracker:
├─ Losses 1-3:  -$600   (7 losses left)
├─ Losses 4-6:  -$1,200 (4 losses left)
├─ Losses 7-9:  -$1,800 (1 loss left)
└─ Loss 10:     -$2,000 (STOP - No more trading today)
```

### Profit Taking Rules:
```
Per Trade TP: 1.5% - 2.0%
Per Trade SL: 2.0%

Daily Profit Target: Unlimited
Daily Loss Limit: $2,000 hard stop

If Daily Profit > $1,000: Consider reducing trade size
If Daily Loss > $1,500: Prepare for circuit breaker
```

---

## KEY SUCCESS FACTORS

✅ **DO:**
1. Execute trades only during scheduled A1 selection windows
2. Follow A2 strategy rules (confidence + volatility checks)
3. Respect $200 position size strictly
4. Close all trades at 5-minute mark
5. Track daily loss limit religiously
6. Log all trades for A4 learning

❌ **DON'T:**
1. Deviate from $200 position size
2. Hold trades longer than 5 minutes
3. Ignore the $2,000 daily loss limit
4. Trade outside A1 selection windows
5. Over-leverage or size up
6. Revenge trade after losses

---

## ACCOUNT RECOVERY STRATEGY

**If account drops below $8,000:**

1. **Reduce Position Size:** $200 → $150 per trade
2. **Lower Confidence Threshold:** 65% → 60%
3. **Fewer Trades:** Focus on high-quality signals only
4. **Increase Profit Target:** 1.5% → 2.5% (wait for better setups)

**If account recovers to $12,000:**

1. **Return to Base Settings:** $200 per trade
2. **Increase Daily Limit:** $2,000 → $2,500
3. **Consider A/B Testing:** Try new strategies on 10% of account

---

## PERFORMANCE METRICS TO TRACK

**Daily Metrics:**
```
├─ Total Trades: Count
├─ Winning Trades: Count  
├─ Losing Trades: Count
├─ Win Rate: % of winning trades
├─ Largest Win: $ amount
├─ Largest Loss: $ amount
├─ Average Win Size: $
├─ Average Loss Size: $
└─ Profit Factor: Total Profit / Total Loss
```

**Weekly Metrics:**
```
├─ Total P&L: $
├─ Best Day: $ + date
├─ Worst Day: $ + date
├─ Consecutive Winning Days: #
├─ Consecutive Losing Days: #
└─ Consistency Score: Profit days / Total days
```

**Monthly Metrics:**
```
├─ ROI: % return on $10,000
├─ Win Rate: % overall
├─ Sharpe Ratio: Risk-adjusted return
├─ Max Drawdown: Largest loss from peak
└─ Strategy Performance: Which A2 strategies worked best
```

---

## IMPLEMENTATION CHECKLIST

- [ ] Configure account size in settings.py: $10,000
- [ ] Set daily loss limit: $2,000
- [ ] Set risk per trade: $200
- [ ] Configure A1 selection times: 9:00, 11:30, 2:00, 4:00 PM
- [ ] Define A2 confidence thresholds: 60-65%
- [ ] Set A3 hold time: 5 minutes
- [ ] Enable A4 learning feedback
- [ ] Enable daily circuit breaker at $2,000 loss
- [ ] Log all trades to file
- [ ] Test on 1-2 days before live trading
- [ ] Monitor win rate and adjust if < 40%
- [ ] Prepare recovery strategy if balance drops to $8,000

---

## MONTHLY GOALS

**Conservative Target:** 5% return = +$500/month
- ~20 winning trades at $200 profit each
- ~20 losing trades at -$200 each
- Net: +$500 with 50% win rate

**Target Win Rate:** 50-55%
- Below 40%: Review strategy and reduce size
- Above 60%: Monitor for over-optimization

**Risk Tolerance:** Maximum 20% loss per month ($2,000)
- If account hits $8,000: Reduce to $150 per trade

---

**Last Updated:** 2026-05-27
**Strategy Version:** 1.0
**Status:** Active
