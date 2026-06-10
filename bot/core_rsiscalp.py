"""core_rsiscalp.py — Pure-RSI mean-reversion scalper.

Deliberately the SIMPLEST strategy in the repo: no divergence, no trend filter,
no weekday/IST/cooldown/same-level gates. Just RSI extremes in, small TP out.

Spec (user request 2026-06-01):
  - Timeframe: 5m
  - Entry: RSI ≤ OVERSOLD  -> LONG
           RSI ≥ OVERBOUGHT -> SHORT
  - DCA: 2 legs total (L1 + L2) at fixed adverse spacing, equal sizing
  - Take profit: ADAPTIVE from AVG entry — 0.50% while only L1 is filled,
      tightening to 0.25% once L2 (DCA) fills (doubled position exits quicker).
  - NO entry filters of any kind — purely RSI.
  - Stop loss: OFF by spec, but a LOOSE catastrophic backstop is provided
    (USE_STOP_LOSS, default ON @ 2% from worst entry). Set USE_STOP_LOSS=False
    for a literally stop-less bot. See the warning in bot_rsiscalp.py.

Re-uses only rsi_series from core.py — nothing else.
"""
from __future__ import annotations
import os
from typing import Optional, Literal

# ═════ Market / sizing ═════
import os as _os
LEVERAGE = float(_os.environ.get("RSISCALP_LEVERAGE", "3.0"))
# 2026-06-01 (user): default 3x. Safe here ONLY because the 1% stop
# fires far before the ~33% liquidation line (0 liqs in 2.9y backtest).
# NEVER run 3x without the stop — a no-SL 3x long in a crash liquidates
# before the bounce. Expect ~50% max DD.
# 2026-06-10: env-var aware so v2.1 can run 5× while v1 stays at 3×.
# At 5× leverage, sizing becomes equity × 0.95 × 5 / 2 = $11,875 per leg
# (vs $7,125 at 3×). Liquidation line at ~20% vs ~33%, still well past SL.
RISK_PCT = 0.06    # informational; sizing uses LEVERAGE × 0.95 of equity.

# ═════ RSI signal (the ONLY entry logic) ═════
# 2026-06-01: tuned on 2.9y BTCUSDT 5m. RSI9 25/75 = ~4 signals/day,
# 72% reach +0.25% / 47% reach +0.5% before a 2% stop (median ~45min),
# only ~6% touch the stop. Faster/looser (RSI7 ≤30) = 11/day but noisier;
# slower/tighter (RSI14 ≤20) = better odds, ~1 trade/2 days.
RSI_PERIOD    = 9    # 9 bars = last 45min on 5m. Best risk-adjusted in sweep
                     # (period 7–9 win; 14/21 trade too rarely to compound).
RSI_OVERSOLD  = 30   # RSI ≤ 30 -> LONG
RSI_OVERBOUGHT = 70  # RSI ≥ 70 -> SHORT

# ═════ Optional 15m trend filter (env-toggled, for A/B paper testing) ═════
# Set RSISCALP_TREND=1 to run the trend-gated variant in parallel.
# ON: LONG only when 15m EMA20 > EMA50 (uptrend); SHORT only when EMA20 < EMA50.
# Gates ENTRY only — once in a position it rides to TP/SL (flip-exit tested
# WORSE: WR 87%->71% for no gain). Backtest 2.9y: +174%/yr, DD -10.8% (vs base
# +496%/yr, DD -25%). Lower return, ~half the drawdown.
USE_TREND_FILTER = os.environ.get("RSISCALP_TREND", "0") == "1"
TREND_TF        = "15m"
TREND_EMA_FAST  = 20
TREND_EMA_SLOW  = 50

# ═════ DCA — 2 legs, equal size, fixed spacing ═════
DCA_LEVELS  = 2       # total fills (L1 + L2). MANDATORY — no-DCA backtests -90%.
DCA_SPACING = 0.005   # 2026-06-01: 0.35% → 0.50%. Sweep: 0.5% spacing the clear
                      # peak (deeper L2 fill → better avg → 0.25% TP hits more).

# ═════ Take profit — small band 0.25%–0.50% from AVG entry ═════
USE_TAKE_PROFIT = True
# Adaptive TP from AVG entry, by fill count (2026-06-01, validated on 2.9y 5m):
#   1 leg filled (no DCA) -> +0.50% — give the un-averaged trade room to run.
#   2 legs filled (DCA'd) -> +0.25% — the doubled position exits on a small
#     bounce off the improved avg (0.25% x 2 legs ~= 0.50% x 1 leg in dollars).
# Backtest beat fixed-0.25% on BOTH return and drawdown (lower DD = the robust
# signal; absolute % is idealized maker-fill, not a live promise).
TP_PCT_SINGLE = 0.005   # +0.50% from avg when only L1 filled
TP_PCT_DCA    = 0.0025  # +0.25% from avg once L2 filled


def tp_pct_for(filled: int) -> float:
    """Adaptive take-profit %: wider before DCA, tight after the average-down."""
    return TP_PCT_SINGLE if filled <= 1 else TP_PCT_DCA

# ═════ Stop loss — loose backstop, NOT a tight filter ═════
# Per spec the bot is "purely RSI / no filters". An SL isn't an entry filter,
# but a leveraged position with NO stop can bleed unbounded if RSI stays
# pinned in a trend. This is a wide catastrophic backstop only. Flip OFF for
# a stop-less bot (relies entirely on RSI mean-reversion + TP).
USE_STOP_LOSS = True   # MANDATORY at 3x — do not disable. The stop is what
                       # keeps you off the liquidation line (no-SL 3x = ruin).
SL_FROM_WORST = 0.01   # 2026-06-01: 2% → 1% for 3x. Backtest: 1% stop at 3x =
                       # +1958% (vs +780% at 2%), ~49% DD, and sits further from
                       # the 33% liquidation line. Tighter = safer AND better here.

# ═════ Circuit breaker — pause after consecutive losses ═════
# Backtest 2.9y: on plain RSI, halves max drawdown (-25% -> -12%) with ~same
# return. Reactive — it breaks loss-CHAINS (steps out of the high-variance
# window so a 2-loss streak can't become a 5-loss streak); it does NOT predict
# bad trades (win rate after 2 losses is ~85%, only slightly below 87%). Pause
# is wall-clock (bot runs per-minute, bars are 5m), tracked in state.json.
# Re-arms: another BREAKER_LOSSES in a row -> pause again. Redundant on the
# +Trend variant (gate already lowers DD) but kept for consistency.
USE_CIRCUIT_BREAKER  = True
BREAKER_LOSSES       = 1     # 2026-06-04 (user): 15-min cooldown after EVERY loss.
BREAKER_PAUSE_HOURS  = 0.25  # NOTE backtest on plain RSI9 25/75 (no filter) was
                             # WORSE this way (-48% -> -87%) — the 2-loss/2h breaker
                             # braked loss-chains better. Applied per user request;
                             # affects rsiscalp + rsiscalp_trend (NOT claude, which
                             # sets its own breaker locally).

Side = Literal["LONG", "SHORT"]


# ═════ Signal ═════
def rsi_signal(rsi: Optional[float]) -> Optional[Side]:
    """Pure RSI extreme -> side. None if RSI is mid-range or unavailable."""
    if rsi is None:
        return None
    if rsi <= RSI_OVERSOLD:
        return "LONG"
    if rsi >= RSI_OVERBOUGHT:
        return "SHORT"
    return None


# ═════ Position helpers ═════
def dca_price(side: Side, worst_entry: float) -> float:
    """Next DCA trigger — DCA_SPACING adverse beyond the worst fill so far."""
    return worst_entry * (1 - DCA_SPACING) if side == "LONG" else worst_entry * (1 + DCA_SPACING)


def sl_price(side: Side, worst_entry: float) -> Optional[float]:
    """Loose catastrophic stop anchored to worst entry. None if disabled."""
    if not USE_STOP_LOSS:
        return None
    return worst_entry * (1 - SL_FROM_WORST) if side == "LONG" else worst_entry * (1 + SL_FROM_WORST)


def per_level_qty(equity: float, price: float) -> float:
    """Equal per-leg sizing. Total notional = LEVERAGE × 0.95 × equity,
    split evenly across DCA_LEVELS legs."""
    if price <= 0:
        return 0.0
    total = (equity * 0.95 * LEVERAGE) / price
    return total / DCA_LEVELS
