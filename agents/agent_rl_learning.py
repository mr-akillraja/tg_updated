"""
agents/agent_rl_learning.py  — v3
Fix: fresh Q-table now defers confidence to Agent1 instead of
     always returning 0.55.  RL confidence only kicks in once the
     table has enough visits (≥10 per state).
"""

import csv
import json
import logging
import os
import random
from collections import defaultdict
from typing import Dict, Optional, Tuple

import numpy as np
from config import settings

log = logging.getLogger("agent2_rl")


class QTable:
    def __init__(self, lr=0.25, gamma=0.90, eps_start=0.30, eps_min=0.02):
        # Slightly more aggressive learning rate and exploration to adapt quickly
        self.lr          = lr
        self.gamma       = gamma
        self.epsilon     = eps_start
        self.epsilon_min = eps_min
        self.epsilon_decay = 0.990
        self.table: Dict[str, list]  = defaultdict(lambda: [0.0, 0.0])
        self.visits: Dict[str, list] = defaultdict(lambda: [0, 0])  # visit counts

    def best_action(self, state: str) -> Tuple[int, float, bool]:
        """
        Returns (action, raw_confidence, is_experienced).
        is_experienced=True once the state has ≥10 visits.
        
        ✓ FIX: Fresh RL should not block trades — return higher confidence
        to allow trading to start, especially on first 5 visits.
        """
        q = self.table[state]
        v = self.visits[state]
        total_visits = sum(v)

        if random.random() < self.epsilon or total_visits < 3:  # keep early exploration
            action = random.randint(0, 1)
            # Provide a slightly higher initial confidence so RL contributes,
            # but mark as inexperienced so fusion logic in orchestrator can decide.
            return action, 0.62, False   # raw_conf=0.62, not experienced

        action = int(np.argmax(q))
        spread = abs(q[0] - q[1])
        # Confidence grows with both Q-spread and visit count
        # ✓ FIX: More aggressive experience bonus to start trading sooner
        experience_bonus = min(0.25, total_visits / 60)
        conf = 0.64 + min(0.35, spread * 8) + experience_bonus
        return action, min(0.95, conf), True

    def update(self, state, action, reward, next_state, quality_penalty: float = 1.0):
        """
        ✓ FIX: Apply quality penalty to reward based on state consensus.
        - quality_penalty=1.0: high-consensus state (normal learning)
        - quality_penalty=0.85: medium-consensus state (15% damping)
        - quality_penalty=0.6: low-consensus state (40% damping)
        
        This prevents Q-table corruption from weak-consensus lucky wins.
        """
        adjusted_reward = reward * quality_penalty
        q  = self.table[state]
        q[action] += self.lr * (adjusted_reward + self.gamma * max(self.table[next_state]) - q[action])
        self.visits[state][action] += 1
        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)
        
        if quality_penalty < 1.0:
            log.debug(f"[RL] state={state} action={action} reward={reward:.2f} "
                      f"quality_penalty={quality_penalty:.2f} adjusted_reward={adjusted_reward:.2f}")

    def save(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump({"table": dict(self.table),
                       "visits": dict(self.visits),
                       "epsilon": self.epsilon}, f, indent=2)

    def load(self, path: str) -> bool:
        if not os.path.exists(path): return False
        try:
            with open(path) as f:
                d = json.load(f)
            self.table   = defaultdict(lambda: [0.0, 0.0], d.get("table",  {}))
            self.visits  = defaultdict(lambda: [0, 0],     d.get("visits", {}))
            self.epsilon = d.get("epsilon", self.epsilon)
            log.info(f"Q-table loaded from {path} | states={len(self.table)} | ε={self.epsilon:.3f}")
            return True
        except Exception as e:
            log.warning(f"Q-table load error: {e}"); return False


class PerformanceTracker:
    def __init__(self, window=50):
        self.window   = window
        self.outcomes = []

    def record(self, win: bool):
        self.outcomes.append(1 if win else 0)
        if len(self.outcomes) > self.window: self.outcomes.pop(0)

    @property
    def win_rate(self) -> float:
        return sum(self.outcomes) / len(self.outcomes) if self.outcomes else 0.0

    @property
    def recent_streak(self) -> int:
        if not self.outcomes: return 0
        last, streak = self.outcomes[-1], 0
        for o in reversed(self.outcomes):
            if o == last: streak += 1
            else: break
        return streak if last == 1 else -streak


class RLFeedbackAgent:
    def __init__(self, model_dir: str):
        self.model_dir   = model_dir
        self.qtable_path = os.path.join(model_dir, "rl_qtable.json")
        self.q           = QTable()
        self.perf        = PerformanceTracker(50)
        self._pending: Dict[str, Tuple[int, str]] = {}
        self._last_state: Optional[str] = None
        self._session_wins = self._session_total = 0
        self.q.load(self.qtable_path)

    def get_state_key(self, tf_indicators: dict) -> str:
        parts = []
        for tf in [5, 15, 30, 60]:
            data = tf_indicators.get(tf, {})
            vote = data.get("vote", "N")
            conf = data.get("confidence", 0.5)
            cb   = "H" if conf >= 0.75 else ("M" if conf >= 0.60 else "L")
            parts.append(f"{tf}{vote[0]}{cb}")
        return "|".join(parts)

    def get_recommendation(self, state_key: str) -> Tuple[int, float]:
        """
        Returns (action, confidence).
        If the Q-table has little experience for this state, returns
        the ε-random action with a low confidence so Agent1's signal
        dominates the fusion step rather than blocking it.
        """
        action, raw_conf, experienced = self.q.best_action(state_key)
        self._last_state = state_key

        if self.perf.recent_streak <= -3:
            log.warning("[RL] Loss streak — reducing RL confidence")
            raw_conf = max(0.50, raw_conf - 0.08)

        log.debug(f"[RL] state={state_key} → action={action} conf={raw_conf:.2%} "
                  f"exp={experienced} ε={self.q.epsilon:.3f}")
        return action, raw_conf

    def register_trade(self, state_key: str, action: int):
        self._pending[state_key] = (action, state_key)

    def _calculate_state_quality(self, state_key: str) -> float:
        """
        ✓ NEW: Calculate quality penalty based on state consensus.
        Used to dampen learning for weak-consensus states.
        
        Format: "5NL|15NL|30NL|60BH"
        - 100% (4/4 same vote): penalty=1.0
        - 75% (3/4):  penalty=0.9
        - 50% (2/4):  penalty=0.85
        - 25% (1/4):  penalty=0.6
        """
        if not state_key:
            return 1.0
        
        parts = state_key.split("|")
        if not parts or len(parts) < 2:
            return 1.0
        
        # Count B, S, N votes
        b_count = sum(1 for v in parts if "B" in v)
        s_count = sum(1 for v in parts if "S" in v)
        n_count = sum(1 for v in parts if "N" in v)
        
        max_vote_count = max(b_count, s_count, n_count)
        total_parts = len(parts)
        consensus_ratio = max_vote_count / total_parts if total_parts > 0 else 1.0
        
        if consensus_ratio >= 0.75:
            return 1.0
        elif consensus_ratio >= 0.50:
            return 0.85
        elif consensus_ratio >= 0.25:
            return 0.60
        else:
            return 0.40

    def update_after_result(self, result: str, pnl: float, quality_penalty: float = 1.0):
        """
        ✓ FIX: Accept quality_penalty to dampen learning for weak-consensus states
        """
        win = result.upper() == "WIN"
        reward = self._shape_reward(pnl, win)
        self._session_total += 1
        if win: self._session_wins += 1
        self.perf.record(win)

        if self._pending:
            sk, (action, _) = next(iter(self._pending.items()))
            del self._pending[sk]
            # ✓ FIX: Pass quality_penalty to Q-table update
            self.q.update(sk, action, reward, self._last_state or sk, quality_penalty=quality_penalty)
            log.info(f"[RL] Q-update state={sk} action={action} reward={reward:+.2f} "
                     f"quality_penalty={quality_penalty:.2f} | win_rate={self.perf.win_rate:.0%}")

        if self._session_total % 5 == 0:
            self.q.save(self.qtable_path)

        log.info(f"[RL] {self._session_wins}/{self._session_total} wins | "
                 f"rolling={self.perf.win_rate:.0%} | streak={self.perf.recent_streak:+d}")

    def learn_from_logs(self, journal_path: str):
        if not os.path.exists(journal_path):
            log.info("[RL] No journal found — starting fresh."); return
        n = 0
        try:
            with open(journal_path, newline="") as f:
                reader = csv.DictReader(f)
                prev_s = prev_a = None
                for row in reader:
                    # Skip mechanical checks before outcomes and replay transitions,
                    # including when the supplied journal is an archive.
                    if row.get("state_key") == "DEMO_EXECUTION_TEST":
                        continue
                    res = row.get("result", "").upper()
                    if res not in ("WIN", "LOSS"): continue
                    sig    = row.get("signal", "BUY").upper()
                    action = 0 if sig == "BUY" else 1
                    pnl    = float(row.get("pnl", 0) or 0)
                    reward = self._shape_reward(pnl, res == "WIN")
                    state  = row.get("state_key") or f"hist_{sig}"
                    
                    # ✓ NEW: Calculate quality penalty based on state consensus
                    quality_penalty = self._calculate_state_quality(state)
                    
                    if prev_s is not None:
                        self.q.update(prev_s, prev_a, reward, state, quality_penalty=quality_penalty)
                    prev_s, prev_a = state, action
                    self.perf.record(res == "WIN")
                    n += 1
        except Exception as e:
            log.warning(f"[RL] Log replay error: {e}")
        self.q.save(self.qtable_path)
        log.info(f"[RL] Replayed {n} trades | historic win-rate={self.perf.win_rate:.0%}")

    def train_from_logs(self, journal_path: str, epochs: int = 1, shuffle: bool = False):
        """Train the RL Q-table by replaying the journal multiple times."""
        if epochs < 1:
            return

        for epoch in range(1, epochs + 1):
            log.info(f"[RL] Training epoch {epoch}/{epochs} from journal {journal_path}")
            self.learn_from_logs(journal_path)
            if shuffle:
                try:
                    import random
                    self._shuffle_journal(journal_path)
                except Exception:
                    pass

        self.q.save(self.qtable_path)
        log.info(f"[RL] Training complete | epochs={epochs} | qtable={self.qtable_path}")

    def _shuffle_journal(self, journal_path: str):
        if not os.path.exists(journal_path):
            return
        with open(journal_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        random.shuffle(rows)
        with open(journal_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

    def analyze_screenshot_feedback(self, path: str) -> float:
        try:
            from PIL import Image
            img = Image.open(path).convert("RGB")
            w, h = img.size
            region = img.crop((int(w * 0.6), int(h * 0.7), w, h))
            arr = np.array(region, dtype=float)
            r, g, b = arr[:,:,0].mean(), arr[:,:,1].mean(), arr[:,:,2].mean()
            gs = (g - r - b * 0.5) / 255.0
            return +0.30 if gs > 0.05 else (-0.30 if gs < -0.05 else 0.0)
        except Exception:
            return 0.0

    def _shape_reward(self, pnl: float, win: bool) -> float:
        # Reward shaping tuned to prefer reaching the target profit per trade
        # and to heavily penalize large losses (to encourage stop-loss behaviour).
        try:
            tgt = float(getattr(settings, 'TARGET_PROFIT_PER_TRADE_USD', 100.0))
            max_loss = float(getattr(settings, 'MAX_TRADE_LOSS_USD', 500.0))
        except Exception:
            tgt, max_loss = 100.0, 500.0

        # Normalize pnl relative to target
        normalized = pnl / (tgt if tgt else 1.0)
        if win:
            # Reward is stronger if pnl meets/exceeds the per-trade target
            base = 1.0 + min(2.0, normalized)
            # small bonus for clean wins (lower variance)
            bonus = 0.2 if abs(pnl) >= tgt else 0.0
            return round(base + bonus, 4)

        # Loss case: punish proportionally to how much the loss exceeded the soft max
        loss_severity = min(2.5, abs(pnl) / (max_loss if max_loss else 1.0))
        # Make losses count more to push policy toward lower-loss actions
        return round(-1.5 * loss_severity - 0.5, 4)