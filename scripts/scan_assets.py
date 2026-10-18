#!/usr/bin/env python3
"""Scan a list of assets and timeframes, rank signals by model confirmation.

Usage: python scripts/scan_assets.py
"""
import json
import os
import sys
from datetime import datetime

# Ensure project root is on sys.path so local packages (data/, utils/, models/) import cleanly
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from data.fetcher import fetch_candles
from scripts.strategy_and_analysis import live_signal


DEFAULT_ASSETS = ['EURUSD', 'GBPUSD', 'USDJPY', 'BTCUSD']
DEFAULT_TFS = ['M1', 'M5', 'M15']


def scan(assets=None, tfs=None):
    if assets is None:
        assets = DEFAULT_ASSETS
    if tfs is None:
        tfs = DEFAULT_TFS

    results = []
    for asset in assets:
        for tf in tfs:
            try:
                out = live_signal(asset, tf)
                results.append(out)
            except Exception as e:
                results.append({'asset': asset, 'timeframe': tf, 'error': str(e)})

    # Rank: confirmed_by_model desc, model_confidence desc
    ranked = sorted(results, key=lambda r: (r.get('confirmed_by_model', False), r.get('model_confidence', 0.0)), reverse=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_file = f'reports/scan_{ts}.json'
    try:
        with open(out_file, 'w') as f:
            json.dump(ranked, f, indent=2)
    except Exception:
        pass

    for r in ranked:
        if 'error' in r:
            print(f"{r['asset']} {r['timeframe']}: ERROR {r['error']}")
            continue
        print(f"{r['asset']} {r['timeframe']}: strategy={r['strategy_signal']} model={r['model_signal']} conf={r['model_confidence']:.2f} confirmed={r['confirmed_by_model']}")

    print('\nSaved scan to', out_file)
    return ranked


if __name__ == '__main__':
    scan()
