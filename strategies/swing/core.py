"""
core.py — Structure Break Strategy (Daily Pivots + DD-Adaptive Risk)

Architecture:
  Daily (1D) execution timeframe
  Daily EMA50 bias filter (prior-day close > EMA50 = bull, no lookahead)
  Daily RSI(14) confirmation (> 50 bull / < 50 bear)
  Pivot-based structure (HH+HL longs, LL+LH shorts) + close breaks latest pivot

  SL: 5-bar swing low/high + 0.1% buffer, capped at 2.5%
  TP ladder: TP1 50% @ 3R -> SL to BE+0.75%; TP2 25% @ 4R; runner 25% with 2.5x ATR trail
  DD-adaptive risk: effective risk = base_risk * max(0.5, 1 + drawdownPct)
  Hard DD halt: 15% -> halt 7 days

Backtest (TradingView, 1D BTCUSDT, Sep 2019 -> Apr 2026, 2x lev, 3% risk):
  +817% abs | 34% DD | PF 2.79 | 45 trades | Calmar ~1.17
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Literal, Dict, Any, Tuple
import pandas as pd
import numpy as np

# ----------------------------------------------------------------
# Strategy Constants
# ----------------------------------------------------------------

LEVERAGE          = 2.0
RISK_PCT          = 0.03        # 3% matches the 279% backtest default

# Pivot structure
PIVOT_LEN         = 3           # left=right=3 bars

# Stop loss
SL_SWING_LEN      = 5
SL_BUFFER_PCT     = 0.001
SL_MAX_PCT        = 0.025

# Take profit ladder
TP1_R             = 3.0         # TP1 at +3R (banks 50% further out than 2R default)
TP1_FRAC          = 0.50
TP2_R             = 4.0
TP2_FRAC          = 0.25
BE_BUF_PCT        = 0.0075      # BE move after TP1 = entry + 0.75%
TRAIL_ATR_MULT    = 2.5

# DD management
DD_HALT_PCT       = 0.15
DD_HALT_BARS      = 7           # 7 daily bars = 7 days
USE_ADAPTIVE_RISK = True
RISK_FLOOR        = 0.5         # min risk multiplier at deep DD

# Cooldown
GENERIC_CD_BARS   = 3           # 3 daily bars = 3 days after any exit

# RSI
RSI_PERIOD        = 14
RSI_LONG_MIN      = 50
RSI_SHORT_MAX     = 50

# ATR (for trailing stop)
ATR_PERIOD        = 14

Side = Literal["LONG", "SHORT"]


# ----------------------------------------------------------------
# Indicators
# ----------------------------------------------------------------

def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi_series(s: pd.Series, n: int = 14) -> pd.Series:
    # Wilder's RMA smoothing to match Pine's ta.rsi()
    d = s.diff()
    gain = d.clip(lower=0)
    loss = -d.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr_series(df: pd.DataFrame, n: int = ATR_PERIOD) -> pd.Series:
    # Wilder's RMA to match Pine's ta.atr()
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, min_periods=n, adjust=False).mean()


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    d = df.set_index("timestamp") if "timestamp" in df.columns else df
    out = d.resample(rule).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    return out.reset_index()


def compute_pivots(df: pd.DataFrame, pivot_len: int = PIVOT_LEN
                   ) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """Last two confirmed pivot highs and lows. Strict comparison.
    Pivots need `pivot_len` bars after to confirm, so no repaint.
    Returns (phLast, phPrev, plLast, plPrev). None if not enough pivots yet.
    """
    highs = df["high"].values
    lows = df["low"].values
    n = len(highs)

    ph_vals = []
    pl_vals = []

    for i in range(pivot_len, n - pivot_len):
        is_ph = True
        is_pl = True
        for j in range(i - pivot_len, i + pivot_len + 1):
            if j == i:
                continue
            if highs[j] >= highs[i]:
                is_ph = False
            if lows[j] <= lows[i]:
                is_pl = False
            if not is_ph and not is_pl:
                break
        if is_ph:
            ph_vals.append(float(highs[i]))
        if is_pl:
            pl_vals.append(float(lows[i]))

    ph_last = ph_vals[-1] if len(ph_vals) >= 1 else None
    ph_prev = ph_vals[-2] if len(ph_vals) >= 2 else None
    pl_last = pl_vals[-1] if len(pl_vals) >= 1 else None
    pl_prev = pl_vals[-2] if len(pl_vals) >= 2 else None
    return ph_last, ph_prev, pl_last, pl_prev


# ----------------------------------------------------------------
# Signal building
# ----------------------------------------------------------------

def build_signals(df_1d: pd.DataFrame) -> pd.DataFrame:
    """Add RSI, ATR, swing SL levels to daily dataframe."""
    df = df_1d.copy()
    df["rsi"] = rsi_series(df["close"], RSI_PERIOD)
    df["atr"] = atr_series(df, ATR_PERIOD)
    df["swing_low"] = df["low"].rolling(SL_SWING_LEN).min()
    df["swing_high"] = df["high"].rolling(SL_SWING_LEN).max()
    return df


def build_htf(df_1d: pd.DataFrame) -> np.ndarray:
    """Daily EMA50 bias. On daily TF this uses PRIOR bar's close/ema50 (no
    lookahead), matching Pine's request.security("1D", close[1])."""
    ts = df_1d["timestamp"]
    # Already daily; no resampling needed. Compute EMA50 and bias on bar.
    ema50 = ema(df_1d["close"], 50)
    bias = np.where(df_1d["close"] > ema50, 1,
            np.where(df_1d["close"] < ema50, -1, 0))
    # Shift by 1 bar so current bar's filter uses PRIOR day's regime — no lookahead
    bias_prev = pd.Series(bias, index=ts).shift(1)
    return bias_prev.values


# ----------------------------------------------------------------
# Signal evaluation
# ----------------------------------------------------------------

@dataclass
class SignalState:
    side: Optional[Side] = None
    price: float = 0.0
    conditions: Dict[str, bool] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


def evaluate_signal(df_4h: pd.DataFrame, last_idx: int, bias_d: int) -> SignalState:
    """Evaluate V7 entry stack on the last CLOSED 4H bar."""
    s = SignalState()
    row = df_4h.iloc[last_idx]
    s.price = float(row["close"])

    # Use bars up to and including last_idx for pivot computation.
    # Pivots require `pivot_len` bars AFTER to confirm, so the most recent
    # confirmable pivot is at (last_idx - pivot_len).
    df_to_bar = df_4h.iloc[:last_idx + 1]
    ph_last, ph_prev, pl_last, pl_prev = compute_pivots(df_to_bar, PIVOT_LEN)

    hh_ok = (ph_last is not None) and (ph_prev is not None) and (ph_last > ph_prev)
    hl_ok = (pl_last is not None) and (pl_prev is not None) and (pl_last > pl_prev)
    lh_ok = (ph_last is not None) and (ph_prev is not None) and (ph_last < ph_prev)
    ll_ok = (pl_last is not None) and (pl_prev is not None) and (pl_last < pl_prev)

    bull_struct = hh_ok and hl_ok
    bear_struct = lh_ok and ll_ok
    break_up = (ph_last is not None) and (s.price > ph_last)
    break_dn = (pl_last is not None) and (s.price < pl_last)

    daily_bull = bias_d == 1
    daily_bear = bias_d == -1

    rsi_v = row["rsi"]
    rsi_ok_l = (not pd.isna(rsi_v)) and rsi_v > RSI_LONG_MIN
    rsi_ok_s = (not pd.isna(rsi_v)) and rsi_v < RSI_SHORT_MAX

    long_ok = daily_bull and rsi_ok_l and bull_struct and break_up
    short_ok = daily_bear and rsi_ok_s and bear_struct and break_dn

    if long_ok:
        s.side = "LONG"
    elif short_ok:
        s.side = "SHORT"

    s.conditions = {
        "Daily EMA50 Bull": bool(daily_bull),
        "Daily EMA50 Bear": bool(daily_bear),
        "Daily RSI >50": bool(rsi_ok_l),
        "Daily RSI <50": bool(rsi_ok_s),
        "HH + HL (pivots)": bool(bull_struct),
        "LH + LL (pivots)": bool(bear_struct),
        "Break above pivot high": bool(break_up),
        "Break below pivot low": bool(break_dn),
    }
    s.raw = {
        "rsi_1d": float(rsi_v) if not pd.isna(rsi_v) else None,
        "rsi_4h": float(rsi_v) if not pd.isna(rsi_v) else None,  # legacy key for dashboard compat
        "bias_d": int(bias_d) if not pd.isna(bias_d) else 0,
        "ph_last": ph_last,
        "ph_prev": ph_prev,
        "pl_last": pl_last,
        "pl_prev": pl_prev,
        "atr": float(row["atr"]) if not pd.isna(row["atr"]) else None,
        "price": s.price,
    }
    return s


# ----------------------------------------------------------------
# Position + SL/TP helpers
# ----------------------------------------------------------------

@dataclass
class Position:
    side: Side
    entry_price: float
    entry_time: str
    qty: float
    orig_qty: float
    sl_price: float
    init_sl_dist: float
    tp1_done: bool = False
    tp2_done: bool = False
    entry_bar: int = 0


def calc_sl(side: Side, price: float, swing_low: float, swing_high: float) -> float:
    if side == "LONG":
        raw = swing_low * (1 - SL_BUFFER_PCT)
        cap = price * (1 - SL_MAX_PCT)
        return max(raw, cap)
    else:
        raw = swing_high * (1 + SL_BUFFER_PCT)
        cap = price * (1 + SL_MAX_PCT)
        return min(raw, cap)


def calc_qty(equity: float, price: float, sl_price: float, drawdown_pct: float = 0.0) -> float:
    """Risk-based sizing with DD-adaptive scaling.
    drawdown_pct is negative (e.g., -0.10 for 10% DD) or 0.
    """
    sl_dist = abs(price - sl_price)
    if sl_dist <= 0 or price <= 0:
        return 0.0
    dd_factor = max(RISK_FLOOR, 1 + drawdown_pct) if USE_ADAPTIVE_RISK else 1.0
    risk_amount = equity * 0.95 * RISK_PCT * dd_factor
    qty_risk = risk_amount / sl_dist
    qty_cap = (equity * 0.95 * LEVERAGE) / price
    return min(qty_risk, qty_cap)


def compute_trail_stop(side: Side, price: float, atr: float, current_stop: float) -> float:
    """Trailing stop after TP2 (runner phase). Only ratchets in favor."""
    if atr is None or atr <= 0 or pd.isna(atr):
        return current_stop
    if side == "LONG":
        new_stop = price - TRAIL_ATR_MULT * atr
        return max(current_stop, new_stop)
    else:
        new_stop = price + TRAIL_ATR_MULT * atr
        return min(current_stop, new_stop)
