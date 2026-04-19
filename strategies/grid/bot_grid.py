#!/usr/bin/env python3
"""
bot_grid.py — Directional Grid Bot (one-shot per 1H candle close).

Long grid in uptrend (EMA200) + Short grid in downtrend.
5 grid levels, 10% range, 2% TP per grid, 15% SL.

Backtest: $5K → $831K | +126% CAGR | -25.3% DD | PF 2.64 | 240 trades/yr

Run:
  python3 strategies/grid/bot_grid.py --env testnet
  python3 strategies/grid/bot_grid.py --env testnet --dry
"""
from __future__ import annotations
import os, sys, json, time, hmac, hashlib, logging, argparse, smtplib, ssl
from datetime import datetime, timezone
from email.message import EmailMessage
import requests
import pandas as pd
import numpy as np

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(os.path.dirname(STRATEGY_DIR))
sys.path.insert(0, STRATEGY_DIR)

from grid_core import (
    LEVERAGE, N_GRIDS, GRID_RANGE_PCT, TP_PER_GRID_PCT, GRID_SL_PCT,
    MAX_HOLD_BARS, RSI_LONG_TRIGGER, RSI_SHORT_TRIGGER, RSI_LONG_EXIT, RSI_SHORT_EXIT,
    build_signals, evaluate_signal, create_grid, GridState,
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
ap.add_argument("--dry", action="store_true")
ARGS, _ = ap.parse_known_args()
ENV = ARGS.env

CFG_PATH = os.path.join(BOT_DIR, "config", f"{ENV}.json")
with open(CFG_PATH) as f:
    CFG = json.load(f)

API_KEY    = os.environ.get(CFG["api_key_env"], "")
API_SECRET = os.environ.get(CFG["api_secret_env"], "")
BASE_URL   = CFG["base_url"]
PAIR       = CFG["pair"]
LEV        = int(CFG.get("leverage", LEVERAGE))

DATA_DIR     = os.path.join(BOT_DIR, "data", ENV)
STATE_FILE   = os.path.join(DATA_DIR, "state.json")
STATUS_FILE  = os.path.join(DATA_DIR, "status.json")
LOG_FILE     = os.path.join(DATA_DIR, "bot.log")
DISABLED_FLAG = os.path.join(DATA_DIR, ".disabled")
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)])
log = logging.getLogger("bot_grid")

if os.path.exists(DISABLED_FLAG):
    log.info(f"[{ENV}] Bot DISABLED."); sys.exit(0)
if not API_KEY or not API_SECRET:
    log.error(f"Missing API keys"); sys.exit(1)

# ─── Email ───
EMAIL_FROM = os.environ.get("BOT_EMAIL", "")
EMAIL_PASS = os.environ.get("BOT_EMAIL_PASS", "")
EMAIL_TO   = os.environ.get("BOT_EMAIL_TO", "")

def send_email(subject, body):
    if not (EMAIL_FROM and EMAIL_PASS and EMAIL_TO): return
    try:
        msg = EmailMessage()
        msg["Subject"] = f"[GRID-{ENV.upper()}] {subject}"
        msg["From"] = EMAIL_FROM; msg["To"] = EMAIL_TO
        msg.set_content(body)
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=15) as s:
            s.login(EMAIL_FROM, EMAIL_PASS); s.send_message(msg)
    except: pass

# ─── Binance Client ───
class BinanceClient:
    def __init__(self, key, secret, base_url):
        self.key=key; self.secret=secret; self.base=base_url
        self.s=requests.Session(); self.s.headers.update({"X-MBX-APIKEY":key})
    def _sign(self, p):
        q="&".join(f"{k}={v}" for k,v in p.items())
        p["signature"]=hmac.new(self.secret.encode(),q.encode(),hashlib.sha256).hexdigest()
        return p
    def _req(self, method, path, params=None, signed=False, retries=3):
        params=params or {}
        if signed: params["timestamp"]=int(time.time()*1000); params["recvWindow"]=5000; params=self._sign(params)
        url=self.base+path
        for a in range(retries):
            try:
                r=self.s.request(method,url,params=params,timeout=10)
                if r.status_code==200: return r.json()
                log.warning(f"  HTTP {r.status_code}: {r.text[:200]}")
                if r.status_code in (418,429): time.sleep(5*(a+1)); continue
                return None
            except Exception as e: log.warning(f"  err {a+1}: {e}"); time.sleep(2)
        return None
    def klines(self, symbol, interval="15m", limit=1500):
        data=self._req("GET","/fapi/v1/klines",{"symbol":symbol,"interval":interval,"limit":limit})
        if not data: return None
        df=pd.DataFrame(data,columns=["open_time","open","high","low","close","volume","close_time","qav","trades","tbbav","tbqav","ignore"])
        df=df.astype({"open":float,"high":float,"low":float,"close":float,"volume":float})
        df["timestamp"]=pd.to_datetime(df["open_time"],unit="ms")
        return df[["timestamp","open","high","low","close","volume"]]
    def account(self): return self._req("GET","/fapi/v2/account",signed=True)
    def positions(self, symbol):
        acc=self.account()
        if not acc: return []
        return [p for p in acc.get("positions",[]) if p["symbol"]==symbol and float(p["positionAmt"])!=0]
    def set_leverage(self, symbol, lev): return self._req("POST","/fapi/v1/leverage",{"symbol":symbol,"leverage":lev},signed=True)
    def market_order(self, symbol, side, qty, reduce_only=False):
        p={"symbol":symbol,"side":side,"type":"MARKET","quantity":f"{qty}"}
        if reduce_only: p["reduceOnly"]="true"
        return self._req("POST","/fapi/v1/order",p,signed=True)
    def cancel_all(self, symbol): self._req("DELETE","/fapi/v1/allOpenOrders",{"symbol":symbol},signed=True)
    def exchange_info(self, symbol):
        data=self._req("GET","/fapi/v1/exchangeInfo")
        if not data: return None
        for s in data["symbols"]:
            if s["symbol"]==symbol:
                step=qty_min=0
                for f in s["filters"]:
                    if f["filterType"]=="LOT_SIZE": step=float(f["stepSize"]); qty_min=float(f["minQty"])
                return {"step":step,"min_qty":qty_min}
        return None

# ─── State ───
def load_state():
    if not os.path.exists(STATE_FILE): return default_state()
    try:
        with open(STATE_FILE) as f: return json.load(f)
    except: return default_state()

def default_state():
    return {"grid":None, "trade_log":[], "peak_equity":0.0}

def save_state(s):
    with open(STATE_FILE,"w") as f: json.dump(s,f,indent=2,default=str)

def round_qty(q, step):
    if step==0: return q
    return round(q-(q%step),8)

def _clean(v):
    if isinstance(v,dict): return {k:_clean(x) for k,x in v.items()}
    if isinstance(v,list): return [_clean(x) for x in v]
    if hasattr(v,"item"): return v.item()
    if isinstance(v,(bool,int,float,str)) or v is None: return v
    return str(v)

def write_status(payload):
    payload["updated_at"]=datetime.now(timezone.utc).isoformat()
    payload["strategy"]="Directional Grid V1"
    try:
        with open(STATUS_FILE,"w") as f: json.dump(_clean(payload),f,indent=2)
    except: pass


# ─── Main ───
def main():
    log.info(f"--- Grid bot tick --- env={ENV} pair={PAIR} dry={ARGS.dry} ---")
    client = BinanceClient(API_KEY, API_SECRET, BASE_URL)

    state = load_state()
    info = client.exchange_info(PAIR)
    if not info: log.error("No exchange info"); return

    df_15m = client.klines(PAIR, interval="15m", limit=1500)
    if df_15m is None or len(df_15m) < 200: log.error("Not enough klines"); return

    df_1h = build_signals(df_15m)
    if len(df_1h) < 50: log.error("Not enough 1H bars"); return

    last = df_1h.iloc[-2]  # last closed 1H bar
    sig = evaluate_signal(last)
    price = sig.price

    acc = client.account()
    if not acc: log.error("Account fetch failed"); return
    balance = float(acc["totalWalletBalance"])
    log.info(f"  Balance ${balance:,.2f}  Price ${price:,.2f}  RSI={sig.rsi_value:.1f}  Trend={'UP' if sig.trend==1 else 'DOWN' if sig.trend==-1 else 'FLAT'}")

    grid_state = state.get("grid")

    status = {
        "env":ENV, "pair":PAIR, "price":price, "balance":balance,
        "leverage":LEV, "raw_indicators":sig.raw,
        "grid_active": grid_state is not None,
        "grid_side": grid_state.get("side") if grid_state else None,
        "conditions": sig.conditions,
    }

    if grid_state:
        # Grid is active — log status
        side = grid_state["side"]
        levels = grid_state["grid_levels"]
        filled = grid_state["grid_filled"]
        n_filled = sum(filled)
        log.info(f"  GRID {side} active | {n_filled}/{len(levels)} levels filled | base=${grid_state['base_price']:,.0f}")

        # Check stop conditions
        should_stop = False; stop_reason = ""
        if side == "LONG" and sig.rsi_value > RSI_LONG_EXIT:
            should_stop = True; stop_reason = "RSI>70"
        elif side == "SHORT" and sig.rsi_value < RSI_SHORT_EXIT:
            should_stop = True; stop_reason = "RSI<30"
        elif side == "LONG" and sig.trend != 1:
            should_stop = True; stop_reason = "TREND_FLIP"
        elif side == "SHORT" and sig.trend != -1:
            should_stop = True; stop_reason = "TREND_FLIP"
        elif side == "LONG" and price < grid_state["base_price"] * (1 - GRID_SL_PCT):
            should_stop = True; stop_reason = "SL"
        elif side == "SHORT" and price > grid_state["base_price"] * (1 + GRID_SL_PCT):
            should_stop = True; stop_reason = "SL"

        if should_stop:
            log.info(f"  GRID STOP: {stop_reason}")
            if not ARGS.dry:
                # Close all positions
                exch_pos = client.positions(PAIR)
                if exch_pos:
                    for p in exch_pos:
                        qty = abs(float(p["positionAmt"]))
                        close_side = "SELL" if float(p["positionAmt"]) > 0 else "BUY"
                        client.market_order(PAIR, close_side, qty, reduce_only=True)
                client.cancel_all(PAIR)
            state["grid"] = None
            send_email(f"Grid {side} closed: {stop_reason}", f"Price: ${price:,.2f}\nReason: {stop_reason}\nBalance: ${balance:,.2f}")
        else:
            status["grid_levels"] = levels
            status["grid_filled"] = filled
    else:
        # No grid — check if we should start one
        log.info(f"  NO GRID — checking conditions")
        for k, v in sig.conditions.items():
            log.info(f"    {k}: {'YES' if v else 'no'}")

        if sig.should_start and not ARGS.dry:
            grid = create_grid(sig.side, price)
            log.info(f"  STARTING {sig.side} GRID at ${price:,.0f}")
            log.info(f"    Levels: {[f'${l:,.0f}' for l in grid.grid_levels]}")

            # Place initial order
            client.set_leverage(PAIR, LEV)
            qty_per_grid = (balance * LEV) / price / N_GRIDS
            qty = round_qty(qty_per_grid, info["step"])
            if qty >= info["min_qty"]:
                side_api = "BUY" if sig.side == "LONG" else "SELL"
                resp = client.market_order(PAIR, side_api, qty)
                if resp:
                    state["grid"] = {
                        "side": sig.side,
                        "base_price": price,
                        "grid_levels": grid.grid_levels,
                        "grid_filled": grid.grid_filled,
                        "start_time": datetime.now(timezone.utc).isoformat(),
                        "qty_per_grid": qty,
                    }
                    send_email(f"Grid {sig.side} started at ${price:,.0f}",
                        f"Side: {sig.side}\nPrice: ${price:,.2f}\nLevels: {len(grid.grid_levels)}\n"
                        f"Range: {GRID_RANGE_PCT*100:.0f}%\nTP/grid: {TP_PER_GRID_PCT*100:.0f}%\nBalance: ${balance:,.2f}")
        elif sig.should_start and ARGS.dry:
            log.info(f"  [DRY] Would start {sig.side} grid at ${price:,.0f}")

        status["state"] = "FLAT"
        status["long_conditions"] = {k:v for k,v in sig.conditions.items() if "Uptrend" in k or "oversold" in k}
        status["short_conditions"] = {k:v for k,v in sig.conditions.items() if "Downtrend" in k or "overbought" in k}

    save_state(state)
    write_status(status)
    log.info("--- tick done ---")


if __name__ == "__main__":
    try: main()
    except Exception as e: log.exception(f"FATAL: {e}"); sys.exit(1)
