#!/usr/bin/env python3
"""Train RandomForest predictors per asset and timeframe.

Usage: python scripts/train_predictors.py
"""
import os
import sys
import json
# add project root to path
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from data.fetcher import fetch_candles
from models.predictor import RandomForestPredictor

ASSETS = ['EURUSD', 'GBPUSD', 'USDJPY', 'BTCUSD']
TFS = ['M1', 'M5', 'M15']


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def train_all(assets=ASSETS, tfs=TFS):
    report = []
    for asset in assets:
        for tf in tfs:
            print(f"Training model for {asset} {tf}...")
            df = fetch_candles(asset, timeframe=tf, n=2000)
            model_dir = os.path.join('models', f"{asset}_{tf}")
            ensure_dir(model_dir)
            pred = RandomForestPredictor(model_dir=model_dir, lookback=200)
            try:
                pred.train(df)
                report.append({'asset': asset, 'tf': tf, 'status': 'trained', 'model_dir': model_dir})
            except Exception as e:
                report.append({'asset': asset, 'tf': tf, 'status': 'error', 'error': str(e)})
    # save report
    try:
        with open('reports/train_report.json', 'w') as f:
            json.dump(report, f, indent=2)
    except Exception:
        pass
    print('Training complete. Report saved to reports/train_report.json')


if __name__ == '__main__':
    train_all()
