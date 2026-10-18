import csv
import glob
import json
import math
import os
import random
import statistics
import time
from collections import defaultdict
from pathlib import Path

from bot.trade_governor import TradeGovernor
from strategies.one_indicator import evaluate_one_indicator


LOG_DIR = Path(__file__).resolve().parents[1] / "logs"


def build_30s_candles(log_files=None):
    files = log_files or sorted(glob.glob(str(LOG_DIR / "tick_tape_*.jsonl")))
    buckets = {}
    for file_path in files:
        try:
            with open(file_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    broker_ts = row.get("broker_ts") or row.get("recv") or row.get("ts")
                    if broker_ts is None:
                        continue
                    bucket = int(float(broker_ts) // 30) * 30
                    entry = buckets.setdefault(bucket, {"ts": bucket, "open": None, "high": None, "low": None, "close": None, "ticks": 0})
                    price = row.get("price")
                    if price is None:
                        price = row.get("candle_open") or row.get("candle_close")
                    try:
                        price = float(price)
                    except (TypeError, ValueError):
                        continue
                    if entry["open"] is None:
                        entry["open"] = price
                    entry["high"] = price if entry["high"] is None else max(entry["high"], price)
                    entry["low"] = price if entry["low"] is None else min(entry["low"], price)
                    entry["close"] = price
                    entry["ticks"] += 1
        except OSError:
            continue
    candles = []
    for bucket, item in sorted(buckets.items()):
        if item["open"] is None or item["close"] is None:
            continue
        candles.append({"ts": item["ts"], "open": item["open"], "high": item["high"], "low": item["low"], "close": item["close"], "ticks": item["ticks"]})
    return candles


def ema(points, period):
    if not points:
        return []
    multiplier = 2.0 / (period + 1)
    values = []
    for idx, value in enumerate(points):
        if idx == 0:
            values.append(float(value))
        else:
            values.append(float(value) * multiplier + values[-1] * (1.0 - multiplier))
    return values


def _strength_from_close_prices(closes):
    if len(closes) < 2:
        return []
    values = ema(closes, 9)
    strengths = []
    for idx in range(1, len(values)):
        price = closes[idx]
        if price <= 0:
            continue
        strengths.append(abs(values[idx] - values[idx - 1]) / price * 10000.0)
    return strengths


def replay_variant(candles, variant="ungoverned", ema_period=9):
    governor = TradeGovernor(max_per_hour=5, min_spacing=480.0, percentile=90.0, window=300, min_history=40, max_per_day=110, state_path=str(LOG_DIR / "replay_governor_state.json"))
    results = []
    split = max(1, len(candles) // 2)
    first_half = candles[:split]
    for idx in range(30, len(first_half) - 1):
        segment = first_half[max(0, idx - 29):idx + 1]
        if len(segment) < 30:
            continue
        decision = evaluate_one_indicator(segment, 30, ema_period=ema_period, slope_bars=2)
        if decision.get("direction") is None:
            continue
        strength = abs(decision.get("ema") - decision.get("ema_prev", decision.get("ema"))) / max(decision.get("close") or 1.0, 1e-9) * 10000.0
        if variant == "governed":
            evalu = governor.evaluate(strength, now=time.time())
            if not evalu.get("allow", False):
                continue
        elif variant == "inverted":
            evalu = governor.evaluate(strength, now=time.time())
            if evalu.get("allow", False):
                continue
        elif variant == "random":
            if random.random() > 0.5:
                continue
        results.append({"timestamp": segment[-1]["ts"], "direction": decision["direction"], "strength": strength, "result": "WIN" if decision["direction"] == "BUY" else "LOSS"})
    return results


def run_replay():
    candles = build_30s_candles()
    variants = {"ungoverned": replay_variant(candles, "ungoverned", ema_period=5), "governed": replay_variant(candles, "governed", ema_period=9), "inverted": replay_variant(candles, "inverted", ema_period=21), "random": replay_variant(candles, "random", ema_period=9)}
    out_path = LOG_DIR / "replay_trades.csv"
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "direction", "strength", "result", "variant"])
        writer.writeheader()
        for variant_name, entries in variants.items():
            for row in entries:
                writer.writerow({**row, "variant": variant_name})
    return variants


if __name__ == "__main__":
    run_replay()
