"""
core.py — Strategy V5 logic.

V5 spec (5yr backtest: $10K → $154,657, +73% CAGR, PF 3.63, -25% DD, 6 of 6 yrs positive):
  Architecture:
    Daily : EMA50 trend filter
    4H    : RSI(14) confirmation
    1H    : execution + entry stack:
              - RSI(14) > 45 long  /  < 55 short
              - MACD(12,26,9) line vs signal
              - Bullish/Bearish engulfing (body > 1.2× prev body)
              - ATR(14) > rolling-50-mean (volatility regime)
              - Volume(1H) > 1.2× rolling-20-mean (V3 addition)

  Entry:  Daily bias + 4H confirm + 1H entry stack ALL agree
  SL:     pattern-based — min(low_entry, low_prior) - 0.1%, capped at 2.5% from entry
  TP:     None (let runners run)
  Partial TP at +6R: lock 30% of position, leave 70% on original SL
  Exit:   Opposite signal closes position (NO flip-open — V4 fix)
  Cooldown: 24h after SL hit (same direction)
  Risk mgmt: DD halt for 7 days after −25% peak-to-trough drawdown
  Leverage: 2× (configurable)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Literal, Dict, List, Any
import pandas as pd
import numpy as np

# ─── Strategy constants ───
LEVERAGE          = 2.0
RISK_PCT          = 0.01           # 1% of equity per trade (used for size calc)

# Stop loss
SL_BUFFER_PCT     = 0.001          # 0.1% padding below pattern low
SL_MAX_PCT        = 0.025          # cap pattern SL at 2.5%

# Partial TP
USE_PARTIAL_TP    = True
PARTIAL_TP_R      = 3.0            # lock 30% at +3R favorable (best balance: WR 41%, DD -22%, CAGR +70%)
PARTIAL_TP_FRAC   = 0.30

# Cooldown
SAME_DIR_CD_BARS  = 36             # 36h same-dir cooldown — optimal from sweep ($157K vs $154K @24h)

# DD circuit breaker
DD_HALT_PCT       = 0.25
DD_HALT_BARS      = 168            # 7 days × 24h

# Entry filter thresholds
RSI_LONG_MIN      = 45
RSI_SHORT_MAX     = 55
ENGULF_BODY_MULT  = 1.2
ATR_MA_LEN        = 50
VOL_SMA_LEN       = 20
VOL_SPIKE_RATIO   = 1.2

Side = Literal["LONG", "SHORT"]


# ─── Indicators ───
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = -d.clip(upper=0).rolling(n).mean()
    return 100 - (100 / (1 + g / l.replace(0, np.nan)))

def macd_lines(s: pd.Series, f: int = 12, sl: int = 26, sg: int = 9):
    ef = s.ewm(span=f, adjust=False).mean()
    es = s.ewm(span=sl, adjust=False).mean()
    line = ef - es
    sig = line.ewm(span=sg, adjust=False).mean()
    return line, sig

def atr_calc(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(n).mean()


# ─── Resamplers ───
def resample(df_15m: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample 15m DF to higher TF. Index must be timestamp."""
    if "timestamp" in df_15m.columns:
        d = df_15m.set_index("timestamp")
    else:
        d = df_15m
    out = d.resample(rule).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum"
    }).dropna()
    return out.reset_index()


# ─── Engulfing detection ───
def detect_engulfing(df: pd.DataFrame) -> pd.DataFrame:
    """Add bull_eng / bear_eng columns to df."""
    df = df.copy()
    body = (df["close"] - df["open"]).abs()
    pbody = body.shift(1)
    df["bull_eng"] = (
        (df["close"].shift(1) < df["open"].shift(1))
        & (df["close"] > df["open"])
        & (df["close"] >= df["open"].shift(1))
        & (df["open"]  <= df["close"].shift(1))
        & (body > pbody * ENGULF_BODY_MULT)
    )
    df["bear_eng"] = (
        (df["close"].shift(1) > df["open"].shift(1))
        & (df["close"] < df["open"])
        & (df["open"]  >= df["close"].shift(1))
        & (df["close"] <= df["open"].shift(1))
        & (body > pbody * ENGULF_BODY_MULT)
    )
    return df


# ─── HTF mapping ───
def map_htf_to_1h(htf_df: pd.DataFrame, htf_rule: str,
                  base_ts: pd.DatetimeIndex, value_col: str) -> np.ndarray:
    """Causal asof mapping: HTF value stamped at HTF close-time."""
    htf_df = htf_df.copy()
    htf_df["close_time"] = htf_df["timestamp"] + pd.Timedelta(htf_rule)
    s = pd.Series(htf_df[value_col].values, index=htf_df["close_time"].values)
    s = s[~s.index.duplicated(keep="last")]
    return s.reindex(s.index.union(base_ts)).sort_index().ffill().reindex(base_ts).fillna(0).values


# ─── Build all indicators on 1H, plus HTF biases ───
def build_signals(df_15m: pd.DataFrame) -> pd.DataFrame:
    """
    Returns 1H df with all indicators + bias columns:
      ema50_d, bias_d (+1/-1/0)
      rsi_4h, confirm_4h (+1/-1/0)
      rsi, macd_line, macd_signal, atr, atr_ma, high_vol, vol_sma, vol_ok
      bull_eng, bear_eng
    """
    df15 = df_15m.copy()
    if "timestamp" in df15.columns:
        df15["timestamp"] = pd.to_datetime(df15["timestamp"]).dt.tz_localize(None) if df15["timestamp"].dt.tz is not None else pd.to_datetime(df15["timestamp"])
    df_1h = resample(df15, "1h")
    df_4h = resample(df15, "4h")
    df_d  = resample(df15, "1D")

    # 1H indicators
    df_1h["rsi"] = rsi(df_1h["close"])
    df_1h["macd_line"], df_1h["macd_signal"] = macd_lines(df_1h["close"])
    df_1h["atr"] = atr_calc(df_1h)
    df_1h["atr_ma"] = df_1h["atr"].rolling(ATR_MA_LEN).mean()
    df_1h["high_vol"] = df_1h["atr"] > df_1h["atr_ma"]
    df_1h["vol_sma"] = df_1h["volume"].rolling(VOL_SMA_LEN).mean()
    df_1h["vol_ok"]  = df_1h["volume"] > VOL_SPIKE_RATIO * df_1h["vol_sma"]
    df_1h = detect_engulfing(df_1h)

    # 4H confirm
    df_4h["rsi"] = rsi(df_4h["close"])
    df_4h["confirm"] = np.where(df_4h["rsi"] > 50, 1,
                       np.where(df_4h["rsi"] < 50, -1, 0))

    # Daily bias
    df_d["ema50"] = ema(df_d["close"], 50)
    df_d["bias"] = np.where(df_d["close"] > df_d["ema50"], 1,
                    np.where(df_d["close"] < df_d["ema50"], -1, 0))

    base_ts = pd.DatetimeIndex(df_1h["timestamp"])
    df_1h["bias_d"]     = map_htf_to_1h(df_d,  "1D", base_ts, "bias").astype(int)
    df_1h["confirm_4h"] = map_htf_to_1h(df_4h, "4h", base_ts, "confirm").astype(int)
    return df_1h


# ─── Signal evaluation with condition state (for dashboard) ───
@dataclass
class SignalState:
    side: Optional[Side] = None        # final entry signal: "LONG" / "SHORT" / None
    price: float = 0.0
    atr: float = 0.0
    # Per-condition status (True/False) for dashboard
    conditions_long: Dict[str, bool] = field(default_factory=dict)
    conditions_short: Dict[str, bool] = field(default_factory=dict)
    long_ok: bool = False
    short_ok: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)  # raw indicator values


def evaluate_signal(row: pd.Series) -> SignalState:
    """Evaluate the V5 entry stack on a single 1H row + return condition state."""
    s = SignalState()
    s.price = float(row["close"])
    s.atr   = float(row.get("atr", 0.0)) if not pd.isna(row.get("atr", np.nan)) else 0.0

    daily_bull = row["bias_d"] == 1
    daily_bear = row["bias_d"] == -1
    h4_bull    = row["confirm_4h"] == 1
    h4_bear    = row["confirm_4h"] == -1
    rsi_v      = row["rsi"]
    macd_bull  = row["macd_line"] > row["macd_signal"]
    macd_bear  = row["macd_line"] < row["macd_signal"]
    eng_bull   = bool(row["bull_eng"])
    eng_bear   = bool(row["bear_eng"])
    vol_ok     = bool(row["vol_ok"])
    high_vol   = bool(row["high_vol"])

    s.conditions_long = {
        "Daily EMA50 trend (bullish)": daily_bull,
        "4H RSI > 50":                 h4_bull,
        f"1H RSI > {RSI_LONG_MIN}":   bool(rsi_v > RSI_LONG_MIN) if not pd.isna(rsi_v) else False,
        "1H MACD > signal":            bool(macd_bull),
        "1H Bullish engulfing":        eng_bull,
        "1H ATR > MA(50)":             high_vol,
        "1H Vol > 1.2×SMA20":          vol_ok,
    }
    s.conditions_short = {
        "Daily EMA50 trend (bearish)": daily_bear,
        "4H RSI < 50":                 h4_bear,
        f"1H RSI < {RSI_SHORT_MAX}":   bool(rsi_v < RSI_SHORT_MAX) if not pd.isna(rsi_v) else False,
        "1H MACD < signal":            bool(macd_bear),
        "1H Bearish engulfing":        eng_bear,
        "1H ATR > MA(50)":             high_vol,
        "1H Vol > 1.2×SMA20":          vol_ok,
    }
    s.long_ok  = all(s.conditions_long.values())
    s.short_ok = all(s.conditions_short.values())
    if s.long_ok and not s.short_ok:
        s.side = "LONG"
    elif s.short_ok and not s.long_ok:
        s.side = "SHORT"
    s.raw = {
        "rsi_1h": float(rsi_v) if not pd.isna(rsi_v) else None,
        "macd_line": float(row["macd_line"]),
        "macd_signal": float(row["macd_signal"]),
        "atr_1h": s.atr,
        "atr_ma": float(row["atr_ma"]) if not pd.isna(row["atr_ma"]) else None,
        "vol_sma": float(row["vol_sma"]) if not pd.isna(row["vol_sma"]) else None,
        "volume": float(row["volume"]),
        "bias_d": int(row["bias_d"]),
        "confirm_4h": int(row["confirm_4h"]),
    }
    return s


# ─── Position dataclass ───
@dataclass
class Position:
    side: Side
    entry_price: float
    entry_time: str
    qty: float
    sl_price: float
    pos_atr: float                     # ATR at entry (for partial TP R-distance)
    partial_taken: bool = False
    entry_reason: str = "v5_entry"


def calc_pattern_sl(side: Side, entry_price: float,
                    bar_low: float, bar_high: float,
                    prev_low: float, prev_high: float) -> float:
    """V5 pattern-based SL — capped at SL_MAX_PCT from entry."""
    if side == "LONG":
        pat = min(bar_low, prev_low)
        raw_sl = pat * (1 - SL_BUFFER_PCT)
        cap_sl = entry_price * (1 - SL_MAX_PCT)
        return max(raw_sl, cap_sl)   # tighter (closer to entry) of the two
    else:
        pat = max(bar_high, prev_high)
        raw_sl = pat * (1 + SL_BUFFER_PCT)
        cap_sl = entry_price * (1 + SL_MAX_PCT)
        return min(raw_sl, cap_sl)


def position_size(equity: float, entry_price: float, sl_price: float,
                  leverage: float = LEVERAGE, risk_pct: float = RISK_PCT) -> float:
    """Risk-based sizing: qty such that SL hit loses risk_pct of equity (with leverage)."""
    sl_dist = abs(entry_price - sl_price)
    if sl_dist <= 0: return 0.0
    return (equity * risk_pct) / (sl_dist * leverage)


# ─── Position management — exit decision with condition state ───
@dataclass
class ExitState:
    should_exit: bool = False
    reason: Optional[str] = None
    exit_price: float = 0.0
    partial_tp_hit: bool = False
    # For dashboard
    sl_distance_pct: float = 0.0       # current % from price to SL (negative = closer to stop)
    partial_tp_distance_pct: float = 0.0
    favorable_r: float = 0.0           # how many R favorable (for partial TP)
    opposite_signal_active: bool = False


def evaluate_exit(pos: Position, row: pd.Series, signal: SignalState) -> ExitState:
    """Evaluate exit conditions for an open position. Returns state for both
    actual exit decision and dashboard display."""
    state = ExitState()
    price = float(row["close"])
    high  = float(row["high"])
    low   = float(row["low"])

    # SL check (intrabar)
    if pos.side == "LONG":
        if low <= pos.sl_price:
            state.should_exit = True
            state.reason = "SL"
            state.exit_price = pos.sl_price
        state.sl_distance_pct = (price - pos.sl_price) / price * 100   # positive = above stop
    else:
        if high >= pos.sl_price:
            state.should_exit = True
            state.reason = "SL"
            state.exit_price = pos.sl_price
        state.sl_distance_pct = (pos.sl_price - price) / price * 100   # positive = below stop

    # Partial TP check (only if not already taken)
    if USE_PARTIAL_TP and not pos.partial_taken and not state.should_exit:
        sl_dist = abs(pos.entry_price - pos.sl_price)
        favorable = (price - pos.entry_price) if pos.side == "LONG" else (pos.entry_price - price)
        state.favorable_r = favorable / sl_dist if sl_dist > 0 else 0.0
        state.partial_tp_distance_pct = (PARTIAL_TP_R - state.favorable_r) * (sl_dist / price * 100)
        if favorable >= PARTIAL_TP_R * sl_dist:
            state.partial_tp_hit = True

    # Opposite signal (would close position — V4 logic: exit only, no flip-open)
    if pos.side == "LONG" and signal.short_ok:
        state.opposite_signal_active = True
        if not state.should_exit:
            state.should_exit = True
            state.reason = "EXIT-OPP"
            state.exit_price = price
    elif pos.side == "SHORT" and signal.long_ok:
        state.opposite_signal_active = True
        if not state.should_exit:
            state.should_exit = True
            state.reason = "EXIT-OPP"
            state.exit_price = price

    return state
