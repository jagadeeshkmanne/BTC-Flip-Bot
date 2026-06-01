#!/usr/bin/env python3
"""bot_rsiscalp.py — Paper bot for the pure-RSI mean-reversion scalper.

Runs every 1 min via cron. Reads BTCUSDT mainnet 5m klines, computes RSI,
enters on RSI extremes, scales out in the 0.25%–0.50% band. NO other logic.

PAPER-ONLY — no live orders, no API keys (public Binance endpoints only).
State / log / status: data/paper_rsiscalp/  (override via RSISCALP_DATA_DIR)
"""
from __future__ import annotations
import os, sys, json, logging
from datetime import datetime, timezone
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
    USE_STOP_LOSS, SL_FROM_WORST,
    rsi_signal, dca_price, sl_price, per_level_qty,
)

# ─── Paths ───
DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("RSISCALP_DATA_DIR", "paper_rsiscalp"))
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE  = os.path.join(DATA_DIR, "state.json")
STATUS_FILE = os.path.join(DATA_DIR, "status.json")
LOG_FILE    = os.path.join(DATA_DIR, "bot.log")

# ─── Config ───
PAIR = "BTCUSDT"
INITIAL_BALANCE = 5000.0
COMMISSION_PCT = 0.0004  # 0.04% taker fee per side
BINANCE_BASE = "https://fapi.binance.com"

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


# ─── Binance public fetchers ───
def fetch_klines(interval: str, limit: int = 500) -> pd.DataFrame | None:
    try:
        r = requests.get(f"{BINANCE_BASE}/fapi/v1/klines",
                         params={"symbol": PAIR, "interval": interval, "limit": limit}, timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        return pd.DataFrame([{
            "timestamp": pd.to_datetime(k[0], unit="ms"),
            "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
            "close": float(k[4]), "volume": float(k[5]),
        } for k in data])
    except Exception as e:
        log.error(f"klines fetch failed ({interval}): {e}")
        return None


def fetch_live_price() -> float | None:
    try:
        r = requests.get(f"{BINANCE_BASE}/fapi/v1/ticker/price", params={"symbol": PAIR}, timeout=10)
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception as e:
        log.error(f"live price fetch failed: {e}")
        return None


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
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, default=str, indent=2)


def write_status(payload):
    with open(STATUS_FILE, "w") as f:
        json.dump(payload, f, default=str, indent=2)


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
    }, is_win=net > 0)
    log.warning(f"  EXIT {side} via {reason} @${exit_px:.2f} | avg ${avg_entry:.2f} | "
                f"net ${net:+.2f} (price {price_move_pct:+.2f}%) | balance ${state['balance']:.2f}")


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
    last_idx = len(df_5m) - 2  # last CLOSED 5m bar
    last = df_5m.iloc[last_idx]
    close_px = float(last["close"])
    rsi_val = float(last["rsi"]) if pd.notna(last["rsi"]) else None

    sig = rsi_signal(rsi_val)
    log.info(f"  Balance: ${state['balance']:,.2f} | {PAIR}: ${close_px:,.2f} | live: ${live_px:,.2f} | "
             f"RSI {rsi_val:.1f} | Signal: {sig or 'NONE'}" if rsi_val is not None else
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
            log.info(f"  IN {side} L{pos['filled']}/{DCA_LEVELS} avg=${avg_entry:.2f} live=${live_px:.2f} fav={fav:+.2f}%")

    # Entry — purely RSI. Don't re-enter on the same tick we just exited.
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
        "env": os.environ.get("RSISCALP_DATA_DIR", "paper_rsiscalp"),
        "pair": PAIR, "price": close_px, "live_price": live_px,
        "balance": state["balance"], "peak_equity": peak, "drawdown_pct": dd_pct,
        "position": pos_status, "signal": sig,
        "indicators": {"rsi": rsi_val, "rsi_oversold": RSI_OVERSOLD, "rsi_overbought": RSI_OVERBOUGHT, "price": close_px},
        "stats": state["stats"],
        "strategy": f"RSI-Scalp (RSI{RSI_PERIOD} {RSI_OVERSOLD}/{RSI_OVERBOUGHT} / TP {TP_PCT_SINGLE*100:.2f}%·{TP_PCT_DCA*100:.2f}% adaptive / {DCA_LEVELS} DCA @ {DCA_SPACING*100:.2f}% / {'SL '+format(SL_FROM_WORST*100,'.1f')+'%' if USE_STOP_LOSS else 'NO SL'}) [PAPER]",
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
