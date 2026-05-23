"""
core.py — S/R DCA Day Strategy V2.2 (5m execution + 1d S/R)

Python port of strategy_sr_dca_5m.pine. V2.2 adds conditional hold-past-EOD
on top of V2.1's divergence + BE-stop foundation. Tested Mar 30–May 6:
V2.2 (hold @ 1.5% threshold) +33.13% / PF 14.08 / DD 2.56% / WR 76.92%
V2.1 baseline                +28.31% / PF 12.41 / DD 2.56% / WR 76.92%

V2.2 vs V2.1 (2026-05-06):
  - HOLD_PAST_EOD_IF_FAV: at 20:00 UTC, if fav ≥ 1.5% from first entry,
    skip the EOD close and let the trade ride. 24h hard cap at next day's
    20:00. Same DD, same trade count, same WR — pure upside on winners
    that were getting cut at EOD.

V2.1 vs V2 (2026-05-06):
  - SL below worst: 1.4% → 2.0% (BE-stop rescues borderline trades that
    would have noise-stopped at 1.4%)
  - Breakeven SL: OFF → ON (1.0% trigger / 0.25% buffer) — V2 said BE
    was redundant with divergence, but Mar 30–May 6 data showed it adds
    +1.3% return + +2.0 PF when paired with 2.0% SL.

V2 vs V1 (still in V2.1):
  - RSI divergence at S/R touch (DEFAULT ON, pivot 5/5, fresh 20 bars)
  - DCA spacing: 0.8% → 0.85%
  - Volume × 20-bar avg: 1.2 → 1.1 (slightly more permissive)
  - RSI anti-extreme filter: ON → OFF (subsumed by divergence)

Logic:
  - Entry: prev_day's L/H touch + fresh divergence + filters
  - DCA: 0.85% beyond L1 (2 levels default)
  - TP: hybrid (prev_mid pre-DCA, fixed % from first entry post-DCA)
  - SL: 2.0% below worst entry, tightens to entry × (1 ± 0.25%) once BE arms
  - BE arms when fav% from first_entry crosses 1.0%
  - EOD flatten at UTC 20:00
  - Max 1 cycle per UTC day

V1 (no divergence, no BE) preserved at v1_backup/ for rollback.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Literal, Dict, Any
import pandas as pd
import numpy as np

# ═════ V2 Constants (match pine V2 defaults) ═════
LEVERAGE       = 2.0
RISK_PCT       = 0.06         # 6% total risk per cycle

DCA_LEVELS     = 2
DCA_SPACING    = 0.003        # 0.3% (was 0.5%, originally 0.85%). Tightened 2026-05-23 at user request — 0.5% spacing only fired DCA on 25% of trades (2/8). 0.3% should roughly double the DCA-fill rate, giving more averaging when the entry zone re-tests.
SL_BELOW_WORST = 0.020        # V2.1: 2.0% (V2 was 1.4%) — BE-stop rescues noise-stopped trades, looser SL outperforms
SUPPORT_ZONE   = 0.0005       # 0.05% zone around prev H/L — only direct touches qualify

# TP offset: shift prev_mid TP slightly toward current price for reliable fills.
PREV_MID_OFFSET = 0.001       # 0.1%

# TP mode — hybrid: prev_mid pre-DCA, switches to first_entry × (1 ∓ TP_FIXED_PCT) post-DCA.
TP_MODE        = "hybrid"
TP_FIXED_PCT   = 0.04         # 4% — used post-DCA in hybrid mode

# Adaptive S/R range
RANGE_FILTER_MODE  = "extend"
MIN_PREV_RANGE_PCT = 0.02     # 2% floor
MAX_LOOKBACK_DAYS  = 2

# Breakeven SL — V2.1 ON. Once fav% from first entry crosses 1.0%, SL tightens
# to entry × (1 ± 0.25%) to lock in real ~0.20% net profit. With 2.0% SL,
# BE provides the second protection layer that rescues borderline trades.
USE_BREAKEVEN  = True
BE_TRIGGER_PCT = 0.01
BE_BUFFER_PCT  = 0.0025

CLOSE_HOUR     = 20           # UTC hour to force flatten + block new entries

# V2.2 — Conditional hold past EOD. When favorable ≥ HOLD_MIN_FAV_PCT at
# closeHour, skip the EOD close and let the trade ride to TP / SL / BE.
# 24h hard cap: at the next day's CLOSE_HOUR, force close regardless.
# Backtest Mar 30–May 6: ON gives +33.13% / DD 2.56% / PF 14.08 / WR 76.92%
# vs V2.1 baseline +28.31% / PF 12.41 — same DD, +4.82% return on winners
# that were getting cut at 20:00 UTC.
HOLD_PAST_EOD_IF_FAV = True
HOLD_MIN_FAV_PCT     = 0.005  # 0.5% — lowered 2026-05-23 (was 1.5%). Trade-log analysis: 4/8 trades EOD-flattened with avg +$4 because the 1.5% threshold was above where the typical winner sits at 20:00 UTC. 0.5% lets modest-favourable trades mature into TP next session; losing trades still EOD-flatten (overnight safety preserved); 24h hard cap unchanged.

# Entry filters
VOL_MULT       = 1.1          # V2: 1.1× (V1 was 1.2×) — slightly more permissive given divergence gate
USE_RSI_FILTER = False        # V2: RSI anti-extreme OFF (subsumed by divergence). V1 had this True.
RSI_LOW        = 25           # skip long if RSI < (only used when USE_RSI_FILTER)
RSI_HIGH       = 75           # skip short if RSI > (only used when USE_RSI_FILTER)

# RSI Divergence (V2 — required at S/R touch)
USE_RSI_DIVERGENCE = True
DIV_PIVOT_L  = 5              # 5 bars left for pivot confirmation
DIV_PIVOT_R  = 5              # 5 bars right (= 25 min confirmation lag on 5m)
DIV_FRESH_BARS = 20           # divergence stays usable for 20 bars (~100 min)

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

    # ─── V2: RSI divergence detection ───
    # Mirrors strategy_sr_dca_5m.pine. A pivot at index i is confirmed at
    # i+DIV_PIVOT_R (when both left and right windows are fully visible).
    # Bearish div = price HH + RSI LH between two confirmed pivot highs.
    # Bullish div = price LL + RSI HL between two confirmed pivot lows.
    # bars_since_*_div counts forward from the confirmation bar; entries
    # gate on that counter ≤ DIV_FRESH_BARS.
    df = detect_divergence(df, DIV_PIVOT_L, DIV_PIVOT_R)
    return df


def detect_divergence(df: pd.DataFrame, pivot_l: int = 5, pivot_r: int = 5) -> pd.DataFrame:
    """Add bear_div_fired, bull_div_fired, bars_since_bear_div, bars_since_bull_div columns.
    Pivots use strict greater-than on left and right windows (matching pine ta.pivothigh/pivotlow).
    """
    n = len(df)
    bear_fired = np.zeros(n, dtype=bool)
    bull_fired = np.zeros(n, dtype=bool)

    last_phigh = np.nan
    last_r_at_h = np.nan
    last_plow = np.nan
    last_r_at_l = np.nan

    highs = df["high"].values
    lows  = df["low"].values
    rsis  = df["rsi"].values

    # i is the pivot bar; confirmed at i+pivot_r once we've seen all right-side bars.
    for i in range(pivot_l, n - pivot_r):
        bar_rsi = rsis[i]
        if pd.isna(bar_rsi):
            continue

        bar_high = highs[i]
        bar_low  = lows[i]

        left_max_h  = highs[i-pivot_l:i].max() if pivot_l > 0 else -np.inf
        right_max_h = highs[i+1:i+pivot_r+1].max()
        is_phigh = bar_high > left_max_h and bar_high > right_max_h

        left_min_l  = lows[i-pivot_l:i].min() if pivot_l > 0 else np.inf
        right_min_l = lows[i+1:i+pivot_r+1].min()
        is_plow = bar_low < left_min_l and bar_low < right_min_l

        confirm_idx = i + pivot_r

        if is_phigh:
            if not pd.isna(last_phigh) and bar_high > last_phigh and bar_rsi < last_r_at_h:
                bear_fired[confirm_idx] = True
            last_phigh = bar_high
            last_r_at_h = bar_rsi

        if is_plow:
            if not pd.isna(last_plow) and bar_low < last_plow and bar_rsi > last_r_at_l:
                bull_fired[confirm_idx] = True
            last_plow = bar_low
            last_r_at_l = bar_rsi

    bars_since_bear = np.full(n, 9999, dtype=int)
    bars_since_bull = np.full(n, 9999, dtype=int)
    cb = 9999
    cu = 9999
    for i in range(n):
        cb = 0 if bear_fired[i] else cb + 1
        cu = 0 if bull_fired[i] else cu + 1
        bars_since_bear[i] = cb
        bars_since_bull[i] = cu

    df = df.copy()
    df["bear_div_fired"] = bear_fired
    df["bull_div_fired"] = bull_fired
    df["bars_since_bear_div"] = bars_since_bear
    df["bars_since_bull_div"] = bars_since_bull
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

    # RSI anti-extreme filter (V2: OFF by default — divergence subsumes it).
    rsi_ok_long  = (not USE_RSI_FILTER) or rsi_v >= RSI_LOW
    rsi_ok_short = (not USE_RSI_FILTER) or rsi_v <= RSI_HIGH

    # V2: RSI divergence gate — require a fresh divergence pivot within
    # DIV_FRESH_BARS bars before entry. Bearish for shorts at prev_H,
    # bullish for longs at prev_L.
    bars_since_bear = row.get("bars_since_bear_div", 9999)
    bars_since_bull = row.get("bars_since_bull_div", 9999)
    div_ok_long  = (not USE_RSI_DIVERGENCE) or (not pd.isna(bars_since_bull) and bars_since_bull <= DIV_FRESH_BARS)
    div_ok_short = (not USE_RSI_DIVERGENCE) or (not pd.isna(bars_since_bear) and bars_since_bear <= DIV_FRESH_BARS)

    # Touch conditions
    touch_L = row["low"] <= prev_l * (1 + SUPPORT_ZONE) and row["low"] > prev_l * (1 - 0.01)
    touch_H = row["high"] >= prev_h * (1 - SUPPORT_ZONE) and row["high"] < prev_h * (1 + 0.01)

    in_trade_window = utc_h < CLOSE_HOUR

    # NO BIAS GATE. Both directions allowed regardless of trend bias.
    long_ok  = (rsi_ok_long  and vol_ok and touch_L and in_trade_window and div_ok_long)
    short_ok = (rsi_ok_short and vol_ok and touch_H and in_trade_window and div_ok_short)

    if long_ok:
        s.side = "LONG"
    elif short_ok:
        s.side = "SHORT"

    s.conditions = {
        "1h bias BULL":       bool(bias == 1),
        "1h bias BEAR":       bool(bias == -1),
        f"Volume > {VOL_MULT}× avg": bool(vol_ok),
        "Touch prev low":     bool(touch_L),
        "Touch prev high":    bool(touch_H),
        "In trade window":    bool(in_trade_window),
        "Bull div fresh":     bool(div_ok_long),
        "Bear div fresh":     bool(div_ok_short),
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
        "bars_since_bear_div": int(bars_since_bear) if not pd.isna(bars_since_bear) else 9999,
        "bars_since_bull_div": int(bars_since_bull) if not pd.isna(bars_since_bull) else 9999,
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


