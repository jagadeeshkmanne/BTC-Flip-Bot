#!/usr/bin/env python3
"""Dynamic Neutral Grid — 4H BTC perp PAPER bot.

Research/paper mode only:
  - no real orders are placed
  - simulates a neutral futures-style grid from closed 4H candles
  - moves/re-centers the grid when BTC drifts from the center
  - pauses and flattens when ADX says the market is trending too hard

State/log/status: data/grid_btc/
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

import pandas as pd

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(STRATEGY_DIR)
sys.path.insert(0, STRATEGY_DIR)
from bybit_data import fetch_klines, fetch_live_price


PAIR = "BTCUSDT"
INITIAL_BALANCE = 5000.0
FEE_PCT = 0.00055
SLIP_PCT = 0.0005

ATR_LEN = 14
ADX_LEN = 14
LEVELS_EACH_SIDE = 7
ATR_WIDTH_MULT = 4.0
MIN_WIDTH_PCT = 0.035
RECENTER_ATR_MULT = 1.25
MAX_ADX = 26.0
MIN_ATR_PCT = 0.003
MAX_ATR_PCT = 0.035
NOTIONAL_PER_LEVEL_FRAC = 0.03
MAX_POSITION_FRAC = 0.55

DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("GRID_DATA_DIR", "grid_btc"))
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "state.json")
STATUS_FILE = os.path.join(DATA_DIR, "status.json")
LOG_FILE = os.path.join(DATA_DIR, "bot.log")

log = logging.getLogger("bot_grid_4h")
log.setLevel(logging.INFO)
log.handlers.clear()
for h in (logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)):
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(h)


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def adx(df: pd.DataFrame, n: int) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    pdm = ((up > dn) & (up > 0)) * up.clip(lower=0)
    ndm = ((dn > up) & (dn > 0)) * dn.clip(lower=0)
    a = atr(df, n)
    pdi = 100 * ema(pdm, n) / a
    ndi = 100 * ema(ndm, n) / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, 1e-9)
    return ema(dx, n)


def default_state() -> dict:
    return {
        "cash": INITIAL_BALANCE,
        "peak_equity": INITIAL_BALANCE,
        "position_qty": 0.0,
        "avg_entry": 0.0,
        "grid": None,
        "last_processed_bar": None,
        "stats": {"fills": 0, "recenters": 0, "flattens": 0, "realized_pnl": 0.0},
        "fill_log": [],
    }


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return default_state()
    with open(STATE_FILE) as f:
        state = json.load(f)
    base = default_state()
    base.update(state)
    return base


def atomic_write(path: str, payload: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, default=str, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def equity(state: dict, price: float) -> float:
    return float(state["cash"]) + float(state["position_qty"]) * (price - float(state["avg_entry"]))


def configure_grid(state: dict, price: float, atr_now: float) -> None:
    width = max(ATR_WIDTH_MULT * atr_now, price * MIN_WIDTH_PCT)
    step = width / LEVELS_EACH_SIDE
    state["grid"] = {
        "center": price,
        "low": max(price - width, price * 0.25),
        "high": price + width,
        "step": step,
        "levels_each_side": LEVELS_EACH_SIDE,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    state["stats"]["recenters"] += 1
    log.warning(f"GRID CENTER ${price:,.2f} width ${width:,.2f} step ${step:,.2f}")


def flatten(state: dict, price: float, reason: str) -> None:
    qty = float(state["position_qty"])
    if abs(qty) > 0:
        exit_px = price * (1 - SLIP_PCT) if qty > 0 else price * (1 + SLIP_PCT)
        pnl = qty * (exit_px - float(state["avg_entry"]))
        fee = abs(qty) * exit_px * FEE_PCT
        net = pnl - fee
        state["cash"] += net
        state["stats"]["realized_pnl"] += net
        log.warning(f"FLATTEN {reason}: qty {qty:+.5f} @ ${exit_px:,.2f}, net ${net:+.2f}")
    state["position_qty"] = 0.0
    state["avg_entry"] = 0.0
    state["grid"] = None
    state["stats"]["flattens"] += 1


def record_fill(state: dict, side: str, price: float, qty: float, net: float = 0.0) -> None:
    state["stats"]["fills"] += 1
    state.setdefault("fill_log", []).append(
        {
            "time": datetime.now(timezone.utc).isoformat(),
            "side": side,
            "price": price,
            "qty": qty,
            "net_realized": net,
        }
    )
    state["fill_log"] = state["fill_log"][-200:]


def fill_order(state: dict, side: int, level: float) -> None:
    eq = max(equity(state, level), 1e-9)
    qty = NOTIONAL_PER_LEVEL_FRAC * eq / level
    max_abs_qty = MAX_POSITION_FRAC * eq / level
    old_qty = float(state["position_qty"])
    if abs(old_qty + side * qty) > max_abs_qty:
        return

    fill = level * (1 + SLIP_PCT) if side == 1 else level * (1 - SLIP_PCT)
    fee = qty * fill * FEE_PCT
    new_qty = old_qty + side * qty
    net_realized = 0.0

    if old_qty == 0 or old_qty * side > 0:
        avg = float(state["avg_entry"])
        state["avg_entry"] = fill if old_qty == 0 else (abs(old_qty) * avg + qty * fill) / abs(new_qty)
        state["position_qty"] = new_qty
        state["cash"] -= fee
    else:
        closing_qty = min(abs(old_qty), qty)
        avg = float(state["avg_entry"])
        pnl = closing_qty * (fill - avg) if old_qty > 0 else closing_qty * (avg - fill)
        net_realized = pnl - fee
        state["cash"] += net_realized
        state["stats"]["realized_pnl"] += net_realized
        remaining_qty = qty - closing_qty
        if remaining_qty > 1e-12:
            state["position_qty"] = side * remaining_qty
            state["avg_entry"] = fill
        else:
            state["position_qty"] = old_qty + side * qty
            if abs(float(state["position_qty"])) < 1e-12:
                state["position_qty"] = 0.0
                state["avg_entry"] = 0.0
    record_fill(state, "BUY" if side == 1 else "SELL", fill, qty, net_realized)


def process_path(state: dict, start: float, end: float) -> None:
    grid = state.get("grid")
    if not grid:
        return
    center = float(grid["center"])
    step = float(grid["step"])
    levels = int(grid["levels_each_side"])
    if end < start:
        for i in range(1, levels + 1):
            level = center - i * step
            if end <= level < start:
                fill_order(state, 1, level)
    elif end > start:
        for i in range(1, levels + 1):
            level = center + i * step
            if start < level <= end:
                fill_order(state, -1, level)


def process_closed_bar(state: dict, bar: pd.Series) -> None:
    o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
    if c >= o:
        process_path(state, o, l)
        process_path(state, l, h)
        process_path(state, h, c)
    else:
        process_path(state, o, h)
        process_path(state, h, l)
        process_path(state, l, c)


def main() -> None:
    state = load_state()
    df = fetch_klines("4h", 500, PAIR, log)
    if df is None or len(df) < 220:
        log.error("insufficient 4h klines")
        return
    live = fetch_live_price(PAIR, log)
    if live is None:
        log.error("live price unavailable")
        return

    closed = df.iloc[:-1].copy()
    closed["atr"] = atr(closed, ATR_LEN)
    closed["adx"] = adx(closed, ADX_LEN)
    closed["atr_pct"] = closed["atr"] / closed["close"]
    last = closed.iloc[-1]
    bar_id = str(pd.Timestamp(last["timestamp"]))
    atr_now = float(last["atr"])
    adx_now = float(last["adx"])
    atr_pct = float(last["atr_pct"])
    regime_ok = adx_now <= MAX_ADX and MIN_ATR_PCT <= atr_pct <= MAX_ATR_PCT

    if state.get("last_processed_bar") != bar_id:
        if not regime_ok:
            if state.get("grid") or abs(float(state["position_qty"])) > 0:
                flatten(state, float(last["open"]), "TREND_OR_VOL_REGIME")
        else:
            grid = state.get("grid")
            drift = abs(float(last["open"]) - float(grid["center"])) if grid else float("inf")
            outside = bool(grid and (float(last["open"]) < float(grid["low"]) or float(last["open"]) > float(grid["high"])))
            if (not grid) or outside or drift >= RECENTER_ATR_MULT * atr_now:
                configure_grid(state, float(last["open"]), atr_now)
            process_closed_bar(state, last)
        state["last_processed_bar"] = bar_id

    eq = equity(state, live)
    state["peak_equity"] = max(float(state.get("peak_equity", eq)), eq)
    dd = eq / float(state["peak_equity"]) - 1.0

    grid = state.get("grid")
    next_levels = {}
    if grid:
        c = float(grid["center"])
        step = float(grid["step"])
        next_levels = {
            "nearest_buy": c - step,
            "nearest_sell": c + step,
            "low": float(grid["low"]),
            "high": float(grid["high"]),
            "center": c,
            "step": step,
        }

    status = {
        "env": os.environ.get("GRID_DATA_DIR", "grid_btc"),
        "pair": PAIR,
        "paper_mode": True,
        "live_price": live,
        "equity": eq,
        "cash": state["cash"],
        "peak_equity": state["peak_equity"],
        "drawdown_pct": dd * 100,
        "position_qty": state["position_qty"],
        "avg_entry": state["avg_entry"],
        "regime": "GRID_ON" if regime_ok else "PAUSED_TREND_OR_VOL",
        "closed_bar": bar_id,
        "indicators": {"adx": adx_now, "atr_pct": atr_pct * 100, "atr": atr_now},
        "grid": next_levels,
        "stats": state["stats"],
        "strategy": (
            "Dynamic neutral 4H grid. Recenter when price drifts by ATR, pause/flatten "
            "when ADX/volatility regime is unsafe. PAPER ONLY; backtest currently negative."
        ),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write(STATE_FILE, state)
    atomic_write(STATUS_FILE, status)
    log.info(
        f"bar {bar_id} live ${live:,.2f} regime {'ON' if regime_ok else 'PAUSED'} "
        f"ADX {adx_now:.1f} ATR% {atr_pct*100:.2f} eq ${eq:,.2f} DD {dd*100:+.2f}% "
        f"pos {float(state['position_qty']):+.5f}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log.exception(f"FATAL: {exc}")
        sys.exit(1)
