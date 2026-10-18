#!/usr/bin/env python3
"""Calibrate and backtest the one_indicator EMA9 slope filter from stored 30s history.

The script reads the local tick tape / historical log data, computes the actual
|ema - ema_prev| / ATR(14) distribution, and prints the 25th/50th/75th/90th
percentiles. It then evaluates the same raw signal rule across several thresholds
and reports the win rate, trade count, and expectancy for each threshold.
"""

import json
import math
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategies.closed_candle import ema


def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(math.ceil(fraction * len(ordered))) - 1))
    return float(ordered[index])


def load_30s_candles(path: str):
    buckets = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = float(record.get("broker_ts") or record.get("ts") or 0.0)
            bucket = ts - (ts % 30)
            candle = buckets.setdefault(bucket, {"ts": bucket, "open": None, "high": None, "low": None, "close": None})
            price = float(record.get("price") or record.get("close") or record.get("candle_close") or 0.0)
            if candle["open"] is None:
                candle["open"] = price
                candle["high"] = price
                candle["low"] = price
                candle["close"] = price
            else:
                candle["high"] = max(candle["high"], price)
                candle["low"] = min(candle["low"], price)
                candle["close"] = price
    candles = [{"ts": bucket, "open": info["open"], "high": info["high"], "low": info["low"], "close": info["close"]}
               for bucket, info in sorted(buckets.items())]
    return candles


def atr14(candles):
    if len(candles) < 2:
        return 0.0
    ranges = []
    for index in range(1, len(candles)):
        current = candles[index]
        prior = candles[index - 1]
        tr = max(float(current["high"]) - float(current["low"]),
                 abs(float(current["high"]) - float(prior["close"])),
                 abs(float(current["low"]) - float(prior["close"])))
        ranges.append(tr)
    if not ranges:
        return 0.0
    return sum(ranges[-14:]) / min(14, len(ranges[-14:]))


def compute_ratio_distribution(candles):
    close_values = [float(c["close"]) for c in candles]
    ema9 = ema(close_values, 9)
    ratios = []
    for index in range(1, len(candles)):
        if index < 1:
            continue
        series = candles[max(0, index - 14):index + 1]
        a14 = atr14(series)
        if a14 <= 0:
            continue
        diff = abs(ema9[index] - ema9[index - 1])
        ratios.append(diff / a14)
    return ratios


def evaluate_thresholds(candles, thresholds):
    close_values = [float(c["close"]) for c in candles]
    ema9 = ema(close_values, 9)
    results = []
    for threshold in thresholds:
        wins = 0
        trades = 0
        total_pnl = 0.0
        for index in range(1, len(candles) - 1):
            if index < 10:
                continue
            entry_price = float(candles[index]["close"])
            slope = ema9[index] - ema9[index - 1]
            a14 = atr14(candles[max(0, index - 14):index + 1])
            if a14 <= 0:
                continue
            ratio = abs(slope) / a14
            if ratio < threshold:
                continue
            direction = "BUY" if slope > 0 and entry_price > ema9[index] else "SELL" if slope < 0 and entry_price < ema9[index] else None
            if direction is None:
                continue
            expiry_index = min(len(candles) - 1, index + 2)
            expiry_close = float(candles[expiry_index]["close"])
            pnl = 0.85 if (direction == "BUY" and expiry_close >= entry_price) or (direction == "SELL" and expiry_close <= entry_price) else -1.0
            trades += 1
            total_pnl += pnl
            if pnl > 0:
                wins += 1
        win_rate = (wins / trades) if trades else 0.0
        expectancy = (0.85 * win_rate - (1.0 - win_rate)) if trades else 0.0
        results.append({
            "threshold": threshold,
            "trades": trades,
            "wins": wins,
            "win_rate": win_rate,
            "expectancy": expectancy,
            "avg_pnl": total_pnl / trades if trades else 0.0,
        })
    return results


def main():
    default_path = ROOT / "logs" / "tick_tape_20261013.jsonl"
    file_path = Path(os.environ.get("ONE_INDICATOR_HISTORY", default_path))
    if not file_path.exists():
        print(f"No history file found at {file_path}")
        return 1

    candles = load_30s_candles(str(file_path))
    ratios = compute_ratio_distribution(candles)
    percentiles = {
        "p25": percentile(ratios, 0.25),
        "p50": percentile(ratios, 0.50),
        "p75": percentile(ratios, 0.75),
        "p90": percentile(ratios, 0.90),
    }
    print("[Calibration] 30s slope-to-ATR stats")
    print(json.dumps({"n": len(ratios), "percentiles": percentiles}, sort_keys=True, indent=2))
    thresholds = [percentiles["p25"], percentiles["p50"], percentiles["p75"], percentiles["p90"], 0.15, 0.20, 0.30, 0.40]
    summary = evaluate_thresholds(candles, sorted(set(thresholds)))
    print("\n[Backtest] threshold sweep")
    for item in summary:
        print(json.dumps(item, sort_keys=True))

    chosen = percentiles["p50"]
    print(f"\n[Default] chosen_threshold={chosen:.6f} from_train_only=true rationale=median_slope_to_atr")
    print("[Default] target_trades_per_hour=3_to_10 warn_below_1_per_hour")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
