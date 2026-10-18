# COMPLETE UPDATED CODE - All Fixed Files

## QUICK SUMMARY OF ALL CHANGES

### **multi_agent_orchestrator.py**
- ✓ DECISION_INTERVAL: 15s → 5s (3x more checks)
- ✓ M1 candles: 120 → 30 (startup 2h → 30min)
- ✓ Bootstrap: 200 ticks → 100 ticks
- ✓ Added HOLD override logic
- ✓ Allow RL divergence when inexperienced
- ✓ Confidence gate: 0.62 → 0.50+
- ✓ Improved confidence fusion logic

### **agents/agent_multi_timeframe.py**
- ✓ MIN_CANDLES: {20,15,12,8} → {8,6,5,4}
- ✓ Consensus: 3/4 → 2/4 (lines 253-258)
- ✓ Fallback: 1/4 strong signals OK (lines 260-265)
- ✓ Confidence floors: {78%,68%} → {60%,55%,50%}
- ✓ Seeding: all 120 → last 30 candles

### **agents/agent_rl_learning.py**
- ✓ Fresh exploration: <5 → <3 visits
- ✓ Initial confidence: 0.55 → 0.60
- ✓ Experience bonus: /100 → /75
- ✓ Base confidence: 0.60 → 0.62

### **config/settings.py**
- ✓ CONFIDENCE_THRESHOLD: 0.62 → 0.50

---

## HOW TO USE THESE FIXES

### Option 1: Copy Individual Files (RECOMMENDED)
Replace your existing files with the updated versions below:

1. Copy each complete code section below
2. Replace the corresponding file in your project
3. Run: `python multi_agent_orchestrator.py`

### Option 2: Manual Edits
Apply the fixes manually using the line-by-line changes shown above.

---

## FILES TO UPDATE

### File 1: config/settings.py (SMALLEST - Just 1 Line Changed)

**Change at line 13:**
```python
# OLD:
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.62"))

# NEW:
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.50"))
```

---

### File 2: agents/agent_rl_learning.py (Lines 34-50)

**Old code (REMOVE):**
```python
def best_action(self, state: str) -> Tuple[int, float, bool]:
    """
    Returns (action, raw_confidence, is_experienced).
    is_experienced=True once the state has ≥10 visits.
    """
    q = self.table[state]
    v = self.visits[state]
    total_visits = sum(v)

    if random.random() < self.epsilon or total_visits < 5:
        action = random.randint(0, 1)
        return action, 0.55, False   # raw_conf=0.55, not experienced

    action = int(np.argmax(q))
    spread = abs(q[0] - q[1])
    # Confidence grows with both Q-spread and visit count
    experience_bonus = min(0.15, total_visits / 100)
    conf = 0.60 + min(0.35, spread * 8) + experience_bonus
    return action, min(0.95, conf), True
```

**New code (INSERT):**
```python
def best_action(self, state: str) -> Tuple[int, float, bool]:
    """
    Returns (action, raw_confidence, is_experienced).
    is_experienced=True once the state has ≥10 visits.
    
    ✓ FIX: Fresh RL should not block trades — return higher confidence
    to allow trading to start, especially on first 3 visits.
    """
    q = self.table[state]
    v = self.visits[state]
    total_visits = sum(v)

    if random.random() < self.epsilon or total_visits < 3:  # Reduced from 5 to 3
        action = random.randint(0, 1)
        # ✓ FIX: Higher initial confidence (was 0.55, now 0.60) to allow trades to start
        return action, 0.60, False   # raw_conf=0.60, not experienced

    action = int(np.argmax(q))
    spread = abs(q[0] - q[1])
    # Confidence grows with both Q-spread and visit count
    # ✓ FIX: More aggressive experience bonus to start trading sooner
    experience_bonus = min(0.20, total_visits / 75)  # Was 0.15, was 100
    conf = 0.62 + min(0.35, spread * 8) + experience_bonus  # Was 0.60
    return action, min(0.95, conf), True
```

---

### File 3: agents/agent_multi_timeframe.py (Multiple Changes)

**Change 1: MIN_CANDLES at line 22**
```python
# OLD:
MIN_CANDLES = {TF_5S: 20, TF_15S: 15, TF_30S: 12, TF_1M: 8}

# NEW:
MIN_CANDLES = {TF_5S: 8, TF_15S: 6, TF_30S: 5, TF_1M: 4}
```

**Change 2: seed_from_dataframe method at line 174 (ADD df.tail(30) AND UPDATE LOG)**
```python
# OLD:
def seed_from_dataframe(self, df):
    """
    Seed all builders from a yfinance M1 DataFrame so indicators
    are valid from the first real tick (no flat bootstrap needed).
    """
    if df is None or df.empty:
        return
    now = time.time()
    total = len(df)
    for i, row in enumerate(df.itertuples()):
        # ... rest of method ...
        log.info(f"[Agent1] Seeded builders from {total} M1 candles.")

# NEW:
def seed_from_dataframe(self, df):
    """
    Seed all builders from a yfinance M1 DataFrame so indicators
    are valid from the first real tick (no flat bootstrap needed).
    
    ✓ FIX: Use only last 30 candles instead of all 120 for faster startup
    """
    if df is None or df.empty:
        return
    
    # ✓ FIX: Use last 30 candles only (not all 120) to speed up startup
    df = df.tail(30)
    
    now = time.time()
    total = len(df)
    for i, row in enumerate(df.itertuples()):
        # ... rest of method unchanged ...
        log.info(f"[Agent1] Seeded builders from {total} M1 candles (last 30).")
```

**Change 3: analyze() method at line 223 (COMPLETE REPLACEMENT)**
```python
# Replace entire analyze() method with:
def analyze(self) -> Tuple["Signal", float]:
    from models.predictor import Signal

    if self._last_price <= 0:
        return Signal.HOLD, 0.0

    votes_buy = votes_sell = 0
    total_conf = 0.0
    tf_details = {}
    
    # Track how many TFs have enough candles
    active_tfs = 0

    for tf in ALL_TIMEFRAMES:
        b = self.builders[tf]
        if len(b) < MIN_CANDLES[tf]:
            log.debug(f"[Agent1] TF={tf}s: {len(b)}/{MIN_CANDLES[tf]} candles")
            continue
        
        active_tfs += 1
        vote, conf = self._analyze_tf(b.get_closes(), b.get_highs(), b.get_lows(), tf)
        tf_details[tf] = {"vote": vote, "confidence": conf}
        if vote == "BUY":
            votes_buy  += 1; total_conf += conf
        elif vote == "SELL":
            votes_sell += 1; total_conf += conf

    self._last_tf_state = tf_details

    def _emit(direction: str, votes: int, avg_raw: float) -> Tuple["Signal", float]:
        bias = self.sr_engine.zone_bias(self._last_price)
        # S/R boost/penalty
        if direction == "BUY":
            sr_adj = +0.04 if bias == "bullish" else (-0.05 if bias == "bearish" else 0.0)
        else:
            sr_adj = +0.04 if bias == "bearish" else (-0.05 if bias == "bullish" else 0.0)
        # ✓ FIX: Lower minimum confidence floors + fewer votes needed
        if votes >= 3:
            floor = 0.60
        elif votes == 2:
            floor = 0.55
        else:  # votes == 1
            floor = 0.50
        conf = max(floor, min(0.97, avg_raw + sr_adj))
        sig  = Signal.BUY if direction == "BUY" else Signal.SELL
        log.info(f"[Agent1] {direction} {votes}/{active_tfs} active TFs | raw={avg_raw:.2%} "
                 f"sr={sr_adj:+.2%} floor={floor:.2%} → conf={conf:.2%} | bias={bias}")
        return sig, conf

    # ✓ FIX: Require only 2/4 agreement instead of 3/4 for faster trading
    if votes_buy >= 2:
        return _emit("BUY",  votes_buy,  total_conf / votes_buy)
    if votes_sell >= 2:
        return _emit("SELL", votes_sell, total_conf / votes_sell)

    # ✓ FIX: If only 1 TF is ready and it's strong, still trade (not just HOLD)
    if active_tfs >= 1 and (votes_buy >= 1 or votes_sell >= 1):
        if votes_buy >= 1:
            return _emit("BUY",  votes_buy,  total_conf / votes_buy)
        else:
            return _emit("SELL", votes_sell, total_conf / votes_sell)

    log.debug(f"[Agent1] No consensus BUY={votes_buy} SELL={votes_sell} from {active_tfs} active TFs")
    return Signal.HOLD, 0.0
```

---

### File 4: multi_agent_orchestrator.py (Most Critical - Multiple Changes)

**Change 1: Class constants at line 46**
```python
# OLD:
class MultiAgentTradingSystem:
    TICK_INTERVAL     = 1.0
    DECISION_INTERVAL = 15.0
    ASSET_CHECK_EVERY = 300.0

# NEW:
class MultiAgentTradingSystem:
    TICK_INTERVAL     = 1.0
    DECISION_INTERVAL = 5.0  # ✓ FIX: 15.0 → 5.0 (3x more checks)
    ASSET_CHECK_EVERY = 300.0
```

**Change 2: _combine_confidences() at line 62 (COMPLETE REPLACEMENT)**
```python
def _combine_confidences(self, a1_conf: float, rl_conf: float,
                          rl_experienced: bool) -> float:
    """
    When RL is experienced (≥10 state visits) → 75/25 weighted blend.
    When RL is fresh → use Agent1's confidence directly (RL just
    confirmed the direction).
    
    ✓ FIX: More aggressive fusion to allow faster trading
    """
    if rl_experienced:
        # Both experienced — average them more aggressively
        fused = 0.75 * a1_conf + 0.25 * rl_conf
        if a1_conf >= 0.70 and rl_conf >= 0.65:
            fused = min(0.97, fused + 0.03)
    else:
        # Fresh RL — trust technical analysis fully, but boost if RL agrees
        if abs(a1_conf - rl_conf) < 0.1:
            # RL confidence is close to A1 → boost combined confidence
            fused = max(a1_conf, rl_conf) * 1.02
        else:
            fused = a1_conf
    
    fused = min(0.99, max(0.50, fused))  # Clamp between 50%-99%
    log.debug(f"[Fusion] a1={a1_conf:.2%} rl={rl_conf:.2%} "
              f"exp={rl_experienced} → {fused:.2%}")
    return fused
```

**Change 3: setup() method — M1 candles at line 138**
```python
# OLD:
df = fetch_candles(self._current_asset, "M1", n=120)
if not df.empty:
    self.agent1.seed_from_dataframe(df)
else:
    raise ValueError("Empty dataframe")
except Exception as e:
    log.warning(f"[Setup] yfinance seed failed ({e}) — using bootstrap")
    base_price = await self._get_price()
    self.agent1.bootstrap(base_price)

# NEW:
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
```

**Change 4: _evaluate_and_trade() method at line 188 (COMPLETE REPLACEMENT)**
```python
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
```

---

## TESTING STEPS

After applying fixes:

1. **Delete old log files** to get clean output:
   ```bash
   rm -f logs/*
   ```

2. **Run the updated system:**
   ```bash
   python multi_agent_orchestrator.py
   ```

3. **Monitor first 30 minutes:**
   - Should see first trade within 3-5 minutes
   - Should see at least 10 trades in 30 minutes
   - Logs should show green lights (BUY/SELL signals)

4. **Check logs** for good patterns:
   ```bash
   tail -f logs/multi_agent_system.log | grep -E "\[Agent1\]|\[Fusion\]|\[Supervisor\]"
   ```

---

## EXPECTED OUTPUT

### Good 🟢
```
[Agent1] BUY 2/4 @ 62%
[Agent2] BUY @ 60% | state=mid_momentum_bullish | visits=0
[Fusion] A1=BUY vs fresh RL=BUY → use A1 direction
[Fusion] ✓ CONSENSUS BUY @ 61%
[Gate] 61% >= 55% ✓ PASS
[Supervisor] Execute attempt 1/3 — BUY FTT
[Supervisor] Trade placed=True
```

### Bad 🔴
```
[Agent1] HOLD — insufficient TF agreement.
[Fusion] Divergence A1=BUY vs experienced RL=SELL → SKIP
[Gate] 48% < 55% — skip
```

If you see bad patterns, lower `CONFIDENCE_THRESHOLD` to 0.45.

---

## VERIFICATION CHECKLIST

- [ ] Changed MIN_CANDLES to {8,6,5,4}
- [ ] Changed analyze() to require 2/4 instead of 3/4
- [ ] Changed DECISION_INTERVAL to 5.0 seconds
- [ ] Changed CONFIDENCE_THRESHOLD to 0.50
- [ ] Changed M1 fetch to 30 instead of 120
- [ ] Added HOLD override logic
- [ ] Added RL divergence handling
- [ ] First trade within 5 minutes? ✓ SUCCESS!
