#!/usr/bin/env python3
"""bot_allweather_4h.py — All-Weather 4-coin basket (4h perp, long/short reverse) PAPER bot.

THE FINALIZED STRATEGY (STRATEGY.md 🏆 FINAL — best risk-adjusted of everything tested):
  BASKET : BTC + ETH + BNB + SOL, equal weight (25% each, 4 concurrent positions).
  SIGNAL (per coin, on the last CLOSED 4h bar):
      EMA8 > EMA200  ->  target LONG
      EMA8 < EMA200  ->  target SHORT
  ENTRY/EXIT : always in market. Enter on the cross; on the opposite cross, close and
               REVERSE (no flat, no take-profit, no stop — all proven to reduce returns).
  LEVERAGE   : 1x (none). 3x/5x = liquidation (proven). No leverage, no stops, no TP.
  Backtest (IS, 2020-08→2026): CAGR ~99% (2021-inflated) / realistic ~42%/yr, maxDD −45%,
  ret/DD 2.21 — the best of ~30 honest backtests. NOT walk-forward validated → run PAPER.

PORTFOLIO MODEL (honest, low-fee, realistic):
  4 independent equal-start sub-accounts (25% of initial each), each running the single-coin
  reverse rule at 1x. Total equity = Σ sub-account equity. Winners are allowed to run (no
  forced daily rebalance — the backtest's mean-of-returns implies a daily rebalance, which
  would churn fees on the whole book every bar; sub-accounts are the faithful low-fee live
  form, and diverge from the backtest only by letting weights drift).

HONEST PAPER ACCOUNTING (same rules as bot_trend_4h.py):
  - every fill at the LIVE price (±slippage), never a target price
  - taker fee 0.055%/side on notional; funding charged on held notional each 4h bar
  - decisions ONLY on CLOSED 4h bars; at most one decision per coin per 4h bar
  - 1x: qty = sub_balance/entry; sub_equity = sub_balance + qty*(price-entry)*dir

Cron: every 15 min (status freshness); trades only when a new 4h bar closes.
State/log/status: data/allweather/
"""
from __future__ import annotations
import os, sys, json, logging
from datetime import datetime, timezone

import pandas as pd

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(STRATEGY_DIR)
sys.path.insert(0, STRATEGY_DIR)
from bybit_data import fetch_klines, fetch_live_price

# ─── Parameters (FIXED — validated as a set; do not tune live) ───
COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
EMA_F, EMA_G = 8, 200
LEVERAGE = 1.0
INITIAL_BALANCE = 5000.0          # total across the basket
FEE_PCT = 0.00055                 # perp taker per side
SLIP_PCT = 0.0005

DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("ALLWEATHER_DATA_DIR", "allweather"))
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "state.json")
STATUS_FILE = os.path.join(DATA_DIR, "status.json")
LOG_FILE = os.path.join(DATA_DIR, "bot.log")

log = logging.getLogger("bot_allweather_4h")
log.setLevel(logging.INFO); log.handlers.clear()
for h in (logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)):
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")); log.addHandler(h)


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def fetch_funding(symbol: str) -> float:
    """Current 8h funding rate (fraction). Returns 0.0 on failure (neutral)."""
    try:
        import requests
        r = requests.get("https://api.bybit.com/v5/market/tickers",
                         params={"category": "linear", "symbol": symbol}, timeout=10)
        return float(r.json()["result"]["list"][0].get("fundingRate") or 0.0)
    except Exception as e:
        log.error(f"funding fetch failed {symbol}: {e}"); return 0.0


def blank_sub() -> dict:
    return {"balance": INITIAL_BALANCE / len(COINS), "peak_equity": INITIAL_BALANCE / len(COINS),
            "position": None, "last_decision_bar": None, "last_funding_bar": None,
            "stats": {"total": 0, "wins": 0, "losses": 0, "pnl": 0.0}, "trade_log": []}


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"subs": {c: blank_sub() for c in COINS}}
    with open(STATE_FILE) as f:
        state = json.load(f)
    for c in COINS:                       # tolerate adding coins later
        state.setdefault("subs", {}).setdefault(c, blank_sub())
    return state


def atomic_write(path: str, payload: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, default=str, indent=2); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def sub_equity(sub: dict, price: float) -> float:
    pos = sub.get("position")
    if not pos:
        return sub["balance"]
    direction = 1 if pos["side"] == "LONG" else -1
    return sub["balance"] + pos["qty"] * (price - pos["entry"]) * direction * LEVERAGE


def process_coin(symbol: str, sub: dict) -> dict:
    """Run the reverse rule for one coin on its sub-account. Returns a per-coin status dict."""
    df = fetch_klines("4h", 1000, symbol, log)
    if df is None or len(df) < EMA_G + 5:
        log.error(f"{symbol}: insufficient 4h klines"); return {"symbol": symbol, "ok": False}
    live_px = fetch_live_price(symbol, log)
    if live_px is None:
        log.error(f"{symbol}: live price unavailable"); return {"symbol": symbol, "ok": False}

    closed = df.iloc[:-1].copy()                      # decisions on CLOSED bars only
    closed["ema_f"] = ema(closed["close"], EMA_F)
    closed["ema_g"] = ema(closed["close"], EMA_G)
    last = closed.iloc[-1]
    bar_id = str(pd.Timestamp(last["timestamp"]))
    target = "LONG" if bool(last["ema_f"] > last["ema_g"]) else "SHORT"

    fr8 = fetch_funding(symbol)
    pos = sub.get("position")

    # funding accrual on held position once per closed 4h bar
    if pos and sub.get("last_funding_bar") != bar_id:
        sub["last_funding_bar"] = bar_id
        sign = 1 if pos["side"] == "LONG" else -1     # longs pay when funding>0, shorts receive
        notional = pos["qty"] * live_px * LEVERAGE
        sub["balance"] -= fr8 * 0.5 * notional * sign  # ~half an 8h period per 4h bar

    # one decision per closed 4h bar
    if sub.get("last_decision_bar") != bar_id:
        sub["last_decision_bar"] = bar_id

        # close current position if it disagrees with target (reverse)
        if pos and pos["side"] != target:
            direction = 1 if pos["side"] == "LONG" else -1
            fill = live_px * (1 - SLIP_PCT * direction)
            pnl = pos["qty"] * (fill - pos["entry"]) * direction * LEVERAGE
            fee = FEE_PCT * pos["qty"] * fill * LEVERAGE
            net = pnl - fee
            sub["balance"] += net
            st = sub["stats"]; st["total"] += 1; st["pnl"] += net
            st["wins" if net > 0 else "losses"] += 1
            sub.setdefault("trade_log", []).append({
                "side": pos["side"], "entry": pos["entry"], "exit": fill, "qty": pos["qty"],
                "net_usd": net, "net_pct": net / pos["cost_basis"] * 100,
                "entry_time": pos["entry_time"], "exit_time": datetime.now(timezone.utc).isoformat(),
                "reason": "REVERSE"})
            sub["trade_log"] = sub["trade_log"][-100:]
            sub["position"] = pos = None
            log.warning(f"  {symbol} CLOSE {st['total']} net ${net:+,.2f} ({net/INITIAL_BALANCE*100:+.2f}% basket)")

        # open in the target direction if flat (first run, or just reversed)
        if pos is None:
            direction = 1 if target == "LONG" else -1
            fill = live_px * (1 + SLIP_PCT * direction)
            bal = sub["balance"]
            qty = bal * LEVERAGE / fill
            fee = FEE_PCT * qty * fill
            sub["balance"] = bal - fee
            sub["position"] = {"side": target, "qty": qty, "entry": fill, "cost_basis": bal,
                               "entry_time": datetime.now(timezone.utc).isoformat()}
            log.warning(f"  {symbol} {target} {qty:.4f} @ ${fill:,.2f} (fee ${fee:.2f})")

    pos = sub.get("position")
    eq = sub_equity(sub, live_px)
    sub["peak_equity"] = max(sub.get("peak_equity", eq), eq)
    log.info(f"  {symbol}: close ${last['close']:,.2f} EMA{EMA_F}/{EMA_G} "
             f"{last['ema_f']:,.2f}/{last['ema_g']:,.2f} | target {target} | "
             f"pos {pos['side'] if pos else 'FLAT'} | live ${live_px:,.2f} | sub-eq ${eq:,.2f}")
    return {
        "symbol": symbol, "ok": True, "price": float(last["close"]), "live_price": live_px,
        "signal": target, "sub_equity": eq, "balance": sub["balance"],
        "position": ({"side": pos["side"], "qty": pos["qty"], "avg_entry": pos["entry"],
                      "entry_time": pos["entry_time"],
                      "unrealized_usd": pos["qty"] * (live_px - pos["entry"]) *
                      (1 if pos["side"] == "LONG" else -1) * LEVERAGE} if pos else None),
        "indicators": {"ema_f": float(last["ema_f"]), "ema_g": float(last["ema_g"]),
                       "funding_8h_pct": fr8 * 100, "closed_bar": bar_id},
        "stats": sub["stats"],
    }


def main() -> None:
    state = load_state()
    coin_status = []
    for c in COINS:
        coin_status.append(process_coin(c, state["subs"][c]))

    total_eq = 0.0
    for cs in coin_status:
        if cs.get("ok"):
            total_eq += cs["sub_equity"]
        else:
            total_eq += state["subs"][cs["symbol"]]["balance"]   # stale fallback if a coin failed

    total_peak = state.get("peak_equity", INITIAL_BALANCE)
    total_peak = max(total_peak, total_eq)
    state["peak_equity"] = total_peak
    dd = (total_eq / total_peak - 1) if total_peak > 0 else 0.0

    agg = {"total": 0, "wins": 0, "losses": 0, "pnl": 0.0}
    for c in COINS:
        for k in agg:
            agg[k] += state["subs"][c]["stats"][k]

    atomic_write(STATE_FILE, state)
    atomic_write(STATUS_FILE, {
        "env": os.environ.get("ALLWEATHER_DATA_DIR", "allweather"),
        "basket": COINS, "balance": total_eq, "peak_equity": total_peak, "drawdown_pct": dd,
        "coins": coin_status, "stats": agg,
        "strategy": (f"All-Weather Basket — 4h perp, long/short reverse, 1x. Per coin: "
                     f"EMA{EMA_F}>EMA{EMA_G} -> LONG else SHORT; enter on cross, reverse on "
                     f"opposite cross; no stop/TP. BTC+ETH+BNB+SOL equal weight (25% each). "
                     f"Backtest IS ~42%/yr realistic / -45% DD, ret/DD 2.21 [PAPER]"),
        "paper_mode": True,
        "n_positions": sum(1 for cs in coin_status if cs.get("ok") and cs.get("position")),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    log.info(f"BASKET equity ${total_eq:,.2f} (peak ${total_peak:,.2f}, DD {dd*100:+.2f}%) | "
             f"trades {agg['total']} | realized P&L ${agg['pnl']:+,.2f}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception(f"FATAL: {e}"); sys.exit(1)
