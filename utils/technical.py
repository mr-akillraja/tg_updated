"""Technical indicators helpers: Heikin-Ashi, Parabolic SAR, and strategy rules.
"""
import numpy as np
import pandas as pd


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """Return a DataFrame with heikin-ashi OHLC columns appended as ha_open, ha_high, ha_low, ha_close.

    Assumes df has 'open','high','low','close' columns.
    """
    ha = pd.DataFrame(index=df.index)
    ha['ha_close'] = (df['open'] + df['high'] + df['low'] + df['close']) / 4.0
    ha['ha_open'] = 0.0
    for i in range(len(df)):
        if i == 0:
            ha.iat[0, ha.columns.get_loc('ha_open')] = (df['open'].iat[0] + df['close'].iat[0]) / 2.0
        else:
            ha.iat[i, ha.columns.get_loc('ha_open')] = (ha.iat[i-1, ha.columns.get_loc('ha_open')] + ha.iat[i-1, ha.columns.get_loc('ha_close')]) / 2.0
    ha['ha_high'] = pd.concat([df['high'], ha['ha_open'], ha['ha_close']], axis=1).max(axis=1)
    ha['ha_low']  = pd.concat([df['low'], ha['ha_open'], ha['ha_close']], axis=1).min(axis=1)
    return pd.concat([df, ha[['ha_open','ha_high','ha_low','ha_close']]], axis=1)


def parabolic_sar(df: pd.DataFrame, af_start=0.02, af_step=0.02, af_max=0.2) -> pd.Series:
    """Compute Parabolic SAR series.

    Returns a Series aligned with df.index representing the SAR value.
    """
    high = df['high'].astype(float).values
    low = df['low'].astype(float).values
    length = len(df)
    if length < 2:
        return pd.Series(np.nan, index=df.index)

    sar = np.zeros(length)
    bull = True
    af = af_start
    ep = high[0]
    sar[0] = low[0]

    for i in range(1, length):
        prev_sar = sar[i-1]
        if bull:
            sar[i] = prev_sar + af * (ep - prev_sar)
        else:
            sar[i] = prev_sar + af * (ep - prev_sar)

        # For current bar, adjust sar to not be inside price
        if bull:
            if sar[i] > low[i] or sar[i] > low[i-1]:
                sar[i] = min(low[i], low[i-1])
        else:
            if sar[i] < high[i] or sar[i] < high[i-1]:
                sar[i] = max(high[i], high[i-1])

        # Check reversal
        if bull:
            if low[i] < sar[i]:
                bull = False
                sar[i] = ep
                ep = low[i]
                af = af_start
        else:
            if high[i] > sar[i]:
                bull = True
                sar[i] = ep
                ep = high[i]
                af = af_start

        # Update EP and AF if no reversal
        if bull:
            if high[i] > ep:
                ep = high[i]
                af = min(af + af_step, af_max)
        else:
            if low[i] < ep:
                ep = low[i]
                af = min(af + af_step, af_max)

    return pd.Series(sar, index=df.index)


def macd_hist(df: pd.DataFrame) -> pd.Series:
    return df['macd'] - df['macd_signal'] if 'macd' in df.columns and 'macd_signal' in df.columns else pd.Series(np.nan, index=df.index)


def strategy_signal(df: pd.DataFrame) -> (str, dict):
    """Return ('BUY'|'SELL'|'HOLD', details) based on the combined rules.

    Rules (simple, configurable):
      - Market filter: Heikin-Ashi trend + RSI
        * Bullish filter: last 3 HA candles are bullish (ha_close > ha_open) and RSI > 50
        * Bearish filter: last 3 HA candles are bearish and RSI < 50
      - Entry timing: Parabolic SAR flip + MACD histogram cross
        * Bull entry: PSAR flips below price (sar < low) and MACD hist crosses positive
        * Sell entry: PSAR flips above price and MACD hist crosses negative
      - Final confirmation: leave to external 'Signals' predictor (not applied here)
    """
    details = {}
    if len(df) < 10:
        return 'HOLD', details

    df = df.copy()
    df = heikin_ashi(df)
    df['psar'] = parabolic_sar(df)
    df['macd_hist'] = macd_hist(df)

    # Market filter
    last = df.iloc[-3:]
    ha_bull = (last['ha_close'] > last['ha_open']).all()
    ha_bear = (last['ha_close'] < last['ha_open']).all()
    rsi = df['rsi'].iloc[-1] if 'rsi' in df.columns else 50
    details['ha_bull'] = bool(ha_bull)
    details['ha_bear'] = bool(ha_bear)
    details['rsi'] = float(rsi)

    if not (ha_bull or ha_bear):
        return 'HOLD', details
    if ha_bull and rsi <= 50:
        return 'HOLD', details
    if ha_bear and rsi >= 50:
        return 'HOLD', details

    # Entry timing
    # PSAR: compare last two sar vs price
    sar_now = df['psar'].iloc[-1]
    sar_prev = df['psar'].iloc[-2]
    price_now = df['close'].iloc[-1]
    price_prev = df['close'].iloc[-2]
    macd_now = df['macd_hist'].iloc[-1]
    macd_prev = df['macd_hist'].iloc[-2]

    details.update({'psar_now': float(sar_now), 'psar_prev': float(sar_prev), 'macd_now': float(macd_now), 'macd_prev': float(macd_prev)})

    # Bullish entry: sar flips below recent lows and macd_hist crosses from <=0 to >0
    bull_entry = (sar_prev > price_prev and sar_now < price_now) and (macd_prev <= 0 and macd_now > 0)
    bear_entry = (sar_prev < price_prev and sar_now > price_now) and (macd_prev >= 0 and macd_now < 0)

    if ha_bull and bull_entry:
        return 'BUY', details
    if ha_bear and bear_entry:
        return 'SELL', details
    return 'HOLD', details
