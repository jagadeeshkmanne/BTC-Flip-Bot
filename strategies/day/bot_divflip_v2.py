#!/usr/bin/env python3
"""bot_divflip_v2.py — Paper-mode bot for the Divergence-Flip v2 strategy.

3rd paper bot — A/B variant of bot_divflip.py. Same divergence entry logic;
differs only in position management: 2-DCA 50/50 @ 0.5%, worst-anchored
SL 1.0% (see core_divflip_v2.py). Runs every 1 min via cron.

This is PAPER-ONLY — no live order placement, no Binance API auth. Pulls
prices from Binance Futures mainnet public endpoints (no key needed).

State / log / status: data/paper_divflip_v2/
"""
from __future__ import annotations
import os, sys, json, time, logging
from datetime import datetime, timezone
import requests
import pandas as pd
import numpy as np

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(os.path.dirname(STRATEGY_DIR))
sys.path.insert(0, STRATEGY_DIR)

from core import build_features, detect_divergence, rsi_series
from core_divflip_v2 import (
    LEVERAGE, RISK_PCT, DCA_LEVELS, DCA_SPACING, SL_FROM_WORST,
    USE_BREAKEVEN, BE_TRIGGER_PCT, BE_BUFFER_PCT, TRAIL_DIST_PCT, DIV_FRESH_BARS,
    USE_FLIP, RSI_LONG_MAX, RSI_SHORT_MIN,
    USE_TAKE_PROFIT, TP_PCT,
    DIV_PIVOT_L, DIV_PIVOT_R, RSI_PERIOD,
    evaluate_signal_divflip, dca_price,
    sl_price_divflip, be_should_activate, per_level_qty,
)

# ─── Paths ───
DATA_DIR = os.path.join(BOT_DIR, "data", "paper_divflip_v2")
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE  = os.path.join(DATA_DIR, "state.json")
STATUS_FILE = os.path.join(DATA_DIR, "status.json")
LOG_FILE    = os.path.join(DATA_DIR, "bot.log")

# ─── Config ───
PAIR = "BTCUSDT"
INITIAL_BALANCE = 5000.0
COMMISSION_PCT = 0.0004  # 0.04% taker fee per side

# Binance Futures USDT-M mainnet — public endpoints (no auth needed)
BINANCE_BASE = "https://fapi.binance.com"

# ─── Logging ───
log = logging.getLogger("bot_divflip_v2")
log.setLevel(logging.INFO)
log.handlers.clear()
fh = logging.FileHandler(LOG_FILE)
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(fh)
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(sh)


# ─── Binance public price fetchers ───
def fetch_klines(interval: str, limit: int = 500) -> pd.DataFrame | None:
    try:
        r = requests.get(f"{BINANCE_BASE}/fapi/v1/klines",
                         params={"symbol": PAIR, "interval": interval, "limit": limit},
                         timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        rows = [{
            "timestamp": pd.to_datetime(k[0], unit="ms"),
            "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
            "close": float(k[4]), "volume": float(k[5]),
        } for k in data]
        return pd.DataFrame(rows)
    except Exception as e:
        log.error(f"klines fetch failed ({interval}): {e}")
        return None


def fetch_live_price() -> float | None:
    try:
        r = requests.get(f"{BINANCE_BASE}/fapi/v1/ticker/price",
                         params={"symbol": PAIR}, timeout=10)
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception as e:
        log.error(f"live price fetch failed: {e}")
        return None


# ─── State I/O ───
def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "balance": INITIAL_BALANCE,
            "peak_equity": INITIAL_BALANCE,
            "position": None,  # dict or None
            "stats": {"total": 0, "wins": 0, "pnl": 0.0},
            "trade_log": [],
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
    if total_qty <= 0:
        return pos.get("first_entry", 0.0)
    return sum(e["px"] * e["qty"] for e in entries) / total_qty


def close_position(state, pos, exit_px: float, reason: str, live_px: float) -> dict:
    """Realize PnL on full position, update stats, append trade_log entry."""
    side = pos["side"]
    qty_total = pos["qty_total"]
    avg_entry = avg_entry_of(pos)

    if side == "LONG":
        gross = (exit_px - avg_entry) * qty_total
    else:
        gross = (avg_entry - exit_px) * qty_total
    fees = exit_px * qty_total * COMMISSION_PCT
    net = gross - fees
    balance_before = state["balance"]                   # capital base BEFORE this trade's net is added
    state["balance"] += net

    # pnl_pct is the BALANCE-impact %, not the price-move %. Reasoning:
    # users care "how much did my account grow on this trade?", which with
    # leverage is roughly (price_move × leverage) − fee_drag. Storing it as
    # balance % makes the trade log directly reflect equity changes (and
    # additive day-bucket sums approximate compound growth).
    # The raw price-move % is preserved separately as `price_move_pct`.
    price_move_pct = (exit_px / avg_entry - 1) * 100 * (1 if side == "LONG" else -1)
    pnl_pct = (net / balance_before * 100) if balance_before > 0 else 0.0
    trade_record = {
        "side": side,
        "first_entry": pos["first_entry"],
        "avg_entry": avg_entry,
        "exit": exit_px,
        "entries": len(pos.get("entries", [])),
        "qty_total": qty_total,
        "reason": reason,
        "pnl_usd": net,
        "pnl_pct": pnl_pct,              # BALANCE impact (new)
        "price_move_pct": price_move_pct, # raw price move from avg→exit (preserved for reference)
        "leverage": pos.get("leverage"),
        "entry_time": pos.get("entry_time"),
        "exit_time": datetime.now(timezone.utc).isoformat(),
    }
    state.setdefault("trade_log", []).append(trade_record)
    state["trade_log"] = state["trade_log"][-200:]
    state["stats"]["total"] += 1
    state["stats"]["pnl"] += pnl_pct
    if net > 0:
        state["stats"]["wins"] += 1
    log.warning(f"  EXIT {side} via {reason} @${exit_px:.2f} | avg_entry ${avg_entry:.2f} | "
                f"gross ${gross:+.2f} − fees ${fees:.2f} = net ${net:+.2f} "
                f"(price {price_move_pct:+.2f}% / balance {pnl_pct:+.2f}%) | "
                f"balance ${state['balance']:.2f}")
    return trade_record


def open_position(state, side: str, entry_px: float, balance: float) -> dict:
    # Snapshot leverage at L1 entry so DCA legs use the leverage in effect
    # when this position opened, regardless of later changes to the constant.
    pos_lev = LEVERAGE
    qty = per_level_qty(balance, entry_px, leg_idx=0, leverage=pos_lev)
    qty = round(qty, 3)  # BTCUSDT step 0.001
    if qty <= 0:
        log.warning(f"  qty {qty} too small to open")
        return None
    fees = entry_px * qty * COMMISSION_PCT
    state["balance"] -= fees
    pos = {
        "side": side,
        "first_entry": entry_px,
        "worst_entry": entry_px,
        "peak_price": entry_px,    # high-water (LONG) / low-water (SHORT) mark, drives trailing SL
        "entries": [{"px": entry_px, "qty": qty}],
        "qty_total": qty,
        "filled": 1,
        "be_activated": False,
        "leverage": pos_lev,
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "cycle_day": str(datetime.now(timezone.utc).date()),
    }
    state["position"] = pos
    log.warning(f"  OPENED {side} {qty}@${entry_px:.2f} | fees ${fees:.2f} | balance ${state['balance']:.2f}")
    return pos


def _apply_dca_leg(pos, fill_px: float, balance: float, state, reason: str) -> bool:
    """Add a DCA leg at fill_px. Returns True if filled. Uses martingale
    sizing — leg_idx = current filled count (new leg's index). Honors the
    leverage that was snapshotted at L1 entry so mid-position config changes
    to the global LEVERAGE constant don't resize this position's legs."""
    side = pos["side"]
    pos_lev = pos.get("leverage", LEVERAGE)
    qty = per_level_qty(balance, fill_px, leg_idx=pos["filled"], leverage=pos_lev)
    qty = round(qty, 3)
    if qty <= 0:
        return False
    # Leverage cap guard — total notional must stay within balance × pos_lev × 0.95
    max_total_qty = (balance * 0.95 * pos_lev) / fill_px
    if pos["qty_total"] + qty > max_total_qty:
        remaining = max_total_qty - pos["qty_total"]
        if remaining < 0.001:
            log.info(f"  DCA L{pos['filled']+1} ({reason}) skipped — leverage cap reached")
            return False
        qty = round(remaining, 3)
    fees = fill_px * qty * COMMISSION_PCT
    state["balance"] -= fees
    pos["entries"].append({"px": fill_px, "qty": qty})
    if side == "LONG":
        pos["worst_entry"] = min(pos["worst_entry"], fill_px)
    else:
        pos["worst_entry"] = max(pos["worst_entry"], fill_px)
    pos["qty_total"] = sum(e["qty"] for e in pos["entries"])
    pos["filled"] += 1
    log.warning(f"  DCA L{pos['filled']} {side} {qty}@${fill_px:.2f} ({reason}) | "
                f"new avg=${avg_entry_of(pos):.2f} worst=${pos['worst_entry']:.2f}")
    return True


def maybe_dca_fixed(pos, live_px: float, balance: float, state) -> bool:
    """Fixed-distance DCA — fires at DCA_SPACING adverse from worst entry,
    up to DCA_LEVELS total. All levels use fixed spacing (no div-confirmation
    required for L3+).
    """
    if pos["filled"] >= DCA_LEVELS:
        return False
    side = pos["side"]
    dca_trigger = dca_price(side, pos["worst_entry"])
    crossed = (side == "LONG" and live_px <= dca_trigger) or \
              (side == "SHORT" and live_px >= dca_trigger)
    if not crossed:
        return False
    # Use martingale qty for THIS leg's index (filled count = leg_idx for the new leg)
    return _apply_dca_leg(pos, dca_trigger, balance, state, f"fixed -{DCA_SPACING*100:.1f}% mart{int(__import__('core_divflip_v2').MARTINGALE_RATIOS[pos['filled']])}x")


# ─── Main tick ───
def main():
    log.info("=" * 60)
    log.info(f"Divergence-Flip v2 Paper Bot — RSI filter ≤{RSI_LONG_MAX:.0f}/≥{RSI_SHORT_MIN:.0f} | {DCA_LEVELS} DCA @ {DCA_SPACING*100:.1f}% | "
             f"L2-anchored SL {SL_FROM_WORST*100:.1f}% | "
             f"{'TP ' + format(TP_PCT*100, '.2f') + '% from avg' if USE_TAKE_PROFIT else 'NO TP'} | "
             f"BE @{BE_TRIGGER_PCT*100:.2f}% trail {TRAIL_DIST_PCT*100:.2f}% | "
             f"{'flip' if USE_FLIP else 'NO flip'} | no EOD | fresh {DIV_FRESH_BARS}b")

    state = load_state()

    # ─ Fetch market data ─
    df_5m = fetch_klines("5m", 500)
    df_1d = fetch_klines("1d", 100)
    if df_5m is None or len(df_5m) < 100 or df_1d is None or len(df_1d) < 60:
        log.error("insufficient klines")
        return
    live_px = fetch_live_price()
    if live_px is None:
        log.error("live price unavailable")
        return

    df = build_features(df_5m, df_1d)
    # RSI period override — build_features uses core.RSI_PERIOD (14) by
    # default; divflip wants 10 (TV-tuned, faster pivot RSI reaction).
    # Recompute the RSI column BEFORE detect_divergence so pivot RSI values
    # reflect the override.
    df["rsi"] = rsi_series(df["close"], RSI_PERIOD)
    # Override pivot R from V2.2's 5/5 to TV-tuned 5/2 — re-runs divergence
    # detection so bars_since_*_div columns reflect the faster confirmation.
    df = detect_divergence(df, DIV_PIVOT_L, DIV_PIVOT_R)
    last_idx = len(df) - 2  # last CLOSED 5m bar
    last = df.iloc[last_idx]
    close_px = float(last["close"])

    log.info(f"  Balance: ${state['balance']:,.2f} | {PAIR}: ${close_px:,.2f} | live: ${live_px:,.2f}")

    pos = state.get("position")
    current_side = pos["side"] if pos else None

    sig = evaluate_signal_divflip(df, last_idx, current_side)

    bsb = sig.raw.get("bars_since_bear_div", 9999)
    bsu = sig.raw.get("bars_since_bull_div", 9999)
    bsb_str = f"{bsb}b" if bsb <= 50 else "—"
    bsu_str = f"{bsu}b" if bsu <= 50 else "—"
    log.info(f"  Signal: {sig.side or 'NONE'} | RSI div bear={bsb_str} / bull={bsu_str} (fresh ≤ {DIV_FRESH_BARS}b)")

    # ─ Update peak equity / DD ─
    if state["balance"] > state.get("peak_equity", 0):
        state["peak_equity"] = state["balance"]
    peak = state.get("peak_equity", state["balance"])
    dd_pct = (state["balance"] / peak - 1) if peak > 0 else 0.0

    # ─ Position management ─
    new_pos_after_flip = None      # set if we flip (open reverse same tick)
    exit_reason_this_tick = None   # set if we exit (blocks same-tick re-entry on natural exits)
    if pos:
        side = pos["side"]
        first_entry = pos["first_entry"]
        worst_entry = pos["worst_entry"]

        # Update peak (high-water mark for LONG, low-water for SHORT).
        # Drives the trailing SL — captured before any DCA fill so trailing
        # tracks actual price excursion, not the new leg's fill price.
        prev_peak = pos.get("peak_price", first_entry)
        if side == "LONG":
            pos["peak_price"] = max(prev_peak, live_px)
        else:
            pos["peak_price"] = min(prev_peak, live_px)
        peak_price = pos["peak_price"]

        # Fixed-distance DCA — fires when price reaches worst_entry × (1 ∓ DCA_SPACING),
        # up to DCA_LEVELS total. v2: SL is anchored to WORST entry (raw_sl uses
        # worst_entry), so the stop rides down with each DCA leg.
        dca_fired = maybe_dca_fixed(pos, live_px, state["balance"], state)
        if dca_fired:
            pos = state["position"]
            worst_entry = pos["worst_entry"]

        # BE activation check — avg-anchored (matches v1): arms at +BE_TRIGGER
        # from AVG entry (not L1); the BE floor is avg-anchored too. Sticky.
        avg_entry = avg_entry_of(pos)
        if not pos["be_activated"] and be_should_activate(side, avg_entry, live_px):
            pos["be_activated"] = True
            log.warning(f"  BE armed at live=${live_px:.2f} (fav crossed {BE_TRIGGER_PCT*100:.1f}% from avg ${avg_entry:.2f}) — trailing SL now active")

        # Composite SL: raw / BE / trailing (whichever is tightest). avg_entry
        # passed as the BE-anchor arg so the BE floor is avg ± buffer.
        sl_px = sl_price_divflip(side, worst_entry, avg_entry, pos["be_activated"], peak_price)

        exit_reason = None
        exit_px = None

        # Fixed TP from avg entry — primary exit when armed. Recomputed each
        # tick because DCA updates avg_entry. Checked BEFORE the SL block so
        # in-profit exits always win.
        if USE_TAKE_PROFIT:
            tp_px = avg_entry * (1 + TP_PCT) if side == "LONG" else avg_entry * (1 - TP_PCT)
            if (side == "LONG" and live_px >= tp_px) or (side == "SHORT" and live_px <= tp_px):
                exit_reason = "TP"
                exit_px = tp_px

        # Hard SL / trailing SL check
        if exit_px is None and side == "LONG" and live_px <= sl_px:
            # Classify exit reason: BE if SL == BE level; TRAIL if SL > BE level; SL otherwise
            be_sl = avg_entry * (1 + BE_BUFFER_PCT)
            if pos["be_activated"] and sl_px > be_sl + 0.01:
                exit_reason = "TRAIL"
            elif pos["be_activated"] and abs(sl_px - be_sl) < 0.01:
                exit_reason = "BE"
            else:
                exit_reason = "SL"
            exit_px = sl_px
        elif exit_px is None and side == "SHORT" and live_px >= sl_px:
            be_sl = avg_entry * (1 - BE_BUFFER_PCT)
            if pos["be_activated"] and sl_px < be_sl - 0.01:
                exit_reason = "TRAIL"
            elif pos["be_activated"] and abs(sl_px - be_sl) < 0.01:
                exit_reason = "BE"
            else:
                exit_reason = "SL"
            exit_px = sl_px

        # Flip on opposite divergence — gated by USE_FLIP. Currently OFF
        # (TV-tuned config) — trades ride to SL / trailing exit.
        if USE_FLIP and exit_px is None and sig.flip_opposite:
            exit_reason = "FLIP"
            exit_px = live_px
            new_pos_after_flip = "SHORT" if side == "LONG" else "LONG"

        if exit_px is not None:
            close_position(state, pos, exit_px, exit_reason, live_px)
            state["position"] = None
            pos = None
            exit_reason_this_tick = exit_reason
        else:
            # No exit fired — log current state
            fav_pct = ((live_px - avg_entry) / avg_entry * 100) * (1 if side == "LONG" else -1)
            peak_pct = ((peak_price - first_entry) / first_entry * 100) * (1 if side == "LONG" else -1)
            be_tag = " [BE]" if pos["be_activated"] else ""
            log.info(f"  IN {side} L{pos['filled']}/{DCA_LEVELS} avg=${avg_entry:.2f} live=${live_px:.2f} "
                     f"fav={fav_pct:+.2f}% peak=${peak_price:.2f}({peak_pct:+.2f}%) SL=${sl_px:.2f}{be_tag}")

    # ─ Entry check (if flat) ─
    # Don't re-enter on the same tick as a natural TP/SL/BE exit — wait for a
    # NEW divergence to fire on a future bar. Only the FLIP path opens
    # immediately (that's the whole point of flip).
    if state["position"] is None:
        if new_pos_after_flip:
            open_position(state, new_pos_after_flip, live_px, state["balance"])
        elif exit_reason_this_tick in ("SL", "BE", "TRAIL"):
            log.info(f"  Just exited via {exit_reason_this_tick} this tick — waiting for new signal")
        elif sig.side:
            open_position(state, sig.side, live_px, state["balance"])

    # ─ Stats line ─
    stats = state["stats"]
    wr = (stats["wins"] / stats["total"] * 100) if stats["total"] > 0 else 0.0
    total_pnl_pct = (state["balance"] / INITIAL_BALANCE - 1) * 100
    log.info(f"  Stats: {stats['total']} trades | WR {wr:.0f}% | PnL {total_pnl_pct:+.2f}%")

    # ─ Persist + status ─
    save_state(state)

    pos = state.get("position")
    pos_status = None
    if pos:
        avg_e = avg_entry_of(pos)
        worst_e = pos["worst_entry"]
        first_e = pos["first_entry"]
        peak_e = pos.get("peak_price", first_e)
        sl_p = sl_price_divflip(pos["side"], worst_e, avg_e, pos["be_activated"], peak_e)
        fav_p = ((live_px - avg_e) / avg_e * 100) * (1 if pos["side"] == "LONG" else -1)
        peak_p = ((peak_e - first_e) / first_e * 100) * (1 if pos["side"] == "LONG" else -1)
        # tp_px = avg_entry × (1 ± TP_PCT). Recomputed each tick — DCA fills
        # shift avg, so TP moves with it. Trailing SL is the backstop.
        tp_p = (avg_e * (1 + TP_PCT) if pos["side"] == "LONG" else avg_e * (1 - TP_PCT)) if USE_TAKE_PROFIT else None
        # be_arm_px — the price at which BE arms. avg-anchored (matches v1).
        # Written to status so the dashboard reads it, never recomputes it.
        be_arm_px = avg_e * (1 + BE_TRIGGER_PCT) if pos["side"] == "LONG" else avg_e * (1 - BE_TRIGGER_PCT)
        pos_status = {
            "side": pos["side"],
            "first_entry": first_e,
            "avg_entry": avg_e,
            "worst_entry": worst_e,
            "peak_price": peak_e,
            "qty_total": pos["qty_total"],
            "filled": pos["filled"],
            "tp_px": tp_p,
            "sl_px": sl_p,
            "be_arm_px": be_arm_px,
            "be_activated": pos["be_activated"],
            "fav_pct": fav_p,
            "peak_pct": peak_p,
            "entry_time": pos.get("entry_time"),
        }

    status = {
        "env": "paper_divflip_v2",
        "pair": PAIR,
        "price": close_px,
        "live_price": live_px,
        "balance": state["balance"],
        "peak_equity": peak,
        "drawdown_pct": dd_pct,
        "position": pos_status,
        "signal": sig.side,
        "indicators": sig.raw,
        "conditions": sig.conditions,
        "stats": state["stats"],
        "strategy": f"Divergence-Flip v2 ({'TP ' + format(TP_PCT*100, '.1f') + '% / ' if USE_TAKE_PROFIT else ''}trail {TRAIL_DIST_PCT*100:.1f}% after BE / SL {SL_FROM_WORST*100:.1f}% from L2(worst) / {DCA_LEVELS} DCA @ {DCA_SPACING*100:.1f}% 50-50 / {'flip' if USE_FLIP else 'NO flip'} / no EOD) [PAPER]",
        "paper_mode": True,
        "state": "IN_POSITION" if pos else "FLAT",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_status(status)
    log.info("=" * 60 + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception(f"FATAL: {e}")
        sys.exit(1)
