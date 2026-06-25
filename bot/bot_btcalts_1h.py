#!/usr/bin/env python3
"""bot_btcalts_1h.py — BTC-led ALT basket (1h) PAPER bot. The session's validated winner.

STRATEGY (walk-forward validated, ret/DD ~1.3-2.0, best of ~40 honest backtests):
  SIGNAL  : BTC slow trend on 1h — EMA32 > EMA800 -> BULL ; EMA32 < EMA800 -> BEAR.
  TRADE   : apply BTC's signal LONG/SHORT to an equal-weight basket of the 3 best alts
            (ETH, BNB, SOL) — they are high-beta to BTC, so BTC's clean signal + alt amplitude.
  SIZING  : vol-scaled — exposure = clip( median_realised_vol / current_vol , 0.2, 1.0 ).
            De-levers in high vol, never exceeds 1x. (This scaling lifted WF ret/DD 0.5 -> ~1.3.)
  Equal weight: each alt = 1/3 of capital, all driven by the SAME BTC signal/size.
  1x only (3x = wipeout, proven). No per-coin stop/TP — the BTC trend flip is the exit.

WHY top-3 (not 10/20): wider baskets monotonically WORSE (WF ret/DD 1.97 @3 -> 0.72 @20) —
the lower-cap alts add noise+drawdown without diversifying (all crash with BTC). Concentration wins.

HONEST PAPER ACCOUNTING (same rules as bot_allweather_4h.py):
  - fills at LIVE price (+/- slippage); taker fee 0.055%/side on traded notional
  - decisions only on CLOSED 1h bars; at most one rebalance per coin per 1h bar
  - rebalance only when target exposure moves > REBAL_THRESH of equity (avoids fee churn)

Cron: every 15 min (status freshness); trades only when a new 1h bar closes.
State/log/status: data/btcalts/
"""
from __future__ import annotations
import os, sys, json, logging, time
from datetime import datetime, timezone

import pandas as pd
import requests

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(STRATEGY_DIR)
sys.path.insert(0, STRATEGY_DIR)
from bybit_data import fetch_live_price

# ─── Parameters (FIXED — validated as a set; do not tune live) ───
SIGNAL_COIN = "BTCUSDT"
COINS = ["ETHUSDT", "BNBUSDT", "SOLUSDT"]
EMA_F, EMA_S = 32, 800
VOL_LEN, MED_LEN = 24, 720          # realised-vol window / rolling-median reference (30d)
VOL_MIN, VOL_MAX = 0.2, 1.0         # vol-scale clamp
REBAL_THRESH = 0.10                 # only rebalance when target exposure moves >10% of equity
INITIAL_BALANCE = 5000.0
FEE_PCT = 0.00055
SLIP_PCT = 0.0005
BYBIT = "https://api.bybit.com"

DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("BTCALTS_DATA_DIR", "btcalts"))
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "state.json")
STATUS_FILE = os.path.join(DATA_DIR, "status.json")
LOG_FILE = os.path.join(DATA_DIR, "bot.log")

log = logging.getLogger("bot_btcalts_1h")
log.setLevel(logging.INFO); log.handlers.clear()
for h in (logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)):
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")); log.addHandler(h)


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def fetch_btc_1h(bars=2600):
    """Paginate Bybit 1h klines backward to ~bars (enough to warm EMA800 + vol median)."""
    rows, end_ms = [], None
    while len(rows) < bars:
        p = {"category": "linear", "symbol": SIGNAL_COIN, "interval": "60", "limit": 1000}
        if end_ms is not None:
            p["end"] = end_ms
        try:
            b = requests.get(f"{BYBIT}/v5/market/kline", params=p, timeout=20).json()
        except Exception as e:
            log.error(f"btc kline fetch failed: {e}"); break
        batch = b.get("result", {}).get("list", [])
        if not batch:
            break
        rows.extend(batch); end_ms = min(int(x[0]) for x in batch) - 1; time.sleep(0.05)
    if not rows:
        return None
    rows = sorted({int(x[0]): x for x in rows}.values(), key=lambda x: int(x[0]))
    return pd.DataFrame({"timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
                         "close": [float(x[4]) for x in rows]}).reset_index(drop=True)


def blank_sub():
    return {"balance": INITIAL_BALANCE / len(COINS), "peak_equity": INITIAL_BALANCE / len(COINS),
            "position": None, "last_bar": None,
            "stats": {"total": 0, "wins": 0, "losses": 0, "pnl": 0.0}, "trade_log": []}


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"subs": {c: blank_sub() for c in COINS}}
    with open(STATE_FILE) as f:
        st = json.load(f)
    for c in COINS:
        st.setdefault("subs", {}).setdefault(c, blank_sub())
    return st


def atomic_write(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, default=str, indent=2); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def sub_equity(sub, px):
    pos = sub.get("position")
    if not pos:
        return sub["balance"]
    d = 1 if pos["side"] == "LONG" else -1
    return sub["balance"] + d * (px - pos["entry"]) * pos["qty"]


def rebalance(sub, symbol, px, target_side, target_notional, bar_id):
    """Move the sub-account's position toward (target_side, target_notional). Paper fills at px."""
    eq = sub_equity(sub, px)
    pos = sub.get("position")
    cur_side = pos["side"] if pos else None
    cur_qty = pos["qty"] if pos else 0.0
    cur_notional = cur_qty * px
    tgt_qty = target_notional / px if target_notional > 0 else 0.0

    def fee_on(notional):
        return FEE_PCT * notional

    # ----- flip (or open from flat in a direction) -----
    if pos and cur_side != target_side:
        # close current fully
        fill = px * (1 - SLIP_PCT * (1 if cur_side == "LONG" else -1))
        d = 1 if cur_side == "LONG" else -1
        pnl = d * (fill - pos["entry"]) * cur_qty - fee_on(cur_qty * px)
        sub["balance"] += pnl
        st = sub["stats"]; st["total"] += 1; st["pnl"] += pnl; st["wins" if pnl > 0 else "losses"] += 1
        sub.setdefault("trade_log", []).append({
            "side": cur_side, "entry": pos["entry"], "exit": fill, "qty": cur_qty,
            "net_usd": pnl, "exit_time": datetime.now(timezone.utc).isoformat(), "reason": "FLIP"})
        sub["trade_log"] = sub["trade_log"][-100:]
        sub["position"] = pos = None
        log.warning(f"  {symbol} CLOSE {cur_side} net ${pnl:+,.2f}")

    # ----- open fresh if flat and we want exposure -----
    if not sub.get("position") and tgt_qty > 0:
        fill = px * (1 + SLIP_PCT * (1 if target_side == "LONG" else -1))
        sub["balance"] -= fee_on(tgt_qty * px)
        sub["position"] = {"side": target_side, "qty": tgt_qty, "entry": fill,
                           "entry_time": datetime.now(timezone.utc).isoformat()}
        log.warning(f"  {symbol} OPEN {target_side} {tgt_qty:.4f} @ ${fill:,.2f}")
        return

    # ----- same side: resize only if the change is material -----
    pos = sub.get("position")
    if pos and pos["side"] == target_side:
        dq = tgt_qty - pos["qty"]
        if abs(dq) * px < REBAL_THRESH * eq:
            return                                  # immaterial — hold
        d = 1 if target_side == "LONG" else -1
        if dq > 0:                                  # add
            fill = px * (1 + SLIP_PCT * d)
            pos["entry"] = (pos["qty"] * pos["entry"] + dq * fill) / (pos["qty"] + dq)
            pos["qty"] += dq; sub["balance"] -= fee_on(dq * px)
            log.info(f"  {symbol} ADD {dq:.4f} -> {pos['qty']:.4f}")
        else:                                       # trim
            q = -dq; fill = px * (1 - SLIP_PCT * d)
            pnl = d * (fill - pos["entry"]) * q - fee_on(q * px)
            sub["balance"] += pnl; pos["qty"] -= q
            log.info(f"  {symbol} TRIM {q:.4f} net ${pnl:+,.2f} -> {pos['qty']:.4f}")
            if pos["qty"] <= 1e-9:
                sub["position"] = None


def main():
    state = load_state()
    btc = fetch_btc_1h()
    if btc is None or len(btc) < EMA_S + 5:
        log.error("insufficient BTC 1h klines"); return
    closed = btc.iloc[:-1].copy()                  # decisions on CLOSED 1h bar only
    closed["ema_f"] = ema(closed["close"], EMA_F)
    closed["ema_s"] = ema(closed["close"], EMA_S)
    ret = closed["close"].pct_change()
    rv = ret.rolling(VOL_LEN).std()
    med = rv.rolling(MED_LEN, min_periods=VOL_LEN * 3).median()
    last = closed.iloc[-1]
    bar_id = str(pd.Timestamp(last["timestamp"]))
    bull = bool(last["ema_f"] > last["ema_s"])
    target_side = "LONG" if bull else "SHORT"
    cur_rv = float(rv.iloc[-1]); ref = float(med.iloc[-1]) if pd.notna(med.iloc[-1]) else cur_rv
    vol_frac = max(VOL_MIN, min(VOL_MAX, ref / cur_rv if cur_rv > 0 else VOL_MAX))

    log.info(f"BTC bar {bar_id}: close ${last['close']:,.0f} EMA{EMA_F}/{EMA_S} "
             f"{last['ema_f']:,.0f}/{last['ema_s']:,.0f} -> {target_side} | volFrac {vol_frac:.2f}")

    coin_status = []
    for c in COINS:
        sub = state["subs"][c]
        px = fetch_live_price(c, log)
        if px is None:
            log.error(f"{c}: live price unavailable")
            coin_status.append({"symbol": c, "ok": False}); continue
        # one rebalance per coin per closed 1h bar
        if sub.get("last_bar") != bar_id:
            sub["last_bar"] = bar_id
            eq = sub_equity(sub, px)
            rebalance(sub, c, px, target_side, eq * vol_frac, bar_id)
        pos = sub.get("position")
        eq = sub_equity(sub, px)
        sub["peak_equity"] = max(sub.get("peak_equity", eq), eq)
        coin_status.append({
            "symbol": c, "ok": True, "live_price": px, "signal": target_side, "sub_equity": eq,
            "balance": sub["balance"],
            "position": ({"side": pos["side"], "qty": pos["qty"], "avg_entry": pos["entry"],
                          "entry_time": pos.get("entry_time"),
                          "unrealized_usd": (1 if pos["side"] == "LONG" else -1) * (px - pos["entry"]) * pos["qty"]}
                         if pos else None),
            "stats": sub["stats"]})

    total_eq = sum(cs["sub_equity"] if cs.get("ok") else state["subs"][cs["symbol"]]["balance"]
                   for cs in coin_status)
    peak = max(state.get("peak_equity", INITIAL_BALANCE), total_eq)
    state["peak_equity"] = peak
    dd = (total_eq / peak - 1) if peak > 0 else 0.0
    agg = {"total": 0, "wins": 0, "losses": 0, "pnl": 0.0}
    for c in COINS:
        for k in agg:
            agg[k] += state["subs"][c]["stats"][k]

    atomic_write(STATE_FILE, state)
    atomic_write(STATUS_FILE, {
        "env": os.environ.get("BTCALTS_DATA_DIR", "btcalts"),
        "basket": COINS, "signal_coin": SIGNAL_COIN,
        "balance": total_eq, "peak_equity": peak, "drawdown_pct": dd,
        "signal": target_side, "vol_frac": vol_frac,
        "indicators": {"btc_close": float(last["close"]), "ema_f": float(last["ema_f"]),
                       "ema_s": float(last["ema_s"]), "vol_frac": vol_frac, "closed_bar": bar_id},
        "coins": coin_status, "stats": agg,
        "strategy": (f"BTC-led ALT basket — 1h, long/short, 1x vol-scaled. BTC EMA{EMA_F}>{EMA_S} -> "
                     f"LONG else SHORT, applied to eqw ETH/BNB/SOL; exposure scaled by BTC vol. "
                     f"Walk-forward ret/DD ~1.3-2.0, CAGR ~58-76%, DD ~-52% [PAPER]"),
        "paper_mode": True,
        "n_positions": sum(1 for cs in coin_status if cs.get("ok") and cs.get("position")),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    log.info(f"BASKET equity ${total_eq:,.2f} (peak ${peak:,.2f}, DD {dd*100:+.2f}%) | "
             f"trades {agg['total']} | realized P&L ${agg['pnl']:+,.2f}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception(f"FATAL: {e}"); sys.exit(1)
