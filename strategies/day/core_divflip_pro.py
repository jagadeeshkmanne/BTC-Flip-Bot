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
DIV_PIVOT_L = 5
DIV_PIVOT_R = 1   # Pro: loose pivot (1-bar confirm) — relies on 15m EMA + ATR for quality

# ═════ RSI period override (TV-tuned: 10) ═════
# V2.2 core uses RSI 14 (Wilder default). divflip uses RSI 10 — faster
# reaction, more pivots qualify the ≤50/≥70 filter, ~15% more trades.
# Bot runner recomputes df["rsi"] with this period after build_features,
# then re-runs detect_divergence so pivot RSI values reflect the override.
RSI_PERIOD = 14   # 2026-05-24: 10 → 14 (Wilder standard, smoother)

# ═════ Constants ═════
# LEVERAGE is the DEFAULT used for NEW positions only. Each position captures
# its leverage at L1 entry into pos["leverage"] so subsequent DCA legs use the
# leverage that was in effect when the position opened — changes to this
# constant only take effect for the next fresh entry.
LEVERAGE       = 3.0
RISK_PCT       = 0.06

DCA_LEVELS     = 2   # v1b: L3 disabled (live data shows L3 fills lose -0.58% avg)
DCA_SPACING    = 0.005        # 0.5% — tuned 2026-05-25 (was 0.35%, too tight, fired on noise).
                              # Median 2h MAE is -0.31%; 0.5% catches 25%-ile pullback only.
                              # TV-tuned: wider than 0.3% — DCA fires on deeper dips, better fills.

# Mixed-shape sizing — biggest qty in the MIDDLE leg (L2). With 3:4:1.5 ratio:
# L1 = 3/8.5 = 35.3%, L2 = 4/8.5 = 47.1%, L3 = 1.5/8.5 = 17.6% of leverage cap.
# Heavier L2 catches the typical -0.35% dip with the largest weight while
# L3 is small enough to keep the worst-case SL bounded. Producing +152.00% /
# 9.39% DD / 124 trades / 97.58% WR / PF 93 / Calmar 16.2 on the user's
# TV backtest (Apr 6 – May 14 2026, BTCUSDT 5m).
# Total notional still capped by LEVERAGE — ratios just redistribute within cap.
MARTINGALE_RATIOS = [3.0, 4.0, 1.5]   # qty multiplier per leg (L1, L2, L3) — mixed shape
SL_FROM_AVG    = 0.008      # 0.8% from AVG entry (widened 2026-05-26 — fix SL/L2 collision)
                            # See core_divflip_sharp.py for details. L2 must fire before SL.
                              # explicit, repeated request — AGAINST the backtest: year-wise OOS
                              # shows 1% worst-anchored loses MORE than the wide 5% stop every year.
                              # Kept only because the user asked for it on the paper bot.

# v1b SAFETY: hard max-loss cap on leveraged uPnL.
# DISABLED 2026-05-24: redundant with raw SL in paper bot (same cron polling rate).
USE_MAX_LOSS_CAP = False
MAX_LOSS_PCT     = 0.03

# Time-stop on LOSS — DISABLED 2026-05-25. SL handles fast losses; UK + Friday block
# protect the catastrophic-weekend scenario.
USE_TIME_STOP_LOSS = False
TIME_STOP_HOURS    = 24

# 6h same-direction cooldown after LOSS — catches falling-knife re-entries.
# Opposite-direction allowed immediately. SL'd direction blocked for N hours.
USE_LOSS_COOLDOWN     = True
LOSS_COOLDOWN_HOURS   = 6

# v1b ENTRY FILTERS (added 2026-05-24 based on live data + multi-agent analysis):
# - UK hours: only enter UTC 08-16 (blocks ASIA thin-liquidity + late US/overnight)
# - EOD flatten: force-close at 20:00 UTC (no overnight gap risk)
# Live data: UK + EOD20 turns -$353 baseline into +$486 over 19 trades.
USE_UK_HOURS_FILTER = True
UK_HOUR_START       = 8     # UTC, inclusive
UK_HOUR_END         = 16    # UTC, exclusive

USE_EOD_FLATTEN     = False
# Block Friday entries only (allow Mon-Thu + Sat-Sun). Friday WR is 17% vs Mon-Thu 40-80%.
USE_WEEKDAY_FILTER  = True
BLOCKED_WEEKDAYS    = [4]   # 0=Mon ... 4=Fri ... 6=Sun. Block Friday only.
EOD_HOUR_UTC        = 20

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
BE_TRIGGER_PCT = 0.005       # L1-only: arm at +0.50% favorable from avg (loose, give winners room)
BE_TRIGGER_AFTER_L2 = 0.003  # After L2 fires: arm at +0.30% (DCA recovery — lock profit fast)
BE_BUFFER_PCT  = 0.002        # initial floor at firstEntry ± 0.2% (after-fee lock-in)
TRAIL_DIST_PCT = 0.01       # L1-only: 1% from peak (loose)
TRAIL_DIST_AFTER_L2 = 0.005  # After L2 fires: 0.5% tight trail (lock profit fast)

# Flip on opposite divergence — OFF in TV-tuned config. Lets trades ride
# to SL / trailing exit instead of bouncing between sides on every opposite
# signal. With useFlip=ON, mid-position whipsaws ate into wins.
USE_FLIP = False

# RSI filter — asymmetric (TV-tuned). Loose bull (≤50) lets through most
# bull divergences in this BTC uptrend regime. Strict bear (≥70) only
# accepts genuinely overbought tops. Long-biased by design — caught the
# Apr–May 2026 uptrend with 74.83% WR over 145 trades.
USE_RSI_LEVEL_FILTER = True
RSI_LONG_MAX  = 50            # Pro: loose RSI gate (EMA filter does the quality work)
RSI_SHORT_MIN = 66            # Pro: loose RSI gate (was 60 — 66 is v1 baseline)

# Divergence freshness window — 21 bars on 5m = 105 min.
DIV_FRESH_BARS = 21

# ─ Pro-only filters (added 2026-05-24) ─
# Trend regime filter on 15m EMA50 with buffer (looser than 5m EMA200 strict)
# 15m EMA50 ≈ 12.5h trend, captures real regime without 5m whip
# 1% buffer = price within 1% of the EMA still allowed (catches dip-buy entries)
USE_EMA_TREND_FILTER  = True
EMA_TREND_TIMEFRAME   = "15min"   # resampled from 5m (use "15min", not "15m" — "m" = months in pandas)
EMA_TREND_PERIOD      = 50
EMA_TREND_BUFFER_PCT  = 0.003   # 0.3% buffer — TIGHT (quality filter)

# ATR volatility guard — block entries when market is too flat/dead
USE_ATR_GUARD     = True
ATR_PERIOD        = 14
ATR_MIN_PCT       = 0.0004    # 0.04% of price — below this, treat as dead zone

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
                     be_activated: bool = False, peak_price: Optional[float] = None,
                     filled: int = 1) -> float:
    """Composite SL — PRO config 2026-05-25 (asymmetric trail after L2)."""
    anchor = first_entry if first_entry is not None and first_entry > 0 else worst_entry
    raw_sl = anchor * (1 - SL_FROM_AVG) if side == "LONG" else anchor * (1 + SL_FROM_AVG)
    if not (USE_BREAKEVEN and be_activated and first_entry is not None):
        return raw_sl
    be_sl = first_entry * (1 + BE_BUFFER_PCT) if side == "LONG" else first_entry * (1 - BE_BUFFER_PCT)
    if peak_price is None or peak_price <= 0:
        return max(raw_sl, be_sl) if side == "LONG" else min(raw_sl, be_sl)
    trail_dist = TRAIL_DIST_AFTER_L2 if filled >= 2 else TRAIL_DIST_PCT
    trail_sl = peak_price * (1 - trail_dist) if side == "LONG" else peak_price * (1 + trail_dist)
    if side == "LONG":
        return max(raw_sl, be_sl, trail_sl)
    return min(raw_sl, be_sl, trail_sl)


def be_should_activate(side: Side, first_entry: float, current_price: float, filled: int = 1) -> bool:
    """True if favorable% crossed BE trigger (asymmetric: tight after L2)."""
    if not USE_BREAKEVEN or first_entry is None or first_entry <= 0:
        return False
    fav = (current_price - first_entry) / first_entry if side == "LONG" else (first_entry - current_price) / first_entry
    trigger = BE_TRIGGER_AFTER_L2 if filled >= 2 else BE_TRIGGER_PCT
    return fav >= trigger


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
