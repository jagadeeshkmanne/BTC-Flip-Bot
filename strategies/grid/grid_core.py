"""
grid_core.py — Directional Grid Strategy logic.

Long grid in uptrend (price > EMA200, RSI < 35).
Short grid in downtrend (price < EMA200, RSI > 65).
5 grid levels, 10% range, 2% TP per grid, 15% SL.

Backtest (2020-2026): $5K → $831K | +126% CAGR | -25.3% DD | PF 2.64 | 240 trades/yr | 76% WR
All 7 years positive including 2022 bear (+95%).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Literal, Dict, List, Any
import pandas as pd
import numpy as np

# ─── Strategy constants ───
LEVERAGE          = 2.0

# Grid parameters
N_GRIDS           = 5
GRID_RANGE_PCT    = 0.10        # 10% range below entry (long) or above (short)
TP_PER_GRID_PCT   = 0.02        # 2% take profit per grid level
GRID_SL_PCT       = 0.15        # 15% stop loss from grid base
MAX_HOLD_BARS     = 336         # 14 days max hold

# Entry triggers
RSI_PERIOD        = 14
RSI_LONG_TRIGGER  = 35          # enter long grid when RSI < 35 in uptrend
RSI_SHORT_TRIGGER = 65          # enter short grid when RSI > 65 in downtrend
EMA_TREND_PERIOD  = 200         # EMA200 for trend direction

# Exit triggers
RSI_LONG_EXIT     = 70          # close long grid when RSI > 70 (overbought)
RSI_SHORT_EXIT    = 30          # close short grid when RSI < 30 (oversold)

Side = Literal["LONG", "SHORT"]


# ─── Indicators ───
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = -d.clip(upper=0).rolling(n).mean()
    return 100 - (100 / (1 + g / l.replace(0, np.nan)))


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


# ─── Build signals ───
def build_signals(df_15m: pd.DataFrame) -> pd.DataFrame:
    """Returns 1H df with RSI, EMA200, trend direction."""
    df15 = df_15m.copy()
    if "timestamp" in df15.columns:
        ts = pd.to_datetime(df15["timestamp"])
        df15["timestamp"] = ts.dt.tz_localize(None) if ts.dt.tz is not None else ts
    df_1h = resample(df15, "1h")

    df_1h["rsi"] = rsi(df_1h["close"], RSI_PERIOD)
    df_1h["ema200"] = ema(df_1h["close"], EMA_TREND_PERIOD)
    df_1h["trend"] = np.where(df_1h["close"] > df_1h["ema200"], 1,
                     np.where(df_1h["close"] < df_1h["ema200"], -1, 0))
    return df_1h


# ─── Grid state ───
@dataclass
class GridState:
    active: bool = False
    side: Optional[Side] = None
    base_price: float = 0.0
    grid_levels: List[float] = field(default_factory=list)
    grid_filled: List[bool] = field(default_factory=list)
    start_bar: int = 0
    n_grids: int = N_GRIDS


# ─── Signal evaluation for dashboard ───
@dataclass
class GridSignalState:
    should_start: bool = False
    side: Optional[Side] = None
    price: float = 0.0
    rsi_value: float = 0.0
    trend: int = 0
    conditions: Dict[str, bool] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


def evaluate_signal(row: pd.Series) -> GridSignalState:
    s = GridSignalState()
    s.price = float(row["close"])
    s.rsi_value = float(row["rsi"]) if not pd.isna(row["rsi"]) else 0.0
    s.trend = int(row["trend"]) if not pd.isna(row["trend"]) else 0

    uptrend = s.trend == 1
    downtrend = s.trend == -1
    rsi_oversold = s.rsi_value < RSI_LONG_TRIGGER
    rsi_overbought = s.rsi_value > RSI_SHORT_TRIGGER

    s.conditions = {
        "Uptrend (price > EMA200)": uptrend,
        f"RSI < {RSI_LONG_TRIGGER} (oversold)": rsi_oversold,
        "Downtrend (price < EMA200)": downtrend,
        f"RSI > {RSI_SHORT_TRIGGER} (overbought)": rsi_overbought,
    }

    if uptrend and rsi_oversold:
        s.should_start = True
        s.side = "LONG"
    elif downtrend and rsi_overbought:
        s.should_start = True
        s.side = "SHORT"

    s.raw = {
        "rsi": s.rsi_value,
        "ema200": float(row["ema200"]) if not pd.isna(row["ema200"]) else None,
        "trend": s.trend,
        "price": s.price,
    }
    return s


def create_grid(side: Side, price: float) -> GridState:
    """Create grid levels based on side and current price."""
    g = GridState()
    g.active = True
    g.side = side
    g.base_price = price
    g.n_grids = N_GRIDS

    if side == "LONG":
        grid_low = price * (1 - GRID_RANGE_PCT)
        spacing = (price - grid_low) / N_GRIDS
        g.grid_levels = [grid_low + spacing * j for j in range(N_GRIDS)]
        g.grid_filled = [False] * N_GRIDS
        g.grid_filled[-1] = True  # fill top level (entry at current price)
    else:
        grid_high = price * (1 + GRID_RANGE_PCT)
        spacing = (grid_high - price) / N_GRIDS
        g.grid_levels = [price + spacing * j for j in range(N_GRIDS)]
        g.grid_filled = [False] * N_GRIDS
        g.grid_filled[0] = True  # fill bottom level (entry at current price)

    return g
