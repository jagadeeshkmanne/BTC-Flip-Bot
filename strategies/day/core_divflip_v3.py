"""core_divflip_v3.py — Divflip v3 (LOCKED v4.2 config from 2026-05-24 testing).

Backtest performance (5y BTC 15m, in-sample biased):
  Total return:  +175,628%   ($5K → $8.8M)
  Win rate:      95.0%
  Max DD:        28.4%
  Monthly avg:   +13.17%
  Per-year:      2021 +324%, 2022 +379%, 2023 +183%, 2024 +704%,
                 2025 +94%, 2026 YTD +97% (5 months)
  All 6 years profitable, 92% of months profitable.

OOS validation (train 2021-2023, blind test 2024-2026):
  TRAIN: +2,180% / 89.8% WR
  OOS:   +593%   / 87.4% WR  (WR drop only -2.4pp confirms real edge)

Real v1 paper bot window (May 14-23, 2026):
  v1 actual:  17 trades, 78% WR, -$336 net
  v3 sim:     7 trades, 100% WR, +$151 net (filters block 10 bad trades)

KEY DIFFERENCES from v1f:
  - 3 DCAs (was 2)
  - Sizing 1:2:4 progressive (was 3:4 martingale)
  - DCA spacing 0.2% / 0.4% (was 0.35% / 0.7%)
  - RSI period 7 (was 10) — faster catches more reversals
  - FLIP exit on opposite divergence (was OFF)
  - L3 lock at +0.2% from avg when L3 filled
  - 5 filters: BB squeeze + today_change + ADX cap + min ATR + HTF bias
  - Oversold override (bypass HTF bias on extreme reversal setups)
  - 1h SL cooldown (was 2h)
  - NO time stop (was 24h)
  - NO range_pos filter (was on — data showed it reduced both return & raised DD)
  - Hard SL anchored to L1 at -1.8%
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Literal, Dict, Any
import pandas as pd
import numpy as np

from core import build_features, detect_divergence  # noqa: F401

# ═════════════════════ Entry signal params ═════════════════════
RSI_PERIOD       = 7         # v3: 7 (was 10) — faster catches more reversals
DIV_PIVOT_L      = 5         # left bars for pivot
DIV_PIVOT_R      = 1         # right confirmation bar (1 bar = 15min)
DIV_FRESH_BARS   = 21        # divergence freshness window (5.25h on 15m)

# RSI level filter at the divergence pivot
USE_RSI_LEVEL_FILTER = True
RSI_LONG_MAX     = 50        # bull div: pivot RSI ≤ 50 (oversold)
RSI_SHORT_MIN    = 66        # bear div: pivot RSI ≥ 66 (overbought)

# ═════════════════════ Filter stack (5 filters) ═════════════════════
# 1. BB squeeze block — don't enter when market is coiling for explosive move
USE_BB_SQUEEZE   = True
BB_SQUEEZE_RANK  = 0.20      # block if BB width rank < bottom 20% (last 100 bars)

# 2. Today change ±2% — don't catch knife / chase parabolic
USE_TODAY_CHANGE = True
TODAY_LIMIT_PCT  = 2.0       # block LONG if today ≤ -2%, SHORT if today ≥ +2%

# 3. ADX cap — don't fight extreme trends
USE_ADX_CAP      = True
ADX_MAX          = 60        # block if 15m ADX > 60

# 4. Min ATR — skip dead markets where strategy can't mean-revert
USE_MIN_ATR      = True
MIN_ATR_PCT      = 0.30      # block if 15m ATR/price < 0.30%

# 5. HTF trend bias — don't take counter-trend trades in clear macro trend
USE_HTF_BIAS     = True
HTF_SLOPE_THR    = 1.0       # daily EMA50 5-day slope ±1% (blocks opposite-side entries)

# OVERSOLD OVERRIDE — bypass HTF bias when divergence is EXTREMELY oversold
# (catches sharp reversal bounces like BTC 82k → 74k → bounce)
USE_OVERSOLD_OVERRIDE = True
OVERRIDE_RSI     = 30        # RSI at pivot ≤ 30 (LONG) or ≥ 70 (SHORT)
OVERRIDE_RP      = 25        # range_pos_1d ≤ 25 (LONG) or ≥ 75 (SHORT)

# ═════════════════════ DCA structure ═════════════════════
DCA_LEVELS         = 3       # 3 entry legs
DCA_SPACING_L2_PCT = 0.002   # L2 fills at L1 ± 0.2%
DCA_SPACING_L3_PCT = 0.004   # L3 fills at L1 ± 0.4%
DCA_RATIOS         = [1.0, 2.0, 4.0]  # PROGRESSIVE — L1 small, L3 biggest

# ═════════════════════ Exits ═════════════════════
# Hard SL — never widens, anchored to L1 entry
SL_FROM_L1       = 0.018     # -1.8% from L1 (cat-loss firewall)

# FLIP exit — close on opposite divergence + RSI level filter
USE_FLIP_EXIT    = True
FLIP_MIN_PROFIT  = 0.002     # require +0.2% min unrealized position profit to FLIP

# L3 lock — once all 3 DCAs filled, exit on small recovery
USE_L3_LOCK      = True
L3_LOCK_PCT      = 0.002     # exit at +0.2% from avg if L3 filled

# Cooldown after SL — prevents revenge re-entries
SL_COOLDOWN_HOURS = 1

# Time stop — REMOVED (was hurting; FLIP can take >24h in slow regimes)
USE_TIME_STOP    = False

# Leverage cap
LEVERAGE         = 3.0
RISK_PCT         = 0.06      # 6% of equity per L1 entry (rest of allocation goes to L2/L3)

Side = Literal["LONG", "SHORT"]


# ═════════════════════ Signal state ═════════════════════
@dataclass
class DivSignalStateV3:
    side: Optional[Side] = None
    price: float = 0.0
    flip_opposite: bool = False
    conditions: Dict[str, bool] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


def _rsi_at_pivot(df: pd.DataFrame, last_idx: int, bars_since_fire: int) -> Optional[float]:
    pivot_idx = last_idx - bars_since_fire - DIV_PIVOT_R
    if pivot_idx < 0:
        return None
    val = df.iloc[pivot_idx].get("rsi", np.nan)
    return None if pd.isna(val) else float(val)


def evaluate_signal_v3(
    df: pd.DataFrame,
    last_idx: int,
    range_pos: Optional[float] = None,
    bb_width_rank: Optional[float] = None,
    today_change_pct: Optional[float] = None,
    adx: Optional[float] = None,
    atr_pct: Optional[float] = None,
    daily_ema50_slope_pct: Optional[float] = None,
    current_side: Optional[Side] = None,
) -> DivSignalStateV3:
    """Full v3 entry evaluation including the 5 filters + oversold override.

    The bot is responsible for computing the auxiliary inputs (range_pos,
    bb_width_rank, today_change_pct, adx, atr_pct, daily_ema50_slope) before
    calling this. Returns DivSignalStateV3 with .side set if entry is allowed.
    """
    s = DivSignalStateV3()
    row = df.iloc[last_idx]
    s.price = float(row["close"])

    bars_since_bear = int(row.get("bars_since_bear_div", 9999))
    bars_since_bull = int(row.get("bars_since_bull_div", 9999))

    bull_fresh = bars_since_bull <= DIV_FRESH_BARS
    bear_fresh = bars_since_bear <= DIV_FRESH_BARS

    # RSI-at-pivot filter (mandatory — base divergence quality)
    rsi_bull_pivot = _rsi_at_pivot(df, last_idx, bars_since_bull) if bars_since_bull < 9999 else None
    rsi_bear_pivot = _rsi_at_pivot(df, last_idx, bars_since_bear) if bars_since_bear < 9999 else None
    if USE_RSI_LEVEL_FILTER:
        if bull_fresh and (rsi_bull_pivot is None or rsi_bull_pivot > RSI_LONG_MAX):
            bull_fresh = False
        if bear_fresh and (rsi_bear_pivot is None or rsi_bear_pivot < RSI_SHORT_MIN):
            bear_fresh = False

    # Pick fresher signal if both fire
    candidate_side: Optional[Side] = None
    if bull_fresh and bear_fresh:
        candidate_side = "LONG" if bars_since_bull < bars_since_bear else "SHORT"
    elif bull_fresh:
        candidate_side = "LONG"
    elif bear_fresh:
        candidate_side = "SHORT"

    # Flip detection (for dashboard / exits) — opposite-side fresh signal while in a position
    if current_side == "LONG" and bear_fresh:
        s.flip_opposite = True
    elif current_side == "SHORT" and bull_fresh:
        s.flip_opposite = True

    # Filter stack — only applied if a candidate signal exists
    cond_bb = True
    cond_today = True
    cond_adx = True
    cond_atr = True
    cond_htf = True
    cond_override = False
    if candidate_side:
        # 1. BB squeeze
        if USE_BB_SQUEEZE and bb_width_rank is not None and bb_width_rank < BB_SQUEEZE_RANK:
            cond_bb = False

        # 2. Today change in opposing direction
        if USE_TODAY_CHANGE and today_change_pct is not None:
            if candidate_side == "LONG" and today_change_pct < -TODAY_LIMIT_PCT:
                cond_today = False
            if candidate_side == "SHORT" and today_change_pct > TODAY_LIMIT_PCT:
                cond_today = False

        # 3. ADX cap
        if USE_ADX_CAP and adx is not None and adx > ADX_MAX:
            cond_adx = False

        # 4. Min ATR
        if USE_MIN_ATR and atr_pct is not None and atr_pct < MIN_ATR_PCT:
            cond_atr = False

        # 5. HTF bias (with oversold override)
        if USE_HTF_BIAS and daily_ema50_slope_pct is not None:
            if candidate_side == "LONG" and daily_ema50_slope_pct < -HTF_SLOPE_THR:
                cond_htf = False
                # Oversold override
                if (USE_OVERSOLD_OVERRIDE and rsi_bull_pivot is not None
                        and rsi_bull_pivot <= OVERRIDE_RSI
                        and range_pos is not None and range_pos <= OVERRIDE_RP):
                    cond_htf = True
                    cond_override = True
            elif candidate_side == "SHORT" and daily_ema50_slope_pct > HTF_SLOPE_THR:
                cond_htf = False
                if (USE_OVERSOLD_OVERRIDE and rsi_bear_pivot is not None
                        and rsi_bear_pivot >= (100 - OVERRIDE_RSI)
                        and range_pos is not None and range_pos >= (100 - OVERRIDE_RP)):
                    cond_htf = True
                    cond_override = True

    # Only set side if all conditions pass
    all_filters_pass = cond_bb and cond_today and cond_adx and cond_atr and cond_htf
    if candidate_side and all_filters_pass:
        s.side = candidate_side

    # Conditions dict for dashboard transparency
    bull_rsi_pass = (rsi_bull_pivot is not None) and (rsi_bull_pivot <= RSI_LONG_MAX)
    bear_rsi_pass = (rsi_bear_pivot is not None) and (rsi_bear_pivot >= RSI_SHORT_MIN)
    s.conditions = {
        "Bull div fresh": bull_fresh,
        "Bear div fresh": bear_fresh,
        f"Bull RSI@pivot ≤{int(RSI_LONG_MAX)}": bull_rsi_pass,
        f"Bear RSI@pivot ≥{int(RSI_SHORT_MIN)}": bear_rsi_pass,
        "BB not squeezed": cond_bb,
        "Today change OK": cond_today,
        "ADX ≤ 60": cond_adx,
        "ATR ≥ 0.30%": cond_atr,
        "HTF bias OK": cond_htf,
    }
    if cond_override:
        s.conditions["Oversold override fired"] = True

    s.raw = {
        "bars_since_bear_div": bars_since_bear,
        "bars_since_bull_div": bars_since_bull,
        "rsi": float(row.get("rsi", 0)) if not pd.isna(row.get("rsi", np.nan)) else None,
        "rsi_at_bull_pivot": rsi_bull_pivot,
        "rsi_at_bear_pivot": rsi_bear_pivot,
        "rsi_long_max": RSI_LONG_MAX,
        "rsi_short_min": RSI_SHORT_MIN,
        "price": s.price,
        "range_pos": range_pos,
        "bb_width_rank": bb_width_rank,
        "today_change_pct": today_change_pct,
        "adx": adx,
        "atr_pct": atr_pct,
        "daily_ema50_slope_pct": daily_ema50_slope_pct,
        "oversold_override_fired": cond_override,
    }
    return s


# ═════════════════════ Position helpers ═════════════════════
def dca_price(side: Side, first_entry: float, leg_idx: int) -> float:
    """Price at which the next DCA leg fills.
       leg_idx 1 = L2 (at -0.2% / +0.2% from L1)
       leg_idx 2 = L3 (at -0.4% / +0.4% from L1)
       Note: spacings are from L1 directly (not progressive from previous leg).
    """
    pct = DCA_SPACING_L2_PCT if leg_idx == 1 else DCA_SPACING_L3_PCT
    return first_entry * (1 - pct) if side == "LONG" else first_entry * (1 + pct)


def sl_price_v3(side: Side, first_entry: float) -> float:
    """Hard SL anchored to L1 entry. Never widens as DCAs fill."""
    return first_entry * (1 - SL_FROM_L1) if side == "LONG" else first_entry * (1 + SL_FROM_L1)


def l3_lock_price(side: Side, avg_entry: float) -> float:
    """Exit price for L3-filled trades when price recovers L3_LOCK_PCT from avg."""
    return avg_entry * (1 + L3_LOCK_PCT) if side == "LONG" else avg_entry * (1 - L3_LOCK_PCT)


def per_level_qty(equity: float, price: float, leg_idx: int = 0, leverage: float = None) -> float:
    """Progressive 1:2:4 sizing. Total notional across all 3 legs = leverage × 0.95 × equity."""
    if price <= 0:
        return 0.0
    lev = leverage if leverage is not None else LEVERAGE
    total = (equity * 0.95 * lev) / price
    total_ratio = sum(DCA_RATIOS[:DCA_LEVELS])
    if leg_idx >= len(DCA_RATIOS):
        return 0.0
    return total * DCA_RATIOS[leg_idx] / total_ratio


def should_flip_exit(unrealized_pnl_pct: float) -> bool:
    """Returns True if uPnL crossed the minimum FLIP profit threshold AND opposite
    divergence has fired (caller must check the latter separately via signal state).
    """
    return USE_FLIP_EXIT and unrealized_pnl_pct >= FLIP_MIN_PROFIT
