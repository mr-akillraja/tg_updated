"""
agents/multi_timeframe_consensus.py — Multi-Timeframe Consensus Analyzer v2
════════════════════════════════════════════════════════════════════════════════
Removed 5s, 10s, 15s, 20s timeframes — OlympTrade does not provide these.
Now uses only 30s and 60s timeframes to avoid permanent HOLD warnings.
"""

from __future__ import annotations
import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import logging

log = logging.getLogger("multi_tf_consensus")


@dataclass
class TimeframeSignal:
    timeframe_sec: int
    signal: str        # BUY, SELL, HOLD
    confidence: float  # 0.0-1.0
    bull_confluences: int
    bear_confluences: int
    trend_strength: float  # 0.0-1.0


@dataclass
class ConsensusResult:
    primary_signal: str       # BUY, SELL, HOLD
    consensus_score: float    # 0.0-1.0
    signals_by_timeframe: Dict[int, TimeframeSignal]
    trend_direction: str      # UP, DOWN, SIDEWAYS
    trend_confidence: float   # 0.0-1.0
    downtrend_strength: float # 0.0-1.0
    summary: str


class MultiTimeframeConsensusAnalyzer:
    """
    Analyzes 30s and 60s timeframes and builds consensus.
    Signals trades when 60%+ of timeframes agree (lowered from 75%).
    """

    TIMEFRAMES_SEC = [30, 60]   # removed 5, 10, 15, 20 — not available on OlympTrade
    MIN_CONSENSUS_PCT = 0.60    # lowered from 0.75 to get more trade signals

    def __init__(self, chart_analyzer):
        self.chart = chart_analyzer
        self.signals_history: Dict[int, List[TimeframeSignal]] = {
            tf: [] for tf in self.TIMEFRAMES_SEC
        }

    def analyze_consensus(
        self,
        candles_dict: Dict[int, Dict[str, np.ndarray]]
    ) -> ConsensusResult:
        signals_by_tf = {}
        bull_count = 0
        sell_count = 0
        hold_count = 0

        for tf_sec in self.TIMEFRAMES_SEC:
            if tf_sec not in candles_dict:
                # No warning spam — just skip silently
                continue

            ohlc = candles_dict[tf_sec]
            signal = self._analyze_timeframe(tf_sec, ohlc)
            signals_by_tf[tf_sec] = signal

            if signal.signal == "BUY":
                bull_count += 1
            elif signal.signal == "SELL":
                sell_count += 1
            else:
                hold_count += 1

        total_tf = len(signals_by_tf)
        if total_tf == 0:
            return ConsensusResult(
                primary_signal="HOLD",
                consensus_score=0.0,
                signals_by_timeframe={},
                trend_direction="SIDEWAYS",
                trend_confidence=0.0,
                downtrend_strength=0.0,
                summary="[Consensus] No candle data available yet"
            )

        bull_consensus = bull_count / total_tf
        sell_consensus = sell_count / total_tf
        hold_consensus = hold_count / total_tf

        if bull_consensus >= self.MIN_CONSENSUS_PCT:
            primary = "BUY"
            consensus = bull_consensus
        elif sell_consensus >= self.MIN_CONSENSUS_PCT:
            primary = "SELL"
            consensus = sell_consensus
        else:
            primary = "HOLD"
            consensus = max(bull_consensus, sell_consensus, hold_consensus)

        trend_dir, trend_conf, downtrend_str = self._detect_trend(signals_by_tf)

        signal_counts = f"{bull_count}B/{sell_count}S/{hold_count}H"
        summary = (
            f"[Consensus] {primary}@{consensus:.0%} ({signal_counts}) | "
            f"Trend={trend_dir}@{trend_conf:.0%} | Downtrend={downtrend_str:.0%}"
        )

        return ConsensusResult(
            primary_signal=primary,
            consensus_score=consensus,
            signals_by_timeframe=signals_by_tf,
            trend_direction=trend_dir,
            trend_confidence=trend_conf,
            downtrend_strength=downtrend_str,
            summary=summary
        )

    def _analyze_timeframe(self, tf_sec: int, ohlc: Dict) -> TimeframeSignal:
        try:
            o = np.array(ohlc.get('o', []), dtype=float)
            h = np.array(ohlc.get('h', []), dtype=float)
            l = np.array(ohlc.get('l', []), dtype=float)
            c = np.array(ohlc.get('c', []), dtype=float)

            if len(c) < 5:
                return TimeframeSignal(
                    timeframe_sec=tf_sec,
                    signal="HOLD",
                    confidence=0.0,
                    bull_confluences=0,
                    bear_confluences=0,
                    trend_strength=0.0
                )

            result = self.chart.analyze(o, h, l, c)

            trend_str = min(result.bull_confluences, result.bear_confluences)
            trend_str = float(trend_str) / result.max_confluences if result.max_confluences > 0 else 0.0

            return TimeframeSignal(
                timeframe_sec=tf_sec,
                signal=result.signal,
                confidence=result.confidence,
                bull_confluences=result.bull_confluences,
                bear_confluences=result.bear_confluences,
                trend_strength=trend_str
            )

        except Exception as e:
            log.warning(f"[ConsensusAnalyzer] Error analyzing {tf_sec}s: {e}")
            return TimeframeSignal(
                timeframe_sec=tf_sec,
                signal="HOLD",
                confidence=0.0,
                bull_confluences=0,
                bear_confluences=0,
                trend_strength=0.0
            )

    def _detect_trend(
        self,
        signals_by_tf: Dict[int, TimeframeSignal]
    ) -> Tuple[str, float, float]:
        if not signals_by_tf:
            return "SIDEWAYS", 0.0, 0.0

        bull_signals = sum(1 for s in signals_by_tf.values() if s.signal == "BUY")
        sell_signals = sum(1 for s in signals_by_tf.values() if s.signal == "SELL")
        total = len(signals_by_tf)

        bull_pct = bull_signals / total
        sell_pct = sell_signals / total

        if bull_pct >= 0.60:
            trend = "UP"
            confidence = bull_pct
        elif sell_pct >= 0.60:
            trend = "DOWN"
            confidence = sell_pct
        else:
            trend = "SIDEWAYS"
            confidence = max(bull_pct, sell_pct)

        downtrend_strength = sell_pct
        return trend, confidence, downtrend_strength

    def should_block_buy_on_downtrend(
        self,
        consensus: ConsensusResult,
        downtrend_threshold: float = 0.67
    ) -> Tuple[bool, str]:
        if consensus.downtrend_strength >= downtrend_threshold:
            return True, (
                f"Strong downtrend detected ({consensus.downtrend_strength:.0%} TFs selling) "
                f"— blocking BUY orders"
            )
        return False, ""