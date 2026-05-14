"""core_divflip.py — Divergence-Flip strategy logic.

Always-in-market strategy that flips on every fresh RSI divergence.

Spec (TV-tuned 2026-05-14 — 100.00% WR / +72.50% / PF 582 / 7.52% DD over 103 trades):
  - Entry: fresh bull div -> LONG, fresh bear div -> SHORT (no S/R touch required, no volume filter)
  - DCA: 3 levels at 0.3% spacing, ANTI-martingale qty 3:3:2 (L1=37.5%, L2=37.5%, L3=25%)
  - TP: 1% from AVG entry (PRIMARY exit — high hit rate, fires before trail in most trades)
  - SL: 5% from L1 first entry (WIDE backstop — DCA never widens SL)
  - BE: arms at +0.55% favorable from first entry, then trails peak ± 0.2% (backstop if TP doesn't hit)
  - Flip: OFF (rides to TP/SL/trail — opposite divergence does NOT close position)
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
from core import build_features, detect_divergence, DIV_PIVOT_R as _CORE_DIV_PIVOT_R  # noqa: F401

# ═════ Pivot override (TV-tuned: 5L/2R) ═════
# V2.2 core uses 5/5 (25-min confirmation lag). The TV-tuned divflip config
# uses 5L/2R — pivot fires 10 min after the low instead of 25, catching
# reversals 3 bars earlier. Bot runner re-runs detect_divergence with these
# values after build_features so divergence columns reflect the override.
DIV_PIVOT_L = 5
DIV_PIVOT_R = 2

# ═════ Constants ═════
LEVERAGE       = 2.0
RISK_PCT       = 0.06

DCA_LEVELS     = 3
DCA_SPACING    = 0.003        # 0.3% adverse triggers each DCA leg (L2 at -0.3%, L3 at -0.6%).
                              # Tight spacing fills L3 often (~50% of trades) → deep averaging.

# ANTI-martingale sizing — biggest qty at BEST (L1) price, smallest at
# deepest leg. With 3:3:2 ratio: L1=37.5%, L2=37.5%, L3=25% of leverage cap.
# Rationale: with USE_TAKE_PROFIT=True @ 1% from avg, front-loading L1
# means TP fires sooner on a reversal. Producing 100.00% WR / PF 582 /
# +72.50% / 7.52% DD over 103 trades on the user's TV backtest.
# Total notional still capped by LEVERAGE — ratios just redistribute within cap.
MARTINGALE_RATIOS = [3.0, 3.0, 2.0]   # qty multiplier per leg (L1, L2, L3) — anti-martingale
SL_FROM_WORST  = 0.05         # 5% from first entry (L1-anchored). Wide backstop — TV-tuned config relies on
                              # 1% TP from avg entry (USE_TAKE_PROFIT below) and 0.2% trail-after-BE for actual
                              # exits. SL only fires on a genuine adverse run (rare → 99% WR / 7.3% DD).

# ─ Fixed TP from avg entry (TV-tuned: ON @ 1%) ─
# Primary exit. Recomputed when DCA fires (avg moves closer to live), so a deep
# DCA-filled position needs less recovery to hit TP. Trailing SL is the backstop
# when price never reaches +1% (rare in TV backtest).
USE_TAKE_PROFIT = True
TP_PCT          = 0.01        # 1% from avg entry

# 3Commas-style trailing — only arms at a meaningful profit threshold.
# Below BE trigger: raw SL only (loss-bound). At BE trigger: trailing arms,
# locks in min profit via BE_BUFFER floor, then trails peak − TRAIL_DIST_PCT.
USE_BREAKEVEN  = True
BE_TRIGGER_PCT = 0.0055       # arm BE / trailing at +0.55% favorable from first entry (TV-tuned)
BE_BUFFER_PCT  = 0.002        # initial floor at firstEntry ± 0.2% (after-fee lock-in)
TRAIL_DIST_PCT = 0.002        # 0.2% trail below peak (LONG) / above trough (SHORT)

# Flip on opposite divergence — OFF in TV-tuned config. Lets trades ride
# to SL / trailing exit instead of bouncing between sides on every opposite
# signal. With useFlip=ON, mid-position whipsaws ate into wins.
USE_FLIP = False

# RSI filter — asymmetric (TV-tuned). Loose bull (≤50) lets through most
# bull divergences in this BTC uptrend regime. Strict bear (≥70) only
# accepts genuinely overbought tops. Long-biased by design — caught the
# Apr–May 2026 uptrend with 74.83% WR over 145 trades.
USE_RSI_LEVEL_FILTER = True
RSI_LONG_MAX  = 50            # bull div: RSI at pivot ≤ 50 (loose — catches most lows)
RSI_SHORT_MIN = 70            # bear div: RSI at pivot ≥ 70 (strict — only extreme tops)

# Divergence freshness window — 21 bars on 5m = 105 min.
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

    # RSI filter at pivot — bull needs RSI ≤ RSI_LONG_MAX (oversold),
    # bear needs RSI ≥ RSI_SHORT_MIN (overbought). Strips out the
    # mid-range divergences that tend to fakeout.
    # Compute RSI@pivot even if not "fresh" so dashboard can show last
    # pivot's RSI value alongside the freshness check.
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

    # Pre-compute pass/fail of the RSI@pivot check for dashboard display
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


def per_level_qty(equity: float, price: float, leg_idx: int = 0) -> float:
    """Martingale-weighted per-leg sizing. Total notional cap = LEVERAGE × 0.95 ×
    equity is distributed across legs via MARTINGALE_RATIOS.

    For 1:2:4 ratios: L1 = 1/7 of cap, L2 = 2/7, L3 = 4/7. Deepest leg gets
    biggest qty (averages closer to SL, smallest unit loss when SL hits)."""
    if price <= 0:
        return 0.0
    total = (equity * 0.95 * LEVERAGE) / price
    total_ratio = sum(MARTINGALE_RATIOS[:DCA_LEVELS])
    if leg_idx >= len(MARTINGALE_RATIOS):
        return 0.0
    return total * MARTINGALE_RATIOS[leg_idx] / total_ratio
