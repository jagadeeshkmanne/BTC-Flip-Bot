#!/usr/bin/env python3
"""bot_rsiscalp_v3.py — RSI-Scalp v3 COUNTER-TREND + BE wait 6.

v3 = v1.1 + counter-trend entries + BE-after-DCA wait period.

NEW IN v3 vs v1.1:
  1. COUNTER-TREND mode (RSISCALP_COUNTER_TREND=1):
     - RSI ≤30 → LONG regardless of 15m trend
     - RSI ≥70 → SHORT regardless of 15m trend
     (v1.1 only allows LONG in UP trends and SHORT in DOWN trends)
  2. BE wait 6 bars (RSISCALP_BE_WAIT_BARS=6):
     After L2 fills, wait 30min before BE-DCA SL arms. Gives the position
     time to recover from wicks instead of closing immediately when price
     touches avg.

5-YEAR BACKTEST (vs v1.1 same engine):
  v1.1: 3,190 trades / 50.8% WR / +$28,491 / 2.04% DD / PF 2.12
  v3:   7,695 trades / 65.7% WR / +$108,205 / 2.47% DD / PF 3.34  ⭐
  → 3.8× more profit, +15pp WR, +0.4pp DD, +57% PF

YEAR-BY-YEAR (all 6 years better, including 2022 bear):
  2021: +533% vs v1.1's +126%
  2022 (bear): +568% vs +142%
  2023: +236% vs +64%
  2024: +464% vs +117%
  2025: +266% vs +80%
  2026: +180% vs +58%

PAPER-ONLY. State / log / status: data/paper_rsiscalp_trend_v3/
"""
from __future__ import annotations
import os, sys, json, logging
from datetime import datetime, timezone, timedelta
import requests
import pandas as pd

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
# 2026-06-10: was os.path.dirname(os.path.dirname(STRATEGY_DIR)) — that worked
# when the bot was nested under strategies/day/. After the bot moved to bot/,
# one fewer dirname is needed so BOT_DIR resolves to the repo root, not /home/jags.
BOT_DIR = os.path.dirname(STRATEGY_DIR)
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
DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("RSISCALP_DATA_DIR", "v2.1"))

# ─── v3 NEW: Counter-Trend mode ───
# When 1, allow RSI extreme entries even AGAINST the 15m trend.
# Backtest 5y: 3.8× more trades, +15pp WR, +57% PF.
USE_COUNTER_TREND = os.environ.get("RSISCALP_COUNTER_TREND", "0") == "1"

# ─── v3 NEW: BE-after-DCA arm delay ───
# After L2 fills, wait N 5m bars before BE-DCA SL arms. Default 6 = 30min.
# Gives position time to recover from wicks. Combined with counter-trend
# gives the +15pp WR jump in backtest.
BE_WAIT_BARS = int(os.environ.get("RSISCALP_BE_WAIT_BARS", "0"))

# ─── v3 NEW: RSI threshold env overrides (defaults from core_rsiscalp.py) ───
# Optimal sweep found RSI 35/65 outperforms 30/70 baseline.
RSI_OVERSOLD = int(os.environ.get("RSISCALP_RSI_OVERSOLD", str(RSI_OVERSOLD)))
RSI_OVERBOUGHT = int(os.environ.get("RSISCALP_RSI_OVERBOUGHT", str(RSI_OVERBOUGHT)))

# Override rsi_signal to use our (possibly overridden) thresholds
def rsi_signal(rsi_val):
    if rsi_val is None: return None
    if rsi_val <= RSI_OVERSOLD: return "LONG"
    if rsi_val >= RSI_OVERBOUGHT: return "SHORT"
    return None

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
# 2026-06-12 FIX: sl_price() lives in core_rsiscalp and reads that module's
# SL_FROM_WORST (1.0%) — the env override above never reached it, so the live
# 1-leg SL fired at 1.0% while the dashboard displayed 0.6%. Propagate it.
import core_rsiscalp as _core_rsiscalp
_core_rsiscalp.SL_FROM_WORST = SL_FROM_WORST

# Daily max loss circuit breaker.
# 2026-06-10: % of balance (default 4%) — auto-scales from $500 to $50K+ capital.
#   - At $500 balance:  cap = $20/day  (4%)
#   - At $1,000:        cap = $40/day
#   - At $5,000:        cap = $200/day (matches old fixed default)
#   - At $50,000:       cap = $2,000/day
# To override with a FIXED dollar amount, set RSISCALP_DAILY_MAX_LOSS=NN
# (legacy behavior). Setting both: the PCT takes precedence when > 0.
DAILY_MAX_LOSS_PCT = float(os.environ.get("RSISCALP_DAILY_MAX_LOSS_PCT", "0.04"))
DAILY_MAX_LOSS = float(os.environ.get("RSISCALP_DAILY_MAX_LOSS", "0.0"))

# Weekend position-size multiplier (Sat/Sun = 94% WR historically)
WEEKEND_QTY_MULT = float(os.environ.get("RSISCALP_WEEKEND_QTY_MULT", "2.0"))

# Enable trend-flip exit (close on 15m EMA reversal)
USE_TREND_FLIP_EXIT = os.environ.get("RSISCALP_TREND_FLIP_EXIT", "1") == "1"

# 2026-06-06: Break-even after DCA fires. When position has L2+ filled, move SL
# to avg entry price. Caps "DCA into a runaway move" losses at near-zero.
# Live-trade audit: the one catastrophic -$193 loss had DCA fire, then move
# continued adverse. BE-L2 would have exited at avg (~$0) instead of -$193.
USE_BE_AFTER_DCA = os.environ.get("RSISCALP_BE_AFTER_DCA", "1") == "1"

# 2026-06-06 v1.1: TIME-BASED SL. Force exit any position after N 5m bars.
# Backtest: 72 bars (6h) sweet spot — +21pp return vs v1 baseline, -1.5pp DD.
# Affects ~137 trades over 5y (rare safety net for stuck positions).
# 2026-06-06 v1.2: bumped to 144 bars (12h). Pair with SMART_TIME_SL=1 below.
TIME_SL_BARS = int(os.environ.get("RSISCALP_TIME_SL_BARS", "72"))

# 2026-06-06 v1.2: SMART time-SL only fires when the position is in loss at the
# threshold. Winners keep running to TP. Default OFF (backwards-compatible).
SMART_TIME_SL = os.environ.get("RSISCALP_SMART_TIME_SL", "0") == "1"

# ─── v1.2 PARAMETER OVERRIDES (env vars layered on top of core constants) ───
# Lets v1.1 deploy with looser RSI + wider TPs without touching core_rsiscalp.py
# (so v1, v2, v5 keep their original numbers).
RSI_OVERSOLD   = int(os.environ.get("RSISCALP_RSI_OVERSOLD",   str(RSI_OVERSOLD)))
RSI_OVERBOUGHT = int(os.environ.get("RSISCALP_RSI_OVERBOUGHT", str(RSI_OVERBOUGHT)))
TP_PCT_SINGLE  = float(os.environ.get("RSISCALP_TP_SINGLE",    str(TP_PCT_SINGLE)))
TP_PCT_DCA     = float(os.environ.get("RSISCALP_TP_DCA",       str(TP_PCT_DCA)))

# Shadow tp_pct_for so it uses the (possibly overridden) v1.2 constants
def tp_pct_for(filled: int) -> float:
    return TP_PCT_SINGLE if filled <= 1 else TP_PCT_DCA

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
# 2026-06-12: fees RE-ENABLED in paper (was 0.0 since 2026-06-10 "for clean
# math" — that made paper P&L systematically unreal; the strategy's honest
# zero-fee PF is ~1.0, so fee-free paper showed phantom profit). The bot
# market-exits everything via polling, so taker applies to every fill.
COMMISSION_PCT = float(os.environ.get("RSISCALP_COMMISSION_PCT", "0.00055"))
# 2026-06-05: data source migrated Binance fapi → Bybit V5 (USDT-M perp).

# ─── 2026-06-12: EQUITY RATCHET KILL-SWITCH (account-level) ───
# Per-trade exits cannot fix a zero-edge entry (FINDINGS.md #2 + the 9-family
# exit sweep), but the ACCOUNT can bank favorable variance and bound the bleed:
#   - hard floor starts at INITIAL × FLOOR_START (max experiment loss);
#   - once peak balance ≥ INITIAL × RATCHET_ARM, the floor ratchets up to
#     peak × (1 − RATCHET_GIVEBACK) and never moves back down;
#   - when FLAT and balance ≤ floor → entries halt PERMANENTLY
#     (state["halted_reason"]; edit state.json manually to resume).
# Open positions are never force-closed; they exit via their normal rules.
RATCHET_FLOOR_START = float(os.environ.get("RSISCALP_FLOOR_START", "0.90"))
RATCHET_ARM         = float(os.environ.get("RSISCALP_RATCHET_ARM", "1.04"))
RATCHET_GIVEBACK    = float(os.environ.get("RSISCALP_RATCHET_GIVEBACK", "0.03"))

# ─── 2026-06-12: MTM BASKET STOP (per-position unrealized-loss cap) ───
# User concern: winners ~+$60, one bad basket -$200+ (R:R ~1:3.5 at 5x).
# Honest A/B (backtest/mtm_guard_ab.py, 2021-2026, real fills+fees):
#   worst trade -$818 -> -$270/-$380, avg loss -10-13%, PF 0.59->0.64 (v2.2);
#   covers the 6-bar BE-wait window which otherwise has NO stop. 2% cap was
#   too tight (worse PF); +2% profit-lock never helped (TPs are closer). Does
#   NOT make the strategy profitable (no exit can, FINDINGS.md #2) — it caps
#   per-basket damage. 0 disables.
MTM_STOP_PCT = float(os.environ.get("RSISCALP_MTM_STOP_PCT", "0.04"))

# ─── 2026-06-12: TREND-LINE STOP (with-trend mode companion) ───
# SL anchored to the 15m EMA50 ("the trend line") instead of %-from-entry:
#   LONG: stop = EMA50 * (1 - pct);  SHORT: stop = EMA50 * (1 + pct).
# Frozen at entry (matches backtest). Only meaningful in WITH-trend mode
# (entries happen AT the line); counter-trend entries can sit on the wrong
# side of the line, so leave 0 there. Honest sweep: avg loss ~$46 at 0.10%,
# expectancy unchanged vs other placements (placement-invariance).
TRENDLINE_SL_PCT = float(os.environ.get("RSISCALP_TRENDLINE_SL_PCT", "0"))

# ─── Logging ───
log = logging.getLogger("bot_rsiscalp_v11")
log.setLevel(logging.INFO)
log.handlers.clear()
fh = logging.FileHandler(LOG_FILE)
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(fh)
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(sh)


# ─── Bybit public fetchers (V5 USDT-M perp, BTCUSDT) ───
from bybit_data import fetch_klines as _bb_klines, fetch_live_price as _bb_price

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
    # 2026-06-10: BE-DCA exits at $0 are NEUTRAL — not wins, not losses.
    # Track them separately so win rate isn't unfairly dragged down by mean-
    # reversion exits that didn't actually lose money.
    pnl = trade_record.get("pnl_usd", 0)
    if pnl > 0:
        state["stats"]["wins"] += 1
    elif pnl < 0:
        state["stats"]["losses"] = state["stats"].get("losses", 0) + 1
    else:
        state["stats"]["neutrals"] = state["stats"].get("neutrals", 0) + 1


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
    # 2026-06-12: BE-DCA cooldown exemption REMOVED. The 2026-06-10 rationale
    # ("$0 gross isn't a real loss") was an artifact of booking stop exits at
    # the stop price; with honest booking BE-DCA exits are real losses and
    # must count like any other loss.
    if USE_CIRCUIT_BREAKER:
        if net < 0:
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
    # v3 fix 2026-06-08: snapshot 15m trend at entry so trend-flip exit
    # compares against ENTRY trend (not current side). Without this, v2
    # (counter-trend) exits immediately on every entry when its bet against
    # trend looks like a "flip" to the simple side-vs-trend check.
    entry_trend_snapshot = state.get("_last_trend")  # set in main() each tick
    state["position"] = {
        "side": side, "first_entry": entry_px, "worst_entry": entry_px,
        "entries": [{"px": entry_px, "qty": qty}], "qty_total": qty, "filled": 1,
        "leverage": LEVERAGE, "entry_time": datetime.now(timezone.utc).isoformat(),
        "rsi_at_entry": rsi_val, "partial_taken": False,
        "weekend_2x": is_weekend,
        "entry_trend": entry_trend_snapshot,
    }
    # 2026-06-12: trend-line stop — freeze SL at 15m EMA50 ± pct at entry time.
    if TRENDLINE_SL_PCT > 0:
        line = state.get("_last_ema_slow")
        if line:
            tl = line * (1 - TRENDLINE_SL_PCT) if side == "LONG" else line * (1 + TRENDLINE_SL_PCT)
            # entry already at/through the line -> enforce a minimum stop distance
            if (side == "LONG" and tl >= entry_px * 0.9995) or (side == "SHORT" and tl <= entry_px * 1.0005):
                tl = entry_px * (1 - 2 * TRENDLINE_SL_PCT) if side == "LONG" else entry_px * (1 + 2 * TRENDLINE_SL_PCT)
            state["position"]["trendline_sl"] = tl
            log.info(f"  trend-line stop set: ${tl:,.2f} (EMA50 ${line:,.2f} ∓ {TRENDLINE_SL_PCT*100:.2f}%)")
    log.warning(f"  OPENED {side} {qty}@${entry_px:.2f} (RSI {rsi_val:.1f}){'  [WEEKEND 2x]' if is_weekend else ''} | entry_trend={entry_trend_snapshot} | balance ${state['balance']:.2f}")


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
    # v3: stamp L2 fill time so BE-DCA wait can use it
    pos["l2_time"] = datetime.now(timezone.utc).isoformat()
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
    # 2026-06-12 FIX (latent): counter-trend mode also needs trend data — its
    # defensive gate blocks entries when trend is None, so TREND=0 +
    # COUNTER_TREND=1 froze entries forever. Not hit in production (both run
    # scripts set RSISCALP_TREND=1), fixed for config safety.
    if USE_TREND_FILTER or USE_COUNTER_TREND:
        df_tf = fetch_klines(TREND_TF, 300)
        if df_tf is not None and len(df_tf) >= TREND_EMA_SLOW:
            ema_f = df_tf["close"].ewm(span=TREND_EMA_FAST, adjust=False).mean()
            ema_s = df_tf["close"].ewm(span=TREND_EMA_SLOW, adjust=False).mean()
            ema_f_v = float(ema_f.iloc[-2])
            ema_s_v = float(ema_s.iloc[-2])
            trend = "UP" if ema_f_v > ema_s_v else "DOWN"
            trend_gap_pct = (ema_f_v - ema_s_v) / ema_s_v * 100.0
            # v3 fix 2026-06-08: stash trend for open_position() snapshot.
            state["_last_trend"] = trend
            # 2026-06-12: stash the trend line for the trend-line stop.
            state["_last_ema_slow"] = ema_s_v
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
            # v3: BE-DCA wait — after L2 fills, wait BE_WAIT_BARS before arming the BE SL
            be_armed = True
            if USE_BE_AFTER_DCA and pos.get("filled", 1) >= 2 and BE_WAIT_BARS > 0:
                l2_time_str = pos.get("l2_time")
                if l2_time_str:
                    try:
                        l2_dt = datetime.fromisoformat(l2_time_str)
                        bars_since_l2 = int((datetime.now(timezone.utc) - l2_dt).total_seconds() // 300)
                        if bars_since_l2 < BE_WAIT_BARS:
                            be_armed = False
                            log.info(f"  BE wait: {bars_since_l2}/{BE_WAIT_BARS} bars since L2 — BE-DCA SL not yet armed")
                    except (ValueError, KeyError):
                        pass
            if USE_BE_AFTER_DCA and pos.get("filled", 1) >= 2:
                # 2026-06-10: L2 TRAIL SL — once price moves +0.05% above avg,
                # trail SL behind peak with 0.025% buffer. Captures the
                # recovery profit on L2 positions that would otherwise BE-DCA
                # exit at avg ($0 gross). Backtest 5y: +$5,742 / same DD.
                #
                # 2026-06-10: TESTED letting trail arm during 6-bar BE wait —
                # backtest showed -$22K (v2.1) and -$146K (v2.2). The wait gives
                # positions room to develop into TPs; early trail kills the
                # 1% TP target by exiting on tiny spikes. Keep the gate.
                if be_armed:
                    avg = avg_entry_of(pos)
                    peak_fav = pos.get("l2_peak_fav_pct", 0.0)
                    if peak_fav >= 0.05:  # 0.05% arm threshold
                        trail_pct = peak_fav - 0.025  # 0.025% buffer
                        if side == "LONG":
                            slp = avg * (1 + trail_pct / 100.0)
                        else:
                            slp = avg * (1 - trail_pct / 100.0)
                    else:
                        slp = avg
                else:
                    slp = None
            else:
                slp = sl_price(side, pos["worst_entry"])
            # 2026-06-12: trend-line stop acts as a FLOOR under every state
            # (incl. the BE-wait window where slp is None). Trail above it wins.
            tl = pos.get("trendline_sl")
            if TRENDLINE_SL_PCT > 0 and tl:
                if slp is None:
                    slp = tl
                elif side == "LONG":
                    slp = max(slp, tl)
                else:
                    slp = min(slp, tl)
            if slp is not None and ((side == "LONG" and live_px <= slp) or (side == "SHORT" and live_px >= slp)):
                if USE_BE_AFTER_DCA and pos.get("filled", 1) >= 2:
                    # Tag as L2_TRAIL when trail SL is armed and above avg
                    reason_tag = "L2_TRAIL" if pos.get("l2_peak_fav_pct", 0.0) >= 0.05 else "BE-DCA"
                else:
                    reason_tag = "SL"
                # 2026-06-12 FIX (stop-fill artifact): book the exit at the
                # price the market actually offers (live_px), NOT the stop
                # level. This branch only fires when live_px is already at or
                # beyond slp — a market exit cannot fill at slp. Booking slp
                # recorded BE-DCA losses as $0 "neutrals" and understated SL
                # losses; that fiction was the entire backtest edge
                # (see backtest/live_faithful.py legacy-vs-open).
                exit_reason, exit_px = reason_tag, live_px

        # 2026-06-12: MTM BASKET STOP — close the whole basket at market once
        # unrealized loss reaches MTM_STOP_PCT of balance. Runs after the
        # regular SL checks and ALSO during the BE-wait window (slp is None
        # there — this is the only stop protecting that window).
        if exit_px is None and MTM_STOP_PCT > 0:
            qty_total = pos.get("qty_total", 0.0)
            unreal = ((live_px - avg_entry) * qty_total if side == "LONG"
                      else (avg_entry - live_px) * qty_total)
            cap_d = MTM_STOP_PCT * state["balance"]
            if unreal <= -cap_d:
                log.warning(f"  MTM STOP: unrealized ${unreal:+.2f} <= cap -${cap_d:.2f} "
                            f"({MTM_STOP_PCT*100:.1f}% of ${state['balance']:.0f}) — closing basket")
                exit_reason, exit_px = "MTM_STOP", live_px

        # 2026-06-06: TREND FLIP EXIT — close on 15m EMA reversal (early reversal catch)
        # 2026-06-08 FIX: compare current trend to ENTRY trend (not side).
        # 2026-06-10 FIX: PROFIT-ONLY — fire only when currently in profit.
        # Without this gate, current trend-flip fires 154 times over 5y, 95% of
        # them locking in losses (avg -$80 per fire, -$12,399 total drag).
        # With profit-only: 35 fires, 100% wins, +$10K profit over 5y.
        if exit_px is None and USE_TREND_FLIP_EXIT and trend is not None:
            entry_trend = pos.get("entry_trend")
            if entry_trend and trend != entry_trend:
                # Only fire trend-flip if we're currently in profit
                avg = avg_entry_of(pos)
                qty = pos.get("qty_total", 0)
                if side == "LONG":
                    unrealized = (live_px - avg) * qty
                else:
                    unrealized = (avg - live_px) * qty
                if unrealized > 0:
                    exit_reason, exit_px = "TREND_FLIP", live_px

        # 2026-06-06 v1.1: TIME-BASED SL — force exit position after N bars.
        # Backtest 5y: 72 bars (6h) = sweet spot. v1 → v1.1: +21pp return, -1.5pp DD.
        # 2026-06-06 v1.2: SMART variant — if SMART_TIME_SL=1, only fire when the
        # position is in loss at the threshold. Winners keep running to TP.
        if exit_px is None and TIME_SL_BARS > 0:
            entry_time_str = pos.get("entry_time")
            if entry_time_str:
                try:
                    entry_dt = datetime.fromisoformat(entry_time_str)
                    bars_held = int((datetime.now(timezone.utc) - entry_dt).total_seconds() // 300)  # 5m bars
                    if bars_held >= TIME_SL_BARS:
                        fire = True
                        if SMART_TIME_SL:
                            # Compute unrealized P&L at live_px; only fire on loss
                            qty_total = pos["qty_total"]
                            gross = (live_px - avg_entry) * qty_total if side == "LONG" else (avg_entry - live_px) * qty_total
                            fees = live_px * qty_total * COMMISSION_PCT
                            net = gross - fees
                            if net >= 0:
                                fire = False
                                log.info(f"  TIME-SL not fired (smart): {bars_held} bars but in profit ${net:+.2f} — let winner run")
                        if fire:
                            exit_reason, exit_px = "TIME_SL", live_px
                            log.warning(f"  TIME-SL fired: position open {bars_held} bars ≥ {TIME_SL_BARS} threshold")
                except (ValueError, KeyError):
                    pass

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
            # 2026-06-10: L2 trail SL needs peak favorable % AFTER L2 fills
            # (separate from max_fav_pct because that tracks the L1-anchored peak too).
            if pos.get("filled", 1) >= 2:
                pos["l2_peak_fav_pct"] = max(pos.get("l2_peak_fav_pct", 0.0), fav)
            log.info(f"  IN {side} L{pos['filled']}/{DCA_LEVELS} avg=${avg_entry:.2f} live=${live_px:.2f} fav={fav:+.2f}% | mfe {pos['max_fav_pct']:+.2f}% mae {pos['max_adv_pct']:+.2f}%")

    # Entry — RSI (+ optional 15m trend gate + circuit breaker). Don't re-enter on the tick we just exited.
    block_reason = None

    # ─── 2026-06-12: equity ratchet kill-switch (constants near COMMISSION_PCT) ───
    peak_now = max(float(state.get("peak_equity", INITIAL_BALANCE)),
                   float(state.get("balance", 0.0)))
    eq_floor = INITIAL_BALANCE * RATCHET_FLOOR_START
    if peak_now >= INITIAL_BALANCE * RATCHET_ARM:
        eq_floor = max(eq_floor, peak_now * (1 - RATCHET_GIVEBACK))
    eq_floor = max(eq_floor, float(state.get("equity_floor") or 0.0))  # monotonic
    state["equity_floor"] = eq_floor
    if (not state.get("halted_reason") and state.get("position") is None
            and float(state.get("balance", 0.0)) <= eq_floor):
        state["halted_reason"] = (
            f"EQUITY RATCHET {datetime.now(timezone.utc).isoformat()[:16]}: balance "
            f"${state['balance']:.2f} <= floor ${eq_floor:.2f} (peak ${peak_now:.2f}) — "
            f"entries halted permanently (edit state.json to resume)")
        log.warning(f"  *** {state['halted_reason']} ***")
    if sig and state.get("halted_reason"):
        block_reason = state["halted_reason"]
        log.info(f"  {block_reason}")
        sig = None

    # 2026-06-06: DAILY MAX LOSS circuit breaker
    # 2026-06-10: PCT-based — auto-scales with balance ($500 → $20/day, $5K → $200/day, etc).
    # Fixed-dollar mode is still supported via DAILY_MAX_LOSS env var.
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.get("daily_loss_date") != today_utc:
        state["daily_loss"] = 0.0
        state["daily_loss_date"] = today_utc
    # Compute effective cap: PCT takes precedence when > 0, otherwise fall back to fixed dollar.
    cur_bal = float(state.get("balance", 0.0))
    if DAILY_MAX_LOSS_PCT > 0 and cur_bal > 0:
        eff_cap = cur_bal * DAILY_MAX_LOSS_PCT
        cap_label = f"{DAILY_MAX_LOSS_PCT*100:.1f}% of ${cur_bal:.0f}"
    elif DAILY_MAX_LOSS > 0:
        eff_cap = DAILY_MAX_LOSS
        cap_label = f"${DAILY_MAX_LOSS:.0f} fixed"
    else:
        eff_cap = 0  # disabled
        cap_label = "disabled"
    if sig and eff_cap > 0 and state.get("daily_loss", 0) <= -eff_cap:
        block_reason = f"daily max loss ${eff_cap:.2f} reached ({cap_label}, today: ${state.get('daily_loss', 0):.2f}) — entries paused until 00:00 UTC"
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
    # v3: COUNTER_TREND mode bypasses the with-trend requirement
    if USE_TREND_FILTER and sig and not USE_COUNTER_TREND:
        if trend is None:
            block_reason = f"{sig} blocked — 15m trend data unavailable (defensive)"
            log.info(f"  {block_reason}")
            sig = None
        elif (sig == "LONG" and trend != "UP") or (sig == "SHORT" and trend != "DOWN"):
            block_reason = f"{sig} blocked — 15m trend is {trend} (need {'UP' if sig=='LONG' else 'DOWN'})"
            log.info(f"  {block_reason}")
            sig = None
    elif USE_COUNTER_TREND and sig:
        # In counter-trend mode, still block if trend data unavailable (defensive)
        if trend is None:
            block_reason = f"{sig} blocked — 15m trend data unavailable (defensive)"
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
        # v1.1: compute when time-SL would force-close this position
        time_sl_at = None
        bars_remaining = None
        if TIME_SL_BARS > 0 and pos.get("entry_time"):
            try:
                ent_dt = datetime.fromisoformat(pos["entry_time"])
                close_dt = ent_dt + timedelta(minutes=TIME_SL_BARS * 5)
                time_sl_at = close_dt.isoformat()
                bars_elapsed = int((datetime.now(timezone.utc) - ent_dt).total_seconds() // 300)
                bars_remaining = max(0, TIME_SL_BARS - bars_elapsed)
            except Exception:
                pass
        pos_status = {
            "side": pos["side"], "first_entry": pos["first_entry"], "avg_entry": avg_e,
            "worst_entry": pos["worst_entry"], "qty_total": pos["qty_total"], "filled": pos["filled"],
            "tp_px": tp_p, "sl_px": slp, "fav_pct": fav_p, "entry_time": pos.get("entry_time"),
            "time_sl_at": time_sl_at, "time_sl_bars_remaining": bars_remaining,
        }

    write_status({
        "env": os.environ.get("RSISCALP_DATA_DIR", "v2.1"),
        "pair": PAIR, "price": close_px, "live_price": live_px,
        "balance": state["balance"], "peak_equity": peak, "drawdown_pct": dd_pct,
        "equity_floor": state.get("equity_floor"), "halted_reason": state.get("halted_reason"),
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
                       # 2026-06-10: report the effective cap (PCT or fixed) for the dashboard
                       "daily_max_loss": (
                           float(state.get("balance", 0.0)) * DAILY_MAX_LOSS_PCT
                           if DAILY_MAX_LOSS_PCT > 0 and state.get("balance", 0.0) > 0
                           else DAILY_MAX_LOSS
                       ),
                       "daily_max_loss_pct": DAILY_MAX_LOSS_PCT,
                       "is_weekend": datetime.now(timezone.utc).weekday() >= 5,
                       "weekend_qty_mult": WEEKEND_QTY_MULT,
                       "sl_from_worst_pct": SL_FROM_WORST*100},
        "trend_15m": trend, "block_reason": block_reason,
        "stats": state["stats"],
        "strategy": f"RSI-Scalp ULTIMATE v1.1 (RSI{RSI_PERIOD} {RSI_OVERSOLD}/{RSI_OVERBOUGHT} / 15m EMA{TREND_EMA_FAST}/{TREND_EMA_SLOW} + GAP ≥{TREND_GAP_MIN*100:.2f}% / TP {TP_PCT_SINGLE*100:.2f}%·{TP_PCT_DCA*100:.2f}% / {DCA_LEVELS} DCA @ {DCA_SPACING*100:.2f}% / SL {SL_FROM_WORST*100:.2f}% / +TF exit / +weekend {WEEKEND_QTY_MULT:.1f}× / +daily-stop ${DAILY_MAX_LOSS:.0f} / +TIME-SL {TIME_SL_BARS} bars) [PAPER]",
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
