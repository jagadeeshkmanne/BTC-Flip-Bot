"""core_divflip.py — Divergence-Flip strategy logic.

Always-in-market strategy that flips on every fresh RSI divergence.

Spec:
  - Entry: fresh bull div -> LONG, fresh bear div -> SHORT (no S/R touch required, no volume filter)
  - DCA: 2 levels at 0.85% spacing
  - TP: 0.5% from AVG entry (recomputed when DCA fires — closer to current price post-DCA)
  - SL: 2% from worst entry
  - BE: arms at +0.4% favorable from first entry, tightens SL to firstEntry +/- 0.25%
  - Flip: opposite divergence -> close current position + open reverse
  - No EOD flatten, multi-cycle (unlimited per day)
  - Leverage cap 2x, 6% risk allocation

Re-uses build_features / detect_divergence from core.py — only the signal
evaluation differs (no S/R touch requirement).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Literal, Dict, Any
import pandas as pd
import numpy as np

# Re-use indicator computation from V2.2 core
from core import build_features, detect_divergence, DIV_PIVOT_R  # noqa: F401

# ═════ Constants ═════
LEVERAGE       = 2.0
RISK_PCT       = 0.06

DCA_LEVELS     = 2
DCA_SPACING    = 0.006        # 0.6% adverse triggers L2 (fixed). 2 levels — RSI filter cuts most trades to 1-leg anyway, bigger per-leg sizing gives bigger wins.
SL_FROM_WORST  = 0.02         # 2% below first entry (raw SL — anchored to L1, room for multi-bar pullbacks to recover)

# 3Commas-style trailing — only arms at a meaningful profit threshold.
# Below BE trigger: raw SL only (loss-bound). At BE trigger: trailing arms,
# locks in min profit via BE_BUFFER floor, then trails peak − TRAIL_DIST_PCT.
USE_BREAKEVEN  = True
BE_TRIGGER_PCT = 0.005        # arm BE / trailing at +0.5% favorable from first entry (was 0.3%)
BE_BUFFER_PCT  = 0.002        # initial floor at firstEntry ± 0.2% (was 0.15% — bigger after-fee lock-in)
TRAIL_DIST_PCT = 0.002        # 0.2% trail below peak (LONG) / above trough (SHORT)

# RSI filter — only fire divergence entries when RSI at the pivot is in
# extreme oversold (bull) / overbought (bear) territory. Cuts mid-range
# divergences (the fakeout-prone ones, like the top-left BULL on chart).
USE_RSI_LEVEL_FILTER = True
RSI_LONG_MAX  = 30            # bull div: RSI at pivot ≤ 30 (oversold) to qualify
RSI_SHORT_MIN = 70            # bear div: RSI at pivot ≥ 70 (overbought) to qualify

# Divergence freshness — tighter than V2.2's 20-bar window because we're
# trading the divergence itself (not as a confirm for an S/R touch).
DIV_FRESH_BARS = 20           # 100-min freshness window — same as V2.2's validated setting.
                              # With RSI filter active, longer freshness = better outcomes (more
                              # time to catch real high-quality signals; backtest confirmed).

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

    # RSI filter at pivot — bull needs RSI ≤ RSI_LONG_MAX (oversold),
    # bear needs RSI ≥ RSI_SHORT_MIN (overbought). Strips out the
    # mid-range divergences that tend to fakeout.
    rsi_bull_pivot = _rsi_at_pivot(df, last_idx, bars_since_bull) if bull_fresh else None
    rsi_bear_pivot = _rsi_at_pivot(df, last_idx, bars_since_bear) if bear_fresh else None
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

    s.conditions = {
        "Bull div fresh": bull_fresh,
        "Bear div fresh": bear_fresh,
        f"In position ({current_side})" if current_side else "Flat": current_side is not None,
        "Opposite signal (flip)": s.flip_opposite,
    }
    s.raw = {
        "bars_since_bear_div": bars_since_bear,
        "bars_since_bull_div": bars_since_bull,
        "rsi": float(row.get("rsi", 0)) if not pd.isna(row.get("rsi", np.nan)) else None,
        "price": s.price,
    }
    return s


# ═════ Position helpers ═════
def dca_price(side: Side, worst_entry: float) -> float:
    """Next DCA trigger price — DCA_SPACING adverse beyond worst entry."""
    return worst_entry * (1 - DCA_SPACING) if side == "LONG" else worst_entry * (1 + DCA_SPACING)


def sl_price_divflip(side: Side, worst_entry: float, first_entry: Optional[float] = None,
                     be_activated: bool = False, peak_price: Optional[float] = None) -> float:
    """Composite SL — anchored to FIRST entry (not worst), so DCA only ever
    improves avg, never widens SL. Max loss is bounded regardless of legs.

    Components (LONG — symmetric for SHORT):
      - Raw SL    = first_entry × (1 − SL_FROM_WORST)         [hard floor, always active]
      - BE SL     = first_entry × (1 + BE_BUFFER_PCT)         [active when BE armed]
      - Trail SL  = peak_price × (1 − TRAIL_DIST_PCT)         [active when BE armed]

    Note: param name `worst_entry` is kept for backward compatibility, but the
    SL uses `first_entry` only — `worst_entry` is no longer consulted here.

    Before BE arms: only raw SL applies (≤1% loss from L1).
    After BE arms: SL trails peak with floor at firstEntry + buffer.
    """
    anchor = first_entry if first_entry is not None else worst_entry
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


def per_level_qty(equity: float, price: float) -> float:
    """Sizing: at LEVERAGE cap, total notional = LEVERAGE × 0.95 × equity.
    Per leg = total / price / DCA_LEVELS."""
    if price <= 0:
        return 0.0
    return (equity * 0.95 * LEVERAGE) / price / DCA_LEVELS
