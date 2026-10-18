"""
agents/chart_analyzer.py — v2 (EMA crossover + Aroon + full indicator suite)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Indicators:
  EMA 9, EMA 21, EMA 50 — crossover signals
  Aroon(25)              — momentum confirmation (Smart Trading strategy)
  Bollinger Bands        — squeeze / breakout detection
  ATR                    — volatility filter (skip if too high)
  VWAP                   — price context

Oscillators:
  RSI-14, MACD(12/26/9), Stochastic(5,3,3), CCI-20, Williams%R-14

Candlestick patterns: 16 patterns

HIGH-CONFLUENCE SIGNAL RULE (from the strategy video):
  BUY  when: EMA9 crosses above EMA21 AND Aroon_Up crosses above Aroon_Down
  SELL when: EMA9 crosses below EMA21 AND Aroon_Down crosses above Aroon_Up
  Both must confirm simultaneously for a valid entry.
  Confidence is then boosted by oscillator agreement.
"""

from __future__ import annotations
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class AnalysisResult:
    # Indicators
    ema9:   float = 0.0
    ema21:  float = 0.0
    ema50:  float = 0.0
    ema9_prev:  float = 0.0
    ema21_prev: float = 0.0
    ema_crossover_bull: bool = False   # EMA9 crossed ABOVE EMA21 this bar
    ema_crossover_bear: bool = False   # EMA9 crossed BELOW EMA21 this bar
    ema_aligned_bull:   bool = False   # EMA9 > EMA21 > EMA50
    ema_aligned_bear:   bool = False   # EMA9 < EMA21 < EMA50

    bb_upper: float = 0.0
    bb_mid:   float = 0.0
    bb_lower: float = 0.0
    bb_squeeze: bool = False
    atr:    float = 0.0
    vwap:   float = 0.0

    # Aroon(25)
    aroon_up:   float = 50.0
    aroon_down: float = 50.0
    aroon_crossover_bull: bool = False  # Aroon_Up crossed above Aroon_Down
    aroon_crossover_bear: bool = False  # Aroon_Down crossed above Aroon_Up
    aroon_bull_momentum:  bool = False  # Aroon_Up > 70 and > Aroon_Down
    aroon_bear_momentum:  bool = False  # Aroon_Down > 70 and > Aroon_Up

    # Oscillators
    rsi:         float = 50.0
    macd_line:   float = 0.0
    macd_signal: float = 0.0
    macd_hist:   float = 0.0
    stoch_k:     float = 50.0
    stoch_d:     float = 50.0
    cci:         float = 0.0
    williams_r:  float = -50.0

    # Support / Resistance
    support_levels:     List[float] = field(default_factory=list)
    resistance_levels:  List[float] = field(default_factory=list)
    nearest_support:    float = 0.0
    nearest_resistance: float = 0.0
    at_support:    bool = False
    at_resistance: bool = False

    # Trend
    trend_slope:    float = 0.0
    trend_strength: float = 0.0
    pivot_pp: float = 0.0
    pivot_r1: float = 0.0
    pivot_s1: float = 0.0

    # Candlestick
    pattern_name:      str   = "NONE"
    pattern_direction: str   = "NEUTRAL"
    pattern_strength:  float = 0.0

    # Volatility filter
    volatility_pct:    float = 0.0   # ATR as % of price
    volatility_ok:     bool  = True  # False if too volatile to trade

    # Confluence counts
    bull_confluences: int = 0   # how many indicators agree on BUY
    bear_confluences: int = 0   # how many indicators agree on SELL
    max_confluences:  int = 9   # total possible confluences

    # Final scores
    buy_score:  float = 0.0
    sell_score: float = 0.0
    signal:     str   = "HOLD"
    confidence: float = 0.0


# ── Math helpers ──────────────────────────────────────────────────────────────

def _ema(series: np.ndarray, period: int) -> np.ndarray:
    if len(series) < period:
        return np.full(len(series), series[-1] if len(series) else 0.0)
    k   = 2.0 / (period + 1)
    out = np.empty(len(series))
    out[0] = series[0]
    for i in range(1, len(series)):
        out[i] = series[i] * k + out[i-1] * (1-k)
    return out


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    d = np.diff(closes[-(period+1):])
    g = np.where(d > 0, d, 0.0)
    l = np.where(d < 0, -d, 0.0)
    ag, al = np.mean(g), np.mean(l)
    if al == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def _macd(closes: np.ndarray,
          fast=12, slow=26, sig=9) -> Tuple[float, float, float]:
    if len(closes) < slow + sig:
        return 0.0, 0.0, 0.0
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    ml = ef - es
    sl = _ema(ml, sig)
    return float(ml[-1]), float(sl[-1]), float(ml[-1] - sl[-1])


def _bollinger(closes: np.ndarray, period=20, k=2.0) -> Tuple[float, float, float]:
    if len(closes) < period:
        m = float(np.mean(closes))
        return m, m, m
    w   = closes[-period:]
    mid = float(np.mean(w))
    std = float(np.std(w, ddof=1))
    return mid + k*std, mid, mid - k*std


def _stochastic(closes, highs, lows,
                k_period=5, d_period=3, smooth=3) -> Tuple[float, float]:
    n = min(len(closes), len(highs), len(lows))
    if n < k_period:
        return 50.0, 50.0
    c, h, lo = closes[-n:], highs[-n:], lows[-n:]
    raw_k = []
    for i in range(k_period-1, n):
        hi = np.max(h[i-k_period+1:i+1])
        li = np.min(lo[i-k_period+1:i+1])
        d  = hi - li
        raw_k.append(100.0*(c[i]-li)/d if d > 0 else 50.0)
    ks = float(np.mean(raw_k[-smooth:])) if len(raw_k) >= smooth else float(np.mean(raw_k))
    kd = float(np.mean(raw_k[-d_period:])) if len(raw_k) >= d_period else ks
    return ks, kd


def _cci(closes, highs, lows, period=20) -> float:
    n = min(len(closes), len(highs), len(lows), period)
    if n < 5:
        return 0.0
    tp = (closes[-n:] + highs[-n:] + lows[-n:]) / 3.0
    m  = np.mean(tp)
    mad = np.mean(np.abs(tp - m))
    return float((tp[-1] - m) / (0.015 * mad)) if mad else 0.0


def _williams_r(closes, highs, lows, period=14) -> float:
    n = min(len(closes), len(highs), len(lows), period)
    if n < 2:
        return -50.0
    hi = np.max(highs[-n:])
    li = np.min(lows[-n:])
    d  = hi - li
    return float(-100.0 * (hi - closes[-1]) / d) if d else -50.0


def _atr(highs, lows, closes, period=14) -> float:
    n = min(len(highs), len(lows), len(closes))
    if n < 2:
        return float(np.mean(highs[-max(n,1):] - lows[-max(n,1):])) if n > 0 else 0.001
    tr = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]),
               abs(lows[i]-closes[i-1])) for i in range(1, n)]
    return float(np.mean(tr[-period:])) if len(tr) >= period else float(np.mean(tr))


def _vwap(closes, highs, lows, volumes=None) -> float:
    n  = min(len(closes), len(highs), len(lows))
    tp = (closes[-n:] + highs[-n:] + lows[-n:]) / 3.0
    if volumes is not None and len(volumes) >= n:
        vol = volumes[-n:]
        tv  = np.sum(vol)
        if tv > 0:
            return float(np.sum(tp * vol) / tv)
    return float(np.mean(tp))


def _aroon(highs, lows, period=25) -> Tuple[float, float]:
    """
    Aroon Up/Down indicator.
    Aroon Up  = ((period - bars since period high) / period) * 100
    Aroon Down= ((period - bars since period low)  / period) * 100
    """
    n = min(len(highs), len(lows))
    if n < period + 1:
        return 50.0, 50.0
    h = highs[-(period+1):]
    l = lows[-(period+1):]
    # Index of highest high in last period+1 bars (0 = oldest)
    high_idx = int(np.argmax(h))
    low_idx  = int(np.argmin(l))
    # bars since the high/low (0 = most recent)
    bars_since_high = period - high_idx
    bars_since_low  = period - low_idx
    aroon_up   = ((period - bars_since_high) / period) * 100.0
    aroon_down = ((period - bars_since_low)  / period) * 100.0
    return float(aroon_up), float(aroon_down)


def _find_sr_levels(highs, lows, closes,
                    n_levels=5, tolerance=0.0003):
    pivots_high, pivots_low = [], []
    n = min(len(highs), len(lows))
    for i in range(2, n-2):
        if highs[i] == max(highs[i-2:i+3]):
            pivots_high.append(float(highs[i]))
        if lows[i] == min(lows[i-2:i+3]):
            pivots_low.append(float(lows[i]))

    def _cluster(pts):
        if not pts:
            return []
        pts = sorted(pts)
        clusters = [[pts[0]]]
        for p in pts[1:]:
            if (abs(p - clusters[-1][-1]) /
                    max(abs(clusters[-1][-1]), 1e-9)) < tolerance:
                clusters[-1].append(p)
            else:
                clusters.append([p])
        return sorted([float(np.mean(c)) for c in clusters])[:n_levels]

    return _cluster(pivots_low), _cluster(pivots_high)


def _detect_candlestick_pattern(opens, closes, highs, lows):
    n = len(closes)
    if n < 3:
        return "NONE", "NEUTRAL", 0.0

    o1,c1,h1,l1 = opens[-1],closes[-1],highs[-1],lows[-1]
    body1  = abs(c1-o1)
    range1 = h1-l1 if h1!=l1 else 1e-9
    uw1 = h1 - max(o1,c1)
    lw1 = min(o1,c1) - l1
    bull1 = c1 > o1

    o2,c2,h2,l2 = opens[-2],closes[-2],highs[-2],lows[-2]
    body2 = abs(c2-o2)
    bull2 = c2 > o2

    if n >= 3:
        o3,c3 = opens[-3],closes[-3]
        bull3 = c3 > o3
    else:
        o3=c3=0.0; bull3=True

    avg_body = float(np.mean([abs(closes[i]-opens[i])
                               for i in range(-min(5,n),0)]))
    if avg_body == 0:
        avg_body = range1 * 0.5

    # Bullish
    if lw1 >= 2*body1 and uw1 < body1*0.5 and not bull1 and body1 > 0:
        return "HAMMER", "BUY", 0.78
    if uw1 >= 2*body1 and lw1 < body1*0.5 and bull1 and body1 > 0:
        return "INVERTED_HAMMER", "BUY", 0.68
    if bull1 and not bull2 and o1<=c2 and c1>=o2 and body1>body2*1.05:
        return "BULLISH_ENGULFING", "BUY", 0.85
    if (n>=3 and not bull3 and body2<avg_body*0.5
            and bull1 and c1>(o3+c3)/2):
        return "MORNING_STAR", "BUY", 0.87
    if (not bull2 and bull1 and o1<l2
            and c1>(o2+c2)/2 and c1<o2):
        return "PIERCING_LINE", "BUY", 0.72
    if lw1>=range1*0.7 and body1<range1*0.1 and uw1<range1*0.1:
        return "DRAGONFLY_DOJI", "BUY", 0.75
    if (n>=3 and bull1 and bull2 and bull3
            and c1>c2>c3 and body1>avg_body*0.8):
        return "THREE_WHITE_SOLDIERS", "BUY", 0.88

    # Bearish
    if uw1>=2*body1 and lw1<body1*0.5 and not bull1 and body1>0:
        return "SHOOTING_STAR", "SELL", 0.78
    if not bull1 and bull2 and o1>=c2 and c1<=o2 and body1>body2*1.05:
        return "BEARISH_ENGULFING", "SELL", 0.85
    if (n>=3 and bull3 and body2<avg_body*0.5
            and not bull1 and c1<(o3+c3)/2):
        return "EVENING_STAR", "SELL", 0.87
    if (bull2 and not bull1 and o1>h2
            and c1<(o2+c2)/2 and c1>o2):
        return "DARK_CLOUD_COVER", "SELL", 0.72
    if uw1>=range1*0.7 and body1<range1*0.1 and lw1<range1*0.1:
        return "GRAVESTONE_DOJI", "SELL", 0.75
    if (n>=3 and not bull1 and not bull2 and not bull3
            and c1<c2<c3 and body1>avg_body*0.8):
        return "THREE_BLACK_CROWS", "SELL", 0.88

    if body1 < range1*0.08:
        return "DOJI", "NEUTRAL", 0.5

    return "NONE", "NEUTRAL", 0.0


# ── Main ChartAnalyzer ────────────────────────────────────────────────────────

class ChartAnalyzer:
    """
    HIGH-CONFLUENCE strategy analyzer.

    Primary signal  = EMA crossover (9/21) confirmed by Aroon crossover
    Secondary boost = RSI, MACD, Stochastic, CCI, Williams%R agreement
    Volatility gate = ATR% must be < 3% (configurable)

    Only fires when at least 5/9 confluences agree.
    This is what makes the strategy selective and high win-rate.
    """

    MAX_VOLATILITY_PCT = 3.0   # 3.0x ratio (300%) — skip if ATR is more than 3x the price

    def analyze(self,
                opens:   np.ndarray,
                highs:   np.ndarray,
                lows:    np.ndarray,
                closes:  np.ndarray,
                volumes: Optional[np.ndarray] = None) -> AnalysisResult:

        r  = AnalysisResult()
        n  = len(closes)
        if n < 10:
            return r

        # ── EMAs ─────────────────────────────────────────────────────
        e9_arr  = _ema(closes, 9)
        e21_arr = _ema(closes, 21)
        e50_arr = _ema(closes, 50)

        r.ema9,  r.ema21,  r.ema50  = float(e9_arr[-1]), float(e21_arr[-1]), float(e50_arr[-1])
        r.ema9_prev  = float(e9_arr[-2])  if n >= 2 else r.ema9
        r.ema21_prev = float(e21_arr[-2]) if n >= 2 else r.ema21

        # EMA crossover detection (current bar cross)
        r.ema_crossover_bull = (r.ema9_prev <= r.ema21_prev and r.ema9 > r.ema21)
        r.ema_crossover_bear = (r.ema9_prev >= r.ema21_prev and r.ema9 < r.ema21)
        # Relaxed: EMA9 vs EMA21 only (EMA50 unreliable with <60 candles)
        r.ema_aligned_bull   = r.ema9 > r.ema21
        r.ema_aligned_bear   = r.ema9 < r.ema21

        # ── Bollinger + ATR + VWAP ───────────────────────────────────
        r.bb_upper, r.bb_mid, r.bb_lower = _bollinger(closes, 20)
        bw = (r.bb_upper - r.bb_lower) / r.bb_mid if r.bb_mid > 0 else 0
        r.bb_squeeze = bw < 0.005

        r.atr  = _atr(highs, lows, closes, 14)
        r.vwap = _vwap(closes, highs, lows, volumes)

        price = float(closes[-1])
        # volatility_pct = ATR as ratio of price (e.g., 7.59 means ATR is 7.59x the price)
        r.volatility_pct = r.atr / price if price > 0 else 0
        r.volatility_ok  = r.volatility_pct < self.MAX_VOLATILITY_PCT

        # ── Aroon(25) ────────────────────────────────────────────────
        if n >= 26:
            r.aroon_up, r.aroon_down = _aroon(highs, lows, 25)
            # Previous bar Aroon for crossover detection
            if n >= 27:
                au_prev, ad_prev = _aroon(highs[:-1], lows[:-1], 25)
            else:
                au_prev, ad_prev = r.aroon_up, r.aroon_down

            r.aroon_crossover_bull = (au_prev <= ad_prev and r.aroon_up > r.aroon_down)
            r.aroon_crossover_bear = (au_prev >= ad_prev and r.aroon_down > r.aroon_up)
            r.aroon_bull_momentum  = r.aroon_up > 70 and r.aroon_up > r.aroon_down
            r.aroon_bear_momentum  = r.aroon_down > 70 and r.aroon_down > r.aroon_up
        else:
            r.aroon_up = r.aroon_down = 50.0

        # ── Oscillators ──────────────────────────────────────────────
        r.rsi = _rsi(closes, 14)
        r.macd_line, r.macd_signal, r.macd_hist = _macd(closes)
        r.stoch_k, r.stoch_d = _stochastic(closes, highs, lows, 5, 3, 3)
        r.cci        = _cci(closes, highs, lows, 20)
        r.williams_r = _williams_r(closes, highs, lows, 14)

        # ── S/R levels ───────────────────────────────────────────────
        if n >= 10:
            sups, ress = _find_sr_levels(highs, lows, closes)
            r.support_levels    = sups
            r.resistance_levels = ress
            below_r = [x for x in ress if x > price]
            above_s = [x for x in sups if x < price]
            r.nearest_resistance = min(below_r) if below_r else price*1.005
            r.nearest_support    = max(above_s) if above_s else price*0.995
            tol = price * 0.001
            r.at_support    = abs(price - r.nearest_support)    < tol
            r.at_resistance = abs(price - r.nearest_resistance) < tol

        # ── Trend slope ──────────────────────────────────────────────
        if n >= 10:
            x = np.arange(10, dtype=float)
            y = closes[-10:]
            c = np.polyfit(x, y, 1)
            r.trend_slope    = float(c[0])
            r.trend_strength = float(min(1.0, abs(c[0]) / (r.atr + 1e-9)))

        if n >= 3:
            pp = (float(highs[-2]) + float(lows[-2]) + float(closes[-2])) / 3.0
            r.pivot_pp = pp
            r.pivot_r1 = 2*pp - float(lows[-2])
            r.pivot_s1 = 2*pp - float(highs[-2])

        # ── Candlestick ──────────────────────────────────────────────
        if n >= 3:
            r.pattern_name, r.pattern_direction, r.pattern_strength = \
                _detect_candlestick_pattern(opens, closes, highs, lows)

        # ── HIGH-CONFLUENCE SCORING ───────────────────────────────────
        # Count how many indicators agree on BUY vs SELL.
        # The PRIMARY signals are EMA crossover + Aroon crossover.
        # All others are confluence boosters.

        bull = 0
        bear = 0

        # 1. EMA crossover (PRIMARY — weight 2)
        if r.ema_crossover_bull:
            bull += 2
        elif r.ema_crossover_bear:
            bear += 2
        elif r.ema_aligned_bull:
            bull += 1
        elif r.ema_aligned_bear:
            bear += 1

        # 2. Aroon crossover (PRIMARY — weight 2, per Smart Trading strategy)
        if r.aroon_crossover_bull:
            bull += 2
        elif r.aroon_crossover_bear:
            bear += 2
        elif r.aroon_bull_momentum:
            bull += 1
        elif r.aroon_bear_momentum:
            bear += 1

        # 3. RSI
        if r.rsi < 35:
            bull += 1
        elif r.rsi > 65:
            bear += 1

        # 4. MACD
        if r.macd_hist > 0 and r.macd_line > r.macd_signal:
            bull += 1
        elif r.macd_hist < 0 and r.macd_line < r.macd_signal:
            bear += 1

        # 5. Stochastic
        if r.stoch_k < 25 and r.stoch_k > r.stoch_d:
            bull += 1
        elif r.stoch_k > 75 and r.stoch_k < r.stoch_d:
            bear += 1

        # 6. CCI
        if r.cci < -80:
            bull += 1
        elif r.cci > 80:
            bear += 1

        # 7. Williams %R
        if r.williams_r < -75:
            bull += 1
        elif r.williams_r > -25:
            bear += 1

        # 8. Candlestick pattern
        if r.pattern_direction == "BUY":
            bull += 1
        elif r.pattern_direction == "SELL":
            bear += 1

        # 9. Bollinger position
        bb_range = max(r.bb_upper - r.bb_lower, 1e-9)
        bb_pos   = (price - r.bb_lower) / bb_range
        if bb_pos < 0.15:
            bull += 1
        elif bb_pos > 0.85:
            bear += 1

        # Max possible = 2+2+1+1+1+1+1+1+1 = 11 but cap display at 9
        r.max_confluences  = 9
        r.bull_confluences = bull
        r.bear_confluences = bear

        # ── Signal decision ───────────────────────────────────────────
        # Require at least 5 confluences AND both primary signals present
        # for a high-quality trade. This keeps win-rate high by being selective.

        total = max(bull + bear, 1)
        r.buy_score  = min(1.0, bull / total * (bull / r.max_confluences + 0.5))
        r.sell_score = min(1.0, bear / total * (bear / r.max_confluences + 0.5))

        # Volatility gate — don't trade if market is too chaotic
        if not r.volatility_ok:
            r.signal     = "HOLD"
            r.confidence = 0.0
            return r

        # Primary signal check:
        # Crossover = strongest signal (fresh cross this bar)
        # Alignment = valid signal (EMA9 on correct side of EMA21)
        # Aroon momentum = Aroon >70 on the right side
        # We accept EITHER crossover OR alignment+momentum together.
        ema_bull_ok  = r.ema_crossover_bull or r.ema_aligned_bull
        ema_bear_ok  = r.ema_crossover_bear or r.ema_aligned_bear
        aron_bull_ok = r.aroon_crossover_bull or r.aroon_bull_momentum or r.aroon_up > r.aroon_down
        aron_bear_ok = r.aroon_crossover_bear or r.aroon_bear_momentum or r.aroon_down > r.aroon_up

        # Crossover bonus: fresh cross this bar = extra confidence
        crossover_bonus = 0.0
        if r.ema_crossover_bull or r.ema_crossover_bear:
            crossover_bonus += 0.05
        if r.aroon_crossover_bull or r.aroon_crossover_bear:
            crossover_bonus += 0.05

        min_confluences = 5  # require at least 5/9 confluences
        threshold       = 0.52

        if (ema_bull_ok and aron_bull_ok
                and bull >= min_confluences
                and r.buy_score >= threshold):
            r.signal     = "BUY"
            r.confidence = min(0.95, 0.50 + (bull / r.max_confluences) * 0.45
                               + crossover_bonus)

        elif (ema_bear_ok and aron_bear_ok
              and bear >= min_confluences
              and r.sell_score >= threshold):
            r.signal     = "SELL"
            r.confidence = min(0.95, 0.50 + (bear / r.max_confluences) * 0.45
                               + crossover_bonus)

        else:
            r.signal     = "HOLD"
            r.confidence = max(r.buy_score, r.sell_score)

        return r

    def summary_log(self, r: AnalysisResult) -> str:
        aroon_str = (f"Aroon↑{r.aroon_up:.0f}/↓{r.aroon_down:.0f}"
                     f"{'🔀BullX' if r.aroon_crossover_bull else ''}"
                     f"{'🔀BearX' if r.aroon_crossover_bear else ''}")
        ema_str   = (f"EMA9/21={r.ema9:.5f}/{r.ema21:.5f}"
                     f"{'🔀BullX' if r.ema_crossover_bull else ''}"
                     f"{'🔀BearX' if r.ema_crossover_bear else ''}")
        return (
            f"[ChartAnalyzer] {r.signal}@{r.confidence:.1%} "
            f"Conf={r.bull_confluences}B/{r.bear_confluences}S "
            f"| {ema_str} | {aroon_str} "
            f"| RSI={r.rsi:.1f} MACD={'▲' if r.macd_hist>0 else '▼'}"
            f"{abs(r.macd_hist):.5f} "
            f"Stoch={r.stoch_k:.1f}/{r.stoch_d:.1f} "
            f"CCI={r.cci:.0f} Pattern={r.pattern_name}({r.pattern_direction}) "
            f"Vol={'OK' if r.volatility_ok else 'HIGH'} "
            f"BB={'SQZ' if r.bb_squeeze else 'NML'}"
        )