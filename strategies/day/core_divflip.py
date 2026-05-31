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
DIV_PIVOT_R = 1

# ═════ RSI period override (TV-tuned: 10) ═════
# V2.2 core uses RSI 14 (Wilder default). divflip uses RSI 10 — faster
# reaction, more pivots qualify the ≤50/≥70 filter, ~15% more trades.
# Bot runner recomputes df["rsi"] with this period after build_features,
# then re-runs detect_divergence so pivot RSI values reflect the override.
RSI_PERIOD = 10

# ═════ Constants ═════
# LEVERAGE is the DEFAULT used for NEW positions only. Each position captures
# its leverage at L1 entry into pos["leverage"] so subsequent DCA legs use the
# leverage that was in effect when the position opened — changes to this
# constant only take effect for the next fresh entry.
LEVERAGE       = 3.0
RISK_PCT       = 0.06

DCA_LEVELS     = 2     # 2026-05-28: 1 → 2. DCA re-enabled (L1+L2 @ 0.35%). Backtest May 7-25:
                       #  DCA cut chop loss -2.36% → -0.98%, lifted WR to 74% (avg-down → TP on recovery).
                       #  Validated across regimes with SL 0.7% + 15m trend filter: bear -17% (vs -80% old).
                       #  Simpler: each trade independent, fixed max loss ~$77, no averaging into losers.
DCA_SPACING    = 0.0035       # 0.35% adverse triggers each DCA leg (L2 at -0.35%, L3 at -0.7%).
                              # TV-tuned: wider than 0.3% — DCA fires on deeper dips, better fills.

# Mixed-shape sizing — biggest qty in the MIDDLE leg (L2). With 3:4:1.5 ratio:
# L1 = 3/8.5 = 35.3%, L2 = 4/8.5 = 47.1%, L3 = 1.5/8.5 = 17.6% of leverage cap.
# Heavier L2 catches the typical -0.35% dip with the largest weight while
# L3 is small enough to keep the worst-case SL bounded. Producing +152.00% /
# 9.39% DD / 124 trades / 97.58% WR / PF 93 / Calmar 16.2 on the user's
# TV backtest (Apr 6 – May 14 2026, BTCUSDT 5m).
# Total notional still capped by LEVERAGE — ratios just redistribute within cap.
MARTINGALE_RATIOS = [3.0, 4.0, 1.5]   # qty multiplier per leg (L1, L2, L3) — mixed shape
SL_FROM_WORST  = 0.007        # 2026-05-28: 0.5% → 0.7%. Backtest showed 0.5% noise-stopped trades;
                              # 0.7% lifted WR 52%→67% (May 7-25) by giving room to recover.
                              # (was 0.5%) — original note: tighten worst-anchored stop to cap
                              # loss size — v1 was losing 4.7× more per loss than per win.

# ─ Fixed TP from avg entry (TV-tuned: ON @ 1%) ─
# Primary exit. Recomputed when DCA fires (avg moves closer to live), so a deep
# DCA-filled position needs less recovery to hit TP. Trailing SL is the backstop
# when price never reaches +1% (rare in TV backtest).
USE_TAKE_PROFIT = True
TP_PCT          = 0.005       # 2026-05-28: 1% → 0.5% from avg. On 5m, 1% rarely hits (15% of trades) vs 0.5% (74%).

# 3Commas-style trailing — only arms at a meaningful profit threshold.
# Below BE trigger: raw SL only (loss-bound). At BE trigger: trailing arms,
# locks in min profit via BE_BUFFER floor, then trails peak − TRAIL_DIST_PCT.
USE_BREAKEVEN  = True
BE_TRIGGER_PCT = 0.003        # 2026-05-28: 0.55% → 0.3%. Must be BELOW the 0.5% TP so BE arms and
                              # protects the remaining 50% (after partial TP at 0.25%) before full TP.
BE_BUFFER_PCT  = 0.002        # initial floor at firstEntry ± 0.2% (after-fee lock-in)
TRAIL_DIST_PCT = 0.002        # 0.2% trail below peak (LONG) / above trough (SHORT)

# Flip on opposite divergence — OFF in TV-tuned config. Lets trades ride
# to SL / trailing exit instead of bouncing between sides on every opposite
# signal. With useFlip=ON, mid-position whipsaws ate into wins.
USE_FLIP = False

# 2026-05-26: cooldowns added to v1 to pair with tighter 0.5% SL.
# Same-direction cooldown after LOSS — blocks immediate re-entry into a fresh SL.
# 2026-05-27: 6h → 0.5h (30 min). User feedback: 6h was sitting out too many setups.
USE_LOSS_COOLDOWN     = True
LOSS_COOLDOWN_HOURS   = 0.5

# 15-min same-direction cooldown after TP — avoids pump-and-dump trap.
USE_TP_COOLDOWN       = True
TP_COOLDOWN_MINUTES   = 15

# 2026-05-27: Block Friday entries. Pattern in v1 27-trade data:
# Fri = 0/2 WR, −$903 (catastrophic), incl. −$701 5/15 Fri 02h LONG that ran 70h
# vs Wed/Mon = 100% WR each. Friday is BTC's position-closing day — divergence
# signals fire but reversals fail on flight-to-cash flows.
USE_WEEKDAY_FILTER  = True
BLOCKED_WEEKDAYS    = [4]   # 0=Mon ... 4=Fri ... 6=Sun

# 2026-05-27: 8h loss-only time-stop. v1 27-trade data:
# Winners avg 6.7h to exit (median 6.1h). Losers avg 22h (median 12.2h).
# A position still underwater at 8h has high probability of being a loser.
# Loss-only — winners still ride to TP / TRAIL. The −$701 catastrophic
# Friday hold (70h) would have closed at 8h with much smaller loss.
USE_TIME_STOP_LOSS_ONLY = True
TIME_STOP_HOURS         = 8

# 2026-05-27: SAME-LEVEL OPPOSITE-FLIP BLOCK
# Pattern in v1 27-trade data: after winning exits (TP/TRAIL/BE) near a price level,
# divergence often flips opposite at the SAME level → bot opens reverse → catches
# breakout in wrong direction. 11 historical setups: 8W/3L, NET -$614 (incl. -$701
# catastrophe, -$209, -$195). Simulation: 0.15% threshold + TP/TRAIL/BE-only filter
# would have flipped v1's $+145 actual result to $+803 (5.5× better). SL exits don't
# trigger this trap (already covered by 30-min same-side cooldown + the SL itself
# is evidence the level broke).
USE_SAME_LEVEL_BLOCK     = True
SAME_LEVEL_PROX_PCT      = 0.0015     # 0.15% — sweet spot from sim
SAME_LEVEL_WINDOW_HOURS  = 12

# 2026-05-28: ONE-SHOT PER DIVERGENCE PIVOT
# Block re-entry on the SAME divergence pivot — once a pivot triggers a trade,
# don't re-fire on it even if still fresh. Forces a NEW pivot for each trade.
USE_ONE_SHOT_PER_PIVOT  = True

# 2026-05-28: IST NIGHT BLOCK — no entries 00:00-06:00 IST (= UTC 18:30-00:30)
# User asleep during this window; also the thin-liquidity overnight period.
USE_IST_NIGHT_BLOCK     = True
IST_BLOCK_START_HOUR    = 0           # 00:00 IST
IST_BLOCK_END_HOUR      = 6           # 06:00 IST

# 2026-05-28: PARTIAL TP — sell PARTIAL_TP_FRACTION of position at +PARTIAL_TP_PCT
# from L1 entry, let the rest ride to the full TP / trail. Locks profit early on
# the frequent small moves (96% of trades reached +0.25%), keeps upside on runners.
USE_PARTIAL_TP          = False       # 2026-05-28: disabled — user wants min 0.5% on whole position,
PARTIAL_TP_PCT          = 0.0025      #  not a 0.25% partial. Full position rides to 0.5%+ via profit trail.
PARTIAL_TP_FRACTION     = 0.5

# 2026-05-28: PROFIT TRAIL WITH FLOOR (for the remaining 50% after partial).
# Once price reaches +TP_PCT (0.5%) favorable, lock a 0.5% MINIMUM and trail
# PROFIT_TRAIL_DIST off the peak above that — lets trend-aligned winners ride
# beyond 0.5% while guaranteeing at least 0.5% on the exit.
#   Exit (LONG) = max(avg+0.5%, peak−0.3%).  Only arms after peak ≥ +0.5%.
USE_PROFIT_TRAIL        = True
PROFIT_TRAIL_DIST       = 0.003       # 0.3% off peak above the 0.5% floor

# 2026-05-28: 15m TREND FILTER — only trade WITH the 15m trend.
# LONG only if 15m EMA50 > EMA200; SHORT only if EMA50 < EMA200.
# Blocks counter-trend entries (the bullish-bias-in-downtrend problem that
# produced the catastrophic losses). Direction gate only — no entry delay.
USE_15M_TREND_FILTER    = True
TREND_TIMEFRAME         = "15m"       # 2026-05-31: extracted for v2 override (Option B: slower trend filter)
TREND_EMA_FAST          = 20          # 2026-05-28: 50/200 → 20/50. Backtest: 20/50 catches trends ~4× faster
TREND_EMA_SLOW          = 50          #  (bull +3.46% vs +0.84%, bear -14% vs -17%). 50/200 lagged ~2 days at turns.

# RSI filter — asymmetric (TV-tuned). Loose bull (≤50) lets through most
# bull divergences in this BTC uptrend regime. Strict bear (≥70) only
# accepts genuinely overbought tops. Long-biased by design — caught the
# Apr–May 2026 uptrend with 74.83% WR over 145 trades.
USE_RSI_LEVEL_FILTER = True
RSI_LONG_MAX  = 40            # 2026-05-28: 50 → 40. Freqtrade multi-regime sweep: tighter LONG filters weak mid-range bull divs. Bull +3.5%→+7.8%, all regimes improved.
RSI_SHORT_MIN = 70            # 2026-05-28: 66 → 70 (user choice). Quality bear-div filter; keeps slightly more short frequency than 75.

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
    """Composite SL — raw stop anchored to WORST entry (L3 once fully filled),
    set 2026-05-20 at user request. The raw stop rides down with each DCA leg.

    Components (LONG — symmetric for SHORT):
      - Raw SL    = worst_entry × (1 − SL_FROM_WORST)         [hard floor, always active]
      - BE SL     = first_entry × (1 + BE_BUFFER_PCT)         [active when BE armed]
      - Trail SL  = peak_price × (1 − TRAIL_DIST_PCT)         [active when BE armed]

    BE floor + trailing still anchor to first_entry / peak — only the raw
    stop's anchor is worst_entry.
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
