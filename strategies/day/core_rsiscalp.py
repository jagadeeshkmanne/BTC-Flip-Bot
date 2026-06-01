"""core_rsiscalp.py — Pure-RSI mean-reversion scalper.

Deliberately the SIMPLEST strategy in the repo: no divergence, no trend filter,
no weekday/IST/cooldown/same-level gates. Just RSI extremes in, small TP out.

Spec (user request 2026-06-01):
  - Timeframe: 5m
  - Entry: RSI ≤ OVERSOLD  -> LONG
           RSI ≥ OVERBOUGHT -> SHORT
  - DCA: 2 legs total (L1 + L2) at fixed adverse spacing, equal sizing
  - Take profit: small, in the 0.25%–0.50% band from AVG entry
      * partial scale-out 50% at +0.25%, remainder rides to +0.50%
  - NO entry filters of any kind — purely RSI.
  - Stop loss: OFF by spec, but a LOOSE catastrophic backstop is provided
    (USE_STOP_LOSS, default ON @ 2% from worst entry). Set USE_STOP_LOSS=False
    for a literally stop-less bot. See the warning in bot_rsiscalp.py.

Re-uses only rsi_series from core.py — nothing else.
"""
from __future__ import annotations
from typing import Optional, Literal

# ═════ Market / sizing ═════
LEVERAGE = 3.0     # 2026-06-01 (user): 3x. Safe here ONLY because the 1% stop
                   # fires far before the ~33% liquidation line (0 liqs in 2.9y
                   # backtest). NEVER run 3x without the stop — a no-SL 3x long
                   # in a crash liquidates before the bounce. Expect ~50% max DD.
RISK_PCT = 0.06    # informational; sizing uses LEVERAGE × 0.95 of equity.

# ═════ RSI signal (the ONLY entry logic) ═════
# 2026-06-01: tuned on 2.9y BTCUSDT 5m. RSI9 25/75 = ~4 signals/day,
# 72% reach +0.25% / 47% reach +0.5% before a 2% stop (median ~45min),
# only ~6% touch the stop. Faster/looser (RSI7 ≤30) = 11/day but noisier;
# slower/tighter (RSI14 ≤20) = better odds, ~1 trade/2 days.
RSI_PERIOD    = 9    # 9 bars = last 45min on 5m. Best risk-adjusted in sweep
                     # (period 7–9 win; 14/21 trade too rarely to compound).
RSI_OVERSOLD  = 30   # RSI ≤ 30 -> LONG.  30/70 beat 25/75 and 20/80 on net AND DD.
RSI_OVERBOUGHT = 70  # RSI ≥ 70 -> SHORT

# ═════ DCA — 2 legs, equal size, fixed spacing ═════
DCA_LEVELS  = 2       # total fills (L1 + L2). MANDATORY — no-DCA backtests -90%
                      # (0.25% TP vs 1% SL is a 1:4 loser without the avg-down).
DCA_SPACING = 0.005   # 2026-06-01: 0.35% → 0.50%. Sweep: 0.5% spacing the clear
                      # peak (deeper L2 fill → better avg → 0.25% TP hits more).

# ═════ Take profit — small band 0.25%–0.50% from AVG entry ═════
USE_TAKE_PROFIT     = True
TP_PCT              = 0.0025  # 2026-06-01 (user): single TP, full exit at +0.25% from avg.
USE_PARTIAL_TP      = False   # disabled — exit the WHOLE position at 0.25% (no 0.5% runner).
PARTIAL_TP_PCT      = 0.0025
PARTIAL_TP_FRACTION = 0.5

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
