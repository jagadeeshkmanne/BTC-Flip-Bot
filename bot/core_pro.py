import os
import pandas as pd
import numpy as np

# ─── Pro Strategy Configuration ───
LEVERAGE = float(os.environ.get("PRO_LEVERAGE", "5.0"))
TIMEFRAME = os.environ.get("PRO_TIMEFRAME", "4h")
EMA_FAST = int(os.environ.get("PRO_EMA_FAST", "50"))
EMA_SLOW = int(os.environ.get("PRO_EMA_SLOW", "200"))
TRAIL_ATR_MULT = float(os.environ.get("PRO_TRAIL_ATR", "4.0"))
ATR_PERIOD = int(os.environ.get("PRO_ATR_PERIOD", "14"))

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculates EMAs and ATR for the Pro Strategy."""
    if df is None or len(df) < EMA_SLOW + 2:
        return df
        
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    
    # ATR Calculation
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(ATR_PERIOD).mean()
    
    return df

def pro_signal(ema_fast_curr, ema_slow_curr, ema_fast_prev, ema_slow_prev):
    """
    Returns LONG on a bullish cross, SHORT on a bearish cross.
    Returns None if no cross occurred.
    """
    if None in (ema_fast_curr, ema_slow_curr, ema_fast_prev, ema_slow_prev) or np.isnan(ema_fast_curr):
        return None
        
    if ema_fast_curr > ema_slow_curr and ema_fast_prev <= ema_slow_prev:
        return "LONG"
    elif ema_fast_curr < ema_slow_curr and ema_fast_prev >= ema_slow_prev:
        return "SHORT"
        
    return None
