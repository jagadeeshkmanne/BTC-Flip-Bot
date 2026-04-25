"""
core.py — S/R DCA Day Strategy (5m execution + 1h bias)

Python port of strategy_sr_dca_5m.pine:
  - Entry: prev_day's L/H touch + 1h EMA20 bias + filters
  - DCA: 1% below L1 (2 levels default)
  - TP: prev_day midpoint
  - SL: 2% below worst entry
  - BE-stop: after +1% favorable, SL → entry + 0.5% (default OFF)
  - EOD flatten at UTC 23:00
  - Max 1 cycle per UTC day

1h bias is resampled from 5m data internally — bot.py keeps its existing
signature (df_5m + df_1d for prev H/L, 1h resampled from 5m for bias).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Literal, Dict, Any
import pandas as pd
import numpy as np

# ═════ Constants (match pine defaults) ═════
LEVERAGE       = 2.0
RISK_PCT       = 0.06         # 6% total risk per cycle

DCA_LEVELS     = 2
DCA_SPACING    = 0.01         # 1% between DCA levels
SL_BELOW_WORST = 0.02         # 2% below worst entry
SUPPORT_ZONE   = 0.001        # 0.1% zone around prev H/L — TV test: same 31 trades, DD 3.32→2.88%, PF 5.65→6.90

CLOSE_HOUR     = 20           # UTC hour to force flatten + block new entries. TV test: 20 > 23 (+2.15% P&L, +9.5pp WR, +3.93 PF) — late-day entries net-losers

# Entry filters
VOL_MULT       = 1.2          # volume > 1.2× 20-bar avg
RSI_LOW        = 25           # skip long if RSI < 25
RSI_HIGH       = 75           # skip short if RSI > 75

RSI_PERIOD     = 14
VOL_AVG_LEN    = 20
EMA_BIAS_LEN   = 20           # 1h EMA period — Apr 2026 BTC backtest (2.31y): EMA20 net -1.33% / PF 1.07 (vs EMA15 -13.41% / 0.92). Stickier bias = fewer false flips when price approaches prev_H/L.

Side = Literal["LONG", "SHORT"]


# ═════ Indicators ═════
def rsi_series(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    gain = d.clip(lower=0)
    loss = -d.clip(upper=0)
    ag = gain.ewm(alpha=1.0/n, min_periods=n, adjust=False).mean()
    al = loss.ewm(alpha=1.0/n, min_periods=n, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


# ═════ Features ═════
def build_features(df_5m: pd.DataFrame, df_1d: pd.DataFrame) -> pd.DataFrame:
    """Add RSI, volume SMA, 1h EMA20 bias, and prev-day H/L/mid to each 5m bar.

    1h bias is resampled from the 5m data (no extra fetch needed).
    Prev-day H/L/mid still come from df_1d.
    """
    df = df_5m.copy().reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["date"] = df["timestamp"].dt.normalize()
    df["utc_hour"] = df["timestamp"].dt.hour

    df["rsi"] = rsi_series(df["close"], RSI_PERIOD)
    df["vol_avg"] = df["volume"].rolling(VOL_AVG_LEN).mean()

    # ─── 1h EMA20 bias (resampled from 5m, label=left so index = bar start) ───
    d5 = df.set_index("timestamp").sort_index()
    h1 = d5["close"].resample("1h").last().dropna().to_frame()
    h1["ema"] = ema(h1["close"], EMA_BIAS_LEN)
    h1["bias"] = np.where(h1["close"] > h1["ema"],  1,
                  np.where(h1["close"] < h1["ema"], -1, 0))
    # Pine's close[1] semantic: at 5m bar T we look at PRIOR closed 1h bar's close.
    # With label=left, the 1h bar at 13:00 contains 13:00–14:00 data (closes at 14:00).
    # For a 5m bar at 14:15, the prior closed 1h bar is the one at 13:00.
    # Map: for 5m bar T → look up h1 at (floor(T,'1H') - 1h).
    bias_h_map = h1["bias"].to_dict()

    def prior_hour_bias(ts):
        prior = ts.floor("1h") - pd.Timedelta(hours=1)
        return bias_h_map.get(prior, 0)

    df["bias_h"] = df["timestamp"].apply(prior_hour_bias).astype(int)

    # ─── Prev-day H/L/mid (still from daily bars) ───
    d1 = df_1d.copy().reset_index(drop=True)
    d1["timestamp"] = pd.to_datetime(d1["timestamp"])
    d1["date"] = d1["timestamp"].dt.normalize()
    d1["prev_H"]   = d1["high"].shift(1)
    d1["prev_L"]   = d1["low"].shift(1)
    d1["prev_mid"] = (d1["prev_H"] + d1["prev_L"]) / 2.0

    prev_h_map = dict(zip(d1["date"], d1["prev_H"]))
    prev_l_map = dict(zip(d1["date"], d1["prev_L"]))
    prev_m_map = dict(zip(d1["date"], d1["prev_mid"]))

    df["prev_H"]   = df["date"].map(prev_h_map)
    df["prev_L"]   = df["date"].map(prev_l_map)
    df["prev_mid"] = df["date"].map(prev_m_map)
    return df


# ═════ Signal evaluation ═════
@dataclass
class SignalState:
    side: Optional[Side] = None
    price: float = 0.0
    conditions: Dict[str, bool] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


def evaluate_signal(df: pd.DataFrame, last_idx: int) -> SignalState:
    """Evaluate 5m entry signal at bar last_idx (uses prev-day's H/L/bias)."""
    s = SignalState()
    row = df.iloc[last_idx]
    s.price = float(row["close"])

    prev_h = row["prev_H"]
    prev_l = row["prev_L"]
    prev_mid = row["prev_mid"]
    bias = int(row["bias_h"])   # 1h EMA20 bias (prior closed 1h bar)
    rsi_v = row["rsi"]
    vol = row["volume"]
    vol_avg = row["vol_avg"]
    utc_h = int(row["utc_hour"])

    # Incomplete data
    if pd.isna(prev_h) or pd.isna(prev_l) or pd.isna(rsi_v) or pd.isna(vol_avg):
        return s

    # Volume filter (on THIS 5m bar)
    vol_ok = vol >= VOL_MULT * vol_avg if vol_avg > 0 else False

    # RSI filter
    rsi_ok_long  = rsi_v >= RSI_LOW
    rsi_ok_short = rsi_v <= RSI_HIGH

    # Touch conditions
    touch_L = row["low"] <= prev_l * (1 + SUPPORT_ZONE) and row["low"] > prev_l * (1 - 0.01)
    touch_H = row["high"] >= prev_h * (1 - SUPPORT_ZONE) and row["high"] < prev_h * (1 + 0.01)

    in_trade_window = utc_h < CLOSE_HOUR

    long_ok  = (bias == 1  and rsi_ok_long  and vol_ok and touch_L and in_trade_window)
    short_ok = (bias == -1 and rsi_ok_short and vol_ok and touch_H and in_trade_window)

    if long_ok:
        s.side = "LONG"
    elif short_ok:
        s.side = "SHORT"

    s.conditions = {
        "1h bias BULL":       bool(bias == 1),
        "1h bias BEAR":       bool(bias == -1),
        "Volume > 1.2× avg":  bool(vol_ok),
        "RSI long ok (>25)":  bool(rsi_ok_long),
        "RSI short ok (<75)": bool(rsi_ok_short),
        "Touch prev low":     bool(touch_L),
        "Touch prev high":    bool(touch_H),
        "In trade window":    bool(in_trade_window),
    }
    s.raw = {
        "prev_H": float(prev_h), "prev_L": float(prev_l), "prev_mid": float(prev_mid),
        "bias_h": bias,
        "rsi":    float(rsi_v) if not pd.isna(rsi_v) else None,
        "vol":    float(vol),
        "vol_avg": float(vol_avg) if not pd.isna(vol_avg) else None,
        "utc_hour": utc_h,
        "price": s.price,
    }
    return s


# ═════ Position helpers ═════
def entry_price_zone(side: Side, prev_h: float, prev_l: float) -> float:
    """Fill-target price inside the S/R zone."""
    return prev_l * (1 + SUPPORT_ZONE / 2) if side == "LONG" else prev_h * (1 - SUPPORT_ZONE / 2)


def dca_price(side: Side, worst_entry: float) -> float:
    """Next DCA trigger price — DCA_SPACING beyond worst entry."""
    return worst_entry * (1 - DCA_SPACING) if side == "LONG" else worst_entry * (1 + DCA_SPACING)


def sl_price(side: Side, worst_entry: float) -> float:
    """Current SL price: SL_BELOW_WORST (2%) below worst entry for longs, above for shorts."""
    return worst_entry * (1 - SL_BELOW_WORST) if side == "LONG" else worst_entry * (1 + SL_BELOW_WORST)


def tp_price(side: Side, prev_mid: float) -> float:
    """TP = prev day's midpoint (same for both sides, absolute target)."""
    return float(prev_mid)


def per_level_qty(equity: float, price: float) -> float:
    """Sizing: total risk spread across DCA_LEVELS legs.
    Worst-case SL distance from L1 = (N-1)*spacing + SL_BELOW_WORST.
    """
    if price <= 0:
        return 0.0
    worst_sl_dist = (DCA_LEVELS - 1) * DCA_SPACING + SL_BELOW_WORST
    total_notional = equity * 0.95 * RISK_PCT / worst_sl_dist
    qty = total_notional / price / DCA_LEVELS
    cap = (equity * 0.95 * LEVERAGE) / price / DCA_LEVELS
    return min(qty, cap)


