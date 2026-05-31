#!/usr/bin/env python3
"""bot_divflip.py — Paper-mode bot for the Divergence-Flip strategy.

Runs every 1 min via cron. Reads BTCUSDT mainnet 5m + 1d klines, evaluates
fresh-divergence signal, simulates fills/exits against virtual $5K balance.

This is PAPER-ONLY — no live order placement, no Binance API auth. Pulls
prices from Binance Futures mainnet public endpoints (no key needed).

State / log / status: data/paper_divflip/
"""
from __future__ import annotations
import os, sys, json, time, logging
from datetime import datetime, timezone, timedelta
import requests
import pandas as pd
import numpy as np

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(os.path.dirname(STRATEGY_DIR))
sys.path.insert(0, STRATEGY_DIR)

from core import build_features, detect_divergence, rsi_series
from core_divflip import (
    LEVERAGE, RISK_PCT, DCA_LEVELS, DCA_SPACING, SL_FROM_WORST,
    USE_BREAKEVEN, BE_TRIGGER_PCT, BE_BUFFER_PCT, TRAIL_DIST_PCT, DIV_FRESH_BARS,
    USE_FLIP, RSI_LONG_MAX, RSI_SHORT_MIN,
    USE_TAKE_PROFIT, TP_PCT,
    DIV_PIVOT_L, DIV_PIVOT_R, RSI_PERIOD,
    USE_LOSS_COOLDOWN, LOSS_COOLDOWN_HOURS,
    USE_TP_COOLDOWN, TP_COOLDOWN_MINUTES,
    USE_WEEKDAY_FILTER, BLOCKED_WEEKDAYS,
    USE_TIME_STOP_LOSS_ONLY, TIME_STOP_HOURS,
    USE_SAME_LEVEL_BLOCK, SAME_LEVEL_PROX_PCT, SAME_LEVEL_WINDOW_HOURS,
    USE_ONE_SHOT_PER_PIVOT,
    USE_IST_NIGHT_BLOCK, IST_BLOCK_START_HOUR, IST_BLOCK_END_HOUR,
    USE_PARTIAL_TP, PARTIAL_TP_PCT, PARTIAL_TP_FRACTION,
    USE_PROFIT_TRAIL, PROFIT_TRAIL_DIST,
    USE_15M_TREND_FILTER, TREND_TIMEFRAME, TREND_EMA_FAST, TREND_EMA_SLOW,
    evaluate_signal_divflip, dca_price,
    sl_price_divflip, be_should_activate, per_level_qty,
)

# ─── Paths ───
DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("DIVFLIP_DATA_DIR", "paper_divflip"))
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
log = logging.getLogger("bot_divflip")
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
        # RSI snapshot at entry — added 2026-05-27 for post-trade RSI analysis.
        "pivot_rsi_at_entry": pos.get("pivot_rsi_at_entry"),
        "live_rsi_at_entry": pos.get("live_rsi_at_entry"),
        "div_bars_at_entry": pos.get("div_bars_at_entry"),
    }
    state.setdefault("trade_log", []).append(trade_record)
    state["trade_log"] = state["trade_log"][-200:]
    state["stats"]["total"] += 1
    state["stats"]["pnl"] += pnl_pct
    if net > 0:
        state["stats"]["wins"] += 1
        if reason == "TP":
            # Track TP exits per side for 15-min same-side cooldown (anti pump-and-dump).
            # BE/TRAIL wins don't trigger TP cooldown — those are partial captures.
            state.setdefault("last_tp_exit", {})[side] = trade_record["exit_time"]
    elif reason == "SL":
        # Only REAL SL hits trigger 30-min same-side cooldown.
        state.setdefault("last_loss_exit", {})[side] = trade_record["exit_time"]
    # Track all WIN-type exits (TP/TRAIL/BE) for same-level opposite-flip gate.
    # SL exits don't count — those don't fit the "took profit at level → divergence
    # flips at same level → trap" mechanism.
    if reason in ("TP", "TRAIL", "BE"):
        state.setdefault("last_win_exit", {})[side] = {
            "exit_time": trade_record["exit_time"],
            "exit_price": exit_px,
            "reason": reason,
        }
    log.warning(f"  EXIT {side} via {reason} @${exit_px:.2f} | avg_entry ${avg_entry:.2f} | "
                f"gross ${gross:+.2f} − fees ${fees:.2f} = net ${net:+.2f} "
                f"(price {price_move_pct:+.2f}% / balance {pnl_pct:+.2f}%) | "
                f"balance ${state['balance']:.2f}")
    return trade_record


def partial_close(state, pos, exit_px: float, fraction: float, live_px: float) -> None:
    """Realize PnL on `fraction` of the position, shrink the remaining position
    proportionally (avg_entry unchanged). Records a partial trade_log entry.
    Used for the 0.25% scale-out — locks profit on part, lets the rest ride."""
    side = pos["side"]
    avg_entry = avg_entry_of(pos)
    sell_qty = pos["qty_total"] * fraction
    if sell_qty <= 0:
        return
    if side == "LONG":
        gross = (exit_px - avg_entry) * sell_qty
    else:
        gross = (avg_entry - exit_px) * sell_qty
    fees = exit_px * sell_qty * COMMISSION_PCT
    net = gross - fees
    balance_before = state["balance"]
    state["balance"] += net
    pnl_pct = (net / balance_before * 100) if balance_before > 0 else 0.0
    price_move_pct = (exit_px / avg_entry - 1) * 100 * (1 if side == "LONG" else -1)

    # Shrink remaining position proportionally — avg unchanged, qty reduced.
    pos["qty_total"] -= sell_qty
    for e in pos.get("entries", []):
        e["qty"] *= (1 - fraction)
    pos["partial_taken"] = True

    trade_record = {
        "side": side, "first_entry": pos["first_entry"], "avg_entry": avg_entry,
        "exit": exit_px, "entries": len(pos.get("entries", [])),
        "qty_total": sell_qty, "reason": "PARTIAL_TP",
        "pnl_usd": net, "pnl_pct": pnl_pct, "price_move_pct": price_move_pct,
        "leverage": pos.get("leverage"), "entry_time": pos.get("entry_time"),
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "pivot_rsi_at_entry": pos.get("pivot_rsi_at_entry"),
        "live_rsi_at_entry": pos.get("live_rsi_at_entry"),
        "div_bars_at_entry": pos.get("div_bars_at_entry"),
    }
    state.setdefault("trade_log", []).append(trade_record)
    state["trade_log"] = state["trade_log"][-200:]
    state["stats"]["total"] += 1
    state["stats"]["pnl"] += pnl_pct
    if net > 0:
        state["stats"]["wins"] += 1
    log.warning(f"  PARTIAL TP: sold {fraction*100:.0f}% ({sell_qty:.4f}) @${exit_px:.2f} "
                f"net ${net:+.2f} | remaining {pos['qty_total']:.4f} rides to full TP")


def open_position(state, side: str, entry_px: float, balance: float, signal=None) -> dict:
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
    # Capture RSI snapshot at entry for post-trade analysis (added 2026-05-27).
    sig_raw = signal.raw if signal is not None else {}
    pivot_rsi_key = "rsi_at_bull_pivot" if side == "LONG" else "rsi_at_bear_pivot"
    bars_since_key = "bars_since_bull_div" if side == "LONG" else "bars_since_bear_div"
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
        "pivot_rsi_at_entry": sig_raw.get(pivot_rsi_key),
        "live_rsi_at_entry": sig_raw.get("rsi"),
        "div_bars_at_entry": sig_raw.get(bars_since_key),
        "partial_taken": False,
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
    return _apply_dca_leg(pos, dca_trigger, balance, state, f"fixed -{DCA_SPACING*100:.1f}% mart{int(__import__('core_divflip').MARTINGALE_RATIOS[pos['filled']])}x")


# ─── Main tick ───
def main():
    log.info("=" * 60)
    log.info(f"Divergence-Flip Paper Bot — RSI filter ≤{RSI_LONG_MAX:.0f}/≥{RSI_SHORT_MIN:.0f} | {DCA_LEVELS} DCA @ {DCA_SPACING*100:.1f}% | "
             f"L3-anchored SL {SL_FROM_WORST*100:.1f}% | "
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

    # ─ Higher-TF trend filter — direction gate (LONG only in uptrend, etc.) ─
    # Timeframe + EMAs configurable via core_divflip constants (v1: 15m 20/50; v2: 1h 50/200).
    trend_15m = None  # "UP" / "DOWN" / None (unknown). Var name kept for status/dashboard compat.
    trend_ema_fast = None
    trend_ema_slow = None
    if USE_15M_TREND_FILTER:
        df_tf = fetch_klines(TREND_TIMEFRAME, 300)
        if df_tf is not None and len(df_tf) >= TREND_EMA_SLOW:
            ema_fast = df_tf["close"].ewm(span=TREND_EMA_FAST, adjust=False).mean()
            ema_slow = df_tf["close"].ewm(span=TREND_EMA_SLOW, adjust=False).mean()
            # use last CLOSED bar (-2) to avoid the still-forming bar
            trend_ema_fast = float(ema_fast.iloc[-2])
            trend_ema_slow = float(ema_slow.iloc[-2])
            trend_15m = "UP" if trend_ema_fast > trend_ema_slow else "DOWN"
        else:
            log.warning(f"  {TREND_TIMEFRAME} trend: insufficient data — filter inactive this tick")

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
        # up to DCA_LEVELS total. SL is anchored to L1 (raw_sl uses first_entry), so
        # DCA can only improve avg — never widens the SL.
        dca_fired = maybe_dca_fixed(pos, live_px, state["balance"], state)
        if dca_fired:
            pos = state["position"]
            worst_entry = pos["worst_entry"]

        # BE activation check — avg-anchored (user request 2026-05-20): arms at
        # +BE_TRIGGER from AVG entry (not L1); the BE floor is avg-anchored too.
        # Sticky once armed.
        avg_entry = avg_entry_of(pos)
        if not pos["be_activated"] and be_should_activate(side, avg_entry, live_px):
            pos["be_activated"] = True
            log.warning(f"  BE armed at live=${live_px:.2f} (fav crossed {BE_TRIGGER_PCT*100:.1f}% from avg ${avg_entry:.2f}) — trailing SL now active")

        # Composite SL: raw / BE / trailing (whichever is tightest). avg_entry
        # passed as the BE-anchor arg so the BE floor is avg ± buffer.
        sl_px = sl_price_divflip(side, worst_entry, avg_entry, pos["be_activated"], peak_price)

        exit_reason = None
        exit_px = None

        # Partial TP — sell PARTIAL_TP_FRACTION at +PARTIAL_TP_PCT from L1 entry.
        # Locks profit on the frequent small moves; remaining qty rides to full TP.
        # Fires once per position (partial_taken flag).
        if USE_PARTIAL_TP and not pos.get("partial_taken"):
            ptp_px = first_entry * (1 + PARTIAL_TP_PCT) if side == "LONG" else first_entry * (1 - PARTIAL_TP_PCT)
            if (side == "LONG" and live_px >= ptp_px) or (side == "SHORT" and live_px <= ptp_px):
                partial_close(state, pos, ptp_px, PARTIAL_TP_FRACTION, live_px)

        # Take-profit exit. Two modes:
        #  - Profit trail (USE_PROFIT_TRAIL): once peak reaches +TP_PCT (0.5%),
        #    lock a 0.5% MINIMUM and trail PROFIT_TRAIL_DIST off the peak above
        #    that → exit = max(avg+0.5%, peak−0.3%). Rides trend-aligned winners
        #    beyond 0.5% while guaranteeing ≥0.5%.
        #  - Else: plain fixed TP at TP_PCT from avg.
        if USE_TAKE_PROFIT:
            tp_floor = avg_entry * (1 + TP_PCT) if side == "LONG" else avg_entry * (1 - TP_PCT)
            if USE_PROFIT_TRAIL:
                if side == "LONG":
                    peak_fav = (peak_price - avg_entry) / avg_entry
                    if peak_fav >= TP_PCT:  # reached the 0.5% floor at some point
                        trail_exit = max(tp_floor, peak_price * (1 - PROFIT_TRAIL_DIST))
                        if live_px <= trail_exit:
                            exit_reason = "TP"
                            exit_px = trail_exit
                else:
                    peak_fav = (avg_entry - peak_price) / avg_entry
                    if peak_fav >= TP_PCT:
                        trail_exit = min(tp_floor, peak_price * (1 + PROFIT_TRAIL_DIST))
                        if live_px >= trail_exit:
                            exit_reason = "TP"
                            exit_px = trail_exit
            else:
                if (side == "LONG" and live_px >= tp_floor) or (side == "SHORT" and live_px <= tp_floor):
                    exit_reason = "TP"
                    exit_px = tp_floor

        # Hard SL / trailing SL check
        if exit_px is None and side == "LONG" and live_px <= sl_px:
            # Classify exit reason: BE if SL == BE level; TRAIL if SL > BE level; SL otherwise
            be_sl = first_entry * (1 + BE_BUFFER_PCT)
            if pos["be_activated"] and sl_px > be_sl + 0.01:
                exit_reason = "TRAIL"
            elif pos["be_activated"] and abs(sl_px - be_sl) < 0.01:
                exit_reason = "BE"
            else:
                exit_reason = "SL"
            exit_px = sl_px
        elif exit_px is None and side == "SHORT" and live_px >= sl_px:
            be_sl = first_entry * (1 - BE_BUFFER_PCT)
            if pos["be_activated"] and sl_px < be_sl - 0.01:
                exit_reason = "TRAIL"
            elif pos["be_activated"] and abs(sl_px - be_sl) < 0.01:
                exit_reason = "BE"
            else:
                exit_reason = "SL"
            exit_px = sl_px

        # 8h loss-only time-stop — closes underwater positions that the SL
        # didn't catch but are bleeding too long. Skipped if pos is in profit
        # (let winners ride to TP / TRAIL).
        if USE_TIME_STOP_LOSS_ONLY and exit_px is None and pos.get("entry_time"):
            try:
                age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(pos["entry_time"])).total_seconds() / 3600
                fav_pct_now = ((live_px - avg_entry) / avg_entry) * (1 if side == "LONG" else -1)
                if age_h >= TIME_STOP_HOURS and fav_pct_now < 0:
                    exit_reason = "TIME8"
                    exit_px = live_px
                    log.warning(f"  TIME-STOP: position held {age_h:.1f}h with fav {fav_pct_now*100:+.2f}% — force-close.")
            except Exception:
                pass

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

    # 6h same-side cooldown after real SL — blocks falling-knife re-entry.
    cooldown_ok = True
    cooldown_msg = ""
    if USE_LOSS_COOLDOWN and sig.side:
        last_loss = state.get("last_loss_exit", {}).get(sig.side)
        if last_loss:
            try:
                last_loss_dt = datetime.fromisoformat(last_loss)
                hours_since = (datetime.now(timezone.utc) - last_loss_dt).total_seconds() / 3600
                if hours_since < LOSS_COOLDOWN_HOURS:
                    cooldown_ok = False
                    remaining = LOSS_COOLDOWN_HOURS - hours_since
                    cooldown_msg = f"only {hours_since:.1f}h since last {sig.side} SL, need {LOSS_COOLDOWN_HOURS}h ({remaining:.1f}h remaining)"
            except Exception:
                pass

    # Friday block — v1 27-trade data shows Fri 0/2 WR / −$903.
    weekday = datetime.now(timezone.utc).weekday()  # 0=Mon ... 6=Sun
    wd_ok = (not USE_WEEKDAY_FILTER) or (weekday not in BLOCKED_WEEKDAYS)

    # Same-level opposite-flip block — after a winning exit (TP/TRAIL/BE) near
    # some price level, don't open the OPPOSITE side within 0.15% of that exit.
    # The pattern: TP/TRAIL near level X → divergence flips at X → bot opens
    # opposite into a level that may be about to break. 11 historical setups
    # netted -$614 (incl. -$701 catastrophe). SL exits don't trigger this
    # (already covered by 30-min same-side cooldown).
    same_level_ok = True
    same_level_msg = ""
    if USE_SAME_LEVEL_BLOCK and sig.side:
        opp_side = "SHORT" if sig.side == "LONG" else "LONG"
        last_win = state.get("last_win_exit", {}).get(opp_side)
        if last_win:
            try:
                last_win_dt = datetime.fromisoformat(last_win["exit_time"])
                gap_h = (datetime.now(timezone.utc) - last_win_dt).total_seconds() / 3600
                if gap_h < SAME_LEVEL_WINDOW_HOURS:
                    prox = abs(live_px - last_win["exit_price"]) / last_win["exit_price"]
                    if prox < SAME_LEVEL_PROX_PCT:
                        same_level_ok = False
                        same_level_msg = (f"opposite-flip within {prox*100:.2f}% of last {opp_side} {last_win['reason']} "
                                          f"exit ${last_win['exit_price']:.0f} ({gap_h:.1f}h ago) — same-level trap")
            except Exception:
                pass

    # 15-min same-side cooldown after TP — anti pump-and-dump.
    tp_cooldown_ok = True
    tp_cooldown_msg = ""
    if USE_TP_COOLDOWN and sig.side and cooldown_ok:
        last_tp = state.get("last_tp_exit", {}).get(sig.side)
        if last_tp:
            try:
                last_tp_dt = datetime.fromisoformat(last_tp)
                minutes_since = (datetime.now(timezone.utc) - last_tp_dt).total_seconds() / 60
                if minutes_since < TP_COOLDOWN_MINUTES:
                    tp_cooldown_ok = False
                    remaining = TP_COOLDOWN_MINUTES - minutes_since
                    tp_cooldown_msg = f"only {minutes_since:.1f}min since last {sig.side} TP, need {TP_COOLDOWN_MINUTES}min ({remaining:.1f}min remaining) — anti pump-and-dump"
            except Exception:
                pass

    # IST night block — no entries 00:00-06:00 IST (UTC+5:30). Thin liquidity + user asleep.
    ist_ok = True
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    if USE_IST_NIGHT_BLOCK and IST_BLOCK_START_HOUR <= ist_now.hour < IST_BLOCK_END_HOUR:
        ist_ok = False

    # Higher-TF trend gate — LONG only in uptrend, SHORT only in downtrend.
    trend_ok = True
    if USE_15M_TREND_FILTER and sig.side and trend_15m is not None:
        if sig.side == "LONG" and trend_15m != "UP":
            trend_ok = False
        elif sig.side == "SHORT" and trend_15m != "DOWN":
            trend_ok = False

    # One-shot per divergence pivot — block re-entry on a pivot that already
    # triggered a trade. Pivot bar = last_idx - bars_since - DIV_PIVOT_R.
    one_shot_ok = True
    pivot_ts = None
    if USE_ONE_SHOT_PER_PIVOT and sig.side:
        bars_since = sig.raw.get("bars_since_bull_div" if sig.side == "LONG" else "bars_since_bear_div", 9999)
        if bars_since < 9999:
            pivot_idx = last_idx - bars_since - DIV_PIVOT_R
            if 0 <= pivot_idx < len(df):
                pivot_ts = str(df.iloc[pivot_idx]["timestamp"])
                if pivot_ts in state.get("consumed_pivots", []):
                    one_shot_ok = False

    block_reason = None   # exact reason no trade opened this tick (written to status for dashboard)
    if state["position"] is None:
        if new_pos_after_flip:
            open_position(state, new_pos_after_flip, live_px, state["balance"], signal=sig)
        elif exit_reason_this_tick in ("SL", "BE", "TRAIL"):
            block_reason = f"Just exited via {exit_reason_this_tick} this tick — waiting for a new signal."
            log.info(f"  Just exited via {exit_reason_this_tick} this tick — waiting for new signal")
        elif sig.side and not wd_ok:
            wd_name = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][weekday]
            block_reason = f"It's Friday ({wd_name}) — entries blocked (historically the worst day)."
            log.info(f"  Signal {sig.side} BLOCKED by weekday filter ({wd_name})")
        elif sig.side and not trend_ok:
            block_reason = f"{sig.side} signal blocked — {TREND_TIMEFRAME} trend is {trend_15m} (need {'UP' if sig.side=='LONG' else 'DOWN'} for {sig.side})."
            log.info(f"  Signal {sig.side} BLOCKED by {TREND_TIMEFRAME} trend filter (trend {trend_15m})")
        elif sig.side and not ist_ok:
            block_reason = f"{sig.side} signal blocked — Indian night-time ({ist_now.strftime('%H:%M')} IST). No entries 00:00-06:00 IST."
            log.info(f"  Signal {sig.side} BLOCKED by IST night filter ({ist_now.strftime('%H:%M')} IST)")
        elif sig.side and not one_shot_ok:
            block_reason = f"{sig.side} signal blocked — this divergence pivot already triggered a trade (one trade per pivot)."
            log.info(f"  Signal {sig.side} BLOCKED — pivot {pivot_ts} already traded")
        elif sig.side and not same_level_ok:
            block_reason = f"{sig.side} signal blocked — {same_level_msg}"
            log.info(f"  Signal {sig.side} BLOCKED — {same_level_msg}")
        elif sig.side and not cooldown_ok:
            block_reason = f"{sig.side} signal blocked — cooldown after last {sig.side} stop-loss: {cooldown_msg}"
            log.info(f"  Signal {sig.side} BLOCKED — {cooldown_msg}")
        elif sig.side and not tp_cooldown_ok:
            block_reason = f"{sig.side} signal blocked — cooldown after last {sig.side} take-profit: {tp_cooldown_msg}"
            log.info(f"  Signal {sig.side} BLOCKED — {tp_cooldown_msg}")
        elif sig.side:
            open_position(state, sig.side, live_px, state["balance"], signal=sig)
            # Record consumed pivot for one-shot rule
            if USE_ONE_SHOT_PER_PIVOT and pivot_ts:
                cp = state.setdefault("consumed_pivots", [])
                cp.append(pivot_ts)
                state["consumed_pivots"] = cp[-100:]  # keep last 100

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
        # be_arm_px — the price at which BE arms. avg-anchored (v1). Written to
        # status so the dashboard reads it from the bot, never recomputes it.
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
        "env": os.environ.get("DIVFLIP_DATA_DIR", "paper_divflip"),
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
        "trend_15m": trend_15m,
        "trend_ema_fast": trend_ema_fast,
        "trend_ema_slow": trend_ema_slow,
        "block_reason": block_reason,
        "stats": state["stats"],
        "strategy": f"Divergence-Flip ({'TP ' + format(TP_PCT*100, '.1f') + '% / ' if USE_TAKE_PROFIT else ''}trail {TRAIL_DIST_PCT*100:.1f}% after BE / SL {SL_FROM_WORST*100:.1f}% from L3 / {DCA_LEVELS} DCA @ {DCA_SPACING*100:.1f}% mart / {'flip' if USE_FLIP else 'NO flip'} / no EOD) [PAPER]",
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
