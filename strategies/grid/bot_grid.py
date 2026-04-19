#!/usr/bin/env python3
"""
bot_grid.py — Directional Grid Bot (one-shot per 1H candle close).

Manages actual limit orders on Binance:
- Places limit BUY orders at each grid level (long grid)
- When a buy fills, places limit SELL at +2% (TP)
- When TP fills, re-places the buy (grid recycles)
- On stop: cancels all orders, market-closes positions

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
    build_signals, evaluate_signal,
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
with open(CFG_PATH) as f: CFG = json.load(f)

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

    def set_leverage(self, symbol, lev):
        return self._req("POST","/fapi/v1/leverage",{"symbol":symbol,"leverage":lev},signed=True)

    def market_order(self, symbol, side, qty, reduce_only=False):
        p={"symbol":symbol,"side":side,"type":"MARKET","quantity":f"{qty}"}
        if reduce_only: p["reduceOnly"]="true"
        return self._req("POST","/fapi/v1/order",p,signed=True)

    def limit_order(self, symbol, side, qty, price):
        """Place a limit order."""
        p={"symbol":symbol,"side":side,"type":"LIMIT","quantity":f"{qty}",
           "price":f"{price:.2f}","timeInForce":"GTC"}
        return self._req("POST","/fapi/v1/order",p,signed=True)

    def stop_market(self, symbol, side, stop_price, close_position=True):
        p={"symbol":symbol,"side":side,"type":"STOP_MARKET",
           "stopPrice":f"{stop_price:.2f}","workingType":"MARK_PRICE",
           "closePosition":"true" if close_position else "false"}
        return self._req("POST","/fapi/v1/order",p,signed=True)

    def take_profit_market(self, symbol, side, stop_price, close_position=True):
        p={"symbol":symbol,"side":side,"type":"TAKE_PROFIT_MARKET",
           "stopPrice":f"{stop_price:.2f}","workingType":"MARK_PRICE",
           "closePosition":"true" if close_position else "false"}
        return self._req("POST","/fapi/v1/order",p,signed=True)

    def cancel_all(self, symbol):
        self._req("DELETE","/fapi/v1/allOpenOrders",{"symbol":symbol},signed=True)

    def open_orders(self, symbol):
        return self._req("GET","/fapi/v1/openOrders",{"symbol":symbol},signed=True) or []

    def exchange_info(self, symbol):
        data=self._req("GET","/fapi/v1/exchangeInfo")
        if not data: return None
        for s in data["symbols"]:
            if s["symbol"]==symbol:
                step=qty_min=price_step=0
                for f in s["filters"]:
                    if f["filterType"]=="LOT_SIZE": step=float(f["stepSize"]); qty_min=float(f["minQty"])
                    if f["filterType"]=="PRICE_FILTER": price_step=float(f["tickSize"])
                return {"step":step,"min_qty":qty_min,"price_step":price_step}
        return None


# ─── State ───
def load_state():
    if not os.path.exists(STATE_FILE): return default_state()
    try:
        with open(STATE_FILE) as f: return json.load(f)
    except: return default_state()

def default_state():
    return {"grid":None, "trade_log":[], "total_realized_pnl":0.0, "total_grids":0, "total_wins":0}

def save_state(s):
    with open(STATE_FILE,"w") as f: json.dump(s,f,indent=2,default=str)

def round_qty(q, step):
    if step==0: return q
    return round(q-(q%step),8)

def round_price(p, step):
    if step==0: return round(p,2)
    return round(round(p/step)*step, 8)

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

    last = df_1h.iloc[-2]
    sig = evaluate_signal(last)
    price = sig.price

    acc = client.account()
    if not acc: log.error("Account fetch failed"); return
    balance = float(acc["totalWalletBalance"])
    log.info(f"  Balance ${balance:,.2f}  Price ${price:,.2f}  RSI={sig.rsi_value:.1f}  Trend={'UP' if sig.trend==1 else 'DOWN' if sig.trend==-1 else 'FLAT'}")

    grid = state.get("grid")
    now_ts = int(time.time())

    status = {
        "env":ENV, "pair":PAIR, "price":price, "balance":balance,
        "leverage":LEV, "raw_indicators":sig.raw,
        "grid_active": grid is not None,
        "grid_side": grid.get("side") if grid else None,
        "conditions": sig.conditions,
        "total_realized_pnl": state.get("total_realized_pnl", 0),
        "total_grids": state.get("total_grids", 0),
        "total_wins": state.get("total_wins", 0),
    }

    if grid:
        side = grid["side"]
        levels = grid["grid_levels"]
        filled = grid["grid_filled"]
        tp_prices = grid.get("tp_prices", [])
        n_filled = sum(filled)
        start_ts = grid.get("start_ts", now_ts)
        held_hours = (now_ts - start_ts) / 3600
        grid_pnl = grid.get("session_pnl", 0)

        log.info(f"  GRID {side} | {n_filled}/{len(levels)} filled | held {held_hours:.1f}h | session PnL {grid_pnl:+.2f}%")

        # ── Check if any grid level filled (by checking exchange position) ──
        if not ARGS.dry:
            exch_pos = client.positions(PAIR)
            exch_qty = 0
            if exch_pos:
                exch_qty = abs(float(exch_pos[0]["positionAmt"]))

            # Check open orders to see which are still pending
            open_ords = client.open_orders(PAIR)
            pending_buy_prices = set()
            pending_sell_prices = set()
            for o in open_ords:
                op = float(o["price"])
                if o["side"] == "BUY": pending_buy_prices.add(round(op, 2))
                else: pending_sell_prices.add(round(op, 2))

            # Reconcile: if a buy order is gone but we expected it, it filled
            qty_per = grid.get("qty_per_grid", 0)
            for g_idx in range(len(levels)):
                lvl_price = round(levels[g_idx], 2)
                if side == "LONG":
                    if not filled[g_idx] and lvl_price not in pending_buy_prices and g_idx < len(levels)-1:
                        # Buy order was placed but is gone → it filled
                        if price <= levels[g_idx] * 1.01:  # price is near or below this level
                            filled[g_idx] = True
                            grid["grid_filled"] = filled
                            log.info(f"    Level {g_idx+1} FILLED at ${levels[g_idx]:,.0f}")
                            # Place TP sell order
                            tp_px = round_price(levels[g_idx] * (1 + TP_PER_GRID_PCT), info["price_step"])
                            resp = client.limit_order(PAIR, "SELL", qty_per, tp_px)
                            if resp:
                                log.info(f"    TP sell placed at ${tp_px:,.0f}")
                    # Check if TP sell filled (level was filled, now unfilled because TP hit)
                    if filled[g_idx] and g_idx < len(levels)-1:
                        tp_px = round(levels[g_idx] * (1 + TP_PER_GRID_PCT), 2)
                        if tp_px not in pending_sell_prices and price >= tp_px * 0.999:
                            # TP sell was placed but is gone → it filled (profit!)
                            pnl = TP_PER_GRID_PCT * LEV / N_GRIDS * 100
                            grid["session_pnl"] = grid.get("session_pnl", 0) + pnl
                            state["total_realized_pnl"] = state.get("total_realized_pnl", 0) + pnl
                            filled[g_idx] = False
                            grid["grid_filled"] = filled
                            grid["fills_completed"] = grid.get("fills_completed", 0) + 1
                            log.info(f"    Level {g_idx+1} TP HIT! +{pnl:.2f}% profit")
                            # Re-place buy order (grid recycles)
                            buy_px = round_price(levels[g_idx], info["price_step"])
                            client.limit_order(PAIR, "BUY", qty_per, buy_px)
                            log.info(f"    Re-placed buy at ${buy_px:,.0f}")
                else:  # SHORT grid
                    if not filled[g_idx] and lvl_price not in pending_sell_prices and g_idx > 0:
                        if price >= levels[g_idx] * 0.99:
                            filled[g_idx] = True
                            grid["grid_filled"] = filled
                            log.info(f"    Level {g_idx+1} FILLED (short) at ${levels[g_idx]:,.0f}")
                            tp_px = round_price(levels[g_idx] * (1 - TP_PER_GRID_PCT), info["price_step"])
                            resp = client.limit_order(PAIR, "BUY", qty_per, tp_px)
                            if resp:
                                log.info(f"    TP buy placed at ${tp_px:,.0f}")
                    if filled[g_idx] and g_idx > 0:
                        tp_px = round(levels[g_idx] * (1 - TP_PER_GRID_PCT), 2)
                        if tp_px not in pending_buy_prices and price <= tp_px * 1.001:
                            pnl = TP_PER_GRID_PCT * LEV / N_GRIDS * 100
                            grid["session_pnl"] = grid.get("session_pnl", 0) + pnl
                            state["total_realized_pnl"] = state.get("total_realized_pnl", 0) + pnl
                            filled[g_idx] = False
                            grid["grid_filled"] = filled
                            grid["fills_completed"] = grid.get("fills_completed", 0) + 1
                            log.info(f"    Level {g_idx+1} TP HIT (short)! +{pnl:.2f}%")
                            sell_px = round_price(levels[g_idx], info["price_step"])
                            client.limit_order(PAIR, "SELL", qty_per, sell_px)

        # ── Stop conditions ──
        should_stop = False; stop_reason = ""
        if side == "LONG" and sig.rsi_value > RSI_LONG_EXIT: should_stop=True; stop_reason="RSI>70"
        elif side == "SHORT" and sig.rsi_value < RSI_SHORT_EXIT: should_stop=True; stop_reason="RSI<30"
        elif side == "LONG" and sig.trend != 1: should_stop=True; stop_reason="TREND_FLIP"
        elif side == "SHORT" and sig.trend != -1: should_stop=True; stop_reason="TREND_FLIP"
        elif side == "LONG" and price < grid["base_price"]*(1-GRID_SL_PCT): should_stop=True; stop_reason="SL_15%"
        elif side == "SHORT" and price > grid["base_price"]*(1+GRID_SL_PCT): should_stop=True; stop_reason="SL_15%"
        elif held_hours > MAX_HOLD_BARS: should_stop=True; stop_reason="MAX_HOLD"

        if should_stop:
            log.info(f"  GRID STOP: {stop_reason}")
            if not ARGS.dry:
                client.cancel_all(PAIR)
                exch_pos = client.positions(PAIR)
                close_pnl = 0
                if exch_pos:
                    for p in exch_pos:
                        qty = abs(float(p["positionAmt"]))
                        entry_px = float(p["entryPrice"])
                        close_side = "SELL" if float(p["positionAmt"]) > 0 else "BUY"
                        resp = client.market_order(PAIR, close_side, qty, reduce_only=True)
                        if resp:
                            fill = float(resp.get("avgPrice", price)) or price
                            if side == "LONG": pnl_pct = (fill - entry_px) / entry_px * LEV * 100
                            else: pnl_pct = (entry_px - fill) / entry_px * LEV * 100
                            close_pnl += pnl_pct

                total_session_pnl = grid.get("session_pnl", 0) + close_pnl
                is_win = total_session_pnl > 0
                state["total_realized_pnl"] = state.get("total_realized_pnl", 0) + close_pnl
                state["total_grids"] = state.get("total_grids", 0) + 1
                if is_win: state["total_wins"] = state.get("total_wins", 0) + 1

                state["trade_log"].append({
                    "side": side, "entry": grid["base_price"], "exit": price,
                    "pnl_pct": total_session_pnl, "reason": stop_reason,
                    "fills": grid.get("fills_completed", 0),
                    "duration_h": held_hours,
                    "entry_time": grid.get("start_time"),
                    "exit_time": datetime.now(timezone.utc).isoformat(),
                })
                state["trade_log"] = state["trade_log"][-100:]
                log.info(f"  Grid closed: {stop_reason} | session PnL {total_session_pnl:+.2f}% | fills: {grid.get('fills_completed',0)}")
                send_email(f"Grid {side} closed: {stop_reason} ({total_session_pnl:+.1f}%)",
                    f"Side: {side}\nEntry: ${grid['base_price']:,.0f}\nExit: ${price:,.0f}\n"
                    f"Reason: {stop_reason}\nSession PnL: {total_session_pnl:+.2f}%\n"
                    f"TP fills: {grid.get('fills_completed',0)}\nHeld: {held_hours:.1f}h\nBalance: ${balance:,.2f}")

            state["grid"] = None
        else:
            # Get REAL position data from exchange
            exch_position = None
            if not ARGS.dry:
                exch_pos = client.positions(PAIR)
                if exch_pos:
                    ep = exch_pos[0]
                    exch_position = {
                        "qty": abs(float(ep["positionAmt"])),
                        "entry_price": float(ep["entryPrice"]),
                        "break_even": float(ep.get("breakEvenPrice", ep["entryPrice"])),
                        "unrealized_pnl_usd": float(ep["unrealizedProfit"]),
                        "margin": float(ep.get("isolatedWallet", ep["positionInitialMargin"])),
                        "notional": float(ep["notional"]),
                        "leverage": int(ep["leverage"]),
                    }
                    pnl_pct = exch_position["unrealized_pnl_usd"] / exch_position["margin"] * 100 if exch_position["margin"] > 0 else 0
                    exch_position["unrealized_pnl_pct"] = pnl_pct

                # Get open orders
                open_ords = client.open_orders(PAIR)
                open_orders_list = []
                for o in open_ords:
                    open_orders_list.append({
                        "side": o["side"],
                        "type": o["type"],
                        "price": float(o["price"]),
                        "qty": float(o["origQty"]),
                    })
            else:
                open_orders_list = []

            status.update({
                "grid_levels": levels, "grid_filled": filled,
                "grid_base": grid["base_price"],
                "held_hours": held_hours,
                "session_pnl": grid.get("session_pnl", 0),
                "fills_completed": grid.get("fills_completed", 0),
                "position": exch_position,
                "open_orders": open_orders_list,
            })

        for lvl, f in zip(levels, filled):
            log.info(f"    ${lvl:>10,.0f}  {'FILLED' if f else 'waiting'}")

    else:
        # ── No grid — check entry ──
        log.info(f"  NO GRID — checking conditions")
        for k, v in sig.conditions.items():
            log.info(f"    {k}: {'YES' if v else 'no'}")

        if sig.should_start and not ARGS.dry:
            side = sig.side
            log.info(f"  STARTING {side} GRID at ${price:,.0f}")

            client.set_leverage(PAIR, LEV)
            client.cancel_all(PAIR)

            qty_per = round_qty((balance * LEV) / price / N_GRIDS, info["step"])
            if qty_per < info["min_qty"]:
                log.warning(f"  qty {qty_per} below min {info['min_qty']}"); return

            # Create grid levels
            if side == "LONG":
                grid_low = price * (1 - GRID_RANGE_PCT)
                spacing = (price - grid_low) / N_GRIDS
                levels = [round_price(grid_low + spacing * j, info["price_step"]) for j in range(N_GRIDS)]
                filled = [False] * N_GRIDS

                # Buy at current price (top level)
                resp = client.market_order(PAIR, "BUY", qty_per)
                if resp:
                    filled[-1] = True
                    fill_price = float(resp.get("avgPrice", price)) or price
                    levels[-1] = fill_price  # update top level to actual fill price
                    log.info(f"    Entry BUY filled at ${fill_price:,.0f}")

                    # Place limit buy orders at lower levels
                    for g_idx in range(N_GRIDS - 1):
                        buy_px = round_price(levels[g_idx], info["price_step"])
                        r = client.limit_order(PAIR, "BUY", qty_per, buy_px)
                        if r: log.info(f"    Limit BUY at ${buy_px:,.0f}")

                    # Place TP sell for the entry level
                    tp_px = round_price(fill_price * (1 + TP_PER_GRID_PCT), info["price_step"])
                    client.limit_order(PAIR, "SELL", qty_per, tp_px)
                    log.info(f"    TP SELL at ${tp_px:,.0f}")

                    # Place SL
                    sl_px = round_price(price * (1 - GRID_SL_PCT), info["price_step"])
                    client.stop_market(PAIR, "SELL", sl_px, close_position=True)
                    log.info(f"    SL at ${sl_px:,.0f}")

            else:  # SHORT
                grid_high = price * (1 + GRID_RANGE_PCT)
                spacing = (grid_high - price) / N_GRIDS
                levels = [round_price(price + spacing * j, info["price_step"]) for j in range(N_GRIDS)]
                filled = [False] * N_GRIDS

                resp = client.market_order(PAIR, "SELL", qty_per)
                if resp:
                    filled[0] = True
                    fill_price = float(resp.get("avgPrice", price)) or price
                    levels[0] = fill_price  # update bottom level to actual fill price
                    log.info(f"    Entry SELL filled at ${fill_price:,.0f}")

                    for g_idx in range(1, N_GRIDS):
                        sell_px = round_price(levels[g_idx], info["price_step"])
                        r = client.limit_order(PAIR, "SELL", qty_per, sell_px)
                        if r: log.info(f"    Limit SELL at ${sell_px:,.0f}")

                    tp_px = round_price(fill_price * (1 - TP_PER_GRID_PCT), info["price_step"])
                    client.limit_order(PAIR, "BUY", qty_per, tp_px)
                    log.info(f"    TP BUY at ${tp_px:,.0f}")

                    sl_px = round_price(price * (1 + GRID_SL_PCT), info["price_step"])
                    client.stop_market(PAIR, "BUY", sl_px, close_position=True)
                    log.info(f"    SL at ${sl_px:,.0f}")

            state["grid"] = {
                "side": side, "base_price": price,
                "grid_levels": levels, "grid_filled": filled,
                "qty_per_grid": qty_per,
                "start_ts": now_ts,
                "start_time": datetime.now(timezone.utc).isoformat(),
                "session_pnl": 0, "fills_completed": 0,
            }
            send_email(f"Grid {side} started at ${price:,.0f}",
                f"Side: {side}\nPrice: ${price:,.2f}\nLevels: {N_GRIDS}\n"
                f"Range: {GRID_RANGE_PCT*100:.0f}%\nTP/grid: {TP_PER_GRID_PCT*100:.0f}%\n"
                f"Levels: {[f'${l:,.0f}' for l in levels]}\nBalance: ${balance:,.2f}")

        elif sig.should_start and ARGS.dry:
            side = sig.side
            if side == "LONG":
                grid_low = price * (1 - GRID_RANGE_PCT)
                spacing = (price - grid_low) / N_GRIDS
                levels = [grid_low + spacing * j for j in range(N_GRIDS)]
            else:
                grid_high = price * (1 + GRID_RANGE_PCT)
                spacing = (grid_high - price) / N_GRIDS
                levels = [price + spacing * j for j in range(N_GRIDS)]
            log.info(f"  [DRY] Would start {side} grid at ${price:,.0f}")
            for l in levels: log.info(f"    ${l:,.0f}")

        status["state"] = "FLAT"
        # Show how far RSI is from trigger
        if sig.trend == 1:
            status["rsi_distance"] = sig.rsi_value - RSI_LONG_TRIGGER
        elif sig.trend == -1:
            status["rsi_distance"] = RSI_SHORT_TRIGGER - sig.rsi_value

    save_state(state)
    write_status(status)
    log.info("--- tick done ---")


if __name__ == "__main__":
    try: main()
    except Exception as e: log.exception(f"FATAL: {e}"); sys.exit(1)
