"""
core.py — S/R DCA Day Strategy (5m execution + 1d S/R)

Python port of strategy_sr_dca_5m.pine. Mirrors the pine tested-best
config: extend-mode adaptive S/R range (2-day fallback at 2% floor),
hybrid TP (prev_mid pre-DCA, first_entry × (1 ∓ 4%) post-DCA), 1.9% SL.
TV backtest Mar 23–May 3 (5w): +40.59% / PF 3.86 / DD 4.80% / 47 trades.

  - Entry: prev_day's L/H touch (with N-day fallback if range < floor) + filters
  - DCA: 0.8% beyond L1 (2 levels default)
  - TP: hybrid (prev_mid pre-DCA, fixed % from first entry post-DCA)
  - SL: 1.9% below worst entry
  - EOD flatten at UTC 20:00
  - Max 1 cycle per UTC day
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
DCA_SPACING    = 0.008        # 0.8% between DCA legs
SL_BELOW_WORST = 0.019        # 1.9% below worst entry (above for shorts) — tested better than 2.0% on 5w window
SUPPORT_ZONE   = 0.0005       # 0.05% zone around prev H/L — only direct touches qualify

# TP offset: shift prev_mid TP slightly toward current price for reliable fills.
# prev_mid sits at a thin-liquidity zone where exact-fill is unreliable; the
# offset catches near-misses where price reverses just before exact mid.
# Backtest Mar 23–May 1: +35.25% with offset vs +32.07% without (same DD).
PREV_MID_OFFSET = 0.001       # 0.1%

# TP mode (matches pine default).
#   "prev_mid" — exit at prev_mid for all legs (legacy baseline, +32% 5w).
#   "hybrid"   — prev_mid pre-DCA, then switch to first_entry × (1 ∓ TP_FIXED_PCT)
#                once DCA fires. Tested best (+40.59% / 5w / Mar 23–May 3) paired
#                with adaptive S/R range extend mode below.
TP_MODE        = "hybrid"
TP_FIXED_PCT   = 0.04         # 4% — used post-DCA in hybrid mode

# Adaptive S/R range — when prev-day range is tight (e.g., 1% squeeze day),
# prev_mid TP target sits ~0.5% from entry while SL is 2% → R:R 0.25, need
# 80% WR to break even. Range filter widens the lookback to N-day rolling H/L
# until the band exceeds the floor, restoring sane R:R.
#   "off"    — always use prev_day H/L (legacy baseline).
#   "skip"   — gate entries when prev_day range < floor (sit out tight days).
#   "extend" — expand to 2..MAX_LOOKBACK_DAYS-day rolling H/L until range ≥ floor.
RANGE_FILTER_MODE  = "extend"
MIN_PREV_RANGE_PCT = 0.02     # 2% floor (R:R ~0.5 with default 2% SL)
MAX_LOOKBACK_DAYS  = 2        # 2 tested best on 5w window — wider lookbacks left more days unfiltered

# Breakeven SL — once favorable% from FIRST entry crosses BE_TRIGGER_PCT,
# tighten SL to first_entry ± BE_BUFFER_PCT for the rest of this cycle.
# Saves losers that round-trip from peak runup, leaves trade count unchanged.
# Tested +43.18% / PF 4.42 vs +40.59% / PF 3.86 baseline (Mar 23–May 3, 5w).
USE_BREAKEVEN  = True
BE_TRIGGER_PCT = 0.01         # 1.0% favorable from first entry
BE_BUFFER_PCT  = 0.0025       # 0.25% buffer — covers 2× taker fee (0.04%×2) + slippage

CLOSE_HOUR     = 20           # UTC hour to force flatten + block new entries

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

    # ─── Prev-day H/L/mid (with optional range-floor fallback) ───
    # Pre-compute rolling N-day H/L offsets for 1..MAX_LOOKBACK_DAYS, all
    # shifted by 1 so today's bar never leaks into "prev". In extend mode,
    # walk the cascade and pick the first lookback that meets the floor.
    d1 = df_1d.copy().reset_index(drop=True)
    d1["timestamp"] = pd.to_datetime(d1["timestamp"])
    d1["date"] = d1["timestamp"].dt.normalize()
    for n in range(1, MAX_LOOKBACK_DAYS + 1):
        d1[f"prev_H_{n}"] = d1["high"].rolling(n).max().shift(1)
        d1[f"prev_L_{n}"] = d1["low"].rolling(n).min().shift(1)

    if RANGE_FILTER_MODE == "extend":
        h = d1["prev_H_1"].copy()
        l = d1["prev_L_1"].copy()
        lookback = pd.Series(1, index=d1.index)
        for n in range(2, MAX_LOOKBACK_DAYS + 1):
            need_extend = ((h - l) / l < MIN_PREV_RANGE_PCT)
            h = h.where(~need_extend, d1[f"prev_H_{n}"])
            l = l.where(~need_extend, d1[f"prev_L_{n}"])
            lookback = lookback.where(~need_extend, n)
        d1["prev_H"]   = h
        d1["prev_L"]   = l
        d1["prev_lookback"] = lookback
    else:
        d1["prev_H"] = d1["prev_H_1"]
        d1["prev_L"] = d1["prev_L_1"]
        d1["prev_lookback"] = 1
    d1["prev_mid"] = (d1["prev_H"] + d1["prev_L"]) / 2.0

    prev_h_map = dict(zip(d1["date"], d1["prev_H"]))
    prev_l_map = dict(zip(d1["date"], d1["prev_L"]))
    prev_m_map = dict(zip(d1["date"], d1["prev_mid"]))
    prev_lb_map = dict(zip(d1["date"], d1["prev_lookback"]))

    df["prev_H"]   = df["date"].map(prev_h_map)
    df["prev_L"]   = df["date"].map(prev_l_map)
    df["prev_mid"] = df["date"].map(prev_m_map)
    df["prev_lookback"] = df["date"].map(prev_lb_map)
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

    # Range-skip gate. In skip mode, block entries when active prev range <
    # floor — sit out tight days entirely. In off/extend modes, build_features
    # has already widened prev_H/prev_L if needed, so the gate passes.
    prev_range_pct = (prev_h - prev_l) / prev_l if prev_l > 0 else 0.0
    range_ok = RANGE_FILTER_MODE != "skip" or prev_range_pct >= MIN_PREV_RANGE_PCT
    if not range_ok:
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

    # NO BIAS GATE. Both directions allowed regardless of trend bias.
    # The bias filter was tested and removed — it blocked 60%+ of valid
    # entries because bias flips BEAR right when price reaches prev_L
    # (likewise BULL when price reaches prev_H).
    long_ok  = (rsi_ok_long  and vol_ok and touch_L and in_trade_window)
    short_ok = (rsi_ok_short and vol_ok and touch_H and in_trade_window)

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
    lookback = row.get("prev_lookback", 1)
    s.raw = {
        "prev_H": float(prev_h), "prev_L": float(prev_l), "prev_mid": float(prev_mid),
        "prev_range_pct": float(prev_range_pct),
        "prev_lookback": int(lookback) if not pd.isna(lookback) else 1,
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


def sl_price(side: Side, worst_entry: float, first_entry: float = None, be_activated: bool = False) -> float:
    """SL price. Defaults to worst_entry × (1 ∓ SL_BELOW_WORST) (1.9%).
    When USE_BREAKEVEN and be_activated, tightens to first_entry × (1 ± BE_BUFFER_PCT)
    to lock in a small profit instead of the full SL distance.
    """
    if USE_BREAKEVEN and be_activated and first_entry is not None:
        return first_entry * (1 + BE_BUFFER_PCT) if side == "LONG" else first_entry * (1 - BE_BUFFER_PCT)
    return worst_entry * (1 - SL_BELOW_WORST) if side == "LONG" else worst_entry * (1 + SL_BELOW_WORST)


def be_should_activate(side: Side, first_entry: float, current_price: float) -> bool:
    """True if favorable% from first_entry has crossed BE_TRIGGER_PCT.
    Caller is expected to OR this with prior `be_activated` and persist
    (BE is sticky — once armed, stays armed for the cycle)."""
    if not USE_BREAKEVEN or first_entry is None or first_entry <= 0:
        return False
    fav = (current_price - first_entry) / first_entry if side == "LONG" else (first_entry - current_price) / first_entry
    return fav >= BE_TRIGGER_PCT


def tp_price(side: Side, prev_mid: float, first_entry: float = None, filled_count: int = 1) -> float:
    """TP target. Defaults to prev_mid (with PREV_MID_OFFSET shift toward
    current price for fill reliability). In hybrid mode, switches to
    first_entry × (1 ∓ TP_FIXED_PCT) once DCA has fired (filled_count ≥ 2),
    matching the pine strategy's tested-best config (+40.59% / 5w).
    """
    if TP_MODE == "hybrid" and filled_count >= 2 and first_entry is not None:
        if side == "LONG":
            return float(first_entry) * (1 + TP_FIXED_PCT)
        return float(first_entry) * (1 - TP_FIXED_PCT)
    if side == "LONG":
        return float(prev_mid) * (1 - PREV_MID_OFFSET)
    return float(prev_mid) * (1 + PREV_MID_OFFSET)


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


