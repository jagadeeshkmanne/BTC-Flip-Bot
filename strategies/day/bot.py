#!/usr/bin/env python3
"""
bot.py — S/R DCA Day Trader live bot (5m exec + 1h bias + 1d S/R).

Runs every 5 min via cron. Fetches 5m klines + 1d klines, computes prev-day
H/L/mid + bias, manages DCA position (entry → L1 → optional L2 → TP/SL/BE).

Architecture mirrors strategies/swing/bot.py:
  core.py  — strategy logic (entry detection, sizing, SL/TP computation)
  bot.py   — exchange I/O, 5-min cron, position state, BE tracking

PAIR: BTCUSDT (separate from swing which runs BNBUSDT).
State file: data/{env}/state_day.json (separate from swing's state.json).
"""
from __future__ import annotations
import os, sys, json, time, hmac, hashlib, logging, argparse
from datetime import datetime, timezone
import requests
import pandas as pd
import numpy as np

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(os.path.dirname(STRATEGY_DIR))
sys.path.insert(0, STRATEGY_DIR)

from core import (
    LEVERAGE, RISK_PCT, DCA_LEVELS, DCA_SPACING, SL_BELOW_WORST, SUPPORT_ZONE,
    CLOSE_HOUR,
    build_features, evaluate_signal,
    entry_price_zone, dca_price, sl_price, tp_price, per_level_qty,
)


# ─── Config ───
def load_dotenv(path):
    if not os.path.exists(path): return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

load_dotenv(os.path.join(BOT_DIR, ".env"))

ap = argparse.ArgumentParser()
ap.add_argument("--env", default="testnet", choices=["testnet", "production"])
ap.add_argument("--dry", action="store_true", help="Log signals only, no orders")
ARGS, _ = ap.parse_known_args()
ENV = ARGS.env

PAIR = "BTCUSDT"
LEV = int(LEVERAGE)
if ENV == "testnet":
    API_KEY = os.environ.get("TESTNET_API_KEY", "")
    API_SECRET = os.environ.get("TESTNET_API_SECRET", "")
    BASE_URL = "https://testnet.binancefuture.com"
else:
    API_KEY = os.environ.get("PRODUCTION_API_KEY", "")
    API_SECRET = os.environ.get("PRODUCTION_API_SECRET", "")
    BASE_URL = "https://fapi.binance.com"

DATA_DIR = os.path.join(BOT_DIR, "data", ENV)
STATE_FILE = os.path.join(DATA_DIR, "state_day.json")
STATUS_FILE = os.path.join(DATA_DIR, "status_day.json")
LOG_FILE = os.path.join(DATA_DIR, "bot_day.log")
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)])
log = logging.getLogger("bot_day")

if not API_KEY or not API_SECRET:
    log.error(f"Missing API keys for {ENV}"); sys.exit(1)


# ─── Binance client (same as swing) ───
class BinanceClient:
    def __init__(self, key, secret, base_url):
        self.key = key; self.secret = secret; self.base = base_url
        self.s = requests.Session()
        self.s.headers.update({"X-MBX-APIKEY": key})

    def _sign(self, params):
        q = "&".join(f"{k}={v}" for k, v in params.items())
        sig = hmac.new(self.secret.encode(), q.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    def _req(self, method, path, params=None, signed=False):
        params = params or {}
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 5000
            params = self._sign(params)
        url = self.base + path
        for attempt in range(3):
            try:
                r = self.s.request(method, url, params=params, timeout=10)
                if r.status_code == 200: return r.json()
                log.warning(f"  HTTP {r.status_code}: {r.text[:200]}")
                if r.status_code in (418, 429): time.sleep(5); continue
                return None
            except Exception as e:
                log.warning(f"  Request err: {e}"); time.sleep(2)
        return None

    def klines(self, symbol, interval, limit=500):
        data = self._req("GET", "/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
        if not data: return None
        df = pd.DataFrame(data, columns=["ot","open","high","low","close","volume","ct","qav","trades","tbbav","tbqav","ig"])
        df = df.astype({"open":float,"high":float,"low":float,"close":float,"volume":float})
        df["timestamp"] = pd.to_datetime(df["ot"], unit="ms")
        return df[["timestamp","open","high","low","close","volume"]]

    def account(self):
        return self._req("GET", "/fapi/v2/account", signed=True)

    def positions(self, symbol):
        acc = self.account()
        if not acc: return []
        return [p for p in acc.get("positions", []) if p["symbol"] == symbol and float(p["positionAmt"]) != 0]

    def set_leverage(self, symbol, lev):
        return self._req("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": lev}, signed=True)

    def market_order(self, symbol, side, qty, reduce_only=False):
        params = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": f"{qty}"}
        if reduce_only: params["reduceOnly"] = "true"
        return self._req("POST", "/fapi/v1/order", params, signed=True)

    def limit_order(self, symbol, side, qty, price, reduce_only=False):
        params = {"symbol": symbol, "side": side, "type": "LIMIT", "quantity": f"{qty}",
                  "price": f"{price}", "timeInForce": "GTC"}
        if reduce_only: params["reduceOnly"] = "true"
        return self._req("POST", "/fapi/v1/order", params, signed=True)

    def live_price(self, symbol):
        r = self._req("GET", "/fapi/v1/ticker/price", {"symbol": symbol})
        return float(r["price"]) if r else None

    def cancel_all(self, symbol):
        self._req("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol}, signed=True)

    def exchange_info(self, symbol):
        data = self._req("GET", "/fapi/v1/exchangeInfo")
        if not data: return None
        for s in data["symbols"]:
            if s["symbol"] == symbol:
                step = qty_min = 0
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        step = float(f["stepSize"]); qty_min = float(f["minQty"])
                return {"step": step, "min_qty": qty_min}
        return None


# ─── State ───
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f: return json.load(f)
        except: pass
    return {
        "position": None,          # {side, first_entry, entries: [{px, qty}], qty_total, sl, cycle_day}
        "cycle_closed_day": "",    # UTC date string of last cycle close — blocks new entries same day
        "last_exit_time": 0,
        "peak_equity": 0.0,
        "trade_log": [],
        "stats": {"total": 0, "wins": 0, "pnl": 0.0},
    }

def save_state(s):
    with open(STATE_FILE, "w") as f: json.dump(s, f, indent=2, default=str)

def write_status(payload):
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(STATUS_FILE, "w") as f: json.dump(payload, f, indent=2, default=str)
    except: pass

def round_qty(q, step):
    if step == 0: return q
    return round(q - (q % step), 8)


# ─── Main ───
def main():
    log.info(f"{'='*50}")
    log.info(f"S/R DCA Day Bot (5m+1h bias+1d S/R) — env={ENV} dry={ARGS.dry}")
    client = BinanceClient(API_KEY, API_SECRET, BASE_URL)

    state = load_state()
    info = client.exchange_info(PAIR)
    if not info: log.error("No exchange info"); return

    # Fetch 5m + 1d klines
    df_5m_raw = client.klines(PAIR, interval="5m", limit=500)
    if df_5m_raw is None or len(df_5m_raw) < 100:
        log.error("Not enough 5m klines"); return
    df_1d_raw = client.klines(PAIR, interval="1d", limit=100)
    if df_1d_raw is None or len(df_1d_raw) < 60:
        log.error("Not enough 1d klines for bias"); return

    df = build_features(df_5m_raw, df_1d_raw)
    last_idx = len(df) - 2  # last CLOSED 5m bar
    last = df.iloc[last_idx]
    sig = evaluate_signal(df, last_idx)
    close_price = sig.price

    live_px = client.live_price(PAIR) or close_price

    # Account + DD
    acc = client.account()
    if not acc: log.error("Account fetch failed"); return
    balance = float(acc["totalWalletBalance"])
    log.info(f"  Balance: ${balance:,.2f} | {PAIR}: ${close_price:,.2f} | live: ${live_px:,.2f}")

    if balance > state.get("peak_equity", 0): state["peak_equity"] = balance
    peak = state.get("peak_equity", balance)
    dd_pct = (balance / peak - 1) if peak > 0 else 0.0
    now_ts = int(time.time())

    pos = state.get("position")

    # Reconcile with exchange
    exch_pos = client.positions(PAIR)
    if exch_pos and pos is None:
        ep = exch_pos[0]
        side = "LONG" if float(ep["positionAmt"]) > 0 else "SHORT"
        entry = float(ep["entryPrice"])
        qty = abs(float(ep["positionAmt"]))
        pos = {
            "side": side, "first_entry": entry,
            "entries": [{"px": entry, "qty": qty}],
            "qty_total": qty, "orig_qty_per_level": qty,
            "sl": entry * (1 - SL_BELOW_WORST) if side == "LONG" else entry * (1 + SL_BELOW_WORST),
            "cycle_day": str(datetime.now(timezone.utc).date()),
            "entry_time": datetime.now(timezone.utc).isoformat(),
        }
        state["position"] = pos
        log.warning(f"  Adopted {side} {qty}@{entry}")
    elif not exch_pos and pos is not None:
        log.warning("  Exchange flat, clearing state")
        state["position"] = None
        pos = None

    # Log signal
    log.info(f"  Signal: {sig.side or 'NONE'}  | "
             f"prev_H {sig.raw.get('prev_H', 0):.2f} / prev_L {sig.raw.get('prev_L', 0):.2f} / mid {sig.raw.get('prev_mid', 0):.2f}")
    log.info(f"  Conditions: {sum(sig.conditions.values())}/{len(sig.conditions)} met")

    status = {
        "env": ENV, "pair": PAIR, "price": close_price, "live_price": live_px,
        "balance": balance, "peak_equity": peak, "drawdown_pct": dd_pct,
        "position": pos, "signal": sig.side, "indicators": sig.raw, "conditions": sig.conditions,
        "stats": state.get("stats", {}),
        "strategy": "S/R DCA Day (5m exec + 1h EMA20 bias + 1d S/R)",
        "cycle_closed_day": state.get("cycle_closed_day", ""),
    }

    today = str(datetime.now(timezone.utc).date())
    utc_hour = datetime.now(timezone.utc).hour

    # ── FLAT: look for entry ──
    if pos is None:
        status["state"] = "FLAT"

        # Block if cycle already closed today
        if state.get("cycle_closed_day") == today:
            log.info(f"  Cycle already closed today ({today}) — waiting for next UTC day")
        elif utc_hour >= CLOSE_HOUR:
            log.info(f"  UTC {utc_hour}:00 past close hour — no new entries today")
        elif sig.side and not ARGS.dry:
            # Pine fills entry at zone price (prev_L*(1+SUPPORT_ZONE/2) for long).
            # Live: market at current price IF price is close to pine target.
            # Skip if price has drifted too far from pine's entry zone.
            target_px = (sig.raw["prev_L"] * (1 + SUPPORT_ZONE / 2)) if sig.side == "LONG" \
                        else (sig.raw["prev_H"] * (1 - SUPPORT_ZONE / 2))
            deviation = abs(live_px - target_px) / target_px
            if deviation > 0.005:  # 0.5% drift — skip, wait for re-touch
                log.info(f"  Price ${live_px:.2f} drifted {deviation*100:.2f}% from target ${target_px:.2f} — skipping")
            else:
                entry_px = live_px
                qty = per_level_qty(balance, entry_px)
                qty = round_qty(qty, info["step"])
                if qty < info["min_qty"]:
                    log.warning(f"  qty {qty} below min {info['min_qty']}")
                else:
                    client.set_leverage(PAIR, LEV * 2)
                    side_api = "BUY" if sig.side == "LONG" else "SELL"
                    resp = client.market_order(PAIR, side_api, qty)
                    if resp:
                        fill = float(resp.get("avgPrice", entry_px)) or entry_px
                        new_pos = {
                            "side": sig.side, "first_entry": fill,
                            "entries": [{"px": fill, "qty": qty}],
                            "qty_total": qty, "orig_qty_per_level": qty,
                            "sl": sl_price(sig.side, fill),
                            "cycle_day": today,
                            "entry_time": datetime.now(timezone.utc).isoformat(),
                        }
                        state["position"] = new_pos
                        pos = new_pos
                        sl_pct = abs(new_pos["sl"] - fill) / fill * 100
                        log.info(f"  OPENED L1 {sig.side} {qty}@${fill:.2f} SL=${new_pos['sl']:.2f} ({sl_pct:.1f}%) target=${target_px:.2f}")
        elif sig.side and ARGS.dry:
            log.info(f"  [DRY] Would open {sig.side} at ${close_price:,.2f}")
        else:
            log.info(f"  No signal — waiting")

    # ── IN POSITION: manage DCA, TP, SL, EOD ──
    if pos is not None:
        side = pos["side"]
        first_entry = pos["first_entry"]
        entries = pos["entries"]
        qty_total = pos["qty_total"]
        orig_qty = pos["orig_qty_per_level"]
        cur_sl = pos["sl"]
        cycle_day = pos.get("cycle_day", today)

        worst_entry = min(e["px"] for e in entries) if side == "LONG" else max(e["px"] for e in entries)
        last_bar = df.iloc[last_idx]

        # Update SL based on current worst entry (2% below/above worst)
        new_sl = sl_price(side, worst_entry)
        if (side == "LONG" and new_sl > cur_sl) or (side == "SHORT" and new_sl < cur_sl):
            pos["sl"] = new_sl
            cur_sl = new_sl
            log.info(f"  SL updated → ${cur_sl:.2f}")

        # DCA check (only if still less than DCA_LEVELS filled)
        if len(entries) < DCA_LEVELS and not ARGS.dry:
            dca_trigger = dca_price(side, worst_entry)
            dca_hit = (live_px <= dca_trigger) if side == "LONG" else (live_px >= dca_trigger)
            if dca_hit:
                dca_qty = round_qty(orig_qty, info["step"])
                if dca_qty >= info["min_qty"]:
                    side_api = "BUY" if side == "LONG" else "SELL"
                    resp = client.market_order(PAIR, side_api, dca_qty)
                    if resp:
                        dca_fill = float(resp.get("avgPrice", live_px)) or live_px
                        entries.append({"px": dca_fill, "qty": dca_qty})
                        pos["entries"] = entries
                        pos["qty_total"] = qty_total + dca_qty
                        qty_total = pos["qty_total"]
                        # Recompute SL with new worst entry
                        worst_entry = min(e["px"] for e in entries) if side == "LONG" else max(e["px"] for e in entries)
                        pos["sl"] = sl_price(side, worst_entry)
                        cur_sl = pos["sl"]
                        log.info(f"  DCA L{len(entries)} filled {dca_qty}@${dca_fill:.2f} | new worst=${worst_entry:.2f} SL=${cur_sl:.2f}")

        # TP: prev_mid of the CYCLE START day (use sig.raw.prev_mid — valid for today's bars)
        tp_px = tp_price(side, sig.raw.get("prev_mid", close_price))

        # Compute current fav%
        fav = (live_px - first_entry) if side == "LONG" else (first_entry - live_px)
        fav_pct = fav / first_entry * 100 if first_entry > 0 else 0
        log.info(f"  IN {side} L{len(entries)} qty={qty_total} entry=${first_entry:.2f} "
                 f"live=${live_px:.2f} fav={fav_pct:+.2f}% SL=${cur_sl:.2f} TP=${tp_px:.2f}")

        status["state"] = "IN_POSITION"
        status["fav_pct"] = fav_pct

        # Exit checks — use both last bar's high/low AND live price
        bar_low = float(last_bar["low"])
        bar_high = float(last_bar["high"])
        if side == "LONG":
            sl_hit = (live_px <= cur_sl) or (bar_low <= cur_sl)
            tp_hit = (live_px >= tp_px)  or (bar_high >= tp_px)
        else:
            sl_hit = (live_px >= cur_sl) or (bar_high >= cur_sl)
            tp_hit = (live_px <= tp_px)  or (bar_low <= tp_px)
        eod    = utc_hour >= CLOSE_HOUR

        reason = None
        if sl_hit: reason = "SL"
        elif tp_hit: reason = "TP"
        elif eod: reason = "EOD"

        if reason and not ARGS.dry:
            client.cancel_all(PAIR)
            close_side = "SELL" if side == "LONG" else "BUY"
            resp = client.market_order(PAIR, close_side, qty_total, reduce_only=True)
            if resp:
                fill = float(resp.get("avgPrice", live_px)) or live_px
                # Compute PnL across all legs
                total_pnl = sum(((fill - e["px"]) if side == "LONG" else (e["px"] - fill)) * e["qty"] for e in entries)
                pnl_pct = total_pnl / balance * 100 if balance > 0 else 0

                state["stats"]["total"] += 1
                state["stats"]["pnl"] += pnl_pct
                if total_pnl > 0: state["stats"]["wins"] += 1

                trade = {
                    "side": side, "first_entry": first_entry, "exit": fill,
                    "entries": len(entries), "avg_entry": sum(e["px"]*e["qty"] for e in entries)/qty_total,
                    "reason": reason, "pnl_usd": total_pnl, "pnl_pct": pnl_pct,
                    "time": datetime.now(timezone.utc).isoformat(),
                }
                state["trade_log"].append(trade)
                state["trade_log"] = state["trade_log"][-100:]

                state["last_exit_time"] = now_ts
                state["position"] = None
                # Lock this UTC day from new entries
                state["cycle_closed_day"] = today
                log.info(f"  EXIT {side} via {reason} @${fill:.2f} PnL ${total_pnl:+.2f} ({pnl_pct:+.2f}%)")
                status["just_closed"] = trade

    save_state(state)
    write_status(status)
    log.info(f"  Stats: {state['stats']['total']} trades | "
             f"WR {state['stats']['wins']/max(state['stats']['total'],1)*100:.0f}% | "
             f"PnL {state['stats']['pnl']:+.2f}%")
    log.info(f"{'='*50}\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception(f"FATAL: {e}")
