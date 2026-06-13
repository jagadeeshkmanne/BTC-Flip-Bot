import os
import sys
import json
import logging
from typing import Optional
from datetime import datetime, timezone, timedelta
import pandas as pd

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
STRATEGY_DIR = os.path.join(BOT_DIR, "strategies")
sys.path.insert(0, STRATEGY_DIR)

from core_pro import (
    LEVERAGE, TIMEFRAME, EMA_FAST, EMA_SLOW, TRAIL_ATR_MULT,
    calculate_indicators, pro_signal
)

# ─── Config ───
DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("PRO_DATA_DIR", "pro_4h"))
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE  = os.path.join(DATA_DIR, "state.json")
STATUS_FILE = os.path.join(DATA_DIR, "status.json")
LOG_FILE    = os.path.join(DATA_DIR, "bot.log")

PAIR = "BTCUSDT"
INITIAL_BALANCE = 5000.0
COMMISSION_PCT = float(os.environ.get("PRO_COMMISSION_PCT", "0.00055"))

log = logging.getLogger("bot_pro")
log.setLevel(logging.INFO)
log.handlers.clear()
fh = logging.FileHandler(LOG_FILE)
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(fh)
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(sh)

# ─── Data Fetchers (Bybit V5) ───
from bybit_data import fetch_klines as _bb_klines, fetch_live_price as _bb_price

def fetch_klines(interval: str, limit: int = 500) -> Optional[pd.DataFrame]:
    return _bb_klines(interval, limit, PAIR, log)

def fetch_live_price() -> Optional[float]:
    return _bb_price(PAIR, log)

# ─── State I/O ───
def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "balance": INITIAL_BALANCE, "peak_equity": INITIAL_BALANCE,
            "position": None, "stats": {"total": 0, "wins": 0, "pnl": 0.0}, "trade_log": [],
        }
    with open(STATE_FILE) as f:
        return json.load(f)

def save_state(s):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, default=str, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATE_FILE)

def write_status(payload):
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, default=str, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, STATUS_FILE)

# ─── Execution ───
def close_position(state, pos, exit_px: float, reason: str):
    side = pos["side"]
    qty = pos["qty"]
    entry = pos["entry_px"]
    
    gross = (exit_px - entry) * qty if side == "LONG" else (entry - exit_px) * qty
    fees = exit_px * qty * COMMISSION_PCT
    net = gross - fees
    
    state["balance"] += net
    
    state["stats"]["total"] += 1
    if net > 0:
        state["stats"]["wins"] += 1
    
    trade_record = {
        "side": side, "entry": entry, "exit": exit_px, "qty": qty, "net_usd": net,
        "reason": reason, "leverage": LEVERAGE, 
        "entry_time": pos.get("entry_time"),
        "exit_time": datetime.now(timezone.utc).isoformat()
    }
    state.setdefault("trade_log", []).append(trade_record)
    state["trade_log"] = state["trade_log"][-200:]
    
    log.warning(f"  EXIT {side} via {reason} @${exit_px:.2f} | net ${net:+.2f} | balance ${state['balance']:.2f}")

def open_position(state, side: str, entry_px: float, atr: float):
    # Pro leverages 95% of available balance
    notional = state["balance"] * 0.95 * LEVERAGE
    qty = round(notional / entry_px, 3)
    
    if qty <= 0:
        return
        
    fee = entry_px * qty * COMMISSION_PCT
    state["balance"] -= fee
    
    # Initial Trailing Stop
    sl_offset = atr * TRAIL_ATR_MULT
    trail_sl = entry_px - sl_offset if side == "LONG" else entry_px + sl_offset
    
    state["position"] = {
        "side": side, "entry_px": entry_px, "qty": qty, "peak_px": entry_px,
        "trail_sl": trail_sl, "leverage": LEVERAGE,
        "entry_time": datetime.now(timezone.utc).isoformat()
    }
    
    log.warning(f"  OPENED {side} {qty}@${entry_px:.2f} | initial_sl ${trail_sl:.2f}")

def update_trailing_stop(pos, live_px, atr):
    side = pos["side"]
    sl_offset = atr * TRAIL_ATR_MULT
    
    if side == "LONG":
        if live_px > pos["peak_px"]:
            pos["peak_px"] = live_px
            new_sl = live_px - sl_offset
            if new_sl > pos["trail_sl"]:
                pos["trail_sl"] = new_sl
    elif side == "SHORT":
        if live_px < pos["peak_px"]:
            pos["peak_px"] = live_px
            new_sl = live_px + sl_offset
            if new_sl < pos["trail_sl"]:
                pos["trail_sl"] = new_sl

def main():
    log.info("=" * 60)
    log.info(f"Pro Swing Bot — {TIMEFRAME} EMA {EMA_FAST}/{EMA_SLOW} | {TRAIL_ATR_MULT} ATR Trail | {LEVERAGE}x Lev")

    state = load_state()
    
    df = fetch_klines(TIMEFRAME, 500)
    if df is None or len(df) < EMA_SLOW + 2:
        log.error("insufficient klines")
        return
        
    live_px = fetch_live_price()
    if live_px is None:
        return
        
    df = calculate_indicators(df)
    
    last_closed = df.iloc[-2]
    prev_closed = df.iloc[-3]
    
    atr_val = float(last_closed["atr"]) if pd.notna(last_closed["atr"]) else 0.0
    
    # Evaluate Entry Signal
    sig = pro_signal(
        last_closed["ema_fast"], last_closed["ema_slow"],
        prev_closed["ema_fast"], prev_closed["ema_slow"]
    )
    
    pos = state.get("position")
    exit_this_tick = False
    
    if pos:
        side = pos["side"]
        
        # Update Trail
        if atr_val > 0:
            update_trailing_stop(pos, live_px, atr_val)
            
        sl_px = pos["trail_sl"]
        exit_px = None
        exit_reason = None
        
        # Check Stop Loss
        if (side == "LONG" and live_px <= sl_px) or (side == "SHORT" and live_px >= sl_px):
            exit_px = live_px
            exit_reason = "TRAIL_SL"
            
        # Optional: Trend reversal force exit (if opposite cross occurs while in a trade)
        if (side == "LONG" and sig == "SHORT") or (side == "SHORT" and sig == "LONG"):
            exit_px = live_px
            exit_reason = "TREND_REVERSAL"
            
        if exit_px is not None:
            close_position(state, pos, exit_px, exit_reason)
            state["position"] = None
            pos = None
            exit_this_tick = True
        else:
            fav_pct = ((live_px - pos["entry_px"]) / pos["entry_px"] * 100) * (1 if side == "LONG" else -1)
            log.info(f"  IN {side} | entry ${pos['entry_px']:.2f} | live ${live_px:.2f} | fav {fav_pct:+.2f}% | sl ${pos['trail_sl']:.2f}")

    if state["position"] is None and not exit_this_tick and sig and atr_val > 0:
        open_position(state, sig, live_px, atr_val)
        
    save_state(state)
    
    write_status({
        "env": os.environ.get("PRO_DATA_DIR", "pro_4h"),
        "pair": PAIR, "live_price": live_px,
        "balance": state["balance"],
        "position": state.get("position"),
        "signal": sig,
        "indicators": {
            "ema_fast": last_closed["ema_fast"],
            "ema_slow": last_closed["ema_slow"],
            "atr": atr_val
        }
    })

if __name__ == "__main__":
    main()
