#!/usr/bin/env python3
"""bot_claude.py — "Claude" RSI5 mean-reversion + EMA200 gate + equity DD-stop.

The user's pick, tuned for max return / min drawdown at a fixed 3x. This is the
best point on the RSI-only frontier from my 5y BTCUSDT 5m backtest:

  RSI5 20/80 + 5m EMA200 trend gate, 3x, 2-leg DCA @0.5%, adaptive TP
  (0.50% pre-DCA / 0.25% post-DCA from avg), 1% catastrophic SL from worst
  entry, consecutive-loss circuit breaker — PLUS a 30% EQUITY DRAWDOWN-STOP
  that pauses NEW entries whenever the account is >30% below its peak.

Backtest (2021-05 → 2026-05, conservative SL-before-TP fills):
    baseline (no DD-stop):  +506% / maxDD -47.7%
    + 30% DD-stop:          +539% / maxDD -30.1%   ← this config
The DD-stop is a Pareto win: higher return AND much lower drawdown. It works by
sitting out the death-spiral stretches (entries off while >30% underwater) and
re-engaging as equity recovers.

Mechanics mirror bot_rsiscalp.py exactly (same DCA/TP/SL/breaker helpers), with
two changes: RSI5 20/80 instead of RSI9 25/75, and a hard 5m-EMA200 entry gate.

PAPER-ONLY. State/log in data/paper_claude/ (override via CLAUDE_DATA_DIR).
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
# Reuse the rsiscalp DCA/TP/SL helpers — their module constants (LEVERAGE 3,
# DCA 2 legs @0.5%, TP .50/.25%, SL 1% from worst, breaker 2/2h) are EXACTLY
# the config the backtest numbers above came from.
from core_rsiscalp import (
    LEVERAGE, DCA_LEVELS, DCA_SPACING,
    USE_TAKE_PROFIT, TP_PCT_SINGLE, TP_PCT_DCA, tp_pct_for,
    USE_STOP_LOSS, SL_FROM_WORST,
    USE_CIRCUIT_BREAKER,
    dca_price, sl_price, per_level_qty,
)

# Cooldown rule (user 2026-06-04): pause 15 min after EVERY loss, instead of
# the rsiscalp default (2 losses -> 2h). Backtest: ~neutral here (+509%/-30%
# vs +539%/-30%) because the EMA200 gate + DD-stop already cap the risk.
BREAKER_LOSSES = 1          # every single loss triggers the cooldown
BREAKER_PAUSE_HOURS = 0.25  # 15 minutes

# ─── Claude-specific config ───
PAIR = "BTCUSDT"
INITIAL_BALANCE = 5000.0
COMMISSION_PCT = 0.0004
BINANCE_BASE = "https://fapi.binance.com"

RSI_PERIOD = 5             # faster than rsiscalp's 9 → ~2.2 signals/day
RSI_OVERSOLD = 20          # RSI5 ≤ 20 → LONG
RSI_OVERBOUGHT = 80        # RSI5 ≥ 80 → SHORT
TREND_EMA = 200            # 5m EMA200 gate: LONG only above, SHORT only below
DRAWDOWN_STOP = 0.30       # pause new entries while >30% below peak equity
DEADLOCK_RESET_HOURS = 24  # safety: if stuck flat & blocked this long, re-baseline peak

DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("CLAUDE_DATA_DIR", "paper_claude"))
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE  = os.path.join(DATA_DIR, "state.json")
STATUS_FILE = os.path.join(DATA_DIR, "status.json")
LOG_FILE    = os.path.join(DATA_DIR, "bot.log")

log = logging.getLogger("bot_claude")
log.setLevel(logging.INFO)
log.handlers.clear()
fh = logging.FileHandler(LOG_FILE); fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")); log.addHandler(fh)
sh = logging.StreamHandler(sys.stdout); sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")); log.addHandler(sh)


# ─── Binance public fetchers ───
def fetch_klines(interval: str, limit: int = 500):
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
        log.error(f"klines fetch failed ({interval}): {e}"); return None


def fetch_live_price():
    try:
        r = requests.get(f"{BINANCE_BASE}/fapi/v1/ticker/price", params={"symbol": PAIR}, timeout=10)
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception as e:
        log.error(f"live price fetch failed: {e}"); return None


# ─── State I/O ───
def load_state():
    if not os.path.exists(STATE_FILE):
        return {"balance": INITIAL_BALANCE, "peak_equity": INITIAL_BALANCE,
                "position": None, "stats": {"total": 0, "wins": 0, "pnl": 0.0}, "trade_log": []}
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, default=str, indent=2)


# ─── Position management (mirrors bot_rsiscalp.py) ───
def avg_entry_of(pos):
    entries = pos.get("entries", [])
    tq = sum(e["qty"] for e in entries)
    return sum(e["px"] * e["qty"] for e in entries) / tq if tq > 0 else pos.get("first_entry", 0.0)


def _record_trade(state, rec, is_win):
    state.setdefault("trade_log", []).append(rec)
    state["trade_log"] = state["trade_log"][-200:]
    state["stats"]["total"] += 1
    state["stats"]["pnl"] += rec["pnl_pct"]
    if is_win:
        state["stats"]["wins"] += 1


def rsi_signal(rsi):
    if rsi is None:
        return None
    if rsi <= RSI_OVERSOLD:
        return "LONG"
    if rsi >= RSI_OVERBOUGHT:
        return "SHORT"
    return None


def open_position(state, side, entry_px, rsi_val):
    qty = round(per_level_qty(state["balance"], entry_px), 3)
    if qty <= 0:
        log.warning(f"  qty {qty} too small"); return
    state["balance"] -= entry_px * qty * COMMISSION_PCT
    state["position"] = {
        "side": side, "first_entry": entry_px, "worst_entry": entry_px,
        "entries": [{"px": entry_px, "qty": qty}], "qty_total": qty, "filled": 1,
        "leverage": LEVERAGE, "entry_time": datetime.now(timezone.utc).isoformat(),
        "rsi_at_entry": rsi_val,
    }
    log.warning(f"  OPENED {side} {qty}@${entry_px:.2f} (RSI5 {rsi_val:.1f}) | balance ${state['balance']:.2f}")


def maybe_dca(pos, live_px, balance, state):
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
    log.warning(f"  DCA L{pos['filled']} {side} {qty}@${trigger:.2f} | new avg=${avg_entry_of(pos):.2f}")
    return True


def close_position(state, pos, exit_px, reason):
    side = pos["side"]; qty_total = pos["qty_total"]; avg = avg_entry_of(pos)
    gross = (exit_px - avg) * qty_total if side == "LONG" else (avg - exit_px) * qty_total
    net = gross - exit_px * qty_total * COMMISSION_PCT
    bal_before = state["balance"]; state["balance"] += net
    move = (exit_px / avg - 1) * 100 * (1 if side == "LONG" else -1)
    _record_trade(state, {
        "side": side, "first_entry": pos["first_entry"], "avg_entry": avg, "exit": exit_px,
        "entries": len(pos.get("entries", [])), "qty_total": qty_total, "reason": reason,
        "pnl_usd": net, "pnl_pct": (net / bal_before * 100) if bal_before else 0.0, "price_move_pct": move,
        "leverage": pos.get("leverage"), "entry_time": pos.get("entry_time"),
        "exit_time": datetime.now(timezone.utc).isoformat(), "rsi_at_entry": pos.get("rsi_at_entry"),
    }, is_win=net > 0)
    log.warning(f"  EXIT {side} via {reason} @${exit_px:.2f} | avg ${avg:.2f} | net ${net:+.2f} "
                f"(price {move:+.2f}%) | balance ${state['balance']:.2f}")
    if USE_CIRCUIT_BREAKER:
        if net <= 0:
            state["consec_losses"] = state.get("consec_losses", 0) + 1
            if state["consec_losses"] >= BREAKER_LOSSES:
                until = datetime.now(timezone.utc) + timedelta(hours=BREAKER_PAUSE_HOURS)
                state["pause_until"] = until.isoformat(); state["consec_losses"] = 0
                log.warning(f"  CIRCUIT BREAKER: {BREAKER_LOSSES} losses — pause until {until.isoformat()[:16]}")
        else:
            state["consec_losses"] = 0


# ─── Main tick ───
def main():
    log.info("=" * 60)
    log.info(f"Claude Paper Bot — RSI{RSI_PERIOD} {RSI_OVERSOLD}/{RSI_OVERBOUGHT} | 5m EMA{TREND_EMA} gate | "
             f"{DCA_LEVELS} DCA @ {DCA_SPACING*100:.2f}% | TP {TP_PCT_SINGLE*100:.2f}/{TP_PCT_DCA*100:.2f}% | "
             f"SL {SL_FROM_WORST*100:.1f}% | DD-stop {DRAWDOWN_STOP*100:.0f}% | {LEVERAGE:.0f}x")
    state = load_state()

    df = fetch_klines("5m", 500)
    if df is None or len(df) < TREND_EMA + 10:
        log.error("insufficient klines"); return
    live_px = fetch_live_price()
    if live_px is None:
        log.error("live price unavailable"); return

    df["rsi"] = rsi_series(df["close"], RSI_PERIOD)
    df["ema"] = df["close"].ewm(span=TREND_EMA, adjust=False).mean()
    last = df.iloc[-2]  # last CLOSED 5m bar
    close_px = float(last["close"])
    rsi_val = float(last["rsi"]) if pd.notna(last["rsi"]) else None
    ema_val = float(last["ema"])
    trend = "UP" if close_px > ema_val else "DOWN"
    sig = rsi_signal(rsi_val)

    # peak/drawdown
    if state["balance"] > state.get("peak_equity", 0):
        state["peak_equity"] = state["balance"]
    peak = state.get("peak_equity", state["balance"])
    dd_pct = (state["balance"] / peak - 1) if peak > 0 else 0.0

    log.info(f"  Balance: ${state['balance']:,.2f} | {PAIR}: ${close_px:,.2f} | live: ${live_px:,.2f} | "
             f"RSI5 {rsi_val:.1f} | EMA{TREND_EMA} {ema_val:,.0f} ({trend}) | Signal {sig or 'NONE'} | DD {dd_pct*100:+.1f}%"
             if rsi_val is not None else f"  Balance: ${state['balance']:,.2f} | RSI n/a")

    pos = state.get("position")
    exit_this_tick = False

    # manage open position
    if pos:
        side = pos["side"]
        if maybe_dca(pos, live_px, state["balance"], state):
            pos = state["position"]
        avg = avg_entry_of(pos)
        exit_reason = exit_px = None
        if USE_TAKE_PROFIT:
            tp_pct = tp_pct_for(pos["filled"])
            tp = avg * (1 + tp_pct) if side == "LONG" else avg * (1 - tp_pct)
            if (side == "LONG" and live_px >= tp) or (side == "SHORT" and live_px <= tp):
                exit_reason, exit_px = "TP", tp
        if exit_px is None and USE_STOP_LOSS:
            slp = sl_price(side, pos["worst_entry"])
            if slp is not None and ((side == "LONG" and live_px <= slp) or (side == "SHORT" and live_px >= slp)):
                exit_reason, exit_px = "SL", slp
        if exit_px is not None:
            close_position(state, pos, exit_px, exit_reason)
            state["position"] = None; pos = None; exit_this_tick = True
        else:
            fav = ((live_px - avg) / avg * 100) * (1 if side == "LONG" else -1)
            log.info(f"  IN {side} L{pos['filled']}/{DCA_LEVELS} avg=${avg:.2f} live=${live_px:.2f} fav={fav:+.2f}%")

    # ── entry gates: trend, circuit breaker, equity DD-stop ──
    block_reason = None
    if sig and USE_CIRCUIT_BREAKER and state.get("pause_until"):
        try:
            if datetime.now(timezone.utc) < datetime.fromisoformat(state["pause_until"]):
                block_reason = f"circuit breaker — paused (until {state['pause_until'][:16]} UTC)"; sig = None
        except Exception:
            pass
    if sig and trend is not None:
        if (sig == "LONG" and trend != "UP") or (sig == "SHORT" and trend != "DOWN"):
            block_reason = f"{sig} blocked — 5m EMA{TREND_EMA} trend is {trend}"; sig = None
    # equity drawdown stop (with deadlock safety)
    if sig and dd_pct <= -DRAWDOWN_STOP:
        since = state.get("ddstop_since")
        now = datetime.now(timezone.utc)
        if since is None:
            state["ddstop_since"] = now.isoformat()
        else:
            try:
                stuck_h = (now - datetime.fromisoformat(since)).total_seconds() / 3600
            except Exception:
                stuck_h = 0
            if stuck_h >= DEADLOCK_RESET_HOURS and state.get("position") is None:
                state["peak_equity"] = state["balance"]; state.pop("ddstop_since", None)
                log.warning(f"  DD-stop: re-baselined peak after {stuck_h:.0f}h stuck flat")
        if dd_pct <= -DRAWDOWN_STOP and state.get("ddstop_since"):
            block_reason = f"equity DD-stop — {dd_pct*100:.0f}% below peak (>{DRAWDOWN_STOP*100:.0f}%)"; sig = None
    elif dd_pct > -DRAWDOWN_STOP:
        state.pop("ddstop_since", None)

    if block_reason:
        log.info(f"  {block_reason}")
    if state["position"] is None and not exit_this_tick and sig:
        open_position(state, sig, live_px, rsi_val)

    stats = state["stats"]
    wr = (stats["wins"] / stats["total"] * 100) if stats["total"] else 0.0
    log.info(f"  Stats: {stats['total']} trades | WR {wr:.0f}% | PnL {(state['balance']/INITIAL_BALANCE-1)*100:+.2f}%")

    save_state(state)

    # status.json (same shape the dashboard/server expect)
    pos = state.get("position")
    pos_status = None
    if pos:
        avg_e = avg_entry_of(pos); slp = sl_price(pos["side"], pos["worst_entry"])
        tp_pct = tp_pct_for(pos["filled"])
        tp_p = (avg_e * (1 + tp_pct) if pos["side"] == "LONG" else avg_e * (1 - tp_pct)) if USE_TAKE_PROFIT else None
        pos_status = {
            "side": pos["side"], "first_entry": pos["first_entry"], "avg_entry": avg_e,
            "worst_entry": pos["worst_entry"], "qty_total": pos["qty_total"], "filled": pos["filled"],
            "tp_px": tp_p, "sl_px": slp,
            "fav_pct": ((live_px - avg_e) / avg_e * 100) * (1 if pos["side"] == "LONG" else -1),
            "entry_time": pos.get("entry_time"),
        }
    # ── live entry checks for the dashboard (current vs required) ──
    def _c(name, cur, ok):
        return {"name": name, "cur": cur, "ok": bool(ok)}
    _rsis = f"{rsi_val:.0f}" if rsi_val is not None else "n/a"
    _free = not block_reason  # not paused / not DD-stopped
    checks = {
        "LONG": [
            _c(f"RSI5 ≤ {RSI_OVERSOLD} (oversold)", _rsis, rsi_val is not None and rsi_val <= RSI_OVERSOLD),
            _c("Price > EMA200 (uptrend)", f"{close_px:.0f} vs {ema_val:.0f}", trend == "UP"),
            _c("Not paused / DD-stopped", "ok" if _free else "blocked", _free),
        ],
        "SHORT": [
            _c(f"RSI5 ≥ {RSI_OVERBOUGHT} (overbought)", _rsis, rsi_val is not None and rsi_val >= RSI_OVERBOUGHT),
            _c("Price < EMA200 (downtrend)", f"{close_px:.0f} vs {ema_val:.0f}", trend == "DOWN"),
            _c("Not paused / DD-stopped", "ok" if _free else "blocked", _free),
        ],
    }
    with open(STATUS_FILE, "w") as f:
        json.dump({
            "env": os.environ.get("CLAUDE_DATA_DIR", "paper_claude"), "pair": PAIR,
            "price": close_px, "live_price": live_px, "balance": state["balance"],
            "peak_equity": peak, "drawdown_pct": dd_pct, "position": pos_status, "signal": sig,
            "indicators": {"rsi": rsi_val, "rsi_oversold": RSI_OVERSOLD, "rsi_overbought": RSI_OVERBOUGHT,
                           "price": close_px, "ema200": ema_val},
            "regime": f"5m {trend}", "checks": checks,
            "trend_5m": trend, "block_reason": block_reason, "stats": state["stats"],
            "strategy": f"Claude RSI{RSI_PERIOD} {RSI_OVERSOLD}/{RSI_OVERBOUGHT} + 5m EMA{TREND_EMA} gate / "
                        f"{DCA_LEVELS} DCA @ {DCA_SPACING*100:.2f}% / TP {TP_PCT_SINGLE*100:.2f}·{TP_PCT_DCA*100:.2f}% / "
                        f"SL {SL_FROM_WORST*100:.1f}% / {DRAWDOWN_STOP*100:.0f}% DD-stop / {LEVERAGE:.0f}x [PAPER]",
            "paper_mode": True, "state": "IN_POSITION" if pos else "FLAT",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, f, default=str, indent=2)
    log.info("=" * 60 + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception(f"FATAL: {e}")
        sys.exit(1)
