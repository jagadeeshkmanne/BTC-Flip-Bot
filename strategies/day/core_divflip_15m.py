"""core_divflip.py — Divergence-Flip strategy logic.

Always-in-market strategy that flips on every fresh RSI divergence.

Spec (TV-tuned 2026-05-14 — +189.83% / 10.86% DD / 138 trades / 97.83% WR / PF 108.6 / Calmar 17.5):
  - Entry: fresh bull div -> LONG, fresh bear div -> SHORT (no S/R touch required, no volume filter)
  - RSI period 10 (faster than Wilder 14 — more pivots qualify the level filter)
  - Pivot 5L/1R (divergence confirms 1 bar = 5 min after the low, fastest setting)
  - RSI level filter: ≤50 (bull) / ≥66 (bear) — looser bear vs prior 70
  - DCA: 3 levels at 0.35% spacing, mixed-shape ratios 3:4:1.5 (L1=35.3%, L2=47.1%, L3=17.6%)
  - TP: 1% from AVG entry (PRIMARY exit — high hit rate, fires before trail in most trades)
  - SL: 5% from L1 first entry (WIDE backstop — DCA never widens SL)
  - BE: arms at +0.55% favorable from first entry, then trails peak ± 0.2% (backstop if TP doesn't hit)
  - Flip: OFF (rides to TP/SL/trail — opposite divergence does NOT close position)
  - No EOD flatten, multi-cycle (unlimited per day)
  - Leverage cap 3x, 6% risk allocation

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

# ═════ Pivot override (TV-tuned: 5L/1R) ═════
# V2.2 core uses 5/5 (25-min confirmation lag). The TV-tuned divflip config
# uses 5L/1R — pivot fires only 1 bar (5 min) after the low instead of 25,
# catching reversals 4 bars earlier. Loosest pivot setting → more pivots
# qualify, more entries. Bot runner re-runs detect_divergence with these
# values after build_features so divergence columns reflect the override.
DIV_PIVOT_L = 7      # 15m TUNED: was 5 — wider pivot, fewer noise signals
DIV_PIVOT_R = 1

# ═════ RSI period override (15m TUNED: 7) ═════
# Lower RSI period on higher TF: 7×15min ≈ similar reactivity to 10×5min.
# Backtested vs RSI 5/10/14/21 — RSI 7 is the optimum on 15m.
RSI_PERIOD = 7

# ═════ Constants ═════
LEVERAGE       = 3.0
RISK_PCT       = 0.06

# 15m v1f tuned config (2026-05-23):
# 45-day backtest: +$3,990 / +79.8% / PF 3.98 / DD 4.7% / 22 trades
# Justification: 7L/1R pivot + RSI 7 + L≤45/S≥65 + DCA 0.75% + SL 2%
# (from L1) + BE 2% (from avg) + trail 0.2% + NO fixed TP + NO cooldown
# + NO max_hold. The high BE bar means only positions that REACH +2%
# favorable arm — that's the filter, not time-based safeties.
DCA_LEVELS     = 2
DCA_SPACING    = 0.0075       # 0.75% (was 0.35% on 5m — 2× wider for 15m bars)
MARTINGALE_RATIOS = [3.0, 4.0]

SL_FROM_WORST  = 0.02         # 2% (was 1% on 5m) — 15m bars cover bigger moves
SL_ANCHOR_FIRST = True        # anchor to first_entry (L1)
SL_COOLDOWN_HOURS = 0         # NO cooldown — BE 2% trigger is the filter
MAX_HOLD_HOURS = 0            # NO time-stop — disabled (set 0 to skip)

# ─ NO fixed TP — let BE+trail do the work ─
# With BE at +2% from avg, winners exit via trail at peak − 0.2% (or higher
# if a stronger peak forms). Fixed TP would cap upside unnecessarily.
USE_TAKE_PROFIT = False
TP_PCT          = 0.02        # unused (USE_TAKE_PROFIT=False) but kept for dashboard

# BE high-bar — arms only when position reaches +2% favorable from AVG.
# At BE arm: SL tightens to first_entry × (1 + buffer) and trail activates.
USE_BREAKEVEN  = True
BE_TRIGGER_PCT = 0.02         # 2% favorable from avg (was 0.55% on 5m)
BE_BUFFER_PCT  = 0.002        # 0.2% buffer above L1 after BE arms
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
RSI_LONG_MAX  = 45            # 15m tuned: was 50 (5m). Bull div RSI at pivot ≤45.
RSI_SHORT_MIN = 65            # 15m tuned: was 66 (5m). Bear div RSI at pivot ≥65.

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
    """Composite SL.
    v1f (2026-05-23): SL_ANCHOR_FIRST=True anchors the raw stop to first_entry
    instead of worst_entry. MAE analysis on 15 paper trades: all winners had
    MAE ≤ 0.78%, both losses had MAE 2.0%+. First-anchored 1% SL catches losses
    BEFORE they go L3-deep without stopping any winners.

    Components (LONG — symmetric for SHORT):
      - Raw SL    = first_entry × (1 − SL_FROM_WORST)  [v1f: first-anchored]
      - BE SL     = first_entry × (1 + BE_BUFFER_PCT)  [when BE armed]
      - Trail SL  = peak_price × (1 − TRAIL_DIST_PCT)  [when BE armed]
    """
    anchor = first_entry if (SL_ANCHOR_FIRST and first_entry is not None) else worst_entry
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

    `leverage` param lets the caller pass the position's captured leverage
    (so subsequent DCA legs honor the leverage that was in effect when L1
    opened, even if the global LEVERAGE constant changes mid-trade).
    """
    if price <= 0:
        return 0.0
    lev = leverage if leverage is not None else LEVERAGE
    total = (equity * 0.95 * lev) / price
    total_ratio = sum(MARTINGALE_RATIOS[:DCA_LEVELS])
    if leg_idx >= len(MARTINGALE_RATIOS):
        return 0.0
    return total * MARTINGALE_RATIOS[leg_idx] / total_ratio
