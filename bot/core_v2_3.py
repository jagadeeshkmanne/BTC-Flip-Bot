"""core_v2_3.py — RSI "Buy The Dip" Strategy with 1% TP.

Designed to address the flaws of v2.2 by only taking mean-reversion trades
in the direction of the higher timeframe trend, and using a larger TP.

Spec (v2.3):
  - Timeframe: 1h
  - Trend Filter: EMA 50 > EMA 200 on 1h (MANDATORY)
  - Entry: RSI(14) ≤ 35 (LONG) / ≥ 65 (SHORT) + Trend Alignment
  - DCA: 2 legs total, spaced by 1.0%
  - Take profit: 1.0% from Average Entry
  - Stop loss: 1.5% from Worst Entry
"""
from __future__ import annotations
import os
from typing import Optional, Literal

# ═════ Market / sizing ═════
LEVERAGE = float(os.environ.get("RSISCALP_LEVERAGE", "5.0"))
RISK_PCT = 0.06

# ═════ RSI signal (the entry logic) ═════
RSI_PERIOD    = int(os.environ.get("RSISCALP_RSI_PERIOD", "14"))
RSI_OVERSOLD  = int(os.environ.get("RSISCALP_RSI_OVERSOLD", "35"))
RSI_OVERBOUGHT = int(os.environ.get("RSISCALP_RSI_OVERBOUGHT", "65"))

# ═════ Trend filter (MANDATORY for v2.3 to avoid falling knives) ═════
USE_TREND_FILTER = True
TREND_TF        = "1h"
TREND_EMA_FAST  = 50
TREND_EMA_SLOW  = 200

# ═════ DCA — 2 legs, equal size, wider spacing ═════
DCA_LEVELS  = 2
DCA_SPACING = float(os.environ.get("RSISCALP_DCA_SPACING", "0.010"))   # 1.0%

# ═════ Take profit — 1.0% minimum as requested ═════
USE_TAKE_PROFIT = True
TP_PCT_SINGLE = float(os.environ.get("RSISCALP_TP_SINGLE", "0.010"))  # 1.0%
TP_PCT_DCA    = float(os.environ.get("RSISCALP_TP_DCA", "0.010"))     # 1.0%

def tp_pct_for(filled: int) -> float:
    """Take-profit % from average price."""
    return TP_PCT_SINGLE if filled <= 1 else TP_PCT_DCA

# ═════ Stop loss — tighter backstop since we trade with the trend ═════
USE_STOP_LOSS = True
SL_FROM_WORST = float(os.environ.get("RSISCALP_SL_FROM_WORST", "0.015")) # 1.5%

# ═════ Circuit breaker ═════
USE_CIRCUIT_BREAKER  = True
BREAKER_LOSSES       = 1
BREAKER_PAUSE_HOURS  = 1.0  # Pause for 1 hour after a loss

Side = Literal["LONG", "SHORT"]

# ═════ Signal ═════
def rsi_signal(rsi: Optional[float], trend_state: Optional[float] = None) -> Optional[Side]:
    """
    Trend-filtered RSI extreme -> side.
    trend_state: 1.0 for uptrend, -1.0 for downtrend.
    """
    if rsi is None:
        return None
        
    # We only buy dips in uptrends, and short rips in downtrends
    if rsi <= RSI_OVERSOLD and trend_state == 1.0:
        return "LONG"
    if rsi >= RSI_OVERBOUGHT and trend_state == -1.0:
        return "SHORT"
        
    return None

# ═════ Position helpers ═════
def dca_price(side: Side, worst_entry: float) -> float:
    """Next DCA trigger — DCA_SPACING adverse beyond the worst fill so far."""
    return worst_entry * (1 - DCA_SPACING) if side == "LONG" else worst_entry * (1 + DCA_SPACING)

def sl_price(side: Side, worst_entry: float) -> Optional[float]:
    """Catastrophic stop anchored to worst entry."""
    if not USE_STOP_LOSS:
        return None
    return worst_entry * (1 - SL_FROM_WORST) if side == "LONG" else worst_entry * (1 + SL_FROM_WORST)

def per_level_qty(equity: float, price: float) -> float:
    """Equal per-leg sizing. Total notional = LEVERAGE × 0.95 × equity."""
    if price <= 0:
        return 0.0
    total = (equity * 0.95 * LEVERAGE) / price
    return total / DCA_LEVELS
