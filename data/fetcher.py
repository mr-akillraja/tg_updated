"""External exchange history only; never substitute it for broker OTC quotes."""

import logging
import time
from typing import Dict

import numpy as np
import pandas as pd

log = logging.getLogger("data.fetcher")

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    log.warning("yfinance not installed — external history unavailable")
    _YF_AVAILABLE = False

_TF_TO_YF_INTERVAL = {
    "M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m",
    "H1": "1h", "D1": "1d",
    60: "1m", 300: "5m", 900: "15m", 1800: "30m", 3600: "1h", 86400: "1d",
}
_TF_TO_YF_PERIOD = {
    "1m": "1d", "5m": "5d", "15m": "60d",
    "30m": "60d", "1h": "730d", "1d": "5y",
}
_SYMBOL_MAP = {
    "EURUSD": "EURUSD=X",   "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",   "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",   "NZDUSD": "NZDUSD=X",
    "EURGBP": "EURGBP=X",   "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",    "LTCUSD": "LTC-USD",
}

# Cache policy is independent of native candle resolution.
_cache: Dict[str, tuple] = {}
_CACHE_TTL = 5.0


class MarketDataUnavailable(RuntimeError):
    """The requested genuine market data is not supplied by this source."""


def _yf_symbol(asset: str) -> str:
    normalized = asset.upper().replace("/", "").replace(" ", "")
    if "OTC" in normalized:
        raise MarketDataUnavailable(
            f"Yahoo does not supply broker OTC data for {asset}; use verified broker quotes/history")
    symbol = _SYMBOL_MAP.get(normalized)
    if symbol is None:
        raise MarketDataUnavailable(f"No verified Yahoo symbol mapping for {asset}")
    return symbol


def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    df["ema9"]  = c.ewm(span=9,  adjust=False).mean()
    df["ema21"] = c.ewm(span=21, adjust=False).mean()
    df["ema50"] = c.ewm(span=50, adjust=False).mean()

    from agents.agent_multi_timeframe import _rsi
    closes = c.to_numpy(dtype=float)
    df["rsi"] = [_rsi(closes[:i + 1], 14) for i in range(len(closes))]

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["macd"]        = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"]   = df["macd"] - df["macd_signal"]

    roll20 = c.rolling(20)
    df["bb_mid"]   = roll20.mean()
    df["bb_upper"] = df["bb_mid"] + 2 * roll20.std()
    df["bb_lower"] = df["bb_mid"] - 2 * roll20.std()

    lo14 = df["low"].rolling(14).min()
    hi14 = df["high"].rolling(14).max()
    df["stoch_k"] = 100 * (c - lo14) / (hi14 - lo14 + 1e-12)
    df["stoch_d"] = df["stoch_k"].rolling(3).mean()

    prev_c = c.shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_c).abs(),
        (df["low"]  - prev_c).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    return df


def fetch_candles(asset: str, timeframe: str = "M1", n: int = 200) -> pd.DataFrame:
    """Fetch genuine closed candles, or fail explicitly without fabricated data."""
    if timeframe not in _TF_TO_YF_INTERVAL:
        raise MarketDataUnavailable(
            f"Yahoo does not provide native {timeframe} candles; lower-resolution OHLC cannot be upsampled")
    symbol = _yf_symbol(asset)
    if n <= 0:
        raise ValueError("Candle count must be positive")
    if not _YF_AVAILABLE:
        raise MarketDataUnavailable("yfinance is not installed")
    cache_key = f"{asset}_{timeframe}_{n}"
    cached = _cache.get(cache_key)
    if cached and (time.time() - cached[1]) < _CACHE_TTL:
        return cached[0].copy()

    yf_interval = _TF_TO_YF_INTERVAL[timeframe]
    yf_period = _TF_TO_YF_PERIOD[yf_interval]
    seconds = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800,
               "1h": 3600, "1d": 86400}[yf_interval]
    try:
        raw = yf.Ticker(symbol).history(period=yf_period, interval=yf_interval)
    except Exception as exc:
        raise MarketDataUnavailable(f"Yahoo history failed for {symbol}: {exc}") from exc
    if raw.empty:
        raise MarketDataUnavailable(f"Yahoo returned no {yf_interval} history for {symbol}")
    raw = raw.rename(columns={"Open": "open", "High": "high", "Low": "low",
                              "Close": "close", "Volume": "volume"})
    required = ["open", "high", "low", "close", "volume"]
    if not set(required).issubset(raw.columns) or not isinstance(raw.index, pd.DatetimeIndex):
        raise MarketDataUnavailable(f"Yahoo returned invalid OHLC history for {symbol}")
    raw = raw[required].sort_index()
    raw.index = raw.index.tz_localize("UTC") if raw.index.tz is None else raw.index.tz_convert("UTC")
    raw = raw.loc[raw.index + pd.Timedelta(seconds=seconds) <= pd.Timestamp.now(tz="UTC")]
    raw = raw.dropna(subset=["open", "high", "low", "close"]).tail(n).copy()
    if raw.empty:
        raise MarketDataUnavailable(f"Yahoo returned no closed {yf_interval} candles for {symbol}")
    values = raw[["open", "high", "low", "close"]]
    if (not np.isfinite(values.to_numpy()).all() or (values <= 0).any().any()
            or (raw["low"] > raw[["open", "close"]].min(axis=1)).any()
            or (raw["high"] < raw[["open", "close"]].max(axis=1)).any()
            or raw.index.has_duplicates):
        raise MarketDataUnavailable(f"Yahoo returned invalid OHLC values for {symbol}")
    raw = _compute_indicators(raw)
    raw.attrs.update(source=f"yahoo:{symbol}", timeframe_seconds=seconds, closed_only=True)
    _cache[cache_key] = (raw, time.time())
    return raw.copy()


def fetch_multi_timeframe(asset: str, timeframes=None) -> Dict[str, pd.DataFrame]:
    if timeframes is None:
        timeframes = ["M1", "M5", "M15", "H1"]
    return {tf: fetch_candles(asset, tf, n=200) for tf in timeframes}

