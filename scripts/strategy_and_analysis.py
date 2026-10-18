#!/usr/bin/env python3
"""Run the Heikin-Ashi+RSI filter + PSAR+MACD entry strategy and analyze historical trades.

Usage: python scripts/strategy_and_analysis.py [ASSET] [TF]
Example: python scripts/strategy_and_analysis.py EURUSD M1
"""
import sys
import os
from datetime import datetime
import json

# Ensure project root is on sys.path for local imports
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pandas as pd

from data.fetcher import fetch_candles
from utils.technical import strategy_signal
from models.predictor import get_predictor, Signal


def live_signal(asset: str, tf: str = 'M1'):
    df = fetch_candles(asset, timeframe=tf, n=300)
    sig, details = strategy_signal(df)
    # Final confirmation: use predictor if available
    # Try asset/timeframe-specific model first, then fallback to generic 'models'
    model_dir_specific = f"models/{asset}_{tf}"
    pred = get_predictor('random_forest', model_dir=model_dir_specific)
    if not pred.load():
        pred = get_predictor('random_forest', model_dir='models')
    try:
        pred.load()
        model_sig, conf = pred.predict(df)
    except Exception:
        model_sig, conf = Signal.HOLD, 0.0

    confirmed = (model_sig.value == sig) and (conf >= 0.7)

    out = {
        'asset': asset,
        'timeframe': tf,
        'strategy_signal': sig,
        'strategy_details': details,
        'model_signal': model_sig.value,
        'model_confidence': float(conf),
        'confirmed_by_model': bool(confirmed),
        'timestamp': datetime.now().isoformat(),
    }
    print(json.dumps(out, indent=2))
    return out


def backtest_against_journal(asset: str = None, tf: str = 'M1', limit: int = 200):
    # Read trade journal and test strategy precision vs settled trades
    try:
        journal = pd.read_csv('logs/trade_journal.csv')
    except Exception as e:
        print('Could not read journal:', e)
        return

    if 'state_key' in journal.columns:
        journal = journal[journal['state_key'] != 'DEMO_EXECUTION_TEST'].copy()
    journal['timestamp'] = pd.to_datetime(journal['timestamp'])
    settled = journal[journal['result'].isin(['WIN', 'LOSS'])].sort_values('timestamp', ascending=False)
    settled = settled.head(limit)

    matched = 0
    wins = 0
    total_checked = 0

    for _, row in settled.iterrows():
        if asset and row['asset'] != asset:
            continue
        ts = row['timestamp']
        try:
            df = fetch_candles(row['asset'], timeframe=tf, n=500)
            # select candles up to trade timestamp
            df_up_to = df.loc[:ts]
            if len(df_up_to) < 15:
                continue
            sig, _ = strategy_signal(df_up_to)
            if sig == 'HOLD':
                continue
            total_checked += 1
            if sig == row['signal']:
                matched += 1
                if row['result'] == 'WIN':
                    wins += 1
        except Exception:
            continue

    if total_checked == 0:
        print('No historical trades matched strategy conditions in sample')
        return

    print(f"Backtest sample: checked={total_checked}, matched_direction={matched}, wins_when_matched={wins}")
    print(f"Precision (direction match): {matched/total_checked:.3f}")
    print(f"Win rate when matched: {wins/max(1, matched):.3f}")


if __name__ == '__main__':
    asset = sys.argv[1] if len(sys.argv) > 1 else 'EURUSD'
    tf = sys.argv[2] if len(sys.argv) > 2 else 'M1'
    print('LIVE SIGNAL:')
    live_signal(asset, tf)
    print('\nRUNNING BACKTEST (recent settled trades):')
    backtest_against_journal(asset=asset, tf=tf, limit=300)
