#!/usr/bin/env python3
"""bot_momo_daily.py — MOMO v1: daily trend-momentum paper bot (long/flat spot).

THE RULE (validated 2026-06-12, backtest/pattern_scan.py + fresh_search.py):
  LONG  when: daily close > SMA200  AND  RSI14 > 70 on any of the last 7
              closed daily bars
  FLAT  otherwise. Full balance per position. No leverage, no DCA, no grid,
        no martingale, no stops-below-water — nothing that can mis-book.

Validation (BTCUSDT 1d, May 2021–Jun 2026, honest engine, fees included):
  IS (2021-23) +28.1%   OOS (2024-26) +87.0%   ALL +139.6% (CAGR 18.7%)
  maxDD (mark-to-market) 21.1%   24 trades   in-market 13% of days
  Robustness: 35/36 RSI/hold variants positive on both halves.
  Yearly: 2021 0% | 2022 0% (cash thru −65% bear) | 2023 +28% | 2024 +82%
          | 2025 +3% | 2026 0% (cash thru bear)

HONEST-ACCOUNTING RULES (lessons from the rsiscalp post-mortem, 2026-06-11):
  - every paper fill happens at the LIVE market price, never at a target price
  - taker fee 0.10% + slippage 0.02% charged on every fill, both sides
  - there are no stop/limit fictions: the bot holds or it doesn't
  - decisions only on CLOSED daily bars; at most one decision per UTC day

Cron: every 15 min (status freshness); trades only when a new day closes.
State/log/status: data/momo_v1/
"""
from __future__ import annotations
import os, sys, json, logging
from datetime import datetime, timezone

import pandas as pd

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(STRATEGY_DIR)
sys.path.insert(0, STRATEGY_DIR)

from bybit_data import fetch_klines, fetch_live_price

# ─── Strategy parameters (FIXED — validated as a set; do not tune live) ───
PAIR = "BTCUSDT"
SMA_LEN = 200
RSI_LEN = 14
RSI_TRIGGER = 70.0
HOLD_DAYS = 7          # signal stays "fresh" for 7 closed daily bars
INITIAL_BALANCE = 5000.0
FEE_PCT = 0.0010       # spot taker per side — charged honestly in paper
SLIP_PCT = 0.0002      # adverse slippage per fill

DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("MOMO_DATA_DIR", "momo_v1"))
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "state.json")
STATUS_FILE = os.path.join(DATA_DIR, "status.json")
LOG_FILE = os.path.join(DATA_DIR, "bot.log")

log = logging.getLogger("bot_momo_daily")
log.setLevel(logging.INFO)
log.handlers.clear()
for h in (logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)):
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(h)


def wilder_rsi(close: pd.Series, n: int) -> pd.Series:
    d = close.diff()
    ag = d.clip(lower=0).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + ag / al)


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"balance": INITIAL_BALANCE, "peak_equity": INITIAL_BALANCE,
                "position": None, "last_decision_day": None,
                "stats": {"total": 0, "wins": 0, "losses": 0, "pnl": 0.0},
                "trade_log": []}
    with open(STATE_FILE) as f:
        return json.load(f)


def atomic_write(path: str, payload: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, default=str, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main() -> None:
    state = load_state()

    df = fetch_klines("1d", 400, PAIR, log)
    if df is None or len(df) < SMA_LEN + 2:
        log.error("insufficient daily klines")
        return
    live_px = fetch_live_price(PAIR, log)
    if live_px is None:
        log.error("live price unavailable")
        return

    # last row is the FORMING day — decisions use closed bars only
    closed = df.iloc[:-1].copy()
    closed["sma200"] = closed["close"].rolling(SMA_LEN).mean()
    closed["rsi14"] = wilder_rsi(closed["close"], RSI_LEN)
    last = closed.iloc[-1]
    last_day = str(pd.Timestamp(last["timestamp"]).date())

    sma_ok = bool(last["close"] > last["sma200"]) if pd.notna(last["sma200"]) else False
    recent_rsi = closed["rsi14"].iloc[-HOLD_DAYS:]
    rsi_ok = bool((recent_rsi > RSI_TRIGGER).any()) if recent_rsi.notna().any() else False
    target_long = sma_ok and rsi_ok

    pos = state.get("position")
    log.info(f"  closed {last_day}: close ${last['close']:,.0f} | SMA200 "
             f"${last['sma200']:,.0f} ({'above' if sma_ok else 'below'}) | "
             f"RSI14 now {last['rsi14']:.1f}, max(last {HOLD_DAYS}d) "
             f"{recent_rsi.max():.1f} | target: {'LONG' if target_long else 'FLAT'} | "
             f"pos: {'LONG' if pos else 'FLAT'} | live ${live_px:,.0f}")

    # one decision per closed daily bar
    if state.get("last_decision_day") != last_day:
        state["last_decision_day"] = last_day
        if target_long and pos is None:
            fill = live_px * (1 + SLIP_PCT)                 # honest: live price + slip
            bal = state["balance"]
            fee = bal * FEE_PCT
            qty = (bal - fee) / fill
            state["balance"] = 0.0
            state["position"] = {"qty": qty, "entry": fill, "entry_fee": fee,
                                 "cost_basis": bal,
                                 "entry_time": datetime.now(timezone.utc).isoformat()}
            log.warning(f"  BUY {qty:.6f} BTC @ ${fill:,.2f} (fee ${fee:.2f}) — "
                        f"close>{SMA_LEN}SMA and RSI14>{RSI_TRIGGER:.0f} within {HOLD_DAYS}d")
        elif not target_long and pos is not None:
            fill = live_px * (1 - SLIP_PCT)                 # honest: live price − slip
            gross = pos["qty"] * fill
            fee = gross * FEE_PCT
            proceeds = gross - fee
            net = proceeds - pos["cost_basis"]
            state["balance"] = proceeds
            state["position"] = None
            state["stats"]["total"] += 1
            state["stats"]["pnl"] += net
            if net > 0:
                state["stats"]["wins"] += 1
            else:
                state["stats"]["losses"] += 1
            state.setdefault("trade_log", []).append({
                "entry": pos["entry"], "exit": fill, "qty": pos["qty"],
                "net_usd": net, "net_pct": net / pos["cost_basis"] * 100,
                "entry_time": pos["entry_time"],
                "exit_time": datetime.now(timezone.utc).isoformat(),
                "reason": "SMA200" if not sma_ok else "MOMENTUM_FADED",
            })
            state["trade_log"] = state["trade_log"][-200:]
            log.warning(f"  SELL {pos['qty']:.6f} BTC @ ${fill:,.2f} (fee ${fee:.2f}) "
                        f"net ${net:+,.2f} ({net / pos['cost_basis'] * 100:+.2f}%) — "
                        f"{'below SMA200' if not sma_ok else 'momentum faded'}")

    # mark-to-market equity (honest: live price, exit-side fee+slip included)
    pos = state.get("position")
    if pos:
        equity = pos["qty"] * live_px * (1 - SLIP_PCT) * (1 - FEE_PCT)
        unreal = equity - pos["cost_basis"]
    else:
        equity = state["balance"]
        unreal = 0.0
    if equity > state.get("peak_equity", 0):
        state["peak_equity"] = equity
    peak = state.get("peak_equity", equity)
    dd_pct = (equity / peak - 1) if peak > 0 else 0.0

    atomic_write(STATE_FILE, state)
    atomic_write(STATUS_FILE, {
        "env": os.environ.get("MOMO_DATA_DIR", "momo_v1"),
        "pair": PAIR, "price": float(last["close"]), "live_price": live_px,
        "balance": equity, "peak_equity": peak, "drawdown_pct": dd_pct,
        "position": ({"side": "LONG", "qty_total": pos["qty"], "avg_entry": pos["entry"],
                      "first_entry": pos["entry"], "entry_time": pos["entry_time"],
                      "unrealized_usd": unreal, "filled": 1} if pos else None),
        "signal": "LONG" if target_long else "FLAT",
        "indicators": {"sma200": float(last["sma200"]), "rsi14": float(last["rsi14"]),
                       "rsi14_max_recent": float(recent_rsi.max()),
                       "rsi_trigger": RSI_TRIGGER, "hold_days": HOLD_DAYS,
                       "above_sma200": sma_ok, "momentum_fresh": rsi_ok,
                       "closed_day": last_day},
        "stats": state["stats"],
        "strategy": (f"MOMO v1 — daily trend-momentum, long/flat spot. LONG when close>"
                     f"SMA{SMA_LEN} AND RSI{RSI_LEN}>{RSI_TRIGGER:.0f} within {HOLD_DAYS}d. "
                     f"No leverage/DCA/grid. Fees {FEE_PCT*100:.2f}%+slip charged honestly. "
                     f"Backtest 5.1y: +139.6%, maxDD 21%, 24 trades [PAPER]"),
        "paper_mode": True,
        "state": "IN_POSITION" if pos else "FLAT",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    log.info(f"  equity ${equity:,.2f} (peak ${peak:,.2f}, DD {dd_pct*100:+.2f}%) | "
             f"trades {state['stats']['total']} | realized P&L ${state['stats']['pnl']:+,.2f}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception(f"FATAL: {e}")
        sys.exit(1)
