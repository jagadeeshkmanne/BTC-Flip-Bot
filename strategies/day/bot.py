#!/usr/bin/env python3
"""
bot.py — S/R DCA Day Trader V2 live bot (5m exec + 1d S/R, no bias).

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

    def algo_stop_market(self, symbol, side, trigger_price, close_position=True):
        """Place a STOP_MARKET via /fapi/v1/algoOrder (the new conditional-order
        endpoint Binance migrated to on 2025-12-09). The legacy /fapi/v1/order
        endpoint now returns -4120 for stop types. closePosition=true closes the
        full position when triggered — simpler than tracking quantity, and the
        right semantic for SL.
        """
        params = {"algoType": "CONDITIONAL", "symbol": symbol, "side": side,
                  "type": "STOP_MARKET", "triggerPrice": f"{trigger_price}",
                  "workingType": "MARK_PRICE"}
        if close_position:
            params["closePosition"] = "true"
        return self._req("POST", "/fapi/v1/algoOrder", params, signed=True)

    def open_orders(self, symbol):
        return self._req("GET", "/fapi/v1/openOrders", {"symbol": symbol}, signed=True) or []

    def open_algo_orders(self, symbol):
        return self._req("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol}, signed=True) or []

    def cancel_algo_order(self, symbol, algo_id):
        return self._req("DELETE", "/fapi/v1/algoOrder",
                         {"symbol": symbol, "algoId": algo_id}, signed=True)

    def user_trades(self, symbol, start_time=None, limit=50):
        params = {"symbol": symbol, "limit": limit}
        if start_time: params["startTime"] = start_time
        return self._req("GET", "/fapi/v1/userTrades", params, signed=True) or []

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
                step = qty_min = tick = 0
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        step = float(f["stepSize"]); qty_min = float(f["minQty"])
                    elif f["filterType"] == "PRICE_FILTER":
                        tick = float(f["tickSize"])
                return {"step": step, "min_qty": qty_min, "tick": tick}
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


def round_price(p, tick):
    if tick <= 0: return p
    n = round(p / tick)
    s = ("%.10f" % tick).rstrip("0")
    decimals = len(s.split(".")[1]) if "." in s else 0
    return round(n * tick, decimals)


def ensure_exits(client, info, pair, side, qty_total, tp_px, sl_px):
    """Maintain reduce-only TP (LIMIT) + SL (STOP_MARKET via algoOrder).

    Pine V2 simulates intra-bar TP/SL fills via strategy.exit(limit=, stop=).
    These resting orders give the live bot the same execution semantics so
    spikes through TP/SL don't get missed by the 5-min cron interval.

    Idempotent: queries open regular + algo orders, only cancels+replaces if
    missing or the TP/SL price has changed (after a DCA leg, SL moves so it
    needs to be re-placed; TP stays at prev_mid throughout the cycle).

    Implementation note: SL goes through /fapi/v1/algoOrder (NOT /fapi/v1/order)
    — Binance migrated all conditional order types to the algo endpoint on
    2025-12-09; the legacy endpoint returns -4120 for STOP_MARKET.
    """
    qty_r = round_qty(qty_total, info["step"])
    tp_r = round_price(tp_px, info["tick"])
    sl_r = round_price(sl_px, info["tick"])
    if qty_r < info["min_qty"]:
        log.warning(f"  ensure_exits: qty {qty_r} below min {info['min_qty']}")
        return
    close_side = "SELL" if side == "LONG" else "BUY"

    # Check existing TP (regular limit order)
    tp_ok = False
    for o in client.open_orders(pair) or []:
        if (o.get("reduceOnly") and o.get("type") == "LIMIT"
            and abs(float(o.get("price", 0)) - tp_r) <= info["tick"]
            and abs(float(o.get("origQty", 0)) - qty_r) < info["step"]):
            tp_ok = True
            break

    # Check existing SL (algo conditional order)
    sl_ok = False
    sl_to_cancel = []
    for ao in client.open_algo_orders(pair) or []:
        if ao.get("orderType") == "STOP_MARKET":
            if abs(float(ao.get("triggerPrice", 0)) - sl_r) <= info["tick"]:
                sl_ok = True
            else:
                sl_to_cancel.append(ao.get("algoId"))

    if tp_ok and sl_ok:
        return  # both already correct

    # Cancel only what's wrong, then place fresh
    if not tp_ok:
        client.cancel_all(pair)  # cancels regular orders only
        tp_resp = client.limit_order(pair, close_side, qty_r, tp_r, reduce_only=True)
        log.info(f"  Resting TP placed: {tp_r} ({'OK' if tp_resp else 'FAIL'}) qty={qty_r}")
    if not sl_ok:
        for algo_id in sl_to_cancel:
            client.cancel_algo_order(pair, algo_id)
        sl_resp = client.algo_stop_market(pair, close_side, sl_r, close_position=True)
        log.info(f"  Resting SL placed (algo): trigger={sl_r} ({'OK' if sl_resp else 'FAIL'})")


def cancel_all_orders_and_algos(client, pair):
    """Cancel BOTH regular open orders and algo (conditional) orders.
    Used when the bot does its own market exit (cron TP/SL/EOD) so no
    leftover resting orders fight the close."""
    client.cancel_all(pair)
    for ao in client.open_algo_orders(pair) or []:
        algo_id = ao.get("algoId")
        if algo_id:
            client.cancel_algo_order(pair, algo_id)


# ─── Main ───
def main():
    log.info(f"{'='*50}")
    log.info(f"S/R DCA Day Bot V2 (5m + 1d S/R, no bias) — env={ENV} dry={ARGS.dry}")
    client = BinanceClient(API_KEY, API_SECRET, BASE_URL)

    state = load_state()
    info = client.exchange_info(PAIR)
    if not info: log.error("No exchange info"); return

    # Force leverage to LEV every tick. Binance keeps the per-symbol leverage
    # setting persistent across runs, so without this a manual change on the UI
    # (or an old setting from before LEV was 2x) would drift. Calling this
    # each tick is idempotent — Binance just no-ops when leverage is already set.
    if not ARGS.dry:
        client.set_leverage(PAIR, LEV)

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
    today = str(datetime.now(timezone.utc).date())
    utc_hour = datetime.now(timezone.utc).hour

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
        # Resting TP or SL filled between cron ticks. Query Binance's userTrades
        # to get the ACTUAL fill price + realizedPnl rather than guessing from
        # balance delta (balance can move from funding fees which aren't trade PnL).
        side = pos["side"]
        entries = pos.get("entries", [])
        qty_total = pos.get("qty_total", 0)
        avg_entry = (sum(e["px"]*e["qty"] for e in entries)/qty_total) if qty_total > 0 else 0

        entry_time_str = pos.get("entry_time", "")
        try:
            entry_ts = int(datetime.fromisoformat(entry_time_str).timestamp() * 1000)
        except Exception:
            entry_ts = (now_ts - 86400) * 1000  # fallback: last 24h

        recent_trades = client.user_trades(PAIR, start_time=entry_ts, limit=50)
        # Sum the closing trades — they're the ones with realizedPnl != 0
        # (entries have realizedPnl=0; exits have non-zero from Binance's calc).
        exit_pnl = sum(float(t.get("realizedPnl", 0)) for t in recent_trades)
        exit_fee = sum(float(t.get("commission", 0)) for t in recent_trades
                       if float(t.get("realizedPnl", 0)) != 0)
        exit_qty_filled = sum(float(t.get("qty", 0)) for t in recent_trades
                              if float(t.get("realizedPnl", 0)) != 0)
        exit_notional = sum(float(t.get("qty", 0)) * float(t.get("price", 0))
                            for t in recent_trades if float(t.get("realizedPnl", 0)) != 0)
        exit_avg = exit_notional / exit_qty_filled if exit_qty_filled > 0 else live_px
        exit_time_ms = max((t["time"] for t in recent_trades
                            if float(t.get("realizedPnl", 0)) != 0), default=now_ts*1000)

        pnl_usd = exit_pnl - exit_fee  # net of close commission
        notional_at_entry = avg_entry * qty_total if qty_total > 0 else (balance or 1)
        pnl_pct = (pnl_usd / notional_at_entry * 100) if notional_at_entry > 0 else 0

        state["stats"]["total"] += 1
        state["stats"]["pnl"] += pnl_pct
        if pnl_usd > 0: state["stats"]["wins"] += 1
        # Reason heuristic: SL trigger > entry for short → exit price closer to SL means SL fired
        sl_px_was = pos.get("sl", 0)
        if side == "SHORT":
            reason = "SL" if exit_avg >= sl_px_was * 0.99 else "TP"
        else:
            reason = "SL" if exit_avg <= sl_px_was * 1.01 else "TP"
        state.setdefault("trade_log", []).append({
            "side": side, "first_entry": pos.get("first_entry"), "exit": exit_avg,
            "entries": len(entries), "avg_entry": avg_entry,
            "reason": reason, "pnl_usd": pnl_usd, "pnl_pct": pnl_pct,
            "time": datetime.fromtimestamp(exit_time_ms/1000, tz=timezone.utc).isoformat(),
        })
        state["trade_log"] = state["trade_log"][-100:]
        state["last_exit_time"] = now_ts
        state["cycle_closed_day"] = today
        state["position"] = None
        cancel_all_orders_and_algos(client, PAIR)  # clean up any orphan exit orders
        log.warning(f"  Exchange flat — exit fired @ ${exit_avg:.2f} via {reason}. "
                    f"PnL ${pnl_usd:+.2f} ({pnl_pct:+.2f}%)")
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
        "strategy": "S/R DCA Day V2 (5m exec + 1d S/R, no bias)",
        "cycle_closed_day": state.get("cycle_closed_day", ""),
    }

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
                        state["balance_at_entry"] = balance  # snapshot for synthetic-trade PnL on exchange-side fills
                        pos = new_pos
                        sl_pct = abs(new_pos["sl"] - fill) / fill * 100
                        log.info(f"  OPENED L1 {sig.side} {qty}@${fill:.2f} SL=${new_pos['sl']:.2f} ({sl_pct:.1f}%) target=${target_px:.2f}")
                        # Place resting TP+SL on exchange so spikes between cron runs still fill.
                        tp_px_new = tp_price(sig.side, sig.raw["prev_mid"])
                        ensure_exits(client, info, PAIR, sig.side, qty, tp_px_new, new_pos["sl"])
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

        # Ensure resting TP+SL exit orders exist on the exchange.
        # Idempotent — only cancels+replaces if missing or prices changed.
        # Without this, TP/SL only fire when the cron tick coincides with price
        # being at-or-past the level. With this, Binance fills the moment price
        # reaches the level. Recovers existing positions on bot restart too.
        if not ARGS.dry:
            tp_px_now = tp_price(side, sig.raw.get("prev_mid", close_price))
            ensure_exits(client, info, PAIR, side, qty_total, tp_px_now, cur_sl)

        # Bar high/low — used by DCA, TP, SL checks below.
        # Without this, DCA only sees `live_px` and misses intra-bar spikes that
        # don't coincide with cron timing (Apr 26 2026: bar high $78,478 hit DCA
        # trigger $78,455 but bot's cron read live_px after the spike collapsed,
        # so the DCA leg never fired and the EOD loss was 60% larger).
        bar_low = float(last_bar["low"])
        bar_high = float(last_bar["high"])

        # DCA check (only if still less than DCA_LEVELS filled)
        if len(entries) < DCA_LEVELS and not ARGS.dry:
            dca_trigger = dca_price(side, worst_entry)
            if side == "LONG":
                dca_hit = (live_px <= dca_trigger) or (bar_low <= dca_trigger)
            else:
                dca_hit = (live_px >= dca_trigger) or (bar_high >= dca_trigger)
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
                        # Replace resting exits to cover the new total qty + updated SL.
                        tp_px_dca = tp_price(side, sig.raw.get("prev_mid", close_price))
                        ensure_exits(client, info, PAIR, side, qty_total, tp_px_dca, cur_sl)

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
        # (bar_low / bar_high already computed above, shared with DCA detection)
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
            cancel_all_orders_and_algos(client, PAIR)  # clear both LIMIT TP + algo SL
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
