"""core_divflip_v2.py — Divergence-Flip v2 strategy logic.

A/B-test variant of core_divflip.py (3rd paper bot). Same divergence ENTRY
logic — the only differences are in DCA structure and the stop loss:

  v1 (core_divflip.py)          v2 (this file)
  ─────────────────────         ──────────────────────
  3 DCA levels                  2 DCA levels
  ratios 3:4:1.5                ratios 1:1 (L1 50% / L2 50%)
  0.35% spacing                 0.5% spacing
  SL 5%, anchored to L1         SL 1%, anchored to WORST entry (rides down w/ DCA)

Rationale: on the live divflip bot's first 9 trades, one trade ran the 5%
L1-anchored stop to −13.5% and erased 7 wins. A 2-DCA / 1% worst-anchored
stop replays those 9 trades to +8% (worst trade −3.8%) — see the candle
replay analysis. Running here as a parallel paper bot for an out-of-sample
read before any change to the v1 bot.

Entry signal, RSI filter, BE/trail, flip, leverage — all identical to v1.

Re-uses build_features / detect_divergence from core.py.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Literal, Dict, Any
import pandas as pd
import numpy as np

# Re-use indicator computation from V2.2 core
from core import build_features, detect_divergence, DIV_PIVOT_R as _CORE_DIV_PIVOT_R  # noqa: F401

# ═════ Pivot override (TV-tuned: 5L/1R) — identical to v1 ═════
DIV_PIVOT_L = 5
DIV_PIVOT_R = 1

# ═════ RSI period override (TV-tuned: 10) — identical to v1 ═════
RSI_PERIOD = 10

# ═════ Constants ═════
LEVERAGE       = 3.0
RISK_PCT       = 0.06

# ─ v2 DCA: 2 levels, 50/50, 0.5% spacing ─
# L2 fills 0.5% adverse from L1. Both legs equal weight (ratios 1:1):
# L1 = 50%, L2 = 50% of the leverage cap. Wider spacing than v1 (0.35%)
# so L2 only fills on a genuine dip, not noise.
DCA_LEVELS     = 2
DCA_SPACING    = 0.005        # 0.5% adverse triggers L2

MARTINGALE_RATIOS = [1.0, 1.0]   # L1 50% / L2 50% — equal split

# ─ v2 SL: 1%, anchored to WORST entry ─
# Anchored to worst_entry (= L2 once filled), NOT first_entry. So the stop
# rides down with each DCA leg — once L2 fills, the stop is L2 × (1∓1%).
# Caps the worst-case loss at ~−3.8% account vs the −13.5% the v1 5%
# L1-anchored stop allowed. See sl_price_divflip() below.
SL_FROM_WORST  = 0.01         # 1% from worst entry

# ─ Fixed TP from avg entry — identical to v1 (ON @ 1%) ─
USE_TAKE_PROFIT = True
TP_PCT          = 0.01        # 1% from avg entry

# 3Commas-style trailing — identical to v1.
USE_BREAKEVEN  = True
BE_TRIGGER_PCT = 0.0055       # arm BE / trailing at +0.55% favorable from first entry
BE_BUFFER_PCT  = 0.002        # initial floor at firstEntry ± 0.2%
TRAIL_DIST_PCT = 0.002        # 0.2% trail below peak (LONG) / above trough (SHORT)

# Flip on opposite divergence — OFF, identical to v1.
USE_FLIP = False

# RSI filter — asymmetric, identical to v1. The ≤40 long variant was tested
# and rejected (it cuts winners and keeps the loser — RSI@pivot does not
# separate winners from losers).
USE_RSI_LEVEL_FILTER = True
RSI_LONG_MAX  = 50            # bull div: RSI at pivot ≤ 50
RSI_SHORT_MIN = 66            # bear div: RSI at pivot ≥ 66

# Divergence freshness window — 21 bars on 5m = 105 min. Identical to v1.
DIV_FRESH_BARS = 21

Side = Literal["LONG", "SHORT"]


@dataclass
class DivSignalState:
    side: Optional[Side] = None
    price: float = 0.0
    flip_opposite: bool = False  # set if we're in a position AND opposite signal fires
    conditions: Dict[str, bool] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


def _rsi_at_pivot(df: pd.DataFrame, last_idx: int, bars_since_fire: int) -> Optional[float]:
    """Get RSI value at the PIVOT bar (= confirmation bar − DIV_PIVOT_R)."""
    pivot_idx = last_idx - bars_since_fire - DIV_PIVOT_R
    if pivot_idx < 0:
        return None
    val = df.iloc[pivot_idx].get("rsi", np.nan)
    return None if pd.isna(val) else float(val)


def evaluate_signal_divflip(df: pd.DataFrame, last_idx: int, current_side: Optional[Side] = None) -> DivSignalState:
    """Fresh-divergence signal evaluation with optional RSI level filter.

    Returns:
      side: "LONG" / "SHORT" if a fresh divergence fired AND passes RSI filter
      flip_opposite: True if current_side is set AND opposite signal fired (filter-gated)
    """
    s = DivSignalState()
    row = df.iloc[last_idx]
    s.price = float(row["close"])

    bars_since_bear = int(row.get("bars_since_bear_div", 9999))
    bars_since_bull = int(row.get("bars_since_bull_div", 9999))

    bull_fresh = bars_since_bull <= DIV_FRESH_BARS
    bear_fresh = bars_since_bear <= DIV_FRESH_BARS

    rsi_bull_pivot = _rsi_at_pivot(df, last_idx, bars_since_bull) if bars_since_bull < 9999 else None
    rsi_bear_pivot = _rsi_at_pivot(df, last_idx, bars_since_bear) if bars_since_bear < 9999 else None
    if USE_RSI_LEVEL_FILTER:
        if bull_fresh and (rsi_bull_pivot is None or rsi_bull_pivot > RSI_LONG_MAX):
            bull_fresh = False
        if bear_fresh and (rsi_bear_pivot is None or rsi_bear_pivot < RSI_SHORT_MIN):
            bear_fresh = False

    # Pick the FRESHER signal if both are fresh (tiebreaker: more recent wins).
    if bull_fresh and bear_fresh:
        if bars_since_bull < bars_since_bear:
            s.side = "LONG"
        else:
            s.side = "SHORT"
    elif bull_fresh:
        s.side = "LONG"
    elif bear_fresh:
        s.side = "SHORT"

    # Flip detection — opposite signal while in position
    if current_side == "LONG" and bear_fresh:
        s.flip_opposite = True
    elif current_side == "SHORT" and bull_fresh:
        s.flip_opposite = True

    bull_rsi_pass = (rsi_bull_pivot is not None) and (rsi_bull_pivot <= RSI_LONG_MAX)
    bear_rsi_pass = (rsi_bear_pivot is not None) and (rsi_bear_pivot >= RSI_SHORT_MIN)

    s.conditions = {
        "Bull div fresh": bull_fresh,
        "Bear div fresh": bear_fresh,
        f"Bull RSI@pivot ≤{int(RSI_LONG_MAX)}": bull_rsi_pass,
        f"Bear RSI@pivot ≥{int(RSI_SHORT_MIN)}": bear_rsi_pass,
        f"In position ({current_side})" if current_side else "Flat": current_side is not None,
        "Opposite signal (flip)": s.flip_opposite,
    }
    s.raw = {
        "bars_since_bear_div": bars_since_bear,
        "bars_since_bull_div": bars_since_bull,
        "rsi": float(row.get("rsi", 0)) if not pd.isna(row.get("rsi", np.nan)) else None,
        "rsi_at_bull_pivot": rsi_bull_pivot,
        "rsi_at_bear_pivot": rsi_bear_pivot,
        "rsi_long_max": RSI_LONG_MAX,
        "rsi_short_min": RSI_SHORT_MIN,
        "price": s.price,
    }
    return s


# ═════ Position helpers ═════
def dca_price(side: Side, worst_entry: float) -> float:
    """Next DCA trigger price — DCA_SPACING adverse beyond worst entry."""
    return worst_entry * (1 - DCA_SPACING) if side == "LONG" else worst_entry * (1 + DCA_SPACING)


def sl_price_divflip(side: Side, worst_entry: float, first_entry: Optional[float] = None,
                     be_activated: bool = False, peak_price: Optional[float] = None) -> float:
    """Composite SL — v2: raw SL anchored to WORST entry (not first entry).

    The raw stop rides DOWN with each DCA leg: while only L1 is filled the
    anchor is L1; once L2 fills the anchor becomes L2, so the stop is
    worst_entry × (1 ∓ SL_FROM_WORST). This is the "decide the stop from L2"
    design — caps the worst-case loss tightly instead of the v1 5% L1 stop.

    Components (LONG — symmetric for SHORT):
      - Raw SL    = worst_entry × (1 − SL_FROM_WORST)        [hard floor, always active]
      - BE SL     = first_entry × (1 + BE_BUFFER_PCT)        [active when BE armed]
      - Trail SL  = peak_price  × (1 − TRAIL_DIST_PCT)       [active when BE armed]

    BE floor + trailing still anchor to first_entry / peak (unchanged from v1)
    — only the raw stop's anchor moved from first_entry to worst_entry.
    """
    anchor = worst_entry
    raw_sl = anchor * (1 - SL_FROM_WORST) if side == "LONG" else anchor * (1 + SL_FROM_WORST)
    if not (USE_BREAKEVEN and be_activated and first_entry is not None):
        return raw_sl
    be_sl = first_entry * (1 + BE_BUFFER_PCT) if side == "LONG" else first_entry * (1 - BE_BUFFER_PCT)
    if peak_price is None or peak_price <= 0:
        return max(raw_sl, be_sl) if side == "LONG" else min(raw_sl, be_sl)
    trail_sl = peak_price * (1 - TRAIL_DIST_PCT) if side == "LONG" else peak_price * (1 + TRAIL_DIST_PCT)
    if side == "LONG":
        return max(raw_sl, be_sl, trail_sl)
    return min(raw_sl, be_sl, trail_sl)


def be_should_activate(side: Side, first_entry: float, current_price: float) -> bool:
    """True if favorable% from first entry crossed BE_TRIGGER_PCT."""
    if not USE_BREAKEVEN or first_entry is None or first_entry <= 0:
        return False
    fav = (current_price - first_entry) / first_entry if side == "LONG" else (first_entry - current_price) / first_entry
    return fav >= BE_TRIGGER_PCT


def per_level_qty(equity: float, price: float, leg_idx: int = 0, leverage: float = None) -> float:
    """Martingale-weighted per-leg sizing. Total notional cap = leverage × 0.95 ×
    equity is distributed across legs via MARTINGALE_RATIOS.

    v2: ratios are [1.0, 1.0] → L1 and L2 each get 50% of the cap.
    """
    if price <= 0:
        return 0.0
    lev = leverage if leverage is not None else LEVERAGE
    total = (equity * 0.95 * lev) / price
    total_ratio = sum(MARTINGALE_RATIOS[:DCA_LEVELS])
    if leg_idx >= len(MARTINGALE_RATIOS):
        return 0.0
    return total * MARTINGALE_RATIOS[leg_idx] / total_ratio
