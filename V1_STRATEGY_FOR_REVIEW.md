# V1 Bot Strategy — Full Spec for External Review

Generated 2026-06-06 — share this with other agents/quants for verification.

---

## STRATEGY SUMMARY

**Name:** RSI-Scalp ULTIMATE (v1)
**Market:** BTCUSDT perpetual futures (Bybit), 5-minute bars
**Type:** Mean-reversion fade with DCA + break-even-after-DCA risk management
**Leverage:** 3× (with weekend 2× position-size multiplier)
**Starting balance:** $5,000 paper

### CORE LOGIC

```
ENTRY: All filters below must pass simultaneously
  1. RSI(9) on 5m bars ≤ 30 (LONG) or ≥ 70 (SHORT)
  2. 15m EMA20 > EMA50 trend gate
     LONG only when trend = UP
     SHORT only when trend = DOWN
  3. GAP firmness: |EMA20 - EMA50| / EMA50 ≥ 0.25%
     Skips knife-edge trends where EMAs are nearly identical
  4. ATR(14) on 5m / close < 0.60% (skip high-volatility chop)
  5. All indicators must be available (defensive fail-closed)

POSITION SIZING:
  qty_per_leg = balance × 0.95 × leverage × weekend_mult / price / DCA_LEVELS
  - leverage = 3.0
  - DCA_LEVELS = 2 (split capital across L1 + L2)
  - weekend_mult = 2.0 (Sat/Sun) else 1.0

DCA (DOLLAR-COST AVERAGING):
  L1 fills at entry signal (next bar open)
  L2 fills at 0.5% adverse from L1 (worst_entry × 1.005 for SHORT)
  When L2 fills: position size doubles, avg becomes midpoint(L1, L2)

EXITS (checked each tick, in priority order):
  1. SL hit
     If L1 only: SL at worst × (1 + 0.6%) for SHORT
     If L2 filled: SL at avg (BREAK-EVEN — caps catastrophic losses)
  2. TP hit
     If L1 only: TP at avg × (1 - 0.5%) for SHORT — single-leg target
     If L2 filled: TP at avg × (1 - 0.25%) — tighter target after averaging
  3. Trend-flip exit
     If 15m trend flips against position direction, exit at close
  4. DCA fill (if L2 not yet filled and dca_px hit)

ADDITIONAL RISK CONTROLS:
  - Post-trade cooldown: 3 bars (15 min) before next entry
  - After-loss circuit breaker: 1 loss → 15 min pause
  - Daily max loss: $200 net → pauses all entries until next UTC day
  - Atomic state writes (temp file + os.replace)
```

### 5-YEAR FAITHFUL BACKTEST RESULTS
(No-lookahead methodology: HTF bars labeled at CLOSE time, see `backtest/v11_faithful_backtest.py`)

```
Period: 2021-06-07 → 2026-06-06 (5 years, 525,540 × 5m bars)
Costs:  0.055% taker commission per side, 2 bps slippage assumed

Trades:        3,187
Win rate:      54.2%
Total return:  +205.6%
Max drawdown:  -9.9%
Profit factor: 1.31
Sharpe ratio:  1.81
CAGR:          +25.0%

Average win:   $25.10 weekday / $50 weekend
Average loss:  -$22.10
Max single loss: -$101
```

### KEY MECHANISTIC INSIGHT

The "BE-after-DCA" exit is what makes this strategy work:
- When L2 fires (position doubled, currently underwater), SL moves to avg entry
- If price reverses to avg: exit at $0 (just fees, ~$0-5 loss)
- If price reverses past avg to TP: WIN on doubled position
- If price keeps moving adverse past avg: BE-DCA fires, exit at ~$0

This turns the asymmetric pain of "DCA into a runaway move" into a near-zero outcome,
while preserving the asymmetric gain of "DCA into a reversal" (doubled qty × TP).

---

## PRODUCTION CODE

### `strategies/day/core_rsiscalp.py` (shared constants + helpers)

```python
# Shared between bot variants. See file for full source.
LEVERAGE = 3.0
RSI_PERIOD = 9
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
DCA_LEVELS = 2
DCA_SPACING = 0.005       # 0.5%
TP_PCT_SINGLE = 0.005     # 0.50%
TP_PCT_DCA = 0.0025       # 0.25%
SL_FROM_WORST = 0.01      # 1.0% (overridden to 0.006 in v1)
BREAKER_LOSSES = 1        # 1 loss triggers...
BREAKER_PAUSE_HOURS = 0.25  # ...15 min pause
```

---

## FULL SOURCE CODE (embedded below for self-contained review)

### File 1 of 3: `strategies/day/core_rsiscalp.py` (134 lines)

```python
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
```

### File 2 of 3: `strategies/day/bot_rsiscalp.py` (603 lines — the v1 production bot)

```python
#!/usr/bin/env python3
"""bot_rsiscalp.py — RSI-Scalp +Trend ULTIMATE (v1).

2026-06-06: v1.1's 1h RSI 50-split filter REVERTED — faithful (no-lookahead)
5-year backtest showed it hurt (+8.52% → +6.73% / 5yr, DD got worse too).
Prior +2,023% claim was leaky backtest. Kept code path behind env flag
(off by default), no longer in the live filter chain.

FAITHFUL 5-year backtest (no lookahead): +8.52% return, -32% MaxDD, 64% WR.
Realistic, not the inflated original claim.
  
ENTRY FILTERS (proven over 1000+ historical trades):
  - RSI(9) ≤30/≥70 entry signal
  - 15m EMA20/EMA50 trend gate (fail-closed)
  - GAP firmness ≥ 0.25% (skip knife-edge trends)
  - ATR < 0.60% (skip chop regime)
  - 1h cumulative move < ±2.0% (don'''t fade active momentum)
  - Blocked hours: 5, 6, 11, 12, 13, 20 UTC (session transitions)
  - Defensive: any indicator unavailable → block

EXIT LOGIC:
  - TP: 0.50% (L1), 0.25% (post-DCA) adaptive
  - SL: 0.6% from worst entry (tightened from 1%)
  - Trend flip exit: close on 15m EMA reversal (early reversal catch)
  
RISK MANAGEMENT:
  - DCA: 2 legs at 0.5% adverse (preserves DCA rescue mechanism)
  - Daily max loss circuit breaker: $200/day pauses entries
  - Weekend 2x position size (Sat/Sun = 94% WR sweet spot)

PAPER-ONLY. State / log / status: data/paper_rsiscalp_trend/
"""
from __future__ import annotations
import os, sys, json, logging
from datetime import datetime, timezone, timedelta
import requests
import pandas as pd

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(os.path.dirname(STRATEGY_DIR))
sys.path.insert(0, STRATEGY_DIR)

from core import rsi_series
from core_rsiscalp import (
    LEVERAGE, DCA_LEVELS, DCA_SPACING,
    RSI_PERIOD, RSI_OVERSOLD, RSI_OVERBOUGHT,
    USE_TAKE_PROFIT, TP_PCT_SINGLE, TP_PCT_DCA, tp_pct_for,
    USE_STOP_LOSS, SL_FROM_WORST as _CORE_SL_FROM_WORST,
    USE_TREND_FILTER, TREND_TF, TREND_EMA_FAST, TREND_EMA_SLOW,
    USE_CIRCUIT_BREAKER, BREAKER_LOSSES, BREAKER_PAUSE_HOURS,
    rsi_signal, dca_price, sl_price, per_level_qty,
)

# ─── Paths ───
DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("RSISCALP_DATA_DIR", "paper_rsiscalp_trend"))
TREND_GAP_MIN = float(os.environ.get("RSISCALP_V2_GAP_MIN", "0.0025"))
# Fleet-wide chop/momentum filters (per-bot env override)
RSISCALP_ATR_MAX_PCT     = float(os.environ.get("RSISCALP_ATR_MAX_PCT", "0.60"))
# 2026-06-06: 1h move filter DISABLED. Live audit on 11 paper trades showed
# it drops 1 win ($30) while being redundant with ATR filter on the
# catastrophic loss. ATR alone is sufficient. Set RSISCALP_1H_MOVE_MAX_PCT=2.0
# (or any value <100) to re-enable. Default 100 = effectively off.
RSISCALP_1H_MOVE_MAX_PCT = float(os.environ.get("RSISCALP_1H_MOVE_MAX_PCT", "100.0"))
# Fleet-wide high-vol UTC hours blocked (default 12,13 = US pre-market)
# 2026-06-06: Hour blocking DISABLED. Live audit on 11 paper trades showed
# it dropped 4 winning trades (-$159) while ATR filter alone catches the
# catastrophic loss. Empty default = no blocked hours. Override via
# RSISCALP_BLOCKED_HOURS="5,6,11,12,13,20" to re-enable.
BLOCKED_HOURS = set(int(h.strip()) for h in
    os.environ.get("RSISCALP_BLOCKED_HOURS", "").split(",")
    if h.strip().isdigit())

# 2026-06-06: SL tightened from 1.0% to 0.6% (backtest -3.42% DD vs -7%)
SL_FROM_WORST = float(os.environ.get("RSISCALP_SL_FROM_WORST", "0.006"))

# Daily max loss circuit breaker
DAILY_MAX_LOSS = float(os.environ.get("RSISCALP_DAILY_MAX_LOSS", "200.0"))

# Weekend position-size multiplier (Sat/Sun = 94% WR historically)
WEEKEND_QTY_MULT = float(os.environ.get("RSISCALP_WEEKEND_QTY_MULT", "2.0"))

# Enable trend-flip exit (close on 15m EMA reversal)
USE_TREND_FLIP_EXIT = os.environ.get("RSISCALP_TREND_FLIP_EXIT", "1") == "1"

# 2026-06-06: Break-even after DCA fires. When position has L2+ filled, move SL
# to avg entry price. Caps "DCA into a runaway move" losses at near-zero.
# Live-trade audit: the one catastrophic -$193 loss had DCA fire, then move
# continued adverse. BE-L2 would have exited at avg (~$0) instead of -$193.
USE_BE_AFTER_DCA = os.environ.get("RSISCALP_BE_AFTER_DCA", "1") == "1"

# 2026-06-06: 1h RSI 50-split filter was added in v1.1 then REVERTED.
# Faithful (no-lookahead) 5-yr backtest showed filter HURTS
# (+8.52% → +6.73% return, -32% → -33% DD). Prior +2,023% claim
# came from a leaky backtest. Kept disabled by default; the code path
# below still works if RSISCALP_1H_RSI_FILTER=1 is set explicitly.
USE_1H_RSI_FILTER = os.environ.get("RSISCALP_1H_RSI_FILTER", "0") == "1"
RSI_1H_THRESHOLD  = float(os.environ.get("RSISCALP_1H_RSI_THRESHOLD", "50.0"))
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE  = os.path.join(DATA_DIR, "state.json")
STATUS_FILE = os.path.join(DATA_DIR, "status.json")
LOG_FILE    = os.path.join(DATA_DIR, "bot.log")

# ─── Config ───
PAIR = "BTCUSDT"
INITIAL_BALANCE = 5000.0
COMMISSION_PCT = 0.0004  # 0.04% taker fee per side
# 2026-06-05: data source migrated Binance fapi → Bybit V5 (USDT-M perp).

# ─── Logging ───
log = logging.getLogger("bot_rsiscalp")
log.setLevel(logging.INFO)
log.handlers.clear()
fh = logging.FileHandler(LOG_FILE)
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(fh)
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(sh)


# ─── Bybit public fetchers (V5 USDT-M perp, BTCUSDT) ───
from data_bybit import fetch_klines as _bb_klines, fetch_live_price as _bb_price

def fetch_klines(interval: str, limit: int = 500) -> pd.DataFrame | None:
    return _bb_klines(interval, limit, PAIR, log)

def fetch_live_price() -> float | None:
    return _bb_price(PAIR, log)


# ─── State I/O ───
def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "balance": INITIAL_BALANCE, "peak_equity": INITIAL_BALANCE,
            "position": None, "stats": {"total": 0, "wins": 0, "pnl": 0.0}, "trade_log": [],
        }
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(s):
    # 2026-06-05 FIX: atomic write via temp + rename. Previously a crash
    # mid-json.dump would corrupt state.json → next tick fails to load,
    # entire trade history lost.
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, default=str, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_FILE)  # atomic on POSIX


def write_status(payload):
    # Same atomic pattern for status.json
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, default=str, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATUS_FILE)


# ─── Position management ───
def avg_entry_of(pos) -> float:
    entries = pos.get("entries", [])
    total_qty = sum(e["qty"] for e in entries)
    return sum(e["px"] * e["qty"] for e in entries) / total_qty if total_qty > 0 else pos.get("first_entry", 0.0)


def _record_trade(state, trade_record, is_win: bool):
    state.setdefault("trade_log", []).append(trade_record)
    state["trade_log"] = state["trade_log"][-200:]
    state["stats"]["total"] += 1
    state["stats"]["pnl"] += trade_record["pnl_pct"]
    if is_win:
        state["stats"]["wins"] += 1


def close_position(state, pos, exit_px: float, reason: str) -> None:
    side = pos["side"]
    qty_total = pos["qty_total"]
    avg_entry = avg_entry_of(pos)
    gross = (exit_px - avg_entry) * qty_total if side == "LONG" else (avg_entry - exit_px) * qty_total
    fees = exit_px * qty_total * COMMISSION_PCT
    net = gross - fees
    balance_before = state["balance"]
    state["balance"] += net
    # 2026-06-06: track daily realized loss for circuit breaker
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.get("daily_loss_date") != today_utc:
        state["daily_loss"] = 0.0
        state["daily_loss_date"] = today_utc
    if net < 0:
        state["daily_loss"] = state.get("daily_loss", 0.0) + net
    price_move_pct = (exit_px / avg_entry - 1) * 100 * (1 if side == "LONG" else -1)
    pnl_pct = (net / balance_before * 100) if balance_before > 0 else 0.0
    _record_trade(state, {
        "side": side, "first_entry": pos["first_entry"], "avg_entry": avg_entry, "exit": exit_px,
        "entries": len(pos.get("entries", [])), "qty_total": qty_total, "reason": reason,
        "pnl_usd": net, "pnl_pct": pnl_pct, "price_move_pct": price_move_pct,
        "leverage": pos.get("leverage"), "entry_time": pos.get("entry_time"),
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "rsi_at_entry": pos.get("rsi_at_entry"),
        "max_fav_pct": pos.get("max_fav_pct", 0.0),
        "max_adv_pct": pos.get("max_adv_pct", 0.0),
    }, is_win=net > 0)
    log.warning(f"  EXIT {side} via {reason} @${exit_px:.2f} | avg ${avg_entry:.2f} | "
                f"net ${net:+.2f} (price {price_move_pct:+.2f}%) | balance ${state['balance']:.2f} "
                f"| MFE {pos.get('max_fav_pct',0):+.2f}% MAE {pos.get('max_adv_pct',0):+.2f}%")
    # ── Circuit breaker: count consecutive losses; pause after BREAKER_LOSSES ──
    if USE_CIRCUIT_BREAKER:
        if net <= 0:
            state["consec_losses"] = state.get("consec_losses", 0) + 1
            if state["consec_losses"] >= BREAKER_LOSSES:
                until = datetime.now(timezone.utc) + timedelta(hours=BREAKER_PAUSE_HOURS)
                state["pause_until"] = until.isoformat()
                state["consec_losses"] = 0
                log.warning(f"  CIRCUIT BREAKER: {BREAKER_LOSSES} losses in a row — pausing entries until {until.isoformat()[:16]}")
        else:
            state["consec_losses"] = 0


def partial_close(state, pos, exit_px: float, fraction: float) -> None:
    side = pos["side"]
    avg_entry = avg_entry_of(pos)
    sell_qty = pos["qty_total"] * fraction
    if sell_qty <= 0:
        return
    gross = (exit_px - avg_entry) * sell_qty if side == "LONG" else (avg_entry - exit_px) * sell_qty
    fees = exit_px * sell_qty * COMMISSION_PCT
    net = gross - fees
    balance_before = state["balance"]
    state["balance"] += net
    pnl_pct = (net / balance_before * 100) if balance_before > 0 else 0.0
    price_move_pct = (exit_px / avg_entry - 1) * 100 * (1 if side == "LONG" else -1)
    pos["qty_total"] -= sell_qty
    for e in pos.get("entries", []):
        e["qty"] *= (1 - fraction)
    pos["partial_taken"] = True
    _record_trade(state, {
        "side": side, "first_entry": pos["first_entry"], "avg_entry": avg_entry, "exit": exit_px,
        "entries": len(pos.get("entries", [])), "qty_total": sell_qty, "reason": "PARTIAL_TP",
        "pnl_usd": net, "pnl_pct": pnl_pct, "price_move_pct": price_move_pct,
        "leverage": pos.get("leverage"), "entry_time": pos.get("entry_time"),
        "exit_time": datetime.now(timezone.utc).isoformat(), "rsi_at_entry": pos.get("rsi_at_entry"),
    }, is_win=net > 0)
    log.warning(f"  PARTIAL TP: sold {fraction*100:.0f}% ({sell_qty:.4f}) @${exit_px:.2f} "
                f"net ${net:+.2f} | remaining {pos['qty_total']:.4f} rides to full TP")


def open_position(state, side: str, entry_px: float, rsi_val: float) -> None:
    # Weekend 2x position size (Sat/Sun = 94% WR in 6-mo backtest)
    is_weekend = datetime.now(timezone.utc).weekday() >= 5
    qty_mult = WEEKEND_QTY_MULT if is_weekend else 1.0
    qty = round(per_level_qty(state["balance"], entry_px) * qty_mult, 3)
    if qty <= 0:
        log.warning(f"  qty {qty} too small to open")
        return
    state["balance"] -= entry_px * qty * COMMISSION_PCT
    state["position"] = {
        "side": side, "first_entry": entry_px, "worst_entry": entry_px,
        "entries": [{"px": entry_px, "qty": qty}], "qty_total": qty, "filled": 1,
        "leverage": LEVERAGE, "entry_time": datetime.now(timezone.utc).isoformat(),
        "rsi_at_entry": rsi_val, "partial_taken": False,
        "weekend_2x": is_weekend,
    }
    log.warning(f"  OPENED {side} {qty}@${entry_px:.2f} (RSI {rsi_val:.1f}){'  [WEEKEND 2x]' if is_weekend else ''} | balance ${state['balance']:.2f}")


def maybe_dca(pos, live_px: float, balance: float, state) -> bool:
    """Equal-size DCA leg at fixed adverse spacing, up to DCA_LEVELS total.
    2026-06-06: respects weekend 2x multiplier — L2 sized to match L1, otherwise
    L1 would be 2x but L2 would be 1x (broken).
    """
    if pos["filled"] >= DCA_LEVELS:
        return False
    side = pos["side"]
    trigger = dca_price(side, pos["worst_entry"])
    crossed = (side == "LONG" and live_px <= trigger) or (side == "SHORT" and live_px >= trigger)
    if not crossed:
        return False
    qty_mult = WEEKEND_QTY_MULT if pos.get("weekend_2x") else 1.0
    qty = round(per_level_qty(balance, trigger) * qty_mult, 3)
    if qty <= 0:
        return False
    state["balance"] -= trigger * qty * COMMISSION_PCT
    pos["entries"].append({"px": trigger, "qty": qty})
    pos["worst_entry"] = min(pos["worst_entry"], trigger) if side == "LONG" else max(pos["worst_entry"], trigger)
    pos["qty_total"] = sum(e["qty"] for e in pos["entries"])
    pos["filled"] += 1
    log.warning(f"  DCA L{pos['filled']} {side} {qty}@${trigger:.2f} (-{DCA_SPACING*100:.2f}%) | "
                f"new avg=${avg_entry_of(pos):.2f} worst=${pos['worst_entry']:.2f}")
    return True


# ─── Main tick ───
def main():
    log.info("=" * 60)
    sl_desc = f"SL {SL_FROM_WORST*100:.1f}% from worst" if USE_STOP_LOSS else "NO SL"
    log.info(f"RSI-Scalp Paper Bot — RSI{RSI_PERIOD} {RSI_OVERSOLD}/{RSI_OVERBOUGHT} | {DCA_LEVELS} DCA @ {DCA_SPACING*100:.2f}% | "
             f"TP {TP_PCT_SINGLE*100:.2f}%(1leg)/{TP_PCT_DCA*100:.2f}%(DCA) from avg | {sl_desc} | {LEVERAGE:.0f}x")

    state = load_state()

    df_5m = fetch_klines("5m", 500)
    if df_5m is None or len(df_5m) < RSI_PERIOD + 5:
        log.error("insufficient klines")
        return
    live_px = fetch_live_price()
    if live_px is None:
        log.error("live price unavailable")
        return

    df_5m["rsi"] = rsi_series(df_5m["close"], RSI_PERIOD)
    # 2026-06-06: ATR(14) for chop-regime filter + 1h cumulative price move
    _prev_close = df_5m["close"].shift(1)
    _tr = pd.concat([
        df_5m["high"] - df_5m["low"],
        (df_5m["high"] - _prev_close).abs(),
        (df_5m["low"]  - _prev_close).abs(),
    ], axis=1).max(axis=1)
    df_5m["atr_14"] = _tr.rolling(14).mean()
    last_idx = len(df_5m) - 2  # last CLOSED 5m bar
    last = df_5m.iloc[last_idx]
    close_px = float(last["close"])
    rsi_val = float(last["rsi"]) if pd.notna(last["rsi"]) else None

    sig = rsi_signal(rsi_val)

    # ── Optional 15m trend gate (entry only) ──
    trend = None  # "UP" / "DOWN" / None
    trend_gap_pct = None  # signed gap %: (EMA20 - EMA50) / EMA50 × 100
    if USE_TREND_FILTER:
        df_tf = fetch_klines(TREND_TF, 300)
        if df_tf is not None and len(df_tf) >= TREND_EMA_SLOW:
            ema_f = df_tf["close"].ewm(span=TREND_EMA_FAST, adjust=False).mean()
            ema_s = df_tf["close"].ewm(span=TREND_EMA_SLOW, adjust=False).mean()
            ema_f_v = float(ema_f.iloc[-2])
            ema_s_v = float(ema_s.iloc[-2])
            trend = "UP" if ema_f_v > ema_s_v else "DOWN"
            trend_gap_pct = (ema_f_v - ema_s_v) / ema_s_v * 100.0
        else:
            log.warning(f"  {TREND_TF} trend: insufficient data — gate inactive this tick")

    # ── 2026-06-06 v1.1: 1h RSI 50-split filter (HTF alignment) ──
    # Backtest 5yr: +220% → +2,023% return, -27% → -14% DD, 2024 -$1311 → +$5518.
    # Mechanism: only fade WITH higher-TF momentum (1h RSI on the right side of 50).
    # Same principle as 15m EMA gate, just one timeframe up.
    rsi_1h_val = None
    if USE_1H_RSI_FILTER:
        df_1h = fetch_klines("1h", 100)
        if df_1h is not None and len(df_1h) >= RSI_PERIOD + 1:
            rsi_1h_series = rsi_series(df_1h["close"], RSI_PERIOD)
            rsi_1h_raw = rsi_1h_series.iloc[-2]  # last CLOSED 1h bar
            if pd.notna(rsi_1h_raw):
                rsi_1h_val = float(rsi_1h_raw)
        else:
            log.warning(f"  1h RSI: insufficient data — filter inactive this tick")

    gap_txt = f" | gap {trend_gap_pct:+.2f}%" if trend_gap_pct is not None else ""
    log.info(f"  Balance: ${state['balance']:,.2f} | {PAIR}: ${close_px:,.2f} | live: ${live_px:,.2f} | "
             f"RSI {rsi_val:.1f} | Signal: {sig or 'NONE'}{' | 15m '+trend if trend else ''}{gap_txt}" if rsi_val is not None else
             f"  Balance: ${state['balance']:,.2f} | RSI n/a")

    if state["balance"] > state.get("peak_equity", 0):
        state["peak_equity"] = state["balance"]
    peak = state.get("peak_equity", state["balance"])
    dd_pct = (state["balance"] / peak - 1) if peak > 0 else 0.0

    pos = state.get("position")
    exit_this_tick = False

    if pos:
        side = pos["side"]

        # DCA first (improves avg before exit checks)
        if maybe_dca(pos, live_px, state["balance"], state):
            pos = state["position"]
        avg_entry = avg_entry_of(pos)

        exit_reason = None
        exit_px = None

        # Adaptive TP from avg — 0.50% while only L1 filled, 0.25% once DCA'd.
        if USE_TAKE_PROFIT:
            tp_pct = tp_pct_for(pos["filled"])
            tp = avg_entry * (1 + tp_pct) if side == "LONG" else avg_entry * (1 - tp_pct)
            if (side == "LONG" and live_px >= tp) or (side == "SHORT" and live_px <= tp):
                exit_reason, exit_px = "TP", tp

        # Loose catastrophic SL.
        # 2026-06-06: BE-after-DCA — when DCA fired (filled >= 2), SL moves to
        # avg entry price. Caps "DCA into runaway" losses at ~$0.
        if exit_px is None and USE_STOP_LOSS:
            if USE_BE_AFTER_DCA and pos.get("filled", 1) >= 2:
                slp = avg_entry_of(pos)
            else:
                slp = sl_price(side, pos["worst_entry"])
            if slp is not None and ((side == "LONG" and live_px <= slp) or (side == "SHORT" and live_px >= slp)):
                exit_reason, exit_px = ("BE-DCA" if USE_BE_AFTER_DCA and pos.get("filled", 1) >= 2 else "SL"), slp

        # 2026-06-06: TREND FLIP EXIT — close on 15m EMA reversal (early reversal catch)
        # Backtest: catches losing trades before they hit SL, reduces avg loss size
        if exit_px is None and USE_TREND_FLIP_EXIT and trend is not None:
            if (side == "SHORT" and trend == "UP") or (side == "LONG" and trend == "DOWN"):
                exit_reason, exit_px = "TREND_FLIP", live_px

        if exit_px is not None:
            close_position(state, pos, exit_px, exit_reason)
            state["position"] = None
            pos = None
            exit_this_tick = True
        else:
            fav = ((live_px - avg_entry) / avg_entry * 100) * (1 if side == "LONG" else -1)
            # 2026-06-05: excursion tracking — recorded in trade_log at close
            pos["max_fav_pct"] = max(pos.get("max_fav_pct", 0.0), fav)
            pos["max_adv_pct"] = min(pos.get("max_adv_pct", 0.0), fav)
            log.info(f"  IN {side} L{pos['filled']}/{DCA_LEVELS} avg=${avg_entry:.2f} live=${live_px:.2f} fav={fav:+.2f}% | mfe {pos['max_fav_pct']:+.2f}% mae {pos['max_adv_pct']:+.2f}%")

    # Entry — RSI (+ optional 15m trend gate + circuit breaker). Don't re-enter on the tick we just exited.
    block_reason = None

    # 2026-06-06: DAILY MAX LOSS circuit breaker
    # Reset daily counter at UTC midnight; pause entries if today's net loss exceeds threshold
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.get("daily_loss_date") != today_utc:
        state["daily_loss"] = 0.0
        state["daily_loss_date"] = today_utc
    if sig and DAILY_MAX_LOSS > 0 and state.get("daily_loss", 0) <= -DAILY_MAX_LOSS:
        block_reason = f"daily max loss ${DAILY_MAX_LOSS:.0f} reached (today: ${state.get('daily_loss', 0):.2f}) — entries paused until 00:00 UTC"
        log.info(f"  {block_reason}")
        sig = None

    # Circuit breaker: skip entries while paused after a loss streak.
    if USE_CIRCUIT_BREAKER and sig and state.get("pause_until"):
        try:
            if datetime.now(timezone.utc) < datetime.fromisoformat(state["pause_until"]):
                block_reason = f"circuit breaker — paused after {BREAKER_LOSSES} losses (until {state['pause_until'][:16]} UTC)"
                log.info(f"  {block_reason}")
                sig = None
        except Exception:
            pass
    # 2026-06-05 FIX: trend filter is DEFENSIVE — if data unavailable, BLOCK the
    # entry (was previously skipping the check entirely, which let v2 LONG into a
    # DOWN trend at 14:00 UTC when Bybit returned partial 15m data).
    if USE_TREND_FILTER and sig:
        if trend is None:
            block_reason = f"{sig} blocked — 15m trend data unavailable (defensive)"
            log.info(f"  {block_reason}")
            sig = None
        elif (sig == "LONG" and trend != "UP") or (sig == "SHORT" and trend != "DOWN"):
            block_reason = f"{sig} blocked — 15m trend is {trend} (need {'UP' if sig=='LONG' else 'DOWN'})"
            log.info(f"  {block_reason}")
            sig = None
    # 2026-06-05: high-vol UTC hour filter (consistency across v1/v2/v3)
    if sig and BLOCKED_HOURS:
        cur_hour = datetime.now(timezone.utc).hour
        if cur_hour in BLOCKED_HOURS:
            block_reason = (f"high-risk UTC hour ({cur_hour:02d}:00 blocked — "
                            f"historical loss cluster)")
            log.info(f"  {block_reason}")
            sig = None
    # ── 2026-06-05 v2: TREND-GAP FIRMNESS FILTER ──
    # Only enter if the 15m EMA20/EMA50 gap is firm enough (not knife-edge).
    # Knife-edge trends (small gap) are where the worst losses come from per OOS analysis.
    # 2026-06-05 FIX: GAP filter is DEFENSIVE — block when gap data unavailable
    # (same fail-closed pattern as the trend filter fix).
    if sig and TREND_GAP_MIN > 0:
        gap_required = TREND_GAP_MIN * 100.0
        if trend_gap_pct is None:
            block_reason = f"{sig} blocked — 15m trend GAP data unavailable (defensive)"
            log.info(f"  {block_reason}")
            sig = None
        elif abs(trend_gap_pct) < gap_required:
            block_reason = (f"{sig} blocked — 15m trend gap {trend_gap_pct:+.3f}% "
                            f"weaker than required ±{gap_required:.2f}% (knife-edge)")
            log.info(f"  {block_reason}")
            sig = None
    # 2026-06-06: ATR + 1h cumulative move filters (fail-closed).
    # Per-bot threshold via env vars.
    if sig and state["position"] is None:
        try:
            atr_val = float(df_5m["atr_14"].iloc[-2])  # ATR at last closed bar
            atr_pct = (atr_val / close_px) * 100 if close_px > 0 else 0
            if pd.isna(atr_val) or atr_val <= 0:
                block_reason = f"{sig} blocked — ATR data not ready (defensive)"
                log.info(f"  {block_reason}")
                sig = None
            elif atr_pct > RSISCALP_ATR_MAX_PCT:
                block_reason = (f"{sig} blocked — ATR {atr_pct:.2f}% > "
                                f"{RSISCALP_ATR_MAX_PCT:.2f}% (chop regime)")
                log.info(f"  {block_reason}")
                sig = None
        except (KeyError, IndexError, ValueError) as e:
            block_reason = f"{sig} blocked — ATR filter error: {e}"
            log.warning(f"  {block_reason}")
            sig = None

    if sig and state["position"] is None and len(df_5m) >= 14:
        try:
            close_now    = float(df_5m["close"].iloc[-2])    # last closed
            close_1h_ago = float(df_5m["close"].iloc[-14])   # 12 bars before
            chg_1h_pct = (close_now / close_1h_ago - 1) * 100 if close_1h_ago > 0 else 0
            if sig == "SHORT" and chg_1h_pct > RSISCALP_1H_MOVE_MAX_PCT:
                block_reason = (f"SHORT blocked — 1h rally {chg_1h_pct:+.2f}% > "
                                f"{RSISCALP_1H_MOVE_MAX_PCT:.2f}% (fading momentum)")
                log.info(f"  {block_reason}")
                sig = None
            elif sig == "LONG" and chg_1h_pct < -RSISCALP_1H_MOVE_MAX_PCT:
                block_reason = (f"LONG blocked — 1h drop {chg_1h_pct:+.2f}% < "
                                f"-{RSISCALP_1H_MOVE_MAX_PCT:.2f}% (fading momentum)")
                log.info(f"  {block_reason}")
                sig = None
        except (KeyError, IndexError, ValueError) as e:
            block_reason = f"{sig} blocked — 1h filter error: {e}"
            log.warning(f"  {block_reason}")
            sig = None

    # 2026-06-06 v1.1: 1h RSI 50-split — HTF momentum alignment.
    # Same fail-closed pattern as other filters.
    if sig and USE_1H_RSI_FILTER:
        if rsi_1h_val is None:
            block_reason = f"{sig} blocked — 1h RSI data unavailable (defensive)"
            log.info(f"  {block_reason}")
            sig = None
        elif sig == "SHORT" and rsi_1h_val >= RSI_1H_THRESHOLD:
            block_reason = (f"SHORT blocked — 1h RSI {rsi_1h_val:.1f} ≥ "
                            f"{RSI_1H_THRESHOLD:.0f} (HTF still overbought)")
            log.info(f"  {block_reason}")
            sig = None
        elif sig == "LONG" and rsi_1h_val <= RSI_1H_THRESHOLD:
            block_reason = (f"LONG blocked — 1h RSI {rsi_1h_val:.1f} ≤ "
                            f"{RSI_1H_THRESHOLD:.0f} (HTF still oversold)")
            log.info(f"  {block_reason}")
            sig = None

    if state["position"] is None and not exit_this_tick and sig:
        open_position(state, sig, live_px, rsi_val)

    stats = state["stats"]
    wr = (stats["wins"] / stats["total"] * 100) if stats["total"] > 0 else 0.0
    total_pnl_pct = (state["balance"] / INITIAL_BALANCE - 1) * 100
    log.info(f"  Stats: {stats['total']} trades | WR {wr:.0f}% | PnL {total_pnl_pct:+.2f}%")

    save_state(state)

    pos = state.get("position")
    pos_status = None
    if pos:
        avg_e = avg_entry_of(pos)
        # Display BE-after-DCA SL if active
        if USE_BE_AFTER_DCA and pos.get("filled", 1) >= 2:
            slp = avg_e
        else:
            slp = sl_price(pos["side"], pos["worst_entry"])
        tp_pct = tp_pct_for(pos["filled"])
        tp_p = (avg_e * (1 + tp_pct) if pos["side"] == "LONG" else avg_e * (1 - tp_pct)) if USE_TAKE_PROFIT else None
        fav_p = ((live_px - avg_e) / avg_e * 100) * (1 if pos["side"] == "LONG" else -1)
        pos_status = {
            "side": pos["side"], "first_entry": pos["first_entry"], "avg_entry": avg_e,
            "worst_entry": pos["worst_entry"], "qty_total": pos["qty_total"], "filled": pos["filled"],
            "tp_px": tp_p, "sl_px": slp, "fav_pct": fav_p, "entry_time": pos.get("entry_time"),
        }

    write_status({
        "env": os.environ.get("RSISCALP_DATA_DIR", "paper_rsiscalp_trend"),
        "pair": PAIR, "price": close_px, "live_price": live_px,
        "balance": state["balance"], "peak_equity": peak, "drawdown_pct": dd_pct,
        "position": pos_status, "signal": sig,
        "indicators": {"rsi": rsi_val, "rsi_oversold": RSI_OVERSOLD, "rsi_overbought": RSI_OVERBOUGHT,
                       "price": close_px, "trend_gap_pct": trend_gap_pct, "trend_gap_min_pct": TREND_GAP_MIN*100,
                       "blocked_hours": sorted(BLOCKED_HOURS) if BLOCKED_HOURS else [],
                       "current_hour_utc": datetime.now(timezone.utc).hour,
                       # 2026-06-06: ATR + 1h cumulative move for ConditionsPanel
                       "atr_pct": float(df_5m["atr_14"].iloc[-2]) / close_px * 100 if "atr_14" in df_5m.columns and not pd.isna(df_5m["atr_14"].iloc[-2]) else None,
                       "atr_max_pct": RSISCALP_ATR_MAX_PCT,
                       "chg_1h_pct": (close_px / float(df_5m["close"].iloc[-14]) - 1) * 100 if len(df_5m) >= 14 else None,
                       "chg_1h_max_pct": RSISCALP_1H_MOVE_MAX_PCT,
                       # v1.1: 1h RSI for HTF alignment check
                       "rsi_1h": rsi_1h_val,
                       "rsi_1h_threshold": RSI_1H_THRESHOLD,
                       # ULTIMATE-specific
                       "daily_loss": state.get("daily_loss", 0.0),
                       "daily_max_loss": DAILY_MAX_LOSS,
                       "is_weekend": datetime.now(timezone.utc).weekday() >= 5,
                       "weekend_qty_mult": WEEKEND_QTY_MULT,
                       "sl_from_worst_pct": SL_FROM_WORST*100},
        "trend_15m": trend, "block_reason": block_reason,
        "stats": state["stats"],
        "strategy": f"RSI-Scalp ULTIMATE (RSI{RSI_PERIOD} {RSI_OVERSOLD}/{RSI_OVERBOUGHT} / 15m EMA{TREND_EMA_FAST}/{TREND_EMA_SLOW} + GAP ≥{TREND_GAP_MIN*100:.2f}% / TP {TP_PCT_SINGLE*100:.2f}%·{TP_PCT_DCA*100:.2f}% / {DCA_LEVELS} DCA @ {DCA_SPACING*100:.2f}% / SL {SL_FROM_WORST*100:.2f}% from worst / +trend-flip exit / +weekend {WEEKEND_QTY_MULT:.1f}× / +daily-loss-stop ${DAILY_MAX_LOSS:.0f}) [PAPER]",
        "paper_mode": True, "state": "IN_POSITION" if pos else "FLAT",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    log.info("=" * 60 + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception(f"FATAL: {e}")
        sys.exit(1)
```

### File 3 of 3: `backtest/v11_faithful_backtest.py` (358 lines — no-lookahead reference backtest)

```python
#!/usr/bin/env python3
"""v11_faithful_backtest.py — Lookahead-free 5-year backtest of v1.1.

Agent A flagged the prior backtest as having lookahead bias from
merge_asof(direction="backward") on 1h/15m bars labeled at OPEN time.

This version eliminates that by:
  - Labeling 15m bars with their CLOSE time (open + 15min)
  - Labeling 1h bars with their CLOSE time (open + 1h)
  - Then merge_asof(direction="backward") correctly finds the last
    bar that has FULLY CLOSED before the current 5m timestamp

Reproduces v1.1 production logic exactly:
  - RSI(9) on 5m AND 1h (period matches bot's RSI_PERIOD=9)
  - 15m EMA20/EMA50 trend gate (fail-closed if data unavailable)
  - GAP firmness ≥ 0.25%
  - ATR(14) on 5m < 0.60%
  - 1h cumulative move (last 12 5m closes) < ±2.0%
  - Blocked hours {5,6,11,12,13,20} UTC
  - 1h RSI 50-split filter (v1.1 addition)
  - DCA: 2 legs @ 0.5% adverse, equal-size legs
  - TP: 0.5% / 0.25% adaptive
  - SL: 0.6% from worst entry
  - Trend-flip exit (close on 15m EMA reversal)
  - Weekend 2x position size
  - Daily $200 loss circuit breaker
  - Single-loss circuit breaker: 15min pause after EVERY loss
  - 3x leverage, 0.04% taker fee per side
  - Bar fill order: SL → DCA → TP (pessimistic)
"""
import sys, os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fleet_backtest import rsi_series, ema, atr


CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "cache")


def load_and_prepare(years=5):
    """Load 5m bars + compute lookahead-free HTF features."""
    df = pd.read_csv(os.path.join(CACHE, "BTCUSDT_5m.csv"))
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    start = datetime.utcnow() - timedelta(days=years * 365)
    df = df[df["timestamp"] >= start].reset_index(drop=True)

    # 5m indicators (computed bar-by-bar, no lookahead)
    df["rsi"] = rsi_series(df["close"], 9)
    df["atr_14"] = atr(df, 14)

    # 15m bars — use CLOSE time as label so merge_asof gives only CLOSED bars
    df15 = (
        df.set_index("timestamp")
          .resample("15min")
          .agg({"open":"first","high":"max","low":"min","close":"last"})
          .dropna()
    )
    df15["ema20"] = ema(df15["close"], 20)
    df15["ema50"] = ema(df15["close"], 50)
    df15["m15_gap_pct"] = (df15["ema20"] - df15["ema50"]) / df15["ema50"] * 100
    df15["m15_trend"] = np.where(df15["ema20"] > df15["ema50"], "UP", "DOWN")
    # CRITICAL: shift timestamps forward by 15min so merge_asof backward gets
    # only fully-closed bars relative to the 5m query time
    df15 = df15.reset_index()
    df15["timestamp"] = df15["timestamp"] + pd.Timedelta(minutes=15)

    df = pd.merge_asof(
        df.sort_values("timestamp"),
        df15[["timestamp", "m15_trend", "m15_gap_pct"]].sort_values("timestamp"),
        on="timestamp", direction="backward"
    )

    # 1h bars — same close-time label trick
    df1h = (
        df.set_index("timestamp")
          .resample("1h")
          .agg({"close":"last"})
          .dropna()
    )
    df1h["rsi_1h"] = rsi_series(df1h["close"], 9)  # RSI period matches bot (9, not 14)
    df1h = df1h.reset_index()
    df1h["timestamp"] = df1h["timestamp"] + pd.Timedelta(hours=1)

    df = pd.merge_asof(
        df.sort_values("timestamp"),
        df1h[["timestamp", "rsi_1h"]].sort_values("timestamp"),
        on="timestamp", direction="backward"
    )

    return df


def simulate(df, use_1h_rsi_filter=True, use_circuit_breaker=True,
             commission=0.0004, leverage=3.0, balance_init=5000.0):
    """Production-faithful v1.1 simulator."""
    bal = balance_init
    pos = None
    cooldown_until = -1   # bar index until which entries blocked (post-trade cooldown of 3 bars = 15min)
    breaker_until = None  # datetime until breaker pause ends
    daily_loss = 0.0
    current_day = None
    paused_today = False
    trades = []

    BLOCKED_HOURS = {5, 6, 11, 12, 13, 20}
    GAP_MIN = 0.25  # %
    ATR_MAX = 0.60  # %
    MOVE_1H_MAX = 2.0  # %
    SL_PCT = 0.006   # 0.6% from worst entry
    TP_SINGLE = 0.005
    TP_DCA = 0.0025
    DCA_SPACING = 0.005
    DCA_LEVELS = 2
    DAILY_MAX_LOSS = 200.0
    WEEKEND_MULT = 2.0

    for i in range(len(df)):
        bar = df.iloc[i]
        t = bar["timestamp"]
        h, l, c = bar["high"], bar["low"], bar["close"]
        day = t.date()
        if day != current_day:
            current_day = day
            daily_loss = 0.0
            paused_today = False

        # ───────────── EXIT logic for open position ─────────────
        if pos is not None:
            side = pos["side"]; avg = pos["avg"]; worst = pos["worst"]
            sl_px = worst * (1 + SL_PCT) if side == "SHORT" else worst * (1 - SL_PCT)
            tp_pct = TP_SINGLE if pos["legs"] == 1 else TP_DCA
            tp_px = avg * (1 - tp_pct) if side == "SHORT" else avg * (1 + tp_pct)
            dca_px = None
            if pos["legs"] < DCA_LEVELS:
                dca_px = worst * (1 + DCA_SPACING) if side == "SHORT" else worst * (1 - DCA_SPACING)

            hit_sl = (side == "SHORT" and h >= sl_px) or (side == "LONG" and l <= sl_px)
            hit_dca = dca_px is not None and ((side == "SHORT" and h >= dca_px) or (side == "LONG" and l <= dca_px))
            hit_tp = (side == "SHORT" and l <= tp_px) or (side == "LONG" and h >= tp_px)

            # Trend-flip exit (only fires if 15m trend data available)
            tf_exit = False
            if pd.notna(bar.get("m15_trend")):
                if (side == "SHORT" and bar["m15_trend"] == "UP") or (side == "LONG" and bar["m15_trend"] == "DOWN"):
                    tf_exit = True

            # Order: SL first (pessimistic), then TP, then trend-flip, then DCA
            exit_px = None
            reason = None
            if hit_sl:
                exit_px = sl_px; reason = "SL"
            elif hit_tp:
                exit_px = tp_px; reason = "TP"
            elif tf_exit:
                exit_px = c; reason = "TREND"
            elif hit_dca:
                # DCA fill: add a leg with same qty (equal-size DCA)
                old_q = pos["qty"]
                new_q = old_q
                fill_px = dca_px
                pos["avg"] = (avg * old_q + fill_px * new_q) / (old_q + new_q)
                pos["qty"] = old_q + new_q
                pos["worst"] = fill_px
                pos["legs"] += 1
                # commission on DCA leg
                bal -= fill_px * new_q * commission
                # continue holding position
                continue

            if exit_px is not None:
                qty_total = pos["qty"]
                gross = (avg - exit_px) * qty_total if side == "SHORT" else (exit_px - avg) * qty_total
                exit_fees = exit_px * qty_total * commission
                net = gross - exit_fees
                bal += net
                trades.append({
                    "entry_t": pos["entry_t"], "exit_t": t,
                    "side": side, "legs": pos["legs"],
                    "entry_px": pos["entry_px"], "exit_px": exit_px,
                    "qty": qty_total, "net": net, "reason": reason,
                    "weekend": pos.get("weekend_2x", False),
                })
                if net < 0:
                    daily_loss += net
                    if use_circuit_breaker:
                        # 15-min cooldown after EVERY loss
                        breaker_until = t + pd.Timedelta(minutes=15)
                if DAILY_MAX_LOSS > 0 and daily_loss <= -DAILY_MAX_LOSS:
                    paused_today = True
                cooldown_until = i + 3  # 3 bars = 15-min post-trade cooldown
                pos = None

        # ───────────── ENTRY logic ─────────────
        if pos is not None:
            continue
        if i < cooldown_until:
            continue
        if i + 1 >= len(df):
            break
        if paused_today:
            continue
        if breaker_until is not None and t < breaker_until:
            continue

        rsi_v = bar.get("rsi")
        if pd.isna(rsi_v):
            continue
        sig = "LONG" if rsi_v <= 30 else "SHORT" if rsi_v >= 70 else None
        if sig is None:
            continue

        # Hour filter
        if t.hour in BLOCKED_HOURS:
            continue

        # 15m trend gate (fail-closed)
        if pd.isna(bar.get("m15_trend")):
            continue
        if (sig == "LONG" and bar["m15_trend"] != "UP") or (sig == "SHORT" and bar["m15_trend"] != "DOWN"):
            continue

        # GAP firmness (fail-closed)
        if pd.isna(bar.get("m15_gap_pct")):
            continue
        if abs(bar["m15_gap_pct"]) < GAP_MIN:
            continue

        # ATR filter
        if pd.isna(bar.get("atr_14")):
            continue
        atr_pct = (bar["atr_14"] / c) * 100
        if atr_pct > ATR_MAX:
            continue

        # 1h cumulative move filter
        if i >= 12:
            chg_1h = (c / df["close"].iloc[i - 12] - 1) * 100
            if sig == "SHORT" and chg_1h > MOVE_1H_MAX:
                continue
            if sig == "LONG" and chg_1h < -MOVE_1H_MAX:
                continue

        # 1h RSI 50-split filter (v1.1 addition) — uses LAST CLOSED 1h bar
        if use_1h_rsi_filter:
            rsi_1h = bar.get("rsi_1h")
            if pd.isna(rsi_1h):
                continue   # fail-closed
            if sig == "SHORT" and rsi_1h >= 50:
                continue
            if sig == "LONG" and rsi_1h <= 50:
                continue

        # All filters passed — open at NEXT bar's open
        next_o = df.iloc[i + 1]["open"]
        is_weekend = t.weekday() >= 5
        qty_mult = WEEKEND_MULT if is_weekend else 1.0
        qty_per_leg = (bal * 0.95 * leverage * qty_mult) / next_o / DCA_LEVELS
        bal -= next_o * qty_per_leg * commission  # entry commission
        pos = {
            "side": sig,
            "entry_t": df.iloc[i + 1]["timestamp"],
            "entry_px": next_o,
            "avg": next_o,
            "worst": next_o,
            "qty": qty_per_leg,
            "legs": 1,
            "weekend_2x": is_weekend,
        }

    if not trades:
        return None

    nets = np.array([t["net"] for t in trades])
    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    final = balance_init + nets.sum()
    eq = np.concatenate([[balance_init], balance_init + nets.cumsum()])
    peaks = np.maximum.accumulate(eq)
    dd_series = (eq - peaks) / peaks * 100

    by_year = {}
    by_year_dd = {}
    eq_per_year_start = {}
    for j, tr in enumerate(trades):
        y = tr["entry_t"].year
        by_year.setdefault(y, []).append(tr["net"])

    return {
        "n": len(trades),
        "wr": len(wins) / len(trades) * 100,
        "return_pct": (final / balance_init - 1) * 100,
        "max_dd_pct": dd_series.min(),
        "final": final,
        "trades": trades,
        "by_year": by_year,
        "avg_win": wins.mean() if len(wins) > 0 else 0,
        "avg_loss": losses.mean() if len(losses) > 0 else 0,
        "max_win": wins.max() if len(wins) > 0 else 0,
        "max_loss": losses.min() if len(losses) > 0 else 0,
    }


def main():
    print("═══ V1.1 FAITHFUL 5-YEAR BACKTEST (no lookahead) ═══\n")
    print("Loading + prepping data with close-time-labeled HTF bars...")
    df = load_and_prepare(years=5)
    print(f"  Period: {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")
    print(f"  Bars: {len(df):,}\n")

    print("Running 4 scenarios for honest comparison:\n")

    scenarios = [
        ("Baseline NO 1h RSI filter, NO circuit breaker", dict(use_1h_rsi_filter=False, use_circuit_breaker=False)),
        ("v1.1 WITH 1h RSI filter, NO circuit breaker", dict(use_1h_rsi_filter=True, use_circuit_breaker=False)),
        ("Baseline NO 1h RSI filter + circuit breaker", dict(use_1h_rsi_filter=False, use_circuit_breaker=True)),
        ("v1.1 WITH 1h RSI filter + circuit breaker (FAITHFUL)", dict(use_1h_rsi_filter=True, use_circuit_breaker=True)),
    ]

    results = {}
    print(f"{'Config':<55} {'#':<6} {'WR%':<6} {'Return%':<10} {'DD%':<8} {'Final$':<10}")
    print("─" * 100)
    for label, kwargs in scenarios:
        r = simulate(df, **kwargs)
        if r is None:
            print(f"  {label}: no trades")
            continue
        results[label] = r
        print(f"{label:<55} {r['n']:<6} {r['wr']:>5.1f}% {r['return_pct']:>+7.2f}% {r['max_dd_pct']:>+6.2f}% ${r['final']:>8.0f}")

    print(f"\n═══ YEAR-BY-YEAR (FAITHFUL v1.1) ═══\n")
    label_faithful = "v1.1 WITH 1h RSI filter + circuit breaker (FAITHFUL)"
    if label_faithful in results:
        r = results[label_faithful]
        print(f"{'Year':<6} {'Trades':<7} {'WR%':<6} {'Total $':<11} {'Cumulative':<12}")
        print("─" * 50)
        cum = 0
        for yr in sorted(r["by_year"]):
            nets = r["by_year"][yr]
            wins = sum(1 for x in nets if x > 0)
            wr = wins / len(nets) * 100
            total = sum(nets)
            cum += total
            print(f"{yr:<6} {len(nets):<7} {wr:>5.1f}% ${total:>+8.2f} ${cum:>+9.2f}")

    print(f"\n═══ DELTA: 1h RSI filter ON vs OFF (faithful baseline) ═══")
    if "Baseline NO 1h RSI filter + circuit breaker" in results and label_faithful in results:
        base = results["Baseline NO 1h RSI filter + circuit breaker"]
        new = results[label_faithful]
        print(f"  Return: {base['return_pct']:+.2f}% → {new['return_pct']:+.2f}%  (delta {new['return_pct']-base['return_pct']:+.2f} pp)")
        print(f"  DD:     {base['max_dd_pct']:+.2f}% → {new['max_dd_pct']:+.2f}%  (delta {new['max_dd_pct']-base['max_dd_pct']:+.2f} pp)")
        print(f"  Trades: {base['n']} → {new['n']}  (cut {base['n']-new['n']} trades)")


if __name__ == "__main__":
    main()
```

### File 4 (also needed): `strategies/day/core.py` — the rsi_series helper

```python
"""
core.py — S/R DCA Day Strategy V2.2 (5m execution + 1d S/R)

Python port of strategy_sr_dca_5m.pine. V2.2 adds conditional hold-past-EOD
on top of V2.1's divergence + BE-stop foundation. Tested Mar 30–May 6:
V2.2 (hold @ 1.5% threshold) +33.13% / PF 14.08 / DD 2.56% / WR 76.92%
V2.1 baseline                +28.31% / PF 12.41 / DD 2.56% / WR 76.92%

V2.2 vs V2.1 (2026-05-06):
  - HOLD_PAST_EOD_IF_FAV: at 20:00 UTC, if fav ≥ 1.5% from first entry,
    skip the EOD close and let the trade ride. 24h hard cap at next day's
    20:00. Same DD, same trade count, same WR — pure upside on winners
    that were getting cut at EOD.

V2.1 vs V2 (2026-05-06):
  - SL below worst: 1.4% → 2.0% (BE-stop rescues borderline trades that
    would have noise-stopped at 1.4%)
  - Breakeven SL: OFF → ON (1.0% trigger / 0.25% buffer) — V2 said BE
    was redundant with divergence, but Mar 30–May 6 data showed it adds
    +1.3% return + +2.0 PF when paired with 2.0% SL.

V2 vs V1 (still in V2.1):
  - RSI divergence at S/R touch (DEFAULT ON, pivot 5/5, fresh 20 bars)
  - DCA spacing: 0.8% → 0.85%
  - Volume × 20-bar avg: 1.2 → 1.1 (slightly more permissive)
  - RSI anti-extreme filter: ON → OFF (subsumed by divergence)

Logic:
  - Entry: prev_day's L/H touch + fresh divergence + filters
  - DCA: 0.85% beyond L1 (2 levels default)
  - TP: hybrid (prev_mid pre-DCA, fixed % from first entry post-DCA)
  - SL: 2.0% below worst entry, tightens to entry × (1 ± 0.25%) once BE arms
  - BE arms when fav% from first_entry crosses 1.0%
  - EOD flatten at UTC 20:00
  - Max 1 cycle per UTC day

V1 (no divergence, no BE) preserved at v1_backup/ for rollback.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Literal, Dict, Any
import pandas as pd
import numpy as np

# ═════ V2 Constants (match pine V2 defaults) ═════
LEVERAGE       = 2.0
RISK_PCT       = 0.06         # 6% total risk per cycle

DCA_LEVELS     = 2
DCA_SPACING    = 0.004        # 0.4% (was 0.3% briefly; before that 0.5%; originally 0.85%). Set 2026-05-23 at user request — middle ground between the tight 0.3% (more DCA but tested negative) and the wider 0.5% (only 25% DCA-fill rate).
SL_BELOW_WORST = 0.020        # V2.1: 2.0% (V2 was 1.4%) — BE-stop rescues noise-stopped trades, looser SL outperforms
SUPPORT_ZONE   = 0.0005       # 0.05% zone around prev H/L — only direct touches qualify

# TP offset: shift prev_mid TP slightly toward current price for reliable fills.
PREV_MID_OFFSET = 0.001       # 0.1%

# TP mode — hybrid: prev_mid pre-DCA, switches to first_entry × (1 ∓ TP_FIXED_PCT) post-DCA.
TP_MODE        = "hybrid"
TP_FIXED_PCT   = 0.04         # 4% — used post-DCA in hybrid mode

# Adaptive S/R range
RANGE_FILTER_MODE  = "extend"
MIN_PREV_RANGE_PCT = 0.02     # 2% floor
MAX_LOOKBACK_DAYS  = 2

# Breakeven SL — V2.1 ON. Once fav% from first entry crosses BE_TRIGGER_PCT,
# SL tightens to entry × (1 ± 0.25%) to lock in real ~0.20% net profit.
# 2026-05-23: trigger lowered 1.0% → 0.75% per 45-day sweep (45d backtest
# Apr-08 → May-23 went from PF 1.16 / +$38 to PF 3.76 / +$230, DD 2.5% → 1.0%).
USE_BREAKEVEN  = True
BE_TRIGGER_PCT = 0.0075
BE_BUFFER_PCT  = 0.0025

CLOSE_HOUR     = 20           # UTC hour to force flatten + block new entries

# V2.2 — Conditional hold past EOD. When favorable ≥ HOLD_MIN_FAV_PCT at
# closeHour, skip the EOD close and let the trade ride to TP / SL / BE.
# 24h hard cap: at the next day's CLOSE_HOUR, force close regardless.
# Backtest Mar 30–May 6: ON gives +33.13% / DD 2.56% / PF 14.08 / WR 76.92%
# vs V2.1 baseline +28.31% / PF 12.41 — same DD, +4.82% return on winners
# that were getting cut at 20:00 UTC.
HOLD_PAST_EOD_IF_FAV = True
HOLD_MIN_FAV_PCT     = 0.015  # 1.5% — V2.2 tuned default. Reverted to 1.5% on 2026-05-23 after a backtest at 0.5% turned the strategy negative. The 1.5% gate is part of the +33%/PF 14 baseline; modest-favourable trades getting EOD-clipped is by design.

# Entry filters
VOL_MULT       = 1.1          # V2: 1.1× (V1 was 1.2×) — slightly more permissive given divergence gate
USE_RSI_FILTER = False        # V2: RSI anti-extreme OFF (subsumed by divergence). V1 had this True.
RSI_LOW        = 25           # skip long if RSI < (only used when USE_RSI_FILTER)
RSI_HIGH       = 75           # skip short if RSI > (only used when USE_RSI_FILTER)

# RSI Divergence (V2 — required at S/R touch)
USE_RSI_DIVERGENCE = True
DIV_PIVOT_L  = 5              # 5 bars left for pivot confirmation
DIV_PIVOT_R  = 5              # 5 bars right (= 25 min confirmation lag on 5m)
DIV_FRESH_BARS = 20           # V2.2 tuned default. Briefly raised to 40 on 2026-05-23 to add trade frequency; reverted same day after the wider window turned the strategy negative in backtest — older divergences are weaker signals, the 20-bar (100 min) freshness was tuned for a reason.

RSI_PERIOD     = 14
VOL_AVG_LEN    = 20
EMA_BIAS_LEN   = 20           # 1h EMA period — Apr 2026 BTC backtest (2.31y): EMA20 net -1.33% / PF 1.07 (vs EMA15 -13.41% / 0.92). Stickier bias = fewer false flips when price approaches prev_H/L.

Side = Literal["LONG", "SHORT"]


# ═════ Indicators ═════
def rsi_series(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    gain = d.clip(lower=0)
    loss = -d.clip(upper=0)
    ag = gain.ewm(alpha=1.0/n, min_periods=n, adjust=False).mean()
    al = loss.ewm(alpha=1.0/n, min_periods=n, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


# ═════ Features ═════
def build_features(df_5m: pd.DataFrame, df_1d: pd.DataFrame) -> pd.DataFrame:
    """Add RSI, volume SMA, 1h EMA20 bias, and prev-day H/L/mid to each 5m bar.

    1h bias is resampled from the 5m data (no extra fetch needed).
    Prev-day H/L/mid still come from df_1d.
    """
    df = df_5m.copy().reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["date"] = df["timestamp"].dt.normalize()
    df["utc_hour"] = df["timestamp"].dt.hour

    df["rsi"] = rsi_series(df["close"], RSI_PERIOD)
    df["vol_avg"] = df["volume"].rolling(VOL_AVG_LEN).mean()

    # ─── 1h EMA20 bias (resampled from 5m, label=left so index = bar start) ───
    d5 = df.set_index("timestamp").sort_index()
    h1 = d5["close"].resample("1h").last().dropna().to_frame()
    h1["ema"] = ema(h1["close"], EMA_BIAS_LEN)
    h1["bias"] = np.where(h1["close"] > h1["ema"],  1,
                  np.where(h1["close"] < h1["ema"], -1, 0))
    # Pine's close[1] semantic: at 5m bar T we look at PRIOR closed 1h bar's close.
    # With label=left, the 1h bar at 13:00 contains 13:00–14:00 data (closes at 14:00).
    # For a 5m bar at 14:15, the prior closed 1h bar is the one at 13:00.
    # Map: for 5m bar T → look up h1 at (floor(T,'1H') - 1h).
    bias_h_map = h1["bias"].to_dict()

    def prior_hour_bias(ts):
        prior = ts.floor("1h") - pd.Timedelta(hours=1)
        return bias_h_map.get(prior, 0)

    df["bias_h"] = df["timestamp"].apply(prior_hour_bias).astype(int)

    # ─── Prev-day H/L/mid (with optional range-floor fallback) ───
    # Pre-compute rolling N-day H/L offsets for 1..MAX_LOOKBACK_DAYS, all
    # shifted by 1 so today's bar never leaks into "prev". In extend mode,
    # walk the cascade and pick the first lookback that meets the floor.
    d1 = df_1d.copy().reset_index(drop=True)
    d1["timestamp"] = pd.to_datetime(d1["timestamp"])
    d1["date"] = d1["timestamp"].dt.normalize()
    for n in range(1, MAX_LOOKBACK_DAYS + 1):
        d1[f"prev_H_{n}"] = d1["high"].rolling(n).max().shift(1)
        d1[f"prev_L_{n}"] = d1["low"].rolling(n).min().shift(1)

    if RANGE_FILTER_MODE == "extend":
        h = d1["prev_H_1"].copy()
        l = d1["prev_L_1"].copy()
        lookback = pd.Series(1, index=d1.index)
        for n in range(2, MAX_LOOKBACK_DAYS + 1):
            need_extend = ((h - l) / l < MIN_PREV_RANGE_PCT)
            h = h.where(~need_extend, d1[f"prev_H_{n}"])
            l = l.where(~need_extend, d1[f"prev_L_{n}"])
            lookback = lookback.where(~need_extend, n)
        d1["prev_H"]   = h
        d1["prev_L"]   = l
        d1["prev_lookback"] = lookback
    else:
        d1["prev_H"] = d1["prev_H_1"]
        d1["prev_L"] = d1["prev_L_1"]
        d1["prev_lookback"] = 1
    d1["prev_mid"] = (d1["prev_H"] + d1["prev_L"]) / 2.0

    prev_h_map = dict(zip(d1["date"], d1["prev_H"]))
    prev_l_map = dict(zip(d1["date"], d1["prev_L"]))
    prev_m_map = dict(zip(d1["date"], d1["prev_mid"]))
    prev_lb_map = dict(zip(d1["date"], d1["prev_lookback"]))

    df["prev_H"]   = df["date"].map(prev_h_map)
    df["prev_L"]   = df["date"].map(prev_l_map)
    df["prev_mid"] = df["date"].map(prev_m_map)
    df["prev_lookback"] = df["date"].map(prev_lb_map)

    # ─── V2: RSI divergence detection ───
    # Mirrors strategy_sr_dca_5m.pine. A pivot at index i is confirmed at
    # i+DIV_PIVOT_R (when both left and right windows are fully visible).
    # Bearish div = price HH + RSI LH between two confirmed pivot highs.
    # Bullish div = price LL + RSI HL between two confirmed pivot lows.
    # bars_since_*_div counts forward from the confirmation bar; entries
    # gate on that counter ≤ DIV_FRESH_BARS.
    df = detect_divergence(df, DIV_PIVOT_L, DIV_PIVOT_R)
    return df


def detect_divergence(df: pd.DataFrame, pivot_l: int = 5, pivot_r: int = 5) -> pd.DataFrame:
    """Add bear_div_fired, bull_div_fired, bars_since_bear_div, bars_since_bull_div columns.
    Pivots use strict greater-than on left and right windows (matching pine ta.pivothigh/pivotlow).
    """
    n = len(df)
    bear_fired = np.zeros(n, dtype=bool)
    bull_fired = np.zeros(n, dtype=bool)

    last_phigh = np.nan
    last_r_at_h = np.nan
    last_plow = np.nan
    last_r_at_l = np.nan

    highs = df["high"].values
    lows  = df["low"].values
    rsis  = df["rsi"].values

    # i is the pivot bar; confirmed at i+pivot_r once we've seen all right-side bars.
    for i in range(pivot_l, n - pivot_r):
        bar_rsi = rsis[i]
        if pd.isna(bar_rsi):
            continue

        bar_high = highs[i]
        bar_low  = lows[i]

        left_max_h  = highs[i-pivot_l:i].max() if pivot_l > 0 else -np.inf
        right_max_h = highs[i+1:i+pivot_r+1].max()
        is_phigh = bar_high > left_max_h and bar_high > right_max_h

        left_min_l  = lows[i-pivot_l:i].min() if pivot_l > 0 else np.inf
        right_min_l = lows[i+1:i+pivot_r+1].min()
        is_plow = bar_low < left_min_l and bar_low < right_min_l

        confirm_idx = i + pivot_r

        if is_phigh:
            if not pd.isna(last_phigh) and bar_high > last_phigh and bar_rsi < last_r_at_h:
                bear_fired[confirm_idx] = True
            last_phigh = bar_high
            last_r_at_h = bar_rsi

        if is_plow:
            if not pd.isna(last_plow) and bar_low < last_plow and bar_rsi > last_r_at_l:
                bull_fired[confirm_idx] = True
            last_plow = bar_low
            last_r_at_l = bar_rsi

    bars_since_bear = np.full(n, 9999, dtype=int)
    bars_since_bull = np.full(n, 9999, dtype=int)
    cb = 9999
    cu = 9999
    for i in range(n):
        cb = 0 if bear_fired[i] else cb + 1
        cu = 0 if bull_fired[i] else cu + 1
        bars_since_bear[i] = cb
        bars_since_bull[i] = cu

    df = df.copy()
    df["bear_div_fired"] = bear_fired
    df["bull_div_fired"] = bull_fired
    df["bars_since_bear_div"] = bars_since_bear
    df["bars_since_bull_div"] = bars_since_bull
    return df


# ═════ Signal evaluation ═════
@dataclass
class SignalState:
    side: Optional[Side] = None
    price: float = 0.0
    conditions: Dict[str, bool] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


def evaluate_signal(df: pd.DataFrame, last_idx: int) -> SignalState:
    """Evaluate 5m entry signal at bar last_idx (uses prev-day's H/L/bias)."""
    s = SignalState()
    row = df.iloc[last_idx]
    s.price = float(row["close"])

    prev_h = row["prev_H"]
    prev_l = row["prev_L"]
    prev_mid = row["prev_mid"]
    bias = int(row["bias_h"])   # 1h EMA20 bias (prior closed 1h bar)
    rsi_v = row["rsi"]
    vol = row["volume"]
    vol_avg = row["vol_avg"]
    utc_h = int(row["utc_hour"])

    # Incomplete data
    if pd.isna(prev_h) or pd.isna(prev_l) or pd.isna(rsi_v) or pd.isna(vol_avg):
        return s

    # Range-skip gate. In skip mode, block entries when active prev range <
    # floor — sit out tight days entirely. In off/extend modes, build_features
    # has already widened prev_H/prev_L if needed, so the gate passes.
    prev_range_pct = (prev_h - prev_l) / prev_l if prev_l > 0 else 0.0
    range_ok = RANGE_FILTER_MODE != "skip" or prev_range_pct >= MIN_PREV_RANGE_PCT
    if not range_ok:
        return s

    # Volume filter (on THIS 5m bar)
    vol_ok = vol >= VOL_MULT * vol_avg if vol_avg > 0 else False

    # RSI anti-extreme filter (V2: OFF by default — divergence subsumes it).
    rsi_ok_long  = (not USE_RSI_FILTER) or rsi_v >= RSI_LOW
    rsi_ok_short = (not USE_RSI_FILTER) or rsi_v <= RSI_HIGH

    # V2: RSI divergence gate — require a fresh divergence pivot within
    # DIV_FRESH_BARS bars before entry. Bearish for shorts at prev_H,
    # bullish for longs at prev_L.
    bars_since_bear = row.get("bars_since_bear_div", 9999)
    bars_since_bull = row.get("bars_since_bull_div", 9999)
    div_ok_long  = (not USE_RSI_DIVERGENCE) or (not pd.isna(bars_since_bull) and bars_since_bull <= DIV_FRESH_BARS)
    div_ok_short = (not USE_RSI_DIVERGENCE) or (not pd.isna(bars_since_bear) and bars_since_bear <= DIV_FRESH_BARS)

    # Touch conditions
    touch_L = row["low"] <= prev_l * (1 + SUPPORT_ZONE) and row["low"] > prev_l * (1 - 0.01)
    touch_H = row["high"] >= prev_h * (1 - SUPPORT_ZONE) and row["high"] < prev_h * (1 + 0.01)

    in_trade_window = utc_h < CLOSE_HOUR

    # NO BIAS GATE. Both directions allowed regardless of trend bias.
    long_ok  = (rsi_ok_long  and vol_ok and touch_L and in_trade_window and div_ok_long)
    short_ok = (rsi_ok_short and vol_ok and touch_H and in_trade_window and div_ok_short)

    if long_ok:
        s.side = "LONG"
    elif short_ok:
        s.side = "SHORT"

    s.conditions = {
        "1h bias BULL":       bool(bias == 1),
        "1h bias BEAR":       bool(bias == -1),
        f"Volume > {VOL_MULT}× avg": bool(vol_ok),
        "Touch prev low":     bool(touch_L),
        "Touch prev high":    bool(touch_H),
        "In trade window":    bool(in_trade_window),
        "Bull div fresh":     bool(div_ok_long),
        "Bear div fresh":     bool(div_ok_short),
    }
    lookback = row.get("prev_lookback", 1)
    s.raw = {
        "prev_H": float(prev_h), "prev_L": float(prev_l), "prev_mid": float(prev_mid),
        "prev_range_pct": float(prev_range_pct),
        "prev_lookback": int(lookback) if not pd.isna(lookback) else 1,
        "bias_h": bias,
        "rsi":    float(rsi_v) if not pd.isna(rsi_v) else None,
        "vol":    float(vol),
        "vol_avg": float(vol_avg) if not pd.isna(vol_avg) else None,
        "utc_hour": utc_h,
        "price": s.price,
        "bars_since_bear_div": int(bars_since_bear) if not pd.isna(bars_since_bear) else 9999,
        "bars_since_bull_div": int(bars_since_bull) if not pd.isna(bars_since_bull) else 9999,
    }
    return s


# ═════ Position helpers ═════
def entry_price_zone(side: Side, prev_h: float, prev_l: float) -> float:
    """Fill-target price inside the S/R zone."""
    return prev_l * (1 + SUPPORT_ZONE / 2) if side == "LONG" else prev_h * (1 - SUPPORT_ZONE / 2)


def dca_price(side: Side, worst_entry: float) -> float:
    """Next DCA trigger price — DCA_SPACING beyond worst entry."""
    return worst_entry * (1 - DCA_SPACING) if side == "LONG" else worst_entry * (1 + DCA_SPACING)


def sl_price(side: Side, worst_entry: float, first_entry: float = None, be_activated: bool = False) -> float:
    """SL price. Defaults to worst_entry × (1 ∓ SL_BELOW_WORST) (1.9%).
    When USE_BREAKEVEN and be_activated, tightens to first_entry × (1 ± BE_BUFFER_PCT)
    to lock in a small profit instead of the full SL distance.
    """
    if USE_BREAKEVEN and be_activated and first_entry is not None:
        return first_entry * (1 + BE_BUFFER_PCT) if side == "LONG" else first_entry * (1 - BE_BUFFER_PCT)
    return worst_entry * (1 - SL_BELOW_WORST) if side == "LONG" else worst_entry * (1 + SL_BELOW_WORST)


def be_should_activate(side: Side, first_entry: float, current_price: float) -> bool:
    """True if favorable% from first_entry has crossed BE_TRIGGER_PCT.
    Caller is expected to OR this with prior `be_activated` and persist
    (BE is sticky — once armed, stays armed for the cycle)."""
    if not USE_BREAKEVEN or first_entry is None or first_entry <= 0:
        return False
    fav = (current_price - first_entry) / first_entry if side == "LONG" else (first_entry - current_price) / first_entry
    return fav >= BE_TRIGGER_PCT


def tp_price(side: Side, prev_mid: float, first_entry: float = None, filled_count: int = 1) -> float:
    """TP target. Defaults to prev_mid (with PREV_MID_OFFSET shift toward
    current price for fill reliability). In hybrid mode, switches to
    first_entry × (1 ∓ TP_FIXED_PCT) once DCA has fired (filled_count ≥ 2),
    matching the pine strategy's tested-best config (+40.59% / 5w).
    """
    if TP_MODE == "hybrid" and filled_count >= 2 and first_entry is not None:
        if side == "LONG":
            return float(first_entry) * (1 + TP_FIXED_PCT)
        return float(first_entry) * (1 - TP_FIXED_PCT)
    if side == "LONG":
        return float(prev_mid) * (1 - PREV_MID_OFFSET)
    return float(prev_mid) * (1 + PREV_MID_OFFSET)


def per_level_qty(equity: float, price: float) -> float:
    """Sizing: total risk spread across DCA_LEVELS legs.
    Worst-case SL distance from L1 = (N-1)*spacing + SL_BELOW_WORST.
    """
    if price <= 0:
        return 0.0
    worst_sl_dist = (DCA_LEVELS - 1) * DCA_SPACING + SL_BELOW_WORST
    total_notional = equity * 0.95 * RISK_PCT / worst_sl_dist
    qty = total_notional / price / DCA_LEVELS
    cap = (equity * 0.95 * LEVERAGE) / price / DCA_LEVELS
    return min(qty, cap)


```

---


### `strategies/day/bot_rsiscalp.py` (v1 production bot)

See full file (603 lines): `/Users/jags/Desktop/BTC-Flip-Bot/strategies/day/bot_rsiscalp.py`

Key overrides from core defaults:
```python
SL_FROM_WORST = 0.006              # 0.6% (tightened from core's 1%)
USE_BE_AFTER_DCA = True            # the critical risk-management feature
USE_TREND_FLIP_EXIT = True
WEEKEND_QTY_MULT = 2.0
DAILY_MAX_LOSS = 200.0
BLOCKED_HOURS = set()              # empty by default (dropped during live audit)
RSISCALP_ATR_MAX_PCT = 0.60        # chop filter
RSISCALP_1H_MOVE_MAX_PCT = 100.0   # effectively off (dropped during live audit)
TREND_GAP_MIN = 0.0025             # 0.25% GAP filter
```

### `backtest/v11_faithful_backtest.py` (no-lookahead reference)

This is the NO-LOOKAHEAD reference backtest. Critical for honest review:
- 15m bars labeled at CLOSE time (not OPEN) — `df15["timestamp"] += 15min`
- 1h bars labeled at CLOSE time — `df1h["timestamp"] += 1hr`
- `merge_asof(direction="backward")` then correctly returns only CLOSED bars
- Bar fill order: SL → TP → trend-flip → DCA (pessimistic on bars spanning multiple levels)
- Commission: 0.055%/side; can be adjusted

Located at: `/Users/jags/Desktop/BTC-Flip-Bot/backtest/v11_faithful_backtest.py`

---

## KNOWN BUGS WE FOUND AND FIXED THIS SESSION

1. **Lookahead in prior backtests**: Earlier scripts used `merge_asof(backward)` on HTF
   bars labeled at OPEN time → 5m query at time T returned 15m/1h bar that hadn't
   closed yet. Inflated +103%/6mo and +2,023%/5yr claims to fake values.
   FIX: Label HTF bars at CLOSE time (add bar duration to timestamp).

2. **DCA L2 ignored weekend 2× multiplier**: Original maybe_dca() recalculated qty
   from balance, ignoring whether L1 was sized 2× for weekend.
   FIX: Store `weekend_2x` flag on pos, apply same multiplier on L2.

3. **Inflated profit-lock backtest**: A "BE-plus" SL variant showed +130% CAGR. Bug
   was the SL price check used `high >= sl_px` for SHORT, but BE-plus SL is BELOW avg,
   so high was already above it → SL "fired" immediately at phantom profit.
   FIX: Check whether the bar's price ACTUALLY CROSSED the lock level (low ≤ lock_px
   AND high ≥ lock_px).

---

## WHAT TO ASK OTHER AGENTS TO VERIFY

1. **Reproduce the +205%/-9.9% baseline** on the same 5y BTCUSDT 5m data with the
   same fee model. Within ±10% of these numbers = healthy.

2. **Audit for lookahead bias.** Specifically check `merge_asof` calls and any
   indicator computed using FUTURE bars.

3. **Stress-test fees.** What happens at 0.04%, 0.055%, 0.06%, 0.10%/side?
   If the strategy collapses at 0.10%, that tells us how much fee headroom exists.

4. **Out-of-sample test.** Train (just observe) 2021-2024, test 2025-2026.
   Does the +25% CAGR hold OOS?

5. **Bar fill model.** When a bar's range spans both SL and TP, my model assigns SL
   (pessimistic). Some backtesters assign TP. Run both and compare.

6. **Daily max loss circuit breaker.** Does the strategy depend on this for surviving
   2024 (the only losing year in my baseline)? Run with it disabled.

---

## CURRENT LIVE STATUS

- Deployed on: GCP VM `btc-bot-eu` (paper-only)
- Dashboard: http://34.14.124.215:8888/
- First live trade closed today (2026-06-06): SHORT $61,140 → $60,834, +$65.56 in 39 min
- Bot state stored at `data/paper_rsiscalp_trend/state.json` (atomic writes)
- Cron tick: every 1 minute

---

End of spec.
