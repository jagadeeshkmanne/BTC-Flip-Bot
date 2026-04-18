#!/usr/bin/env python3
"""
bot_bb.py — BB Confluence V2 live bot (one-shot per 1H candle close).

BB V2 (backtest: $5K → $2.21M, +238% CAGR, PF 2.40, -12.7% DD, 688 trades):
  Entry  : 1H BB lower/upper + near 4H BB + RSI < 30/> 70 + vol 1.3×
  TP     : 4H BB mid
  SL     : 4H BB band ± ATR
  Max hold: 24h (force exit)
  DD halt : 7 days after -25% drawdown
  Cooldown: 4h after each trade

Run:
  python3 bot_bb.py --env testnet
  python3 bot_bb.py --env testnet --dry
"""
from __future__ import annotations
import os, sys, json, time, hmac, hashlib, logging, argparse, smtplib, ssl
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from pathlib import Path
import requests
import pandas as pd
import numpy as np

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(os.path.dirname(STRATEGY_DIR))  # project root (../../)
sys.path.insert(0, STRATEGY_DIR)  # for bb_core import

from bb_core import (
    LEVERAGE as STRAT_LEV,
    DD_HALT_PCT, DD_HALT_BARS, COOLDOWN_BARS, MAX_HOLD_BARS,
    build_signals, evaluate_signal, position_size, BBPosition,
)


# ─── env / config ───
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
ap.add_argument("--dry", action="store_true", help="Log signals only, place no orders")
ARGS, _ = ap.parse_known_args()
ENV = ARGS.env

CFG_PATH = os.path.join(BOT_DIR, "config", f"{ENV}.json")
with open(CFG_PATH) as f:
    CFG = json.load(f)

API_KEY    = os.environ.get(CFG["api_key_env"], "")
API_SECRET = os.environ.get(CFG["api_secret_env"], "")
BASE_URL   = CFG["base_url"]
PAIR       = CFG["pair"]
LEV        = int(CFG.get("leverage", STRAT_LEV))

DATA_DIR     = os.path.join(BOT_DIR, "data", ENV)
STATE_FILE   = os.path.join(DATA_DIR, "state.json")
STATUS_FILE  = os.path.join(DATA_DIR, "status.json")
LOG_FILE     = os.path.join(DATA_DIR, "bot.log")
DISABLED_FLAG = os.path.join(DATA_DIR, ".disabled")
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bot_bb")

if os.path.exists(DISABLED_FLAG):
    log.info(f"[{ENV}] Bot DISABLED. Remove {DISABLED_FLAG} to enable.")
    sys.exit(0)

if not API_KEY or not API_SECRET:
    log.error(f"Missing API keys ({CFG['api_key_env']}, {CFG['api_secret_env']})")
    sys.exit(1)


# ─── Email alerts ───
EMAIL_FROM = os.environ.get("BOT_EMAIL", "")
EMAIL_PASS = os.environ.get("BOT_EMAIL_PASS", "")
EMAIL_TO   = os.environ.get("BOT_EMAIL_TO", "")

def send_email(subject, body):
    if not (EMAIL_FROM and EMAIL_PASS and EMAIL_TO): return
    try:
        msg = EmailMessage()
        msg["Subject"] = f"[BB-{ENV.upper()}] {subject}"
        msg["From"]    = EMAIL_FROM
        msg["To"]      = EMAIL_TO
        msg.set_content(body)
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=15) as s:
            s.login(EMAIL_FROM, EMAIL_PASS)
            s.send_message(msg)
        log.info(f"  alert sent: {subject}")
    except Exception as e:
        log.warning(f"  email send failed: {e}")


# ─── Binance Futures Client (same as bot.py) ───
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

    def _req(self, method, path, params=None, signed=False, retries=3):
        params = params or {}
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 5000
            params = self._sign(params)
        url = self.base + path
        for attempt in range(retries):
            try:
                r = self.s.request(method, url, params=params, timeout=10)
                if r.status_code == 200: return r.json()
                log.warning(f"  HTTP {r.status_code}: {r.text[:200]}")
                if r.status_code in (418, 429):
                    time.sleep(5 * (attempt + 1)); continue
                return None
            except Exception as e:
                log.warning(f"  Request err {attempt+1}: {e}")
                time.sleep(2)
        return None

    def klines(self, symbol, interval="15m", limit=1500):
        data = self._req("GET", "/fapi/v1/klines",
                         {"symbol": symbol, "interval": interval, "limit": limit})
        if not data: return None
        df = pd.DataFrame(data, columns=[
            "open_time","open","high","low","close","volume","close_time",
            "qav","trades","tbbav","tbqav","ignore"
        ])
        df = df.astype({"open":float,"high":float,"low":float,"close":float,"volume":float})
        df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
        return df[["timestamp","open","high","low","close","volume"]]

    def account(self):
        return self._req("GET", "/fapi/v2/account", signed=True)

    def positions(self, symbol):
        acc = self.account()
        if not acc: return []
        return [p for p in acc.get("positions", [])
                if p["symbol"] == symbol and float(p["positionAmt"]) != 0]

    def set_leverage(self, symbol, lev):
        return self._req("POST", "/fapi/v1/leverage",
                         {"symbol": symbol, "leverage": lev}, signed=True)

    def market_order(self, symbol, side, qty, reduce_only=False):
        params = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": f"{qty}"}
        if reduce_only: params["reduceOnly"] = "true"
        return self._req("POST", "/fapi/v1/order", params, signed=True)

    def stop_market(self, symbol, side, stop_price, close_position=True):
        params = {
            "symbol": symbol, "side": side, "type": "STOP_MARKET",
            "stopPrice": f"{stop_price:.2f}", "workingType": "MARK_PRICE",
            "closePosition": "true" if close_position else "false",
        }
        return self._req("POST", "/fapi/v1/order", params, signed=True)

    def limit_order(self, symbol, side, price, close_position=True):
        params = {
            "symbol": symbol, "side": side, "type": "TAKE_PROFIT_MARKET",
            "stopPrice": f"{price:.2f}", "workingType": "MARK_PRICE",
            "closePosition": "true" if close_position else "false",
        }
        return self._req("POST", "/fapi/v1/order", params, signed=True)

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
    if not os.path.exists(STATE_FILE):
        return default_state()
    try:
        with open(STATE_FILE) as f: return json.load(f)
    except Exception:
        return default_state()

def default_state():
    return {
        "position": None,
        "last_exit_time": 0,
        "halt_until_time": 0,
        "peak_equity": 0.0,
        "trade_log": [],
    }

def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=2, default=str)

def round_qty(q, step):
    if step == 0: return q
    return round(q - (q % step), 8)


# ─── Status for dashboard ───
def _clean(v):
    if isinstance(v, dict):  return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):  return [_clean(x) for x in v]
    if hasattr(v, "item"):   return v.item()
    if isinstance(v, (bool, int, float, str)) or v is None: return v
    return str(v)

def write_status(payload):
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    payload["strategy"] = "BB Confluence V2"
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(_clean(payload), f, indent=2)
    except Exception as e:
        log.warning(f"  status write failed: {e}")


# ─── Main ───
def main():
    log.info(f"--- BB V2 bot tick --- env={ENV} pair={PAIR} dry={ARGS.dry} ---")
    client = BinanceClient(API_KEY, API_SECRET, BASE_URL)

    state = load_state()
    info = client.exchange_info(PAIR)
    if not info:
        log.error("No exchange info"); return

    df_15m = client.klines(PAIR, interval="15m", limit=1500)
    if df_15m is None or len(df_15m) < 200:
        log.error("Not enough klines"); return

    df_1h = build_signals(df_15m)
    if len(df_1h) < 50:
        log.error("Not enough 1H bars"); return

    # Evaluate on last CLOSED 1H bar
    last = df_1h.iloc[-2]
    sig = evaluate_signal(last)
    price = sig.price

    # Account
    acc = client.account()
    if not acc:
        log.error("Account fetch failed"); return
    balance = float(acc["totalWalletBalance"])
    log.info(f"  Balance ${balance:,.2f}  Price ${price:,.2f}")

    # Peak equity / DD
    if balance > state.get("peak_equity", 0):
        state["peak_equity"] = balance
    peak = state.get("peak_equity", balance)
    dd_pct = (balance / peak - 1) * 100 if peak > 0 else 0
    now_ts = int(time.time())

    # DD halt
    if dd_pct <= -DD_HALT_PCT * 100 and state.get("halt_until_time", 0) < now_ts:
        state["halt_until_time"] = now_ts + DD_HALT_BARS * 3600
        state["peak_equity"] = balance
        log.warning(f"  DD halt: {dd_pct:+.1f}% — halted 7 days")
        send_email(f"DD HALT {dd_pct:+.1f}%", f"Halted for 7 days.\nBalance: ${balance:,.2f}")

    halted = state.get("halt_until_time", 0) > now_ts

    # Reconcile position with exchange
    exch_pos = client.positions(PAIR)
    pos_dict = state.get("position")
    if exch_pos and pos_dict is None:
        ep = exch_pos[0]
        side = "LONG" if float(ep["positionAmt"]) > 0 else "SHORT"
        entry = float(ep["entryPrice"])
        qty = abs(float(ep["positionAmt"]))
        pos_dict = {
            "side": side, "entry_price": entry,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "qty": qty, "sl_price": 0, "tp_price": 0, "entry_bar_ts": now_ts,
        }
        state["position"] = pos_dict
        log.warning(f"  Adopted {side} {qty}@{entry}")
    elif not exch_pos and pos_dict is not None:
        log.warning("  State has position but exchange flat — clearing")
        state["position"] = None
        pos_dict = None

    # Status payload
    status = {
        "env": ENV, "pair": PAIR, "price": price, "balance": balance,
        "peak_equity": peak, "drawdown_pct": dd_pct, "halted": halted,
        "halt_until_time": state.get("halt_until_time", 0),
        "leverage": LEV, "position": pos_dict, "raw_indicators": sig.raw,
    }

    if pos_dict is not None:
        # ── In position: check TP/SL/TIME ──
        entry_px = pos_dict["entry_price"]
        sl_px = pos_dict["sl_price"]
        tp_px = pos_dict["tp_price"]
        entry_ts = pos_dict.get("entry_bar_ts", now_ts)
        held_hours = (now_ts - entry_ts) / 3600

        log.info(f"  IN {pos_dict['side']} entry=${entry_px:.2f} SL=${sl_px:.2f} TP=${tp_px:.2f} held={held_hours:.1f}h")

        should_exit = False; reason = ""

        # Time-stop: 24h max hold
        if held_hours >= MAX_HOLD_BARS:
            should_exit = True; reason = "TIME"
            log.info(f"  TIME-STOP: held {held_hours:.1f}h >= {MAX_HOLD_BARS}h")

        # SL check (intrabar via last bar's low/high)
        if not should_exit:
            if pos_dict["side"] == "LONG" and float(last["low"]) <= sl_px:
                should_exit = True; reason = "SL"
            elif pos_dict["side"] == "SHORT" and float(last["high"]) >= sl_px:
                should_exit = True; reason = "SL"

        # TP check
        if not should_exit:
            if pos_dict["side"] == "LONG" and float(last["high"]) >= tp_px:
                should_exit = True; reason = "TP"
            elif pos_dict["side"] == "SHORT" and float(last["low"]) <= tp_px:
                should_exit = True; reason = "TP"

        status.update({
            "state": "IN_POSITION",
            "held_hours": held_hours,
            "max_hold_hours": MAX_HOLD_BARS,
            "long_conditions": sig.conditions_long,
            "short_conditions": sig.conditions_short,
        })

        if should_exit and not ARGS.dry:
            client.cancel_all(PAIR)
            close_side = "SELL" if pos_dict["side"] == "LONG" else "BUY"
            resp = client.market_order(PAIR, close_side, pos_dict["qty"], reduce_only=True)
            if resp:
                fill = float(resp.get("avgPrice", price)) or price
                pmp = ((fill - entry_px)/entry_px if pos_dict["side"] == "LONG"
                       else (entry_px - fill)/entry_px)
                pnl = pmp * LEV * 100
                trade = {
                    "side": pos_dict["side"], "entry": entry_px, "exit": fill,
                    "reason": reason, "pnl_pct": pnl,
                    "entry_time": pos_dict["entry_time"],
                    "exit_time": datetime.now(timezone.utc).isoformat(),
                }
                state["trade_log"].append(trade)
                state["trade_log"] = state["trade_log"][-100:]
                state["position"] = None
                state["last_exit_time"] = now_ts
                log.info(f"  EXIT {pos_dict['side']} via {reason} @${fill:.2f} PnL {pnl:+.2f}%")
                status["just_closed"] = trade
                send_email(f"{'W' if pnl>0 else 'L'} {pos_dict['side']} {pnl:+.1f}% {reason}",
                           f"Side: {pos_dict['side']}\nEntry: ${entry_px:,.2f}\nExit: ${fill:,.2f}\n"
                           f"Reason: {reason}\nPnL: {pnl:+.2f}%\nBalance: ${balance:,.2f}")
        elif should_exit and ARGS.dry:
            log.info(f"  [DRY] Would EXIT {pos_dict['side']} via {reason}")

    else:
        # ── Flat: check entry ──
        log.info(f"  FLAT — checking BB confluence")
        log.info(f"    LONG: " + ", ".join(f"{k}={'Y' if v else 'N'}" for k, v in sig.conditions_long.items()))
        log.info(f"    SHORT: " + ", ".join(f"{k}={'Y' if v else 'N'}" for k, v in sig.conditions_short.items()))

        cd_remain = max(0, state.get("last_exit_time", 0) + COOLDOWN_BARS * 3600 - now_ts)

        status.update({
            "state": "FLAT",
            "long_conditions": sig.conditions_long,
            "short_conditions": sig.conditions_short,
            "long_ok": sig.long_ok,
            "short_ok": sig.short_ok,
            "cooldown_remaining_sec": cd_remain,
        })

        if halted:
            log.info(f"  HALTED for {(state['halt_until_time']-now_ts)//60}min")
        elif cd_remain > 0:
            log.info(f"  Cooldown {cd_remain//60}min remaining")
        elif sig.side and not ARGS.dry:
            qty = position_size(balance, price, leverage=LEV)
            qty = round_qty(qty, info["step"])
            if qty < info["min_qty"]:
                log.warning(f"  qty {qty} below min {info['min_qty']}")
            else:
                client.set_leverage(PAIR, LEV)
                side_api = "BUY" if sig.side == "LONG" else "SELL"
                resp = client.market_order(PAIR, side_api, qty)
                if resp:
                    fill = float(resp.get("avgPrice", price)) or price
                    # Place SL and TP orders
                    sl_side = "SELL" if sig.side == "LONG" else "BUY"
                    client.stop_market(PAIR, sl_side, sig.sl_price, close_position=True)
                    # TP as TAKE_PROFIT_MARKET
                    tp_side = "SELL" if sig.side == "LONG" else "BUY"
                    client.limit_order(PAIR, tp_side, sig.tp_price, close_position=True)

                    pos_dict = {
                        "side": sig.side, "entry_price": fill,
                        "entry_time": datetime.now(timezone.utc).isoformat(),
                        "qty": qty, "sl_price": sig.sl_price, "tp_price": sig.tp_price,
                        "entry_bar_ts": now_ts,
                    }
                    state["position"] = pos_dict
                    sl_pct = abs(sig.sl_price - fill)/fill * 100
                    log.info(f"  OPEN {sig.side} {qty}@{fill:.2f} SL=${sig.sl_price:.2f} TP=${sig.tp_price:.2f}")
                    status["just_opened"] = True
                    send_email(f"OPEN {sig.side} @${fill:,.2f}",
                               f"Side: {sig.side}\nEntry: ${fill:,.2f}\nQty: {qty}\n"
                               f"SL: ${sig.sl_price:,.2f} ({sl_pct:.1f}%)\n"
                               f"TP: ${sig.tp_price:,.2f} (4H BB mid)\n"
                               f"Max hold: {MAX_HOLD_BARS}h\nBalance: ${balance:,.2f}")
        elif sig.side and ARGS.dry:
            log.info(f"  [DRY] Would open {sig.side} at ${price:,.2f} SL=${sig.sl_price:.2f} TP=${sig.tp_price:.2f}")

    save_state(state)
    write_status(status)
    log.info("--- tick done ---")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception(f"FATAL: {e}")
        sys.exit(1)
