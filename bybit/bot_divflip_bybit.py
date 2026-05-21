#!/usr/bin/env python3
"""bot_divflip_bybit.py — LIVE Divergence-Flip v1 bot for Bybit futures.

Runs every 1 min (systemd timer / cron). Trades BTCUSDT USDT-perpetual on a
real Bybit account — works with a Copy Trading Master Trader account (copy-trade
orders go through the same /v5/order/create endpoint).

═══ THIS PLACES REAL ORDERS WITH REAL MONEY ═══
Divergence-Flip ("divflip") config #5 is documented as OVERFIT. The year-wise
out-of-sample backtest (2021-2026, BTCUSDT 5m) returned -100% — account wiped.
The +189% headline was in-sample on its own tuning window. See
memory/divflip_tv_tuned_live.md. You asked for this live deployment knowing
that. Set "trading_enabled": false in the config to run monitor-only.

Strategy logic is IDENTICAL to the paper bot (strategies/day/bot_divflip.py):
  - Entry  : fresh RSI divergence -> LONG/SHORT (market)
  - DCA    : 3 fixed-distance legs @ 0.35%, martingale 3:4:1.5
  - SL/BE/TP : computed by the SAME core_divflip.py functions the paper bot
               uses — sl_price_divflip / be_should_activate / TP_PCT. The only
               difference is execution: the computed SL+TP are pushed to
               Bybit's server-side trading-stop each tick so a wick between
               cron runs still exits at exactly the level the paper bot uses.

State / log / status: bybit/data/
"""
from __future__ import annotations
import os, sys, json, time, logging, argparse
from datetime import datetime, timezone

import pandas as pd
import numpy as np

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BOT_DIR)

from bybit_client import BybitClient, BybitError
from core import build_features, detect_divergence, rsi_series
from core_divflip import (
    LEVERAGE, RISK_PCT, DCA_LEVELS, DCA_SPACING, SL_FROM_WORST,
    USE_BREAKEVEN, BE_TRIGGER_PCT, BE_BUFFER_PCT, TRAIL_DIST_PCT, DIV_FRESH_BARS,
    USE_FLIP, RSI_LONG_MAX, RSI_SHORT_MIN,
    USE_TAKE_PROFIT, TP_PCT,
    DIV_PIVOT_L, DIV_PIVOT_R, RSI_PERIOD, MARTINGALE_RATIOS,
    evaluate_signal_divflip, dca_price,
    sl_price_divflip, be_should_activate, per_level_qty,
)

# ─── Paths ───
DATA_DIR = os.path.join(BOT_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "state.json")
STATUS_FILE = os.path.join(DATA_DIR, "status.json")
LOG_FILE = os.path.join(DATA_DIR, "bot.log")

# ─── Logging ───
log = logging.getLogger("bot_divflip_bybit")
log.setLevel(logging.INFO)
log.handlers.clear()
fh = logging.FileHandler(LOG_FILE)
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(fh)
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(sh)
logging.getLogger("bybit_client").handlers = log.handlers
logging.getLogger("bybit_client").setLevel(logging.INFO)


# ─── .env loader (no python-dotenv dependency) ───
def load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


# ─── Config ───
def load_config(path):
    with open(path) as f:
        return json.load(f)


# ─── State I/O ───
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"position": None, "peak_equity": 0.0,
            "stats": {"total": 0, "wins": 0, "pnl": 0.0}, "trade_log": []}


def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, default=str, indent=2)


def write_status(payload):
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(STATUS_FILE, "w") as f:
        json.dump(payload, f, default=str, indent=2)


# ─── Rounding helpers ───
def round_qty(q, step):
    if step <= 0:
        return round(q, 3)
    return round(q - (q % step), 8)


def round_price(p, tick):
    if tick <= 0:
        return round(p, 2)
    n = round(p / tick)
    s = ("%.10f" % tick).rstrip("0")
    decimals = len(s.split(".")[1]) if "." in s else 0
    return round(n * tick, decimals)


def fmt_qty(q, step):
    s = ("%.10f" % step).rstrip("0")
    decimals = len(s.split(".")[1]) if "." in s else 0
    return f"{q:.{decimals}f}"


# ─── Klines -> DataFrame ───
def klines_to_df(rows):
    """Bybit row: [startTime, open, high, low, close, volume, turnover]."""
    if not rows:
        return None
    data = [{
        "timestamp": pd.to_datetime(int(r[0]), unit="ms"),
        "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
        "close": float(r[4]), "volume": float(r[5]),
    } for r in rows]
    return pd.DataFrame(data)


# ─── Trade logging after an exit ───
def log_closed_trade(state, pos, client, symbol, exit_reason_hint=None):
    """A position is gone from the exchange — record the realized trade.
    Pulls Bybit's closed-PnL records since this position opened."""
    entry_ms = pos.get("entry_ms")
    records = client.closed_pnl(symbol, start_ms=entry_ms, limit=50)
    # Keep only records at/after entry time (closed-pnl is newest-first).
    rel = [r for r in records if int(r.get("updatedTime", 0)) >= (entry_ms or 0)]
    if not rel:
        rel = records[:1]  # fallback: most recent record

    pnl_usd = sum(float(r.get("closedPnl", 0) or 0) for r in rel)
    exit_px = float(rel[0].get("avgExitPrice", 0) or 0) if rel else 0.0
    avg_entry = pos.get("avg_entry", pos.get("first_entry", 0.0))
    bal_before = pos.get("balance_at_entry", 0.0)
    pnl_pct = (pnl_usd / bal_before * 100) if bal_before > 0 else 0.0

    # Classify the exit by comparing the fill price to the last computed levels.
    reason = exit_reason_hint or "EXIT"
    last_tp = pos.get("last_tp")
    last_sl = pos.get("last_sl")
    if exit_px > 0:
        if last_tp and abs(exit_px - last_tp) / last_tp < 0.0015:
            reason = "TP"
        elif last_sl and abs(exit_px - last_sl) / last_sl < 0.0020:
            reason = "BE/TRAIL" if pos.get("be_activated") else "SL"

    rec = {
        "side": pos["side"],
        "first_entry": pos.get("first_entry"),
        "avg_entry": avg_entry,
        "exit": exit_px,
        "entries": pos.get("filled"),
        "qty_total": pos.get("qty_total"),
        "reason": reason,
        "pnl_usd": pnl_usd,
        "pnl_pct": pnl_pct,
        "leverage": pos.get("leverage"),
        "entry_time": pos.get("entry_time"),
        "exit_time": datetime.now(timezone.utc).isoformat(),
    }
    state.setdefault("trade_log", []).append(rec)
    state["trade_log"] = state["trade_log"][-200:]
    st = state["stats"]
    st["total"] += 1
    st["pnl"] += pnl_pct
    if pnl_usd > 0:
        st["wins"] += 1
    log.warning(f"  EXIT {pos['side']} via {reason} @${exit_px:.2f} | "
                f"avg_entry ${avg_entry:.2f} | net ${pnl_usd:+.2f} ({pnl_pct:+.2f}%)")
    return rec


# ─── Push the bot-computed SL + TP to Bybit's server-side stop ───
def sync_trading_stop(client, symbol, pos, info, live_px):
    """Recompute SL (composite: raw worst-anchored / BE floor / trail) and TP
    using the SAME core_divflip functions as the paper bot, then push both to
    Bybit so they are enforced server-side between cron ticks.

    Returns (sl_px, tp_px, breached) — breached=True if a level is already
    crossed at tick time (price gapped past it) and the caller must market-close
    now instead of relying on the server-side stop."""
    side = pos["side"]
    worst = pos["worst_entry"]
    avg = pos["avg_entry"]
    peak = pos.get("peak_price", pos["first_entry"])
    be = pos.get("be_activated", False)

    sl_px = sl_price_divflip(side, worst, avg, be, peak)
    tp_px = (avg * (1 + TP_PCT) if side == "LONG" else avg * (1 - TP_PCT)) \
        if USE_TAKE_PROFIT else None

    sl_r = round_price(sl_px, info["tick"])
    tp_r = round_price(tp_px, info["tick"]) if tp_px else None
    pos["last_sl"] = sl_r
    pos["last_tp"] = tp_r

    # Already breached? (price gapped past the level between ticks)
    if side == "LONG":
        breached = live_px <= sl_r or (tp_r and live_px >= tp_r)
    else:
        breached = live_px >= sl_r or (tp_r and live_px <= tp_r)
    if breached:
        return sl_r, tp_r, True

    client.set_trading_stop(symbol, take_profit=str(tp_r) if tp_r else None,
                            stop_loss=str(sl_r))
    return sl_r, tp_r, False


# ─── DCA resting limit orders ───
def place_dca_orders(client, symbol, info, pos, balance):
    """Place resting LIMIT orders for the DCA legs (L2..L{DCA_LEVELS}) at the
    exact divflip trigger prices. A limit order fills AT the trigger (or better)
    — replicating the paper bot's marked-at-trigger fills with no market-order
    slippage and no missed wicks. Returns a list of dca-order records."""
    side = pos["side"]
    side_api = "Buy" if side == "LONG" else "Sell"
    pos_lev = pos.get("leverage", LEVERAGE)
    orders = []
    prev_px = pos["first_entry"]
    for leg_idx in range(1, DCA_LEVELS):              # 1 -> L2, 2 -> L3
        trig = (prev_px * (1 - DCA_SPACING) if side == "LONG"
                else prev_px * (1 + DCA_SPACING))
        trig = round_price(trig, info["tick"])
        qty = round_qty(per_level_qty(balance, trig, leg_idx=leg_idx, leverage=pos_lev),
                        info["qty_step"])
        if qty < info["min_qty"]:
            log.info(f"  L{leg_idx+1} DCA limit skipped — qty {qty} below "
                     f"min {info['min_qty']} (needs more balance)")
            prev_px = trig
            continue
        resp = client.limit_order(symbol, side_api, fmt_qty(qty, info["qty_step"]), str(trig))
        if resp and resp.get("orderId"):
            orders.append({"level": leg_idx + 1, "order_id": resp["orderId"],
                           "price": trig, "qty": qty, "filled": False})
            log.warning(f"  L{leg_idx+1} DCA limit order placed: {qty}@${trig:,.2f}")
        else:
            log.error(f"  L{leg_idx+1} DCA limit order REJECTED by Bybit")
        prev_px = trig
    return orders


def cancel_dca_orders(client, symbol, pos):
    """Cancel any still-resting DCA limit orders — call when the position closes
    so a stale leg can't re-open a position later."""
    for d in (pos.get("dca_orders") or []):
        if not d.get("filled"):
            client.cancel_order(symbol, d["order_id"])


# ─── Connectivity preflight ───
def run_connectivity_check(client, symbol, api_key, api_secret) -> bool:
    """Test API reachability + key validity before the bot is allowed to run.
    Returns True only if every check passes. The install script gates the
    systemd timer on this — the bot is not started unless this passes."""
    log.info("─── Connectivity preflight ───")
    ok = True

    info = client.instrument_info(symbol)
    if info:
        log.info(f"  [PASS] public API — {symbol} reachable (copyTrading={info['copy_trading']})")
    else:
        log.error("  [FAIL] public API — cannot reach Bybit market data")
        ok = False

    px = client.live_price(symbol)
    if px:
        log.info(f"  [PASS] price feed — {symbol} last = ${px:,.2f}")
    else:
        log.error("  [FAIL] price feed — ticker unavailable")
        ok = False

    if not api_key or not api_secret:
        log.error("  [FAIL] API keys — BYBIT_API_KEY / BYBIT_API_SECRET not set in .env")
        ok = False
    else:
        bal = client.wallet_balance()
        if bal is not None:
            log.info(f"  [PASS] signed API — keys valid, wallet equity ${bal:,.2f}")
        else:
            log.error("  [FAIL] signed API — keys rejected or wallet unreadable "
                      "(check key permissions, IP whitelist, or set balance_override)")
            ok = False
        posn = client.position(symbol)
        if posn is not None:
            log.info("  [PASS] position read — account positions accessible")
        else:
            log.error("  [FAIL] position read — /v5/position/list failed")
            ok = False

    log.info("─── Preflight: " +
             ("ALL CHECKS PASSED — safe to start the bot" if ok
              else "FAILED — fix the above before the bot will trade") + " ───")
    return ok


# ─── Main tick ───
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(BOT_DIR, "config", "bybit_live.json"))
    ap.add_argument("--dry", action="store_true", help="Compute + log only, place NO orders")
    ap.add_argument("--check", action="store_true",
                    help="Connectivity preflight — test API reachability + keys, then exit")
    args, _ = ap.parse_known_args()

    load_dotenv(os.path.join(BOT_DIR, ".env"))
    cfg = load_config(args.config)

    SYMBOL = cfg.get("symbol", "BTCUSDT")
    BASE_URL = cfg["base_url"]
    API_KEY = os.environ.get(cfg.get("api_key_env", "BYBIT_API_KEY"), "")
    API_SECRET = os.environ.get(cfg.get("api_secret_env", "BYBIT_API_SECRET"), "")
    TRADING_ENABLED = cfg.get("trading_enabled", False) and not args.dry
    BALANCE_OVERRIDE = float(cfg.get("balance_override", 0) or 0)

    log.info("=" * 64)
    mode = "LIVE-TRADING" if TRADING_ENABLED else "MONITOR-ONLY (no orders)"
    log.info(f"Divflip v1 Bybit bot — {mode} | {SYMBOL} | {BASE_URL}")
    log.warning("Divflip is documented OVERFIT — OOS 2021-26 = -100%. "
                "See memory/divflip_tv_tuned_live.md")

    if TRADING_ENABLED and (not API_KEY or not API_SECRET):
        log.error("trading_enabled but BYBIT_API_KEY / BYBIT_API_SECRET missing in .env — aborting")
        return

    client = BybitClient(API_KEY, API_SECRET, BASE_URL)

    # Connectivity preflight — `--check` tests the API + keys and exits. The
    # install script runs this and only starts the bot's timer if it passes.
    if args.check:
        sys.exit(0 if run_connectivity_check(client, SYMBOL, API_KEY, API_SECRET) else 1)

    state = load_state()

    # ─ Instrument info (qty step, min qty, tick) ─
    info = client.instrument_info(SYMBOL)
    if not info:
        log.error("instrument info unavailable — skipping tick")
        return
    log.info(f"  step={info['qty_step']} min={info['min_qty']} tick={info['tick']} "
             f"copyTrading={info['copy_trading']}")

    # ─ Market data ─
    rows_5m = client.klines(SYMBOL, "5", 500)
    rows_1d = client.klines(SYMBOL, "D", 100)
    df_5m = klines_to_df(rows_5m)
    df_1d = klines_to_df(rows_1d)
    if df_5m is None or len(df_5m) < 100 or df_1d is None or len(df_1d) < 60:
        log.error("insufficient klines — skipping tick")
        return
    live_px = client.live_price(SYMBOL)
    if live_px is None:
        log.error("live price unavailable — skipping tick")
        return

    # ─ Features + divergence (TV-tuned overrides, identical to paper bot) ─
    df = build_features(df_5m, df_1d)
    df["rsi"] = rsi_series(df["close"], RSI_PERIOD)
    df = detect_divergence(df, DIV_PIVOT_L, DIV_PIVOT_R)
    last_idx = len(df) - 2  # last CLOSED 5m bar
    last = df.iloc[last_idx]
    close_px = float(last["close"])

    # ─ Account equity (sizes every order) ─
    # Copy-trading accounts may not report via /v5/account/wallet-balance — if
    # so, set "balance_override" in the config to the equity to size against.
    if BALANCE_OVERRIDE > 0:
        balance = BALANCE_OVERRIDE
        log.info(f"  Equity: ${balance:,.2f} (balance_override)")
    elif API_KEY and API_SECRET:
        balance = client.wallet_balance()
    else:
        balance = None
    if balance is None:
        if TRADING_ENABLED:
            log.error("wallet balance unavailable — copy-trading accounts may not "
                      "expose it; set \"balance_override\" in the config. Skipping tick.")
            return
        balance = 0.0
    if balance > state.get("peak_equity", 0):
        state["peak_equity"] = balance
    peak = state.get("peak_equity", balance)
    dd_pct = (balance / peak - 1) if peak > 0 else 0.0
    log.info(f"  Balance: ${balance:,.2f} | {SYMBOL}: ${close_px:,.2f} | live: ${live_px:,.2f}")

    pos = state.get("position")
    current_side = pos["side"] if pos else None

    sig = evaluate_signal_divflip(df, last_idx, current_side)
    bsb = sig.raw.get("bars_since_bear_div", 9999)
    bsu = sig.raw.get("bars_since_bull_div", 9999)
    log.info(f"  Signal: {sig.side or 'NONE'} | div bear="
             f"{bsb if bsb <= 50 else '—'} / bull={bsu if bsu <= 50 else '—'} "
             f"(fresh ≤ {DIV_FRESH_BARS}b)")

    # ─ Reconcile local state with the exchange ─
    exch = client.position(SYMBOL) if (API_KEY and API_SECRET) else None
    closed_this_tick = False

    if exch is not None:
        if pos is None and exch["side"] is not None:
            # Position exists on Bybit but not in our state — adopt it.
            pos = {
                "side": exch["side"],
                "first_entry": exch["avg_price"],
                "avg_entry": exch["avg_price"],
                "worst_entry": exch["avg_price"],
                "peak_price": exch["avg_price"],
                "qty_total": exch["qty"],
                "filled": 1,
                "be_activated": False,
                "leverage": exch["leverage"] or LEVERAGE,
                "leg_fills": [exch["avg_price"]],
                "balance_at_entry": balance,
                "unrealised_pnl": exch["unrealised_pnl"],
                "entry_time": datetime.now(timezone.utc).isoformat(),
                "entry_ms": int(time.time() * 1000),
            }
            state["position"] = pos
            log.warning(f"  Adopted exchange position {pos['side']} "
                        f"{pos['qty_total']}@${pos['first_entry']:.2f}")
        elif pos is not None and exch["side"] is None:
            # We thought we held a position; exchange is flat -> SL/TP fired
            # server-side between ticks. Cancel resting DCA legs, log the trade.
            cancel_dca_orders(client, SYMBOL, pos)
            log_closed_trade(state, pos, client, SYMBOL)
            state["position"] = None
            pos = None
            closed_this_tick = True
        elif pos is not None and exch["side"] is not None:
            # Both agree — exchange is the source of truth for avg/qty/uPnL.
            pos["avg_entry"] = exch["avg_price"] or pos["avg_entry"]
            pos["qty_total"] = exch["qty"]
            pos["unrealised_pnl"] = exch["unrealised_pnl"]

    # ─ Manage an open position ─
    if pos is not None:
        side = pos["side"]
        # Peak / trough water-mark (drives the trailing SL).
        prev_peak = pos.get("peak_price", pos["first_entry"])
        pos["peak_price"] = max(prev_peak, live_px) if side == "LONG" else min(prev_peak, live_px)

        # ── DCA — resting LIMIT orders at the trigger prices ──
        # L2..Ln are placed as limit orders right after L1 opens (see the entry
        # block / place_dca_orders) so they fill AT the trigger price like the
        # paper bot's marked fills. Here we (a) place them retroactively if a
        # position predates this feature, and (b) detect fills.
        if TRADING_ENABLED and pos.get("dca_orders") is None and pos["filled"] < DCA_LEVELS:
            log.info("  no DCA orders on this position — placing them now")
            pos["dca_orders"] = place_dca_orders(
                client, SYMBOL, info, pos, pos.get("balance_at_entry", balance))

        if TRADING_ENABLED and pos.get("dca_orders"):
            oo = client.open_orders(SYMBOL)
            if oo is not None:
                open_ids = {o["order_id"] for o in oo}
                for d in pos["dca_orders"]:
                    # An order gone from the open list has filled — we only
                    # cancel DCA orders when the position itself closes.
                    if not d["filled"] and d["order_id"] not in open_ids:
                        d["filled"] = True
                        pos["filled"] += 1
                        if side == "LONG":
                            pos["worst_entry"] = min(pos["worst_entry"], d["price"])
                        else:
                            pos["worst_entry"] = max(pos["worst_entry"], d["price"])
                        log.warning(f"  L{d['level']} DCA limit FILLED @${d['price']:,.2f}"
                                    f" — filled {pos['filled']}/{DCA_LEVELS}, "
                                    f"worst=${pos['worst_entry']:,.2f} (SL anchors here)")

        # ── BE arm — avg-anchored, sticky (identical to paper bot) ──
        if not pos.get("be_activated") and be_should_activate(side, pos["avg_entry"], live_px):
            pos["be_activated"] = True
            log.warning(f"  BE armed — fav crossed {BE_TRIGGER_PCT*100:.2f}% from "
                        f"avg ${pos['avg_entry']:.2f}; trailing SL now active")

        # ── SL + TP — same composite logic as paper, enforced server-side ──
        sl_r = tp_r = None
        if TRADING_ENABLED:
            sl_r, tp_r, breached = sync_trading_stop(client, SYMBOL, pos, info, live_px)
            if breached:
                # price gapped past SL/TP — close now at market
                close_side = "Sell" if side == "LONG" else "Buy"
                resp = client.market_order(SYMBOL, close_side,
                                           fmt_qty(pos["qty_total"], info["qty_step"]),
                                           reduce_only=True)
                if resp:
                    time.sleep(1.0)
                    ex2 = client.position(SYMBOL)
                    if ex2 is not None and ex2["side"] is not None:
                        log.error("  gap-close order placed but position still "
                                  "open — will retry next tick")
                    else:
                        cancel_dca_orders(client, SYMBOL, pos)
                        log_closed_trade(state, pos, client, SYMBOL)
                        state["position"] = None
                        pos = None
                        closed_this_tick = True
                        log.warning("  Gap exit — market-closed at tick")
        else:
            # monitor-only: still compute the levels so the log/dashboard show them
            sl_unr = sl_price_divflip(side, pos["worst_entry"], pos["avg_entry"],
                                      pos.get("be_activated", False),
                                      pos.get("peak_price", pos["first_entry"]))
            sl_r = round_price(sl_unr, info["tick"])
            tp_r = round_price(pos["avg_entry"] * (1 + TP_PCT if side == "LONG" else 1 - TP_PCT),
                               info["tick"]) if USE_TAKE_PROFIT else None
            pos["last_sl"], pos["last_tp"] = sl_r, tp_r

        if pos is not None:
            fav = ((live_px - pos["avg_entry"]) / pos["avg_entry"] * 100) * (1 if side == "LONG" else -1)
            upnl = pos.get("unrealised_pnl", 0.0)
            be_tag = " [BE]" if pos.get("be_activated") else ""
            log.info(f"  IN {side} L{pos['filled']}/{DCA_LEVELS} avg=${pos['avg_entry']:.2f} "
                     f"live=${live_px:.2f} fav={fav:+.2f}% uPnL=${upnl:+.2f} "
                     f"SL=${sl_r} TP=${tp_r}{be_tag}")

    # ─ Entry — open a new position if flat and a fresh divergence fired ─
    if state.get("position") is None and not closed_this_tick and sig.side:
        if not TRADING_ENABLED:
            log.info(f"  [monitor] would OPEN {sig.side} @${live_px:.2f}")
        else:
            client.set_leverage(SYMBOL, LEVERAGE)
            qty = per_level_qty(balance, live_px, leg_idx=0, leverage=LEVERAGE)
            qty = round_qty(qty, info["qty_step"])
            if qty < info["min_qty"]:
                log.warning(f"  entry qty {qty} below min {info['min_qty']} — skipped")
            else:
                side_api = "Buy" if sig.side == "LONG" else "Sell"
                resp = client.market_order(SYMBOL, side_api, fmt_qty(qty, info["qty_step"]))
                if resp:
                    # order/create is async — confirm the fill by re-reading the
                    # position before recording any local state.
                    time.sleep(1.0)
                    ex2 = client.position(SYMBOL)
                    if ex2 is not None and ex2["side"] is None:
                        log.error("  entry order placed but exchange shows no "
                                  "position — staying flat, retry on next signal")
                        resp = None
                if resp:
                    fill = ex2["avg_price"] if (ex2 and ex2["side"]) else live_px
                    qty_total = ex2["qty"] if (ex2 and ex2["side"]) else qty
                    pos = {
                        "side": sig.side,
                        "first_entry": fill,
                        "avg_entry": fill,
                        "worst_entry": fill,
                        "peak_price": fill,
                        "qty_total": qty_total,
                        "filled": 1,
                        "be_activated": False,
                        "leverage": LEVERAGE,
                        "leg_fills": [fill],
                        "balance_at_entry": balance,
                        "unrealised_pnl": ex2["unrealised_pnl"] if (ex2 and ex2["side"]) else 0.0,
                        "entry_time": datetime.now(timezone.utc).isoformat(),
                        "entry_ms": int(time.time() * 1000),
                    }
                    state["position"] = pos
                    # Place the server-side SL + TP IMMEDIATELY — the position
                    # is protected the instant L1 fills, before anything else.
                    sync_trading_stop(client, SYMBOL, pos, info, live_px)
                    # Then place the resting DCA limit orders (L2..Ln).
                    pos["dca_orders"] = place_dca_orders(client, SYMBOL, info, pos, balance)
                    log.warning(f"  OPENED {sig.side} {qty_total}@${fill:.2f} | "
                                f"SL=${pos.get('last_sl')} TP=${pos.get('last_tp')} | "
                                f"{len(pos['dca_orders'])} DCA limit order(s) resting")

    # ─ Persist + status ─
    stats = state["stats"]
    wr = (stats["wins"] / stats["total"] * 100) if stats["total"] > 0 else 0.0
    log.info(f"  Stats: {stats['total']} trades | WR {wr:.0f}% | cumPnL {stats['pnl']:+.2f}%")

    save_state(state)

    pos = state.get("position")
    pos_status = None
    if pos:
        fav = ((live_px - pos["avg_entry"]) / pos["avg_entry"] * 100) * \
              (1 if pos["side"] == "LONG" else -1)
        # BE arm price — avg-anchored (divflip v1), same anchor as the paper bot.
        be_arm = (pos["avg_entry"] * (1 + BE_TRIGGER_PCT) if pos["side"] == "LONG"
                  else pos["avg_entry"] * (1 - BE_TRIGGER_PCT))
        pos_status = {
            "side": pos["side"], "first_entry": pos["first_entry"],
            "avg_entry": pos["avg_entry"], "worst_entry": pos["worst_entry"],
            "peak_price": pos.get("peak_price"), "qty_total": pos["qty_total"],
            "filled": pos["filled"], "sl_px": pos.get("last_sl"),
            "tp_px": pos.get("last_tp"), "be_activated": pos.get("be_activated"),
            "be_arm_px": be_arm,
            "fav_pct": fav, "unrealised_pnl": pos.get("unrealised_pnl"),
            "entry_time": pos.get("entry_time"),
        }

    write_status({
        "env": "bybit_live", "exchange": "bybit", "pair": SYMBOL,
        "trading_enabled": TRADING_ENABLED,
        "connectivity": {"public": True, "signed": exch is not None},
        "price": close_px, "live_price": live_px,
        "balance": balance, "peak_equity": peak, "drawdown_pct": dd_pct,
        "position": pos_status, "signal": sig.side,
        "indicators": sig.raw, "conditions": sig.conditions,
        "stats": state["stats"],
        "strategy": f"Divergence-Flip v1 [Bybit {'LIVE' if TRADING_ENABLED else 'MONITOR'}] "
                    f"({DCA_LEVELS} DCA @ {DCA_SPACING*100:.2f}% / SL {SL_FROM_WORST*100:.1f}% "
                    f"worst / TP {TP_PCT*100:.1f}% avg / BE {BE_TRIGGER_PCT*100:.2f}%)",
        "state": "IN_POSITION" if pos else "FLAT",
    })
    log.info("=" * 64 + "\n")


if __name__ == "__main__":
    try:
        main()
    except BybitError as e:
        log.error(f"Bybit API error: {e}")
        sys.exit(1)
    except Exception as e:
        log.exception(f"FATAL: {e}")
        sys.exit(1)
