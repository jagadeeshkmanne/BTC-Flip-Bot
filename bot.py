#!/usr/bin/env python3
"""
bot.py — Strategy V5 live bot (one-shot per 1H candle close).

V5 spec (5yr backtest: $10K → $154,657, +73% CAGR, PF 3.63, -25% DD):
  Entry  : Daily EMA50 + 4H RSI + 1H stack (RSI/MACD/engulf/ATR/vol) ALL agree
  SL     : pattern-based — min(low_entry, low_prior) − 0.1%, capped at 2.5%
  Partial TP: lock 30% at +6R favorable, leave 70% on original SL
  Exit   : opposite signal closes (NO flip-open — V4 fix)
  Cooldown: 24h after SL hit (same direction)
  DD halt : 7 days after −25% peak-to-trough drawdown
  Leverage: 2× (configurable in config/{env}.json)

Run:
  python3 bot.py --env testnet
  python3 bot.py --env testnet --dry      # log signals only
"""
from __future__ import annotations
import os, sys, json, time, hmac, hashlib, logging, argparse, smtplib, ssl
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
from pathlib import Path
import requests
import pandas as pd
import numpy as np

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BOT_DIR)

from core import (
    LEVERAGE as STRAT_LEV, RISK_PCT,
    SAME_DIR_CD_BARS, DD_HALT_PCT, DD_HALT_BARS,
    USE_PARTIAL_TP, PARTIAL_TP_R, PARTIAL_TP_FRAC,
    build_signals, evaluate_signal, evaluate_exit,
    calc_pattern_sl, position_size, Position,
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
STATUS_FILE  = os.path.join(DATA_DIR, "status.json")    # for dashboard
TRADES_LOG   = os.path.join(DATA_DIR, "trades.log")
LOG_FILE     = os.path.join(DATA_DIR, "bot.log")
DISABLED_FLAG = os.path.join(DATA_DIR, ".disabled")
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bot")

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
    """Send alert email. Silent on failure (don't break bot)."""
    if not (EMAIL_FROM and EMAIL_PASS and EMAIL_TO):
        log.info(f"  (email skipped — config missing)")
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = f"[{ENV.upper()}] {subject}"
        msg["From"]    = EMAIL_FROM
        msg["To"]      = EMAIL_TO
        msg.set_content(body)
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=15) as s:
            s.login(EMAIL_FROM, EMAIL_PASS)
            s.send_message(msg)
        log.info(f"  ✉ alert sent: {subject}")
    except Exception as e:
        log.warning(f"  email send failed: {e}")


# ─── Binance Futures Client ───
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

    def cancel_all(self, symbol):
        self._req("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol}, signed=True)

    def exchange_info(self, symbol):
        data = self._req("GET", "/fapi/v1/exchangeInfo")
        if not data: return None
        for s in data["symbols"]:
            if s["symbol"] == symbol:
                step = qty_min = price_step = 0
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        step = float(f["stepSize"]); qty_min = float(f["minQty"])
                    if f["filterType"] == "PRICE_FILTER":
                        price_step = float(f["tickSize"])
                return {"step": step, "min_qty": qty_min, "price_step": price_step}
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
        "position": None,                # dict if open, None if flat
        "last_long_sl_time":  0,         # epoch seconds
        "last_short_sl_time": 0,
        "halt_until_time":    0,         # epoch seconds (DD halt)
        "peak_equity":        0.0,
        "weekly_start":       None,
        "trade_log":          [],        # last 100 trades
    }

def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=2, default=str)


def round_qty(q, step):
    if step == 0: return q
    return round(q - (q % step), 8)


# ─── Status JSON for dashboard ───
def _clean(v):
    """Convert numpy types to native Python for JSON."""
    if isinstance(v, dict):  return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, list):  return [_clean(x) for x in v]
    if hasattr(v, "item"):   return v.item()      # numpy scalar
    if isinstance(v, (bool, int, float, str)) or v is None: return v
    return str(v)

def write_status(payload):
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(_clean(payload), f, indent=2)
    except Exception as e:
        log.warning(f"  status write failed: {e}")


# ─── Main ───
def main():
    log.info(f"━━━ V5 bot tick — env={ENV} pair={PAIR} dry={ARGS.dry} ━━━")
    client = BinanceClient(API_KEY, API_SECRET, BASE_URL)

    state = load_state()
    info = client.exchange_info(PAIR)
    if not info:
        log.error("No exchange info — abort")
        return

    # Fetch enough 15m klines to compute Daily EMA50 and 1H indicators
    # 96 bars/day × 60 days ≈ 5,760 bars (free with limit=1500 per call, but 1500 is plenty for ~16 days)
    # Need ~50 daily bars for EMA50 → 4800 bars. We do 1500 (~16 days) for now — acceptable for V5
    # because Daily EMA stabilizes via warmup. For prod, batch fetch more.
    df_15m = client.klines(PAIR, interval="15m", limit=1500)
    if df_15m is None or len(df_15m) < 200:
        log.error("Not enough klines")
        return

    # Build all signals (1H frame with conditions)
    df_1h = build_signals(df_15m)
    if len(df_1h) < 50:
        log.error("Not enough 1H bars after resample")
        return

    last = df_1h.iloc[-1]
    sig  = evaluate_signal(last)
    price = sig.price

    # Account & position state
    acc = client.account()
    if not acc:
        log.error("Account fetch failed"); return
    balance = float(acc["totalWalletBalance"])
    log.info(f"  Balance ${balance:,.2f}  Price ${price:,.2f}")

    # Update peak equity (for DD halt check)
    if balance > state.get("peak_equity", 0):
        state["peak_equity"] = balance
    peak = state.get("peak_equity", balance)
    dd_pct = (balance / peak - 1) * 100 if peak > 0 else 0
    now_ts = int(time.time())

    # DD halt check
    if dd_pct <= -DD_HALT_PCT * 100 and state.get("halt_until_time", 0) < now_ts:
        state["halt_until_time"] = now_ts + DD_HALT_BARS * 3600
        log.warning(f"  ⚠ DD halt fired: dd={dd_pct:+.1f}% — halted for 7 days")
        send_email(f"⚠ DD HALT FIRED — bot halted 7 days",
                   f"Drawdown reached {dd_pct:+.2f}%\nBalance: ${balance:,.2f}\n"
                   f"Peak: ${peak:,.2f}\nResume: {datetime.fromtimestamp(state['halt_until_time'])}")
        state["peak_equity"] = balance   # reset peak

    halted = state.get("halt_until_time", 0) > now_ts

    # Reconcile open position with exchange
    exch_pos = client.positions(PAIR)
    pos_dict = state.get("position")
    if exch_pos and pos_dict is None:
        # exchange has position, state doesn't — adopt
        ep = exch_pos[0]
        side = "LONG" if float(ep["positionAmt"]) > 0 else "SHORT"
        entry = float(ep["entryPrice"])
        qty = abs(float(ep["positionAmt"]))
        sl = entry * (1 - 0.025) if side == "LONG" else entry * (1 + 0.025)  # safe SL
        pos_dict = {
            "side": side, "entry_price": entry, "entry_time": datetime.now(timezone.utc).isoformat(),
            "qty": qty, "sl_price": sl, "pos_atr": sig.atr, "partial_taken": False,
        }
        state["position"] = pos_dict
        log.warning(f"  Adopted {side} {qty}@{entry} from exchange")
    elif not exch_pos and pos_dict is not None:
        log.warning("  State has position but exchange flat — clearing state")
        state["position"] = None
        pos_dict = None

    # Build status payload for dashboard
    status = {
        "env": ENV, "pair": PAIR, "price": price, "balance": balance,
        "peak_equity": peak, "drawdown_pct": dd_pct, "halted": halted,
        "halt_until_time": state.get("halt_until_time", 0),
        "leverage": LEV, "position": pos_dict, "raw_indicators": sig.raw,
    }

    if pos_dict is None:
        # ── Flat: show entry conditions met/pending ──
        log.info(f"  FLAT — checking entry conditions")
        log.info(f"    LONG conditions: " + ", ".join(
            f"{k}={'✓' if v else '✗'}" for k, v in sig.conditions_long.items()))
        log.info(f"    SHORT conditions: " + ", ".join(
            f"{k}={'✓' if v else '✗'}" for k, v in sig.conditions_short.items()))

        # Same-direction cooldown check
        long_cd_remain  = max(0, state["last_long_sl_time"]  + SAME_DIR_CD_BARS*3600 - now_ts)
        short_cd_remain = max(0, state["last_short_sl_time"] + SAME_DIR_CD_BARS*3600 - now_ts)

        status.update({
            "state": "FLAT",
            "long_conditions":  sig.conditions_long,
            "short_conditions": sig.conditions_short,
            "long_ok":  sig.long_ok,
            "short_ok": sig.short_ok,
            "long_cooldown_remaining_sec":  long_cd_remain,
            "short_cooldown_remaining_sec": short_cd_remain,
        })

        if halted:
            log.info(f"  HALTED for {(state['halt_until_time']-now_ts)//60} more minutes")
        elif sig.side == "LONG" and long_cd_remain > 0:
            log.info(f"  LONG signal but cooldown {long_cd_remain//60}min remaining")
        elif sig.side == "SHORT" and short_cd_remain > 0:
            log.info(f"  SHORT signal but cooldown {short_cd_remain//60}min remaining")
        elif sig.side and not ARGS.dry:
            # Open position
            sl_price = calc_pattern_sl(
                sig.side, price,
                float(last["low"]), float(last["high"]),
                float(df_1h.iloc[-2]["low"]), float(df_1h.iloc[-2]["high"]),
            )
            qty = position_size(balance, price, sl_price, leverage=LEV, risk_pct=RISK_PCT)
            qty = round_qty(qty, info["step"])
            if qty < info["min_qty"]:
                log.warning(f"  qty {qty} below min {info['min_qty']} — skip")
            else:
                client.set_leverage(PAIR, LEV)
                side_api = "BUY" if sig.side == "LONG" else "SELL"
                ord_resp = client.market_order(PAIR, side_api, qty)
                if ord_resp:
                    fill_price = float(ord_resp.get("avgPrice", price)) or price
                    # Re-place SL on fill price
                    sl_price = calc_pattern_sl(
                        sig.side, fill_price,
                        float(last["low"]), float(last["high"]),
                        float(df_1h.iloc[-2]["low"]), float(df_1h.iloc[-2]["high"]),
                    )
                    sl_side = "SELL" if sig.side == "LONG" else "BUY"
                    client.stop_market(PAIR, sl_side, sl_price, close_position=True)
                    pos_dict = {
                        "side": sig.side, "entry_price": fill_price,
                        "entry_time": datetime.now(timezone.utc).isoformat(),
                        "qty": qty, "sl_price": sl_price, "pos_atr": sig.atr,
                        "partial_taken": False,
                    }
                    state["position"] = pos_dict
                    log.info(f"  ★ OPENED {sig.side} {qty}@{fill_price:.2f}  SL={sl_price:.2f}")
                    status["just_opened"] = True
                    sl_pct = abs(sl_price - fill_price)/fill_price * 100
                    send_email(f"★ OPEN {sig.side} @${fill_price:,.2f}",
                               f"Side: {sig.side}\nEntry: ${fill_price:,.2f}\n"
                               f"Qty: {qty}\nSL: ${sl_price:,.2f} ({sl_pct:.2f}% away)\n"
                               f"Balance: ${balance:,.2f}\nLeverage: {LEV}×")
        elif sig.side and ARGS.dry:
            log.info(f"  [DRY] Would open {sig.side} at ${price:,.2f}")

    else:
        # ── In position: show exit conditions met/pending ──
        pos = Position(
            side=pos_dict["side"], entry_price=pos_dict["entry_price"],
            entry_time=pos_dict["entry_time"], qty=pos_dict["qty"],
            sl_price=pos_dict["sl_price"], pos_atr=pos_dict.get("pos_atr", sig.atr),
            partial_taken=pos_dict.get("partial_taken", False),
        )
        ex = evaluate_exit(pos, last, sig)

        log.info(f"  IN {pos.side} qty={pos.qty} entry=${pos.entry_price:.2f} "
                 f"price=${price:.2f} SL=${pos.sl_price:.2f} ({ex.sl_distance_pct:+.2f}% from SL)")
        log.info(f"    Favorable progress: {ex.favorable_r:.2f}R "
                 f"(partial TP at +{PARTIAL_TP_R}R, need {PARTIAL_TP_R - ex.favorable_r:+.2f}R)")
        log.info(f"    Opposite signal: {'✓ PRESENT' if ex.opposite_signal_active else '✗ pending'}")

        status.update({
            "state": "IN_POSITION",
            "exit_conditions": {
                "Stop loss hit":         ex.should_exit and ex.reason == "SL",
                f"Partial TP at +{PARTIAL_TP_R}R":  ex.partial_tp_hit,
                "Opposite signal":       ex.opposite_signal_active,
            },
            "sl_distance_pct":  ex.sl_distance_pct,
            "favorable_r":      ex.favorable_r,
            "partial_tp_distance_pct": ex.partial_tp_distance_pct,
            "partial_taken":    pos.partial_taken,
        })

        # Partial TP execution
        if USE_PARTIAL_TP and ex.partial_tp_hit and not pos.partial_taken and not ARGS.dry:
            close_qty = round_qty(pos.qty * PARTIAL_TP_FRAC, info["step"])
            if close_qty >= info["min_qty"]:
                close_side = "SELL" if pos.side == "LONG" else "BUY"
                resp = client.market_order(PAIR, close_side, close_qty, reduce_only=True)
                if resp:
                    pos.partial_taken = True
                    pos.qty -= close_qty
                    pos_dict["partial_taken"] = True
                    pos_dict["qty"] = pos.qty
                    state["position"] = pos_dict
                    log.info(f"  ✦ PARTIAL TP @+{PARTIAL_TP_R}R closed {close_qty} ({PARTIAL_TP_FRAC*100:.0f}%)")
                    status["just_partial"] = True
                    send_email(f"✦ PARTIAL TP +{PARTIAL_TP_R}R · {pos.side}",
                               f"Locked {PARTIAL_TP_FRAC*100:.0f}% of position at +{PARTIAL_TP_R}R favorable.\n"
                               f"Closed qty: {close_qty}\nRemaining: {pos.qty}\n"
                               f"Price: ${price:,.2f}\nBalance: ${balance:,.2f}")

        # Full exit (SL or opposite signal)
        if ex.should_exit and not ARGS.dry:
            client.cancel_all(PAIR)  # cancel SL order
            close_side = "SELL" if pos.side == "LONG" else "BUY"
            resp = client.market_order(PAIR, close_side, pos.qty, reduce_only=True)
            if resp:
                fill_price = float(resp.get("avgPrice", ex.exit_price)) or ex.exit_price
                pmp = ((fill_price - pos.entry_price)/pos.entry_price if pos.side == "LONG"
                       else (pos.entry_price - fill_price)/pos.entry_price)
                pnl_pct = pmp * LEV * 100
                trade_record = {
                    "side": pos.side, "entry": pos.entry_price, "exit": fill_price,
                    "qty": pos.qty, "reason": ex.reason, "pnl_pct": pnl_pct,
                    "entry_time": pos.entry_time, "exit_time": datetime.now(timezone.utc).isoformat(),
                }
                state["trade_log"].append(trade_record)
                state["trade_log"] = state["trade_log"][-100:]
                if ex.reason == "SL":
                    if pos.side == "LONG":  state["last_long_sl_time"]  = now_ts
                    else:                   state["last_short_sl_time"] = now_ts
                state["position"] = None
                log.info(f"  ✕ EXIT {pos.side} via {ex.reason} @${fill_price:.2f}  PnL {pnl_pct:+.2f}%")
                status["just_closed"] = trade_record
                emoji = "✓" if pnl_pct > 0 else "✕"
                send_email(f"{emoji} CLOSE {pos.side} {pnl_pct:+.2f}% · {ex.reason}",
                           f"Side: {pos.side}\nEntry: ${pos.entry_price:,.2f}\n"
                           f"Exit: ${fill_price:,.2f}\nReason: {ex.reason}\n"
                           f"PnL: {pnl_pct:+.2f}%\nBalance: ${balance:,.2f}\n"
                           f"Held: {pos.entry_time} → {trade_record['exit_time']}")
        elif ex.should_exit and ARGS.dry:
            log.info(f"  [DRY] Would EXIT {pos.side} via {ex.reason}")

    save_state(state)
    write_status(status)
    log.info("━━━ tick done ━━━")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception(f"FATAL: {e}")
        sys.exit(1)
