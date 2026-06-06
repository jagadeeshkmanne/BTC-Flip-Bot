#!/usr/bin/env python3
"""bot_rsiscalp_v5.py — RSI-Scalp +Trend v5 (v2 entries + NO DCA + tight SL).

CLONE of v2 with v4-style risk management. Best of both worlds:
  - v2's proven entry filter (GAP firmness ≥ 0.25%) for high WR
  - v4's bounded loss (no DCA + 0.5% SL from entry)

CHANGES FROM v2:
  - DCA_LEVELS = 1               (no DCA — single entry only)
  - SL = 0.5% from entry         (was 1% from worst-fill in v2)
  - Max loss capped at ~$75      (vs v2's $180 post-DCA SL)
  - Better R:R                   (avg win:loss ~1:1.2)
  
ENTRY FILTERS — IDENTICAL to v2 (all the proven filters):
  - RSI(9) ≤30/≥70 + 15m EMA20/EMA50 trend gate
  - GAP firmness ≥0.25%          ⭐ key filter (backtest: best in fleet)
  - Hour 12-13 UTC blocked
  - ATR/1h fleet-wide filters

NOT INCLUDED (intentionally):
  - Anti-breakout filter (v3/v4 have it — backtest shows marginal value)

6-MONTH BACKTEST RESULTS (vs other bots):
  v2:  +51.68% (DCA design, highest return but $180 max loss)
  v5:  +23.52% (no-DCA design, similar to v4, smoother equity)
  v4:  +22.46% (v3 entries + no-DCA, similar to v5)
  v1:  +32.54% (high DD -28%)
  v3:  +27.13% (over-filtered)

PAPER-ONLY. State / log / status: data/paper_rsiscalp_trend_v5/


Sibling to bot_rsiscalp.py (v1) — does NOT modify v1. Adds ONE entry filter:
  TREND-GAP FIRMNESS: only enter when 15m |EMA20-EMA50|/EMA50 >= TREND_GAP_MIN
  (skips entries when trend is knife-edge / about to flip)

Backtest evidence (29-mo OOS + walk-forward train 2024 / test 2025-26):
  Baseline (no gap filter): -75% / 29mo, PF 1.05
  GAP >= 0.25%:            +44% OOS (2025-26), PF 1.33, MaxDD 21%
  GAP >= 0.40%:            +77% OOS (2025-26), PF 1.62, MaxDD 14%

Default threshold: 0.25% (balanced). Override via RSISCALP_V2_GAP_MIN.

PAPER-ONLY. State / log / status: data/paper_rsiscalp_trend_v2/
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
    LEVERAGE, DCA_LEVELS as _CORE_DCA_LEVELS, DCA_SPACING,
    RSI_PERIOD, RSI_OVERSOLD, RSI_OVERBOUGHT,
    USE_TAKE_PROFIT, TP_PCT_SINGLE, TP_PCT_DCA, tp_pct_for,
    USE_STOP_LOSS, SL_FROM_WORST as _CORE_SL_FROM_WORST,
    USE_TREND_FILTER, TREND_TF, TREND_EMA_FAST, TREND_EMA_SLOW,
    USE_CIRCUIT_BREAKER, BREAKER_LOSSES, BREAKER_PAUSE_HOURS,
    rsi_signal, dca_price, sl_price, per_level_qty,
)
# v5 OVERRIDES — same risk-mgmt as v4 (no DCA + tight SL)
DCA_LEVELS    = 1       # single entry only, no averaging-down
SL_FROM_WORST = 0.005   # 0.5% from entry (= worst, since no DCA)

# Local helpers using OUR overridden constants
def per_level_qty(equity: float, price: float) -> float:
    """v5 sizing — full notional in L1 since DCA_LEVELS=1."""
    if price <= 0: return 0.0
    total = (equity * 0.95 * LEVERAGE) / price
    return total / DCA_LEVELS

def sl_price(side: str, worst_entry: float):
    """v5 SL — 0.5% from entry."""
    if not USE_STOP_LOSS: return None
    return worst_entry * (1 - SL_FROM_WORST) if side == "LONG" else worst_entry * (1 + SL_FROM_WORST)

# ─── Paths ───
DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("RSISCALP_DATA_DIR", "paper_rsiscalp_trend_v5"))
TREND_GAP_MIN = float(os.environ.get("RSISCALP_V2_GAP_MIN", "0.0025"))
# Fleet-wide chop/momentum filters (per-bot env override)
RSISCALP_ATR_MAX_PCT     = float(os.environ.get("RSISCALP_ATR_MAX_PCT", "0.60"))
RSISCALP_1H_MOVE_MAX_PCT = float(os.environ.get("RSISCALP_1H_MOVE_MAX_PCT", "2.0"))
# Fleet-wide high-vol UTC hours blocked (default 12,13 = US pre-market)
BLOCKED_HOURS = set(int(h.strip()) for h in
    os.environ.get("RSISCALP_BLOCKED_HOURS", "12,13").split(",")
    if h.strip().isdigit())
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
log = logging.getLogger("bot_rsiscalp_v5")
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
    qty = round(per_level_qty(state["balance"], entry_px), 3)  # BTCUSDT step 0.001
    if qty <= 0:
        log.warning(f"  qty {qty} too small to open")
        return
    state["balance"] -= entry_px * qty * COMMISSION_PCT
    state["position"] = {
        "side": side, "first_entry": entry_px, "worst_entry": entry_px,
        "entries": [{"px": entry_px, "qty": qty}], "qty_total": qty, "filled": 1,
        "leverage": LEVERAGE, "entry_time": datetime.now(timezone.utc).isoformat(),
        "rsi_at_entry": rsi_val, "partial_taken": False,
    }
    log.warning(f"  OPENED {side} {qty}@${entry_px:.2f} (RSI {rsi_val:.1f}) | balance ${state['balance']:.2f}")


def maybe_dca(pos, live_px: float, balance: float, state) -> bool:
    """Equal-size DCA leg at fixed adverse spacing, up to DCA_LEVELS total."""
    if pos["filled"] >= DCA_LEVELS:
        return False
    side = pos["side"]
    trigger = dca_price(side, pos["worst_entry"])
    crossed = (side == "LONG" and live_px <= trigger) or (side == "SHORT" and live_px >= trigger)
    if not crossed:
        return False
    qty = round(per_level_qty(balance, trigger), 3)
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
        if exit_px is None and USE_STOP_LOSS:
            slp = sl_price(side, pos["worst_entry"])
            if slp is not None and ((side == "LONG" and live_px <= slp) or (side == "SHORT" and live_px >= slp)):
                exit_reason, exit_px = "SL", slp

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
        "env": os.environ.get("RSISCALP_DATA_DIR", "paper_rsiscalp_trend_v5"),
        "pair": PAIR, "price": close_px, "live_price": live_px,
        "balance": state["balance"], "peak_equity": peak, "drawdown_pct": dd_pct,
        "position": pos_status, "signal": sig,
        "indicators": {"rsi": rsi_val, "rsi_oversold": RSI_OVERSOLD, "rsi_overbought": RSI_OVERBOUGHT,
                       "price": close_px, "trend_gap_pct": trend_gap_pct, "trend_gap_min_pct": TREND_GAP_MIN*100,
                       "blocked_hours": sorted(BLOCKED_HOURS) if BLOCKED_HOURS else [],
                       "current_hour_utc": datetime.now(timezone.utc).hour},
        "trend_15m": trend, "block_reason": block_reason,
        "stats": state["stats"],
        "strategy": f"RSI-Scalp +Trend v5 NO-DCA TIGHT-SL (RSI{RSI_PERIOD} {RSI_OVERSOLD}/{RSI_OVERBOUGHT} / 15m EMA{TREND_EMA_FAST}/{TREND_EMA_SLOW} gate + GAP firmness ≥{TREND_GAP_MIN*100:.2f}% / TP {TP_PCT_SINGLE*100:.2f}%·{TP_PCT_DCA*100:.2f}% adaptive / {DCA_LEVELS} DCA @ {DCA_SPACING*100:.2f}% / {'SL '+format(SL_FROM_WORST*100,'.1f')+'%' if USE_STOP_LOSS else 'NO SL'}) [PAPER]",
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
