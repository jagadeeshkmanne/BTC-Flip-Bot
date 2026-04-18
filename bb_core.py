"""
bb_core.py — BB Confluence V2 strategy logic for live bot.

4H + 1H Bollinger Band mean reversion.
Entry: 1H BB band + near 4H BB band + RSI < 30/70 + volume 1.3x
TP: 4H BB mid | SL: 4H BB band ± ATR | Max hold: 24h
DD halt at -25% for 7 days.

V2 backtest: $5K → $2.21M | +238% CAGR | -12.7% DD | PF 2.40 | 688 trades
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Literal, Dict, Any
import pandas as pd
import numpy as np

# ─── Strategy constants ───
LEVERAGE          = 2.0
RISK_PCT          = 0.01

# Bollinger Bands
BB_PERIOD         = 20
BB_STD            = 2.0

# Entry filters
RSI_OVERSOLD      = 30
RSI_OVERBOUGHT    = 70
RSI_PERIOD        = 14
VOL_SPIKE_RATIO   = 1.3
VOL_SMA_LEN       = 20
NEAR_4H_PCT       = 0.01

# Exit
MAX_HOLD_BARS     = 24
DD_HALT_PCT       = 0.25
DD_HALT_BARS      = 168
COOLDOWN_BARS     = 4

Side = Literal["LONG", "SHORT"]


# ─── Indicators ───
def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = -d.clip(upper=0).rolling(n).mean()
    return 100 - (100 / (1 + g / l.replace(0, np.nan)))

def atr_calc(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(n).mean()

def bb_calc(s: pd.Series, n: int = 20, k: float = 2.0):
    m = s.rolling(n).mean()
    std = s.rolling(n).std()
    return m, m + k * std, m - k * std


# ─── Resamplers ───
def resample(df_15m: pd.DataFrame, rule: str) -> pd.DataFrame:
    if "timestamp" in df_15m.columns:
        d = df_15m.set_index("timestamp")
    else:
        d = df_15m
    out = d.resample(rule).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum"
    }).dropna()
    return out.reset_index()


# ─── HTF mapping ───
def map_htf_to_1h(htf_df: pd.DataFrame, htf_rule: str,
                  base_ts: pd.DatetimeIndex, value_col: str) -> np.ndarray:
    htf_df = htf_df.copy()
    htf_df["close_time"] = htf_df["timestamp"] + pd.Timedelta(htf_rule)
    s = pd.Series(htf_df[value_col].values, index=htf_df["close_time"].values)
    s = s[~s.index.duplicated(keep="last")]
    return s.reindex(s.index.union(base_ts)).sort_index().ffill().reindex(base_ts).fillna(np.nan).values


# ─── Build all indicators ───
def build_signals(df_15m: pd.DataFrame) -> pd.DataFrame:
    """Returns 1H df with BB bands, RSI, ATR, volume, and 4H BB mapped."""
    df15 = df_15m.copy()
    if "timestamp" in df15.columns:
        df15["timestamp"] = pd.to_datetime(df15["timestamp"]).dt.tz_localize(None) if df15["timestamp"].dt.tz is not None else pd.to_datetime(df15["timestamp"])
    df_1h = resample(df15, "1h")
    df_4h = resample(df15, "4h")

    # 1H indicators
    df_1h["bb_mid"], df_1h["bb_up"], df_1h["bb_lo"] = bb_calc(df_1h["close"], BB_PERIOD, BB_STD)
    df_1h["rsi"] = rsi(df_1h["close"], RSI_PERIOD)
    df_1h["atr"] = atr_calc(df_1h)
    df_1h["vol_sma"] = df_1h["volume"].rolling(VOL_SMA_LEN).mean()
    df_1h["vol_ok"] = df_1h["volume"] > VOL_SPIKE_RATIO * df_1h["vol_sma"]

    # 4H BB
    df_4h["bb_mid"], df_4h["bb_up"], df_4h["bb_lo"] = bb_calc(df_4h["close"], BB_PERIOD, BB_STD)

    # Map 4H to 1H
    base_ts = pd.DatetimeIndex(df_1h["timestamp"])
    df_1h["h4_bb_lo"] = map_htf_to_1h(df_4h, "4h", base_ts, "bb_lo")
    df_1h["h4_bb_up"] = map_htf_to_1h(df_4h, "4h", base_ts, "bb_up")
    df_1h["h4_bb_mid"] = map_htf_to_1h(df_4h, "4h", base_ts, "bb_mid")

    return df_1h


# ─── Signal evaluation ───
@dataclass
class BBSignalState:
    side: Optional[Side] = None
    price: float = 0.0
    atr: float = 0.0
    sl_price: float = 0.0
    tp_price: float = 0.0
    conditions_long: Dict[str, bool] = field(default_factory=dict)
    conditions_short: Dict[str, bool] = field(default_factory=dict)
    long_ok: bool = False
    short_ok: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)


def evaluate_signal(row: pd.Series) -> BBSignalState:
    s = BBSignalState()
    s.price = float(row["close"])
    s.atr = float(row.get("atr", 0.0)) if not pd.isna(row.get("atr", np.nan)) else 0.0

    bb_lo = row["bb_lo"]; bb_up = row["bb_up"]; bb_mid = row["bb_mid"]
    h4_lo = row["h4_bb_lo"]; h4_up = row["h4_bb_up"]; h4_mid = row["h4_bb_mid"]
    rsi_v = row["rsi"]; vol_ok = bool(row["vol_ok"])
    price = s.price

    # Check nan/validity
    valid = not any(pd.isna(x) for x in [bb_lo, bb_up, h4_lo, h4_up, h4_mid, rsi_v])
    valid = valid and h4_lo > 0 and h4_up > 0

    if valid:
        near_lo = (price - h4_lo) / h4_lo < NEAR_4H_PCT
        near_up = (h4_up - price) / h4_up < NEAR_4H_PCT
    else:
        near_lo = near_up = False

    s.conditions_long = {
        "1H BB lower touch": bool(valid and price <= bb_lo),
        "Near 4H BB lower": bool(near_lo),
        f"RSI < {RSI_OVERSOLD}": bool(not pd.isna(rsi_v) and rsi_v < RSI_OVERSOLD),
        f"Vol > {VOL_SPIKE_RATIO}x SMA": vol_ok,
    }
    s.conditions_short = {
        "1H BB upper touch": bool(valid and price >= bb_up),
        "Near 4H BB upper": bool(near_up),
        f"RSI > {RSI_OVERBOUGHT}": bool(not pd.isna(rsi_v) and rsi_v > RSI_OVERBOUGHT),
        f"Vol > {VOL_SPIKE_RATIO}x SMA": vol_ok,
    }

    s.long_ok = all(s.conditions_long.values())
    s.short_ok = all(s.conditions_short.values())

    if s.long_ok and not s.short_ok:
        s.side = "LONG"
        s.tp_price = float(h4_mid)
        s.sl_price = float(h4_lo - s.atr)
    elif s.short_ok and not s.long_ok:
        s.side = "SHORT"
        s.tp_price = float(h4_mid)
        s.sl_price = float(h4_up + s.atr)

    s.raw = {
        "rsi": float(rsi_v) if not pd.isna(rsi_v) else None,
        "bb_lo": float(bb_lo) if not pd.isna(bb_lo) else None,
        "bb_up": float(bb_up) if not pd.isna(bb_up) else None,
        "bb_mid": float(bb_mid) if not pd.isna(bb_mid) else None,
        "h4_bb_lo": float(h4_lo) if not pd.isna(h4_lo) else None,
        "h4_bb_up": float(h4_up) if not pd.isna(h4_up) else None,
        "h4_bb_mid": float(h4_mid) if not pd.isna(h4_mid) else None,
        "atr": s.atr,
        "volume": float(row["volume"]),
        "vol_sma": float(row["vol_sma"]) if not pd.isna(row["vol_sma"]) else None,
    }
    return s


# ─── Position dataclass ───
@dataclass
class BBPosition:
    side: Side
    entry_price: float
    entry_time: str
    qty: float
    sl_price: float
    tp_price: float
    entry_bar: int = 0


def position_size(equity: float, entry_price: float,
                  leverage: float = LEVERAGE) -> float:
    """Full capital × leverage."""
    return (equity * leverage) / entry_price if entry_price > 0 else 0.0
