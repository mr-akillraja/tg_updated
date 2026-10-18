"""
agents/agent_regime_detector.py — v2 (SELL path + improved validation)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Key fixes vs v1:
  1. HIGH_VOL_DOWN now ALLOWS SELL signals (was blocking everything)
  2. HIGH_VOL_UP   now ALLOWS BUY  signals (was blocking everything)
  3. Regime-aware confidence adjustments returned alongside is_valid
  4. RANGING regime added — small position allowed in either direction
  5. Previous candle analysis now scores both BUY and SELL
"""

from __future__ import annotations
import numpy as np
from typing import Tuple, Dict, Any


# ── Regime labels ────────────────────────────────────────────────────────────
REGIME_BULL_TREND   = "BULL_TREND"       # Clean uptrend, low vol
REGIME_BEAR_TREND   = "BEAR_TREND"       # Clean downtrend, low vol
REGIME_HIGH_VOL_UP  = "HIGH_VOL_UP"      # Volatile but trending up
REGIME_HIGH_VOL_DOWN= "HIGH_VOL_DOWN"    # Volatile but trending down
REGIME_RANGING      = "RANGING"          # Sideways / consolidation
REGIME_BREAKOUT_UP  = "BREAKOUT_UP"      # BB squeeze breakout up
REGIME_BREAKOUT_DOWN= "BREAKOUT_DOWN"    # BB squeeze breakout down
REGIME_UNKNOWN      = "UNKNOWN"


class RegimeDetector:

    def detect_regime(self,
                      closes:      np.ndarray,
                      highs:       np.ndarray,
                      lows:        np.ndarray,
                      rsi:         float,
                      macd_val:    float,
                      macd_signal: float,
                      bb_upper:    float,
                      bb_mid:      float,
                      bb_lower:    float,
                      atr:         float) -> str:
        """
        Classify the current market regime from indicators.
        """
        if len(closes) < 20:
            return REGIME_UNKNOWN

        price = float(closes[-1])

        # Volatility: ATR as % of price
        atr_pct = atr / price if price > 0 else 0
        high_vol = atr_pct > 0.0008   # >0.08% per candle = high vol

        # Trend direction via MACD + linear regression
        slope = 0.0
        if len(closes) >= 10:
            x = np.arange(10, dtype=float)
            y = closes[-10:]
            coeffs = np.polyfit(x, y, 1)
            slope = float(coeffs[0])

        macd_bull = macd_val > macd_signal
        price_above_mid = price > bb_mid

        # Bollinger band width (squeeze = breakout imminent)
        bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 0
        squeeze = bb_width < 0.005  # < 0.5%

        # ── Breakout regimes (highest priority) ──────────────────────
        if squeeze and price > bb_upper:
            return REGIME_BREAKOUT_UP
        if squeeze and price < bb_lower:
            return REGIME_BREAKOUT_DOWN

        # ── Trend + vol combos ────────────────────────────────────────
        trending_up   = macd_bull and slope > 0 and rsi > 50
        trending_down = (not macd_bull) and slope < 0 and rsi < 50

        if trending_up and not high_vol:
            return REGIME_BULL_TREND
        if trending_down and not high_vol:
            return REGIME_BEAR_TREND
        if trending_up and high_vol:
            return REGIME_HIGH_VOL_UP
        if trending_down and high_vol:
            return REGIME_HIGH_VOL_DOWN

        # ── Ranging ──────────────────────────────────────────────────
        return REGIME_RANGING

    def validate_signal(self, signal: str, regime: str) -> Tuple[bool, str, float]:
        """
        Returns (is_valid, reason, confidence_adjustment).

        ── KEY FIX ──
        Previously ALL signals were blocked in HIGH_VOL regimes.
        Now:
          HIGH_VOL_DOWN → SELL is allowed (trend-following), BUY blocked
          HIGH_VOL_UP   → BUY  is allowed (trend-following), SELL blocked
          BEAR_TREND    → SELL strongly preferred, BUY needs extra filter
          BULL_TREND    → BUY  strongly preferred, SELL needs extra filter
          RANGING       → both allowed with moderate confidence penalty
          BREAKOUT_UP   → BUY  only
          BREAKOUT_DOWN → SELL only
        """
        rules: Dict[str, Any] = {
            REGIME_BULL_TREND: {
                "BUY":  (True,  "BUY in bull trend — ideal",          +0.05),
                "SELL": (False, "SELL risky in bull trend — skip",    -0.00),
            },
            REGIME_BEAR_TREND: {
                "SELL": (True,  "SELL in bear trend — ideal",          +0.05),
                "BUY":  (False, "BUY risky in bear trend — skip",     -0.00),
            },
            REGIME_HIGH_VOL_UP: {
                "BUY":  (True,  "BUY in high-vol uptrend — allowed",  -0.05),
                "SELL": (False, "SELL against high-vol uptrend — skip", -0.00),
            },
            REGIME_HIGH_VOL_DOWN: {
                "SELL": (True,  "SELL in high-vol downtrend — allowed", -0.05),
                "BUY":  (False, "BUY risky in high-vol downtrend — skip", -0.00),
            },
            REGIME_RANGING: {
                "BUY":  (True,  "BUY in ranging market — S/R required", -0.08),
                "SELL": (True,  "SELL in ranging market — S/R required", -0.08),
            },
            REGIME_BREAKOUT_UP: {
                "BUY":  (True,  "BUY on breakout up — strong signal",  +0.08),
                "SELL": (False, "SELL against breakout up — skip",     -0.00),
            },
            REGIME_BREAKOUT_DOWN: {
                "SELL": (True,  "SELL on breakout down — strong signal", +0.08),
                "BUY":  (False, "BUY against breakout down — skip",    -0.00),
            },
            REGIME_UNKNOWN: {
                "BUY":  (False, "Unknown regime — skip",               -0.00),
                "SELL": (False, "Unknown regime — skip",               -0.00),
            },
        }

        regime_rules = rules.get(regime, rules[REGIME_UNKNOWN])
        is_valid, reason, conf_adj = regime_rules.get(
            signal, (False, f"No rule for {signal} in {regime}", -0.00))

        return is_valid, reason, float(conf_adj)

    def analyze_previous_candles(self,
                                  closes: np.ndarray,
                                  highs:  np.ndarray,
                                  lows:   np.ndarray,
                                  lookback: int = 5) -> Dict[str, float]:
        """
        Analyze last N candles for directional momentum, trend strength,
        and support/resistance proximity.
        Returns dict with: uptrend_strength, downtrend_strength, momentum,
                           consecutive_up, consecutive_down
        """
        n = min(len(closes), len(highs), len(lows), lookback)
        if n < 2:
            return {
                "uptrend_strength": 0.0, "downtrend_strength": 0.0,
                "momentum": 0.0, "consecutive_up": 0, "consecutive_down": 0
            }

        c = closes[-n:]
        h = highs[-n:]
        lo = lows[-n:]

        # Count consecutive closes in same direction
        consec_up = consec_down = 0
        for i in range(n-1, 0, -1):
            if c[i] > c[i-1]:
                if consec_down == 0:
                    consec_up += 1
                else:
                    break
            elif c[i] < c[i-1]:
                if consec_up == 0:
                    consec_down += 1
                else:
                    break
            else:
                break

        total_range = float(np.max(h) - np.min(lo))
        price_change = float(c[-1] - c[0])
        momentum = price_change / total_range if total_range > 0 else 0.0

        up_candles   = sum(1 for i in range(1, n) if c[i] > c[i-1])
        down_candles = sum(1 for i in range(1, n) if c[i] < c[i-1])
        total_moves  = n - 1

        uptrend_strength   = up_candles   / total_moves if total_moves > 0 else 0.5
        downtrend_strength = down_candles / total_moves if total_moves > 0 else 0.5

        return {
            "uptrend_strength":   uptrend_strength,
            "downtrend_strength": downtrend_strength,
            "momentum":           momentum,
            "consecutive_up":     consec_up,
            "consecutive_down":   consec_down,
        }
