#!/usr/bin/env python3
"""
bot.py — Structure Break + SL-Flip live bot (1h Execution + 1d Bias).

Runs every 5 min via cron. Fetches 1h candles (execution) + 1d candles
(bias), computes pivot structure on 1h, manages position (entry + TP
ladder + software SL via live price + SL-flip).

Architecture:
  core.py  — Strategy logic (pivots, TP ladder, DD-adaptive risk)
  bot.py   — Exchange I/O + position state + intra-cron SL + flip exec

SL-flip rule (V2b): when position closes via SL BEFORE TP1 AND
daily EMA50 bias is now on the opposite side, immediately open the
flipped position. Bias gate prevents ping-ponging in chop.

Signal evaluation at each 5-min tick against the last CLOSED 1h bar.
Bias (EMA50) computed on 1d bars and mapped to 1h (prior day's bias).

PAIR: BNBUSDT (switched from BTCUSDT based on TV 1h 2025 backtest +168%).
NOTE: validated over 15mo bull only, not full 6y history.
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
    LEVERAGE, RISK_PCT, SL_SWING_LEN, SL_BUFFER_PCT, SL_MAX_PCT,
    TP1_R, TP1_FRAC, TP2_R, TP2_FRAC, BE_BUF_PCT, TRAIL_ATR_MULT,
    GENERIC_CD_BARS, DD_HALT_PCT, DD_HALT_BARS,
    USE_ADAPTIVE_RISK, RISK_FLOOR,
    build_signals, build_htf, evaluate_signal,
    calc_sl, calc_qty, compute_trail_stop,
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
ap.add_argument("--dry", action="store_true", help="Log signals only")
ARGS, _ = ap.parse_known_args()
ENV = ARGS.env

PAIR = "BNBUSDT"
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
STATE_FILE = os.path.join(DATA_DIR, "state.json")
STATUS_FILE = os.path.join(DATA_DIR, "status.json")
LOG_FILE = os.path.join(DATA_DIR, "bot.log")
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)])
log = logging.getLogger("bot")

if not API_KEY or not API_SECRET:
    log.error(f"Missing API keys for {ENV}")
    sys.exit(1)


# ─── Binance Client ───
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

    def klines(self, symbol, interval="1d", limit=500):
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
        "position": None,
        "last_exit_time": 0,
        "halt_until_time": 0,
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
    log.info(f"Structure Break + SL-Flip Bot — env={ENV} dry={ARGS.dry}")
    client = BinanceClient(API_KEY, API_SECRET, BASE_URL)

    state = load_state()
    info = client.exchange_info(PAIR)
    if not info: log.error("No exchange info"); return

    # Fetch 1h klines for execution (pivots, RSI, swing SL, ATR all on 1h)
    df_1h_raw = client.klines(PAIR, interval="1h", limit=1500)
    if df_1h_raw is None or len(df_1h_raw) < 100:
        log.error("Not enough 1h klines"); return

    # Fetch 1d klines for DAILY EMA50 BIAS (matches Pine's request.security("1D"))
    df_1d_raw = client.klines(PAIR, interval="1d", limit=100)
    if df_1d_raw is None or len(df_1d_raw) < 60:
        log.error("Not enough 1d klines for bias"); return

    df_1h = build_signals(df_1h_raw)
    bias_daily = build_htf(df_1d_raw)  # one bias entry per daily bar

    # Map daily bias to each 1h bar (prior day's bias applies today)
    df_1d_raw = df_1d_raw.copy()
    df_1d_raw["date_day"] = pd.to_datetime(df_1d_raw["timestamp"]).dt.normalize()
    bias_by_day = dict(zip(df_1d_raw["date_day"], bias_daily))
    df_1h["date_day"] = pd.to_datetime(df_1h["timestamp"]).dt.normalize()
    df_1h["bias_d"] = df_1h["date_day"].map(bias_by_day).fillna(0).astype(int)

    # Last CLOSED 1h bar
    last_idx = len(df_1h) - 2
    last = df_1h.iloc[last_idx]
    sig = evaluate_signal(df_1h, last_idx, int(last["bias_d"]))
    close_price = sig.price

    # Live price (for intra-bar SL/TP checks)
    live_px = client.live_price(PAIR) or close_price

    # Account
    acc = client.account()
    if not acc: log.error("Account fetch failed"); return
    balance = float(acc["totalWalletBalance"])
    log.info(f"  Balance: ${balance:,.2f} | BTC close: ${close_price:,.2f} | live: ${live_px:,.2f}")

    # Peak equity + DD
    if balance > state.get("peak_equity", 0): state["peak_equity"] = balance
    peak = state.get("peak_equity", balance)
    dd_pct = (balance / peak - 1) if peak > 0 else 0.0  # negative number
    now_ts = int(time.time())

    # Hard DD halt
    if dd_pct <= -DD_HALT_PCT and state.get("halt_until_time", 0) < now_ts:
        state["halt_until_time"] = now_ts + DD_HALT_BARS * 3600  # 1h bar = 3600s
        state["peak_equity"] = balance
        log.warning(f"  DD HALT: {dd_pct*100:+.1f}% — halted 7 days")

    halted = state.get("halt_until_time", 0) > now_ts
    pos_dict = state.get("position")

    # Reconcile with exchange
    exch_pos = client.positions(PAIR)
    if exch_pos and pos_dict is None:
        ep = exch_pos[0]
        side = "LONG" if float(ep["positionAmt"]) > 0 else "SHORT"
        entry = float(ep["entryPrice"])
        qty = abs(float(ep["positionAmt"]))
        sl = entry * (1 - SL_MAX_PCT) if side == "LONG" else entry * (1 + SL_MAX_PCT)
        pos_dict = {
            "side": side, "entry_price": entry,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "qty": qty, "orig_qty": qty,
            "sl_price": sl, "init_sl_dist": abs(entry - sl),
            "tp1_done": False, "tp2_done": False,
        }
        state["position"] = pos_dict
        log.warning(f"  Adopted {side} {qty}@{entry}")
    elif exch_pos and pos_dict is not None:
        ep = exch_pos[0]
        ex_qty = abs(float(ep["positionAmt"]))
        ex_entry = float(ep["entryPrice"])
        if abs(ex_qty - pos_dict["qty"]) > 1e-6 or abs(ex_entry - pos_dict["entry_price"]) > 0.5:
            log.warning(f"  DESYNC: state={pos_dict['qty']}@{pos_dict['entry_price']} "
                        f"vs exch={ex_qty}@{ex_entry} — syncing")
            pos_dict["qty"] = ex_qty
            pos_dict["entry_price"] = ex_entry
            pos_dict["init_sl_dist"] = abs(ex_entry - pos_dict["sl_price"])
            state["position"] = pos_dict
    elif not exch_pos and pos_dict is not None:
        log.warning("  Exchange flat, clearing state")
        state["position"] = None
        pos_dict = None

    # Log signal state
    log.info(f"  Signal: {sig.side or 'NONE'}")
    for k, v in sig.conditions.items():
        if v: log.info(f"    {k}: Y")
    log.info(f"  RSI 1D: {sig.raw.get('rsi_4h','?')} | Bias: {sig.raw.get('bias_d','?')}")
    if sig.raw.get("ph_last"):
        log.info(f"  Pivot H: {sig.raw['ph_last']:.0f} (prev {sig.raw.get('ph_prev','?')}) | "
                 f"Pivot L: {sig.raw['pl_last']:.0f} (prev {sig.raw.get('pl_prev','?')})")

    # Status for dashboard
    status = {
        "env": ENV, "pair": PAIR, "price": close_price, "live_price": live_px,
        "balance": balance, "peak_equity": peak, "drawdown_pct": dd_pct,
        "halted": halted, "position": pos_dict,
        "signal": sig.side, "indicators": sig.raw, "conditions": sig.conditions,
        "stats": state.get("stats", {}),
        "strategy": "Structure Break + SL-Flip (Daily)",
    }

    if pos_dict is None:
        # ── FLAT: look for entry ──
        status["state"] = "FLAT"

        # 1h bar = 3600s (was 24*3600 for 1d TF)
        cd_block = max(0, state.get("last_exit_time", 0) + GENERIC_CD_BARS * 3600 - now_ts)

        if halted:
            log.info(f"  HALTED {(state['halt_until_time']-now_ts)//60}min remaining")
        elif cd_block > 0:
            log.info(f"  Signal {sig.side or 'NONE'} but cooldown {cd_block//60}min")
        elif sig.side and not ARGS.dry:
            swing_low = float(last.get("swing_low", close_price * 0.975))
            swing_high = float(last.get("swing_high", close_price * 1.025))
            sl_price = calc_sl(sig.side, close_price, swing_low, swing_high)
            qty = calc_qty(balance, close_price, sl_price, dd_pct)
            qty = round_qty(qty, info["step"])
            if qty < info["min_qty"]:
                log.warning(f"  qty {qty} below min {info['min_qty']}")
            else:
                client.set_leverage(PAIR, LEV * 2)
                side_api = "BUY" if sig.side == "LONG" else "SELL"
                resp = client.market_order(PAIR, side_api, qty)
                if resp:
                    fill = float(resp.get("avgPrice", close_price)) or close_price
                    sl_price = calc_sl(sig.side, fill, swing_low, swing_high)
                    pos_dict = {
                        "side": sig.side, "entry_price": fill,
                        "entry_time": datetime.now(timezone.utc).isoformat(),
                        "qty": qty, "orig_qty": qty,
                        "sl_price": sl_price, "init_sl_dist": abs(fill - sl_price),
                        "tp1_done": False, "tp2_done": False,
                    }
                    state["position"] = pos_dict
                    sl_pct = abs(sl_price - fill)/fill * 100
                    log.info(f"  OPENED {sig.side} {qty}@{fill:.2f} SL=${sl_price:.2f} ({sl_pct:.1f}%)")
        elif sig.side and ARGS.dry:
            log.info(f"  [DRY] Would open {sig.side} at ${close_price:,.2f}")
        else:
            log.info(f"  No signal — waiting")

    else:
        # ── IN POSITION ──
        side = pos_dict["side"]
        entry = pos_dict["entry_price"]
        qty = pos_dict["qty"]
        orig_qty = pos_dict.get("orig_qty", qty)
        sl = pos_dict["sl_price"]
        init_dist = pos_dict.get("init_sl_dist", abs(entry - sl))
        tp1_done = pos_dict.get("tp1_done", False)
        tp2_done = pos_dict.get("tp2_done", False)

        # Use live price for favorability check
        fav = (live_px - entry) if side == "LONG" else (entry - live_px)
        current_r = fav / init_dist if init_dist > 0 else 0

        log.info(f"  IN {side} qty={qty} orig={orig_qty} entry=${entry:.2f} SL=${sl:.2f} "
                 f"live=${live_px:.2f} R={current_r:.1f}")

        status["state"] = "IN_POSITION"
        status["current_r"] = current_r

        # SL check: live price OR previous closed bar
        live_sl_hit = (live_px <= sl) if side == "LONG" else (live_px >= sl)
        bar_sl_hit = (float(last["low"]) <= sl) if side == "LONG" else (float(last["high"]) >= sl)
        sl_hit = live_sl_hit or bar_sl_hit

        # Opposite signal exit
        opp = (sig.side == "SHORT" and side == "LONG") or (sig.side == "LONG" and side == "SHORT")

        # TP1: close 50% of ORIGINAL qty, move SL to BE+buffer
        if not tp1_done and not ARGS.dry:
            tp1_px = entry + TP1_R * init_dist if side == "LONG" else entry - TP1_R * init_dist
            tp1_hit = (live_px >= tp1_px) if side == "LONG" else (live_px <= tp1_px)
            if tp1_hit:
                close_qty = round_qty(orig_qty * TP1_FRAC, info["step"])
                close_qty = min(close_qty, qty)  # can't close more than we have
                if close_qty >= info["min_qty"]:
                    close_side = "SELL" if side == "LONG" else "BUY"
                    resp = client.market_order(PAIR, close_side, close_qty, reduce_only=True)
                    if resp:
                        pos_dict["qty"] = qty - close_qty
                        pos_dict["tp1_done"] = True
                        be_sl = entry * (1 + BE_BUF_PCT) if side == "LONG" else entry * (1 - BE_BUF_PCT)
                        new_sl = max(sl, be_sl) if side == "LONG" else min(sl, be_sl)
                        pos_dict["sl_price"] = new_sl
                        state["position"] = pos_dict
                        sl = new_sl
                        qty = pos_dict["qty"]
                        tp1_done = True
                        log.info(f"  TP1 closed {TP1_FRAC*100:.0f}% @ +{current_r:.1f}R → SL moved to BE ${new_sl:.2f}")

        # TP2: close 25% of ORIGINAL qty
        if tp1_done and not tp2_done and not ARGS.dry:
            tp2_px = entry + TP2_R * init_dist if side == "LONG" else entry - TP2_R * init_dist
            tp2_hit = (live_px >= tp2_px) if side == "LONG" else (live_px <= tp2_px)
            if tp2_hit:
                close_qty = round_qty(orig_qty * TP2_FRAC, info["step"])
                close_qty = min(close_qty, qty)
                if close_qty >= info["min_qty"]:
                    close_side = "SELL" if side == "LONG" else "BUY"
                    resp = client.market_order(PAIR, close_side, close_qty, reduce_only=True)
                    if resp:
                        pos_dict["qty"] = qty - close_qty
                        pos_dict["tp2_done"] = True
                        state["position"] = pos_dict
                        qty = pos_dict["qty"]
                        tp2_done = True
                        log.info(f"  TP2 closed {TP2_FRAC*100:.0f}% @ +{current_r:.1f}R")

        # Runner trailing stop (after TP2)
        if tp2_done and not ARGS.dry:
            atr_val = sig.raw.get("atr")
            new_sl = compute_trail_stop(side, live_px, atr_val, sl)
            if (side == "LONG" and new_sl > sl) or (side == "SHORT" and new_sl < sl):
                pos_dict["sl_price"] = new_sl
                sl = new_sl
                state["position"] = pos_dict
                log.info(f"  Trail: SL → ${new_sl:.2f}")

        # Full exit: SL hit or opposite signal
        should_exit = sl_hit or opp
        reason = "SL" if sl_hit else ("OPP" if opp else None)

        if should_exit and not ARGS.dry:
            client.cancel_all(PAIR)
            close_side = "SELL" if side == "LONG" else "BUY"
            resp = client.market_order(PAIR, close_side, pos_dict["qty"], reduce_only=True)
            if resp:
                fill = float(resp.get("avgPrice", live_px)) or live_px
                pmp = (fill - entry)/entry if side == "LONG" else (entry - fill)/entry
                pnl = pmp * LEV * 100

                state["stats"]["total"] += 1
                state["stats"]["pnl"] += pnl
                if pnl > 0: state["stats"]["wins"] += 1

                trade = {
                    "side": side, "entry": entry, "exit": fill, "reason": reason,
                    "pnl_pct": pnl, "time": datetime.now(timezone.utc).isoformat(),
                    "tp1_done": tp1_done, "tp2_done": tp2_done,
                }
                state["trade_log"].append(trade)
                state["trade_log"] = state["trade_log"][-100:]

                state["last_exit_time"] = now_ts
                state["position"] = None
                pos_dict = None
                log.info(f"  EXIT {side} via {reason} @${fill:.2f} PnL {pnl:+.2f}%")
                status["just_closed"] = trade

                # ── SL-FLIP (bias-gated, V2b) ──
                # Flip ONLY if: SL exit AND TP1 never hit AND daily bias now opposite
                if reason == "SL" and not tp1_done and not halted:
                    current_bias = int(last["bias_d"]) if not pd.isna(last["bias_d"]) else 0
                    want_flip_short = (side == "LONG"  and current_bias == -1)
                    want_flip_long  = (side == "SHORT" and current_bias ==  1)

                    if want_flip_short or want_flip_long:
                        flip_side = "SHORT" if want_flip_short else "LONG"
                        swing_low_v  = float(last.get("swing_low",  fill * 0.975))
                        swing_high_v = float(last.get("swing_high", fill * 1.025))
                        flip_sl = calc_sl(flip_side, fill, swing_low_v, swing_high_v)
                        flip_qty = calc_qty(balance, fill, flip_sl, dd_pct)
                        flip_qty = round_qty(flip_qty, info["step"])

                        if flip_qty < info["min_qty"]:
                            log.warning(f"  FLIP skipped: qty {flip_qty} below min")
                        else:
                            flip_api_side = "SELL" if flip_side == "SHORT" else "BUY"
                            fresp = client.market_order(PAIR, flip_api_side, flip_qty)
                            if fresp:
                                flip_fill = float(fresp.get("avgPrice", fill)) or fill
                                flip_sl = calc_sl(flip_side, flip_fill, swing_low_v, swing_high_v)
                                pos_dict = {
                                    "side": flip_side, "entry_price": flip_fill,
                                    "entry_time": datetime.now(timezone.utc).isoformat(),
                                    "qty": flip_qty, "orig_qty": flip_qty,
                                    "sl_price": flip_sl, "init_sl_dist": abs(flip_fill - flip_sl),
                                    "tp1_done": False, "tp2_done": False,
                                    "is_flip": True,
                                }
                                state["position"] = pos_dict
                                state["last_exit_time"] = 0  # flip bypasses cooldown
                                sl_pct = abs(flip_sl - flip_fill) / flip_fill * 100
                                log.info(f"  FLIP → {flip_side} {flip_qty}@${flip_fill:.2f} "
                                         f"SL=${flip_sl:.2f} ({sl_pct:.1f}%) [bias={current_bias}]")
                                status["flipped_to"] = flip_side
                    else:
                        log.info(f"  No flip: bias={current_bias} side={side} (gate failed)")

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
