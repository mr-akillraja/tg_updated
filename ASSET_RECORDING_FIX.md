# Asset Recording Fix - Complete Code Review & Flow Documentation

## Problem Summary
**Issue**: Logs showed assets like `USDJPY-OTC` being traded, but trade history/journal showed different assets like `USD/CAD-OTC` being recorded.

**Root Cause**: Supervisor was using `self.a1.asset` (MultiTimeframeAgent's asset) for trade placement instead of `self._current_asset` (the asset selected by AssetSelector and synchronized from orchestrator).

**Impact**: Trades were executed on one asset but recorded as a different asset, causing:
- Trade journal mismatch between logged trades and recorded trades  
- AssetSelector win-rate calculations based on mismatched asset names
- Loss-recovery logic unable to properly track consecutive losses per asset

---

## Fix Applied

### File: `agents/agent_pipeline_supervisor.py` - Lines 380, 385

**BEFORE** (Incorrect - uses wrong asset):
```python
# Line 380 (in safe_execute_trade method)
if mode == "ftt":
    result = await asyncio.wait_for(
        self.bot.place_ftt_trade(signal, settings.FTT_DURATION, asset=self.a1.asset),  # ❌ WRONG
        timeout=self.TRADE_TIMEOUT_S,
    )
else:
    # Line 385
    result = await asyncio.wait_for(
        self.bot.place_forex_trade(signal, asset=self.a1.asset),  # ❌ WRONG
        timeout=self.TRADE_TIMEOUT_S,
    )
```

**AFTER** (Correct - uses synchronized asset):
```python
# Line 380 (in safe_execute_trade method)
if mode == "ftt":
    result = await asyncio.wait_for(
        self.bot.place_ftt_trade(signal, settings.FTT_DURATION, asset=self._current_asset),  # ✅ CORRECT
        timeout=self.TRADE_TIMEOUT_S,
    )
else:
    # Line 385
    result = await asyncio.wait_for(
        self.bot.place_forex_trade(signal, asset=self._current_asset),  # ✅ CORRECT
        timeout=self.TRADE_TIMEOUT_S,
    )
```

---

## Complete Asset Flow (After Fix)

### Phase 1: Setup (Lines 295-320 in orchestrator)
```
1. orchestrator._current_asset = await supervisor.ensure_best_asset(orchestrator._current_asset)
   ↓ Gets best asset from AssetSelector based on historical win rates

2. supervisor._current_asset = orchestrator._current_asset
   ↓ Synchronizes supervisor's asset with orchestrator's decision

3. await bot.select_asset(orchestrator._current_asset)
   ↓ Opens the asset chart in the browser (calls select_asset → _asset_display_name logic)

4. agent1.seed_from_dataframe(df for current asset)
   ↓ Initializes timeframe agent with proper asset's candle data
```

### Phase 2: Main Trading Loop (Every tick - Lines 425-500 in orchestrator)
```
1. t0 - self._last_asset_check_ts >= ASSET_CHECK_EVERY
   ↓ Check if it's time to consider asset rotation (default: every 5 min)

2. IF time to check:
   a. new = await supervisor.ensure_best_asset(current_asset)
      ↓ Score all assets, consider rotation timer, pick best

   b. Normalize assets using _asset_to_url_slug() to avoid format mismatches
      ↓ USDCAD-OTC and USD/CAD-OTC both normalize to same slug

   c. IF asset changed:
      - orchestrator._current_asset = new
      - supervisor._current_asset = new
      - agent1.reset(new)
      - await bot.select_asset(new)
        ↓ Opens new asset chart in browser

3. Get current market price for CURRENT asset
   ↓ orchestrator._current_asset

4. Fetch indicators for CURRENT asset
   ↓ agent1 has asset as instance var, generates signals

5. Agent decision: determine signal (BUY/SELL/HOLD)

6. Entry validation: verify 5-bar movement for signal direction

7. Risk approval: check daily limits, confidence thresholds

8. Hedging check: look for opposite trades on CURRENT asset

9. EXECUTE TRADE:
   success = await supervisor.safe_execute_trade(signal, confidence, mode)
   
   Inside safe_execute_trade():
   - Uses self._current_asset (SYNCHRONIZED FROM ORCHESTRATOR) ✅
   - await bot.place_ftt_trade(signal, duration, asset=self._current_asset)
     OR
   - await bot.place_forex_trade(signal, asset=self._current_asset)

10. RECORD TRADE:
    record = TradeRecord(
        timestamp  = ts,
        asset      = self._current_asset,  ✅ SAME ASSET AS TRADE EXECUTION
        mode       = mode,
        signal     = final_signal.value,
        ...
    )
    self.risk.record_trade(record)
    ↓ Writes to CSV: timestamp | asset | mode | signal | ...

11. If FTT mode: Register trade with RL and track in active_trades
    active_trades.append({
        "asset": self._current_asset,  ✅ SAME ASSET
        ...
    })
```

### Phase 3: Trade Settlement (Every tick - Lines 500+ in orchestrator)
```
1. await self._monitor_active_trades(current_price)
   ↓ Checks for TP/SL hits

2. When trade completes:
   - Determine result (WIN/LOSS)
   - Calculate P&L
   - self.risk.update_pnl_and_journal(timestamp, result, pnl)
     ↓ Updates CSV row with final result and P&L

3. Record outcome with supervisor
   ↓ Updates circuit breaker win-rate stats

4. RL agent learns from trade
   ↓ Uses state_key to associate reward with agent2's decision
```

---

## Asset Field Path Through System

### Trade Origination
```
orchestrator._current_asset (Line 299, 463)
    ↓ Synchronized from orchestrator.ensure_best_asset()
    ↓ Used by: agent1 (signals), supervisor (trade execution), risk (recording)
```

### Trade Recording (bot/risk.py - Lines 115-125)
```
TradeRecord(
    asset = orchestrator._current_asset,  ✅ Source value
    ...
)
    ↓ record_trade(record: TradeRecord)
    ↓ row = {"asset": record.asset, ...}  (Line 121)
    ↓ _append_row(row)
    ↓ Appends to CSV: trade_journal.csv
```

### CSV Journal Format (bot/risk.py)
```
timestamp | asset | mode | signal | confidence | amount | result | pnl | state_key
ts        | EURUSD-OTC | ftt | BUY | 0.8234 | 50 | PENDING | 0.0 | state_key_123
```

### Trade Journal Update (bot/risk.py - Lines 130+)
```
update_pnl_and_journal(timestamp, result="WIN", pnl=100.00)
    ↓ _rewrite_journal_result(timestamp, result, pnl)
    ↓ Finds row by timestamp
    ↓ Updates result and pnl columns
    ↓ Asset column remains unchanged (same as original recording)
```

---

## Verification Checklist

After fix, verify these behaviors:

### ✅ Logs and History Match
- [ ] Log shows: `Trading asset: EURUSD-OTC`
- [ ] Trade history shows: Entry with asset = EURUSD-OTC
- [ ] Trade journal CSV has same asset name
- [ ] No format mismatches (EURUSD vs EUR/USD vs EURUSD-OTC)

### ✅ Asset Selector Win Rates Correct
- [ ] Historical trades can be grouped by asset name
- [ ] Win-rate calculation per asset is accurate
- [ ] Asset rotation logic picks correct asset

### ✅ Loss Recovery Works
- [ ] Consecutive losses on same asset increment counter
- [ ] Position sizing reduced after 1, 2, 3+ losses
- [ ] Loss counter resets when asset switches

### ✅ Hedging Works
- [ ] Opposite trades allowed on same asset (if ALLOW_HEDGING=true)
- [ ] Entry validation confirms market movement for each trade
- [ ] Active trades list has correct asset for each trade

### ✅ Browser Automation
- [ ] Asset search uses display name format (EUR/USD) first
- [ ] Chart correctly opens for selected asset
- [ ] Trade placed on correct asset shown in chart

---

## Asset Synchronization Diagram

```
┌─────────────────────────────────────────────────────────┐
│              ORCHESTRATOR                               │
│  self._current_asset = "EURUSD-OTC"                     │
└────────────────────────┬────────────────────────────────┘
                         │
    ┌────────────────────┼────────────────────┐
    │                    │                    │
    ▼                    ▼                    ▼
┌──────────────┐  ┌────────────────┐  ┌────────────────┐
│ SUPERVISOR   │  │ AGENT1 (MTF)   │  │ RISK MANAGER   │
│ ._current_   │  │ .asset =       │  │ .record_trade()│
│  asset =     │  │ current_asset  │  │ → CSV Journal  │
│ "EURUSD-     │  │ (timeframe     │  │ asset field    │
│  OTC"        │  │  analysis)     │  │ = "EURUSD-OTC" │
└──────────────┘  └────────────────┘  └────────────────┘
    │
    ├─ safe_execute_trade()
    │  ├─ place_ftt_trade(asset=self._current_asset)  ✅ FIXED
    │  └─ place_forex_trade(asset=self._current_asset) ✅ FIXED
    │
    └─→ BOT.place_*_trade()
        └─→ BOT.select_asset() (previously called in main loop)
            └─→ select_asset() uses _asset_display_name()
                └─→ Browser searches and opens chart
```

---

## Code Changes Summary

| File | Lines | Change | Status |
|------|-------|--------|--------|
| agents/agent_pipeline_supervisor.py | 380, 385 | Use `self._current_asset` instead of `self.a1.asset` | ✅ FIXED |
| multi_agent_orchestrator.py | 299-300, 463-464 | Synchronization logic (already present) | ✅ VERIFIED |
| bot/risk.py | 121 | Asset field recording (already present) | ✅ VERIFIED |
| bot/browser.py | 250+ | select_asset() and display name logic (already present) | ✅ VERIFIED |

---

## Testing Recommended

1. **Single Trade Test**: Place one trade and verify:
   - Log shows "Trading asset: X"
   - Trade history shows asset X in entry
   - trade_journal.csv has asset X

2. **Asset Rotation Test**: Wait 5 minutes and verify:
   - Orchestrator switches to new asset
   - Supervisor._current_asset updates
   - New chart opens in browser
   - First trade on new asset records with new asset name

3. **Loss Recovery Test**: Force consecutive losses and verify:
   - Position size reduces correctly
   - Loss counter tracks per asset correctly
   - Counter resets on asset switch

4. **Hedging Test** (if ALLOW_HEDGING=true):
   - Execute BUY trade
   - Execute SELL trade on same asset
   - Both should execute and record with same asset name
   - active_trades should show both with matching assets

---

## Deployment Notes

- ✅ No breaking changes to existing functionality
- ✅ No changes to algorithm or trading logic
- ✅ Backward compatible with existing trade journal
- ✅ Fix is localized to supervisor's safe_execute_trade() method
- ✅ Verification logic already in place via synchronization at lines 300, 464
- ✅ No new dependencies required

**Rollout**: Safe to deploy immediately after testing single asset transactions.
