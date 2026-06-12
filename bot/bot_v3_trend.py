#!/usr/bin/env python3
"""bot_v3_trend.py — v3: 4h trend portfolio paper bot (long/flat, 4 perp pairs).

THE RULE (validated 2026-06-12, backtest/trend_improve_round2.py + v3_param_plateau.py):
  per coin, on CLOSED 4h bars:
    LONG when  EMA30 > EMA150  AND  close > EMA50  AND  ADX14 > 20
    alts (ETH/SOL/BNB) additionally require BTC in the same LONG state (leader gate)
    FLAT otherwise.
  Catastrophe stop: live price <= entry * (1-8%)  -> close at LIVE price, any tick.
  Sizing: equal weight, 2x leverage -> per-coin notional = equity * 0.5 at entry.
  No shorts (9 independent tests all failed), no DCA, no grid.

Validation (4h, 2019-2026, honest engine, funding+fees):
  OOS (2023..) +262% / Sharpe 1.82 / maxDD -14% at 1x; @2x +1007% / -28%.
  Param plateau: 54-cell sweep ALL OOS-positive (edge is structural).
  Flat through COVID, May-2021, LUNA, FTX crashes (trend filter exited earlier).
  Worst-ever simultaneous 4h bar while long: -17% of equity at 1x.

HONEST-ACCOUNTING RULES (rsiscalp post-mortem, 2026-06-11):
  - every paper fill at the LIVE market price +/- slippage, never a target price
  - perp taker fee 0.055% + slippage 0.02% per side, on notional
  - funding charged on held positions at each 8h funding event (actual Bybit rate)
  - decisions on CLOSED 4h bars only; SL may fire on any tick (live price)
  - exits booked at live price — no stop-price booking fictions

Cron: every minute (SL responsiveness); entries/exits only on new closed 4h bars.
State/log/status: data/v3_trend/
"""
from __future__ import annotations
import os, sys, json, logging
from datetime import datetime, timezone

import pandas as pd
import requests

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(STRATEGY_DIR)
sys.path.insert(0, STRATEGY_DIR)

from bybit_data import fetch_klines, fetch_live_price

# ─── Strategy parameters (FIXED — validated as a set on a plateau; do not tune live) ───
PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
EMA_FAST, EMA_SLOW, EMA_EXIT = 30, 150, 50
ADX_LEN, ADX_MIN = 14, 20.0
CAT_SL_PCT = 0.08          # catastrophe stop from entry (flash-crash bound)
LEVERAGE = 2.0             # cross 2x: never liquidated in 7y of history
INITIAL_BALANCE = 5000.0
FEE_PCT = 0.00055          # perp taker per side
SLIP_PCT = 0.0002          # adverse slippage per fill

DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("V3_DATA_DIR", "v3_trend"))
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "state.json")
STATUS_FILE = os.path.join(DATA_DIR, "status.json")
LOG_FILE = os.path.join(DATA_DIR, "bot.log")

log = logging.getLogger("bot_v3_trend")
log.setLevel(logging.INFO)
log.handlers.clear()
for h in (logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)):
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(h)


def adx_series(df: pd.DataFrame, n: int) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus = pd.Series(((up > dn) & (up > 0)) * up, index=df.index).fillna(0.0)
    minus = pd.Series(((dn > up) & (dn > 0)) * dn, index=df.index).fillna(0.0)
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / n, adjust=False).mean()
    pdi = 100 * plus.ewm(alpha=1.0 / n, adjust=False).mean() / atr
    mdi = 100 * minus.ewm(alpha=1.0 / n, adjust=False).mean() / atr
    import numpy as np
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / n, adjust=False).mean()


def signal_from_closed(df: pd.DataFrame) -> tuple[bool, dict]:
    """v3 long condition on the last CLOSED 4h bar."""
    c = df["close"]
    ef = c.ewm(span=EMA_FAST, adjust=False, min_periods=EMA_FAST).mean()
    es = c.ewm(span=EMA_SLOW, adjust=False, min_periods=EMA_SLOW).mean()
    ee = c.ewm(span=EMA_EXIT, adjust=False, min_periods=EMA_EXIT).mean()
    ax = adx_series(df, ADX_LEN)
    i = -1
    ok = (pd.notna(es.iloc[i]) and pd.notna(ax.iloc[i])
          and ef.iloc[i] > es.iloc[i] and c.iloc[i] > ee.iloc[i]
          and float(ax.iloc[i]) > ADX_MIN)
    return bool(ok), {"close": float(c.iloc[i]), "ema_fast": float(ef.iloc[i]),
                      "ema_slow": float(es.iloc[i]), "ema_exit": float(ee.iloc[i]),
                      "adx": float(ax.iloc[i]) if pd.notna(ax.iloc[i]) else None}


def fetch_funding_rate(symbol: str) -> float | None:
    try:
        r = requests.get("https://api.bybit.com/v5/market/tickers",
                         params={"category": "linear", "symbol": symbol}, timeout=10)
        lst = r.json()["result"]["list"]
        return float(lst[0]["fundingRate"]) if lst else None
    except Exception as e:
        log.error(f"funding fetch failed {symbol}: {e}")
        return None


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"balance": INITIAL_BALANCE, "peak_equity": INITIAL_BALANCE,
                "positions": {}, "last_decision_bar": None, "last_funding_slot": None,
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


def close_position(state: dict, sym: str, live: float, reason: str) -> None:
    pos = state["positions"].pop(sym)
    fill = live * (1 - SLIP_PCT)                       # honest: live − slip
    gross = (fill - pos["entry"]) * pos["qty"]
    fee = fill * pos["qty"] * FEE_PCT
    net = gross - fee - pos["entry_fee"] - pos.get("funding_paid", 0.0)
    state["balance"] += net
    state["stats"]["total"] += 1
    state["stats"]["pnl"] += net
    state["stats"]["wins" if net > 0 else "losses"] += 1
    state.setdefault("trade_log", []).append({
        "pair": sym, "entry": pos["entry"], "exit": fill, "qty": pos["qty"],
        "net_usd": net, "net_pct": net / pos["margin"] * 100,
        "funding_paid": pos.get("funding_paid", 0.0),
        "entry_time": pos["entry_time"],
        "exit_time": datetime.now(timezone.utc).isoformat(), "reason": reason})
    state["trade_log"] = state["trade_log"][-400:]
    log.warning(f"  CLOSE {sym} @ ${fill:,.2f} net ${net:+,.2f} "
                f"({net / pos['margin'] * 100:+.2f}% of margin) — {reason}")


def main() -> None:
    state = load_state()
    now = datetime.now(timezone.utc)

    live = {}
    for s in PAIRS:
        px = fetch_live_price(s, log)
        if px is None:
            log.error(f"live price unavailable {s} — skipping tick")
            return
        live[s] = px

    # ── 1) catastrophe SL — any tick, live price (the only intra-bar action) ──
    for s in list(state["positions"].keys()):
        pos = state["positions"][s]
        if live[s] <= pos["entry"] * (1 - CAT_SL_PCT):
            close_position(state, s, live[s], f"CAT_SL -{CAT_SL_PCT*100:.0f}%")

    # ── 2) funding — charge held positions once per 8h slot (00/08/16 UTC) ──
    slot = f"{now.date()}T{(now.hour // 8) * 8:02d}"
    if state["positions"] and state.get("last_funding_slot") != slot:
        for s, pos in state["positions"].items():
            fr = fetch_funding_rate(s)
            if fr is not None:
                pay = live[s] * pos["qty"] * fr          # long pays positive funding
                pos["funding_paid"] = pos.get("funding_paid", 0.0) + pay
                log.info(f"  funding {s}: rate {fr*100:+.4f}% -> ${pay:+.2f}")
        state["last_funding_slot"] = slot

    # ── 3) entries/exits — only on a NEW closed 4h bar ──
    dfs = {}
    for s in PAIRS:
        df = fetch_klines("4h", 400, s, log)
        if df is None or len(df) < EMA_SLOW + 10:
            log.error(f"insufficient 4h klines {s}")
            return
        dfs[s] = df.iloc[:-1]                            # drop the forming bar
    bar_id = str(pd.Timestamp(dfs["BTCUSDT"].iloc[-1]["timestamp"]))

    sigs, ind = {}, {}
    for s in PAIRS:
        sigs[s], ind[s] = signal_from_closed(dfs[s])
    for s in PAIRS:                                      # BTC-leader gate on alts
        if s != "BTCUSDT":
            sigs[s] = sigs[s] and sigs["BTCUSDT"]

    if state.get("last_decision_bar") != bar_id:
        state["last_decision_bar"] = bar_id
        # exits first (frees margin), then entries
        for s in PAIRS:
            if s in state["positions"] and not sigs[s]:
                close_position(state, s, live[s], "SIGNAL_OFF")
        equity = state["balance"] + sum(
            (live[s] - p["entry"]) * p["qty"] - p.get("funding_paid", 0.0)
            for s, p in state["positions"].items())
        for s in PAIRS:
            if sigs[s] and s not in state["positions"]:
                margin = equity / len(PAIRS)             # 25% of equity per coin
                notional = margin * LEVERAGE             # 2x
                fill = live[s] * (1 + SLIP_PCT)          # honest: live + slip
                qty = notional / fill
                fee = notional * FEE_PCT
                state["balance"] -= fee
                state["positions"][s] = {
                    "qty": qty, "entry": fill, "entry_fee": fee, "margin": margin,
                    "funding_paid": 0.0,
                    "entry_time": datetime.now(timezone.utc).isoformat()}
                log.warning(f"  OPEN LONG {s} {qty:.4f} @ ${fill:,.2f} "
                            f"(notional ${notional:,.0f} = {LEVERAGE:.0f}x of "
                            f"${margin:,.0f} margin, fee ${fee:.2f}) | ADX "
                            f"{ind[s]['adx']:.1f}")

    # ── 4) mark-to-market equity (honest: exit-side fee+slip included) ──
    unreal = {}
    for s, p in state["positions"].items():
        fill = live[s] * (1 - SLIP_PCT)
        unreal[s] = ((fill - p["entry"]) * p["qty"] - fill * p["qty"] * FEE_PCT
                     - p.get("funding_paid", 0.0))
    equity = state["balance"] + sum(unreal.values())
    state["peak_equity"] = max(state.get("peak_equity", equity), equity)
    peak = state["peak_equity"]
    dd_pct = (equity / peak - 1) if peak > 0 else 0.0

    atomic_write(STATE_FILE, state)
    atomic_write(STATUS_FILE, {
        "env": os.environ.get("V3_DATA_DIR", "v3_trend"),
        "pair": "+".join(p.replace("USDT", "") for p in PAIRS),
        "price": ind["BTCUSDT"]["close"], "live_price": live["BTCUSDT"],
        "balance": equity, "peak_equity": peak, "drawdown_pct": dd_pct,
        "position": ({"side": "LONG",
                      "pairs": {s: {"qty": p["qty"], "entry": p["entry"],
                                    "unrealized_usd": unreal.get(s, 0.0)}
                                for s, p in state["positions"].items()},
                      "filled": len(state["positions"])}
                     if state["positions"] else None),
        "signal": {s: ("LONG" if sigs[s] else "FLAT") for s in PAIRS},
        "indicators": {s: ind[s] for s in PAIRS},
        "stats": state["stats"],
        "strategy": (f"v3 — 4h trend portfolio, long/flat {LEVERAGE:.0f}x perp. LONG when "
                     f"EMA{EMA_FAST}>EMA{EMA_SLOW} + px>EMA{EMA_EXIT} + ADX{ADX_LEN}>"
                     f"{ADX_MIN:.0f}; alts need BTC confirm; cat-SL {CAT_SL_PCT*100:.0f}%. "
                     f"OOS 2023-26: +262%@1x / Sh 1.82 / DD -14%. Funding+fees honest. [PAPER]"),
        "paper_mode": True,
        "state": "IN_POSITION" if state["positions"] else "FLAT",
        "updated_at": now.isoformat(),
    })
    held = ",".join(state["positions"].keys()) or "none"
    log.info(f"  equity ${equity:,.2f} (peak ${peak:,.2f}, DD {dd_pct*100:+.2f}%) | "
             f"held: {held} | trades {state['stats']['total']} | "
             f"realized ${state['stats']['pnl']:+,.2f}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception(f"FATAL: {e}")
        sys.exit(1)
