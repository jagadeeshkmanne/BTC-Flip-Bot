#!/usr/bin/env python3
"""backtest_grid.py — Honest backtest of an OpenTrader-style arithmetic GRID on BTC.

Replicates OpenTrader's grid model (highPrice, lowPrice, gridLevels, quantityPerGrid):
  - evenly spaced grid lines between lowPrice and highPrice
  - resting BUY at each line below price, SELL at each line above
  - a filled buy places a sell one step up; a filled sell places a buy one step down
  - each completed down-buy / up-sell cycle banks ~one grid step (minus fees)

WHY RANGE SELECTION IS THE STRATEGY:
  Grid = mean reversion. It harvests oscillation inside [low, high] but bleeds badly
  on a breakout (price below low => fully-bought losing bag; above high => all cash,
  missed trend). So we pick the range CAUSALLY (walk-forward from the *prior* window's
  realized range) and re-center each segment — never with future data.

HONESTY:
  - fills simulated on REAL intrabar path: up-bar = open->low->high->close,
    down-bar = open->high->low->close (captures wick oscillation, no lookahead)
  - taker fee + slippage on every fill; equity = cash + inventory * mark each bar
  - 1h vs 4h answered by driving the SAME grid logic from each timeframe's candles
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

import pandas as pd
import requests

PAIR = "BTCUSDT"
BYBIT_BASE = "https://api.bybit.com"
FEE_PCT = 0.00055
SLIP_PCT = 0.0005
BUDGET = 1.0          # normalized quote capital per segment


def fetch_bybit(symbol: str, interval: str, bars: int) -> pd.DataFrame:
    rows: list[list[str]] = []
    end_ms: int | None = None
    while len(rows) < bars:
        params = {"category": "linear", "symbol": symbol, "interval": interval,
                  "limit": min(1000, bars - len(rows))}
        if end_ms is not None:
            params["end"] = end_ms
        r = requests.get(f"{BYBIT_BASE}/v5/market/kline", params=params, timeout=20)
        r.raise_for_status()
        body = r.json()
        if body.get("retCode") != 0:
            raise RuntimeError(f"Bybit retCode={body.get('retCode')} {body.get('retMsg')}")
        batch = body.get("result", {}).get("list", [])
        if not batch:
            break
        rows.extend(batch)
        end_ms = min(int(x[0]) for x in batch) - 1
        time.sleep(0.05)
    rows = sorted({int(x[0]): x for x in rows}.values(), key=lambda x: int(x[0]))
    return pd.DataFrame({
        "timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
        "open": [float(x[1]) for x in rows], "high": [float(x[2]) for x in rows],
        "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows],
    }).reset_index(drop=True)


@dataclass
class GridResult:
    net_pct: float
    realized_pct: float       # banked grid profit only
    fills: int
    max_dd_pct: float
    end_inventory_frac: float # fraction of budget still held as base at the end (breakout exposure)
    bars_below: int
    bars_above: int


def run_grid_segment(seg: pd.DataFrame, low: float, high: float, levels: int) -> tuple[float, list, dict]:
    """Run one grid over a segment. Returns (final_equity, equity_curve, stats)."""
    step = (high - low) / levels
    lines = [low + i * step for i in range(levels + 1)]
    p0 = float(seg.iloc[0]["open"])
    # quantity per grid so capital ~half inventory / half cash at start
    q = BUDGET / (levels * p0)

    # seed: buy inventory for every line strictly above p0 (to rest sells there)
    above = [L for L in lines if L > p0]
    inv = q * len(above)
    fill0 = p0 * (1 + SLIP_PCT)
    cash = BUDGET - inv * fill0
    cash -= inv * fill0 * FEE_PCT
    orders = {L: ("sell" if L > p0 else "buy") for L in lines if abs(L - p0) > 1e-9}

    fills = 0
    realized = 0.0
    eq = []
    bars_below = bars_above = 0

    def cross_down(a, b):                       # price moves a -> b, b < a : fill buys
        nonlocal cash, inv, fills, realized
        for L in lines:
            if b <= L < a and orders.get(L) == "buy":
                fpx = L * (1 - SLIP_PCT)
                cost = q * fpx
                cash -= cost + cost * FEE_PCT
                inv += q
                orders[L] = None
                up = L + step                   # place sell one step up
                if up <= high + 1e-9:
                    orders[up] = "sell"
                fills += 1

    def cross_up(a, b):                          # price moves a -> b, b > a : fill sells
        nonlocal cash, inv, fills, realized
        for L in lines:
            if a < L <= b and orders.get(L) == "sell":
                fpx = L * (1 + SLIP_PCT)
                proceeds = q * fpx
                cash += proceeds - proceeds * FEE_PCT
                inv -= q
                orders[L] = None
                dn = L - step                   # place buy one step down
                if dn >= low - 1e-9:
                    orders[dn] = "buy"
                realized += q * step
                fills += 1

    for i in range(len(seg)):
        row = seg.iloc[i]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        if c >= o:                               # up bar: open -> low -> high -> close
            cross_down(o, l); cross_up(l, h)
        else:                                    # down bar: open -> high -> low -> close
            cross_up(o, h); cross_down(h, l)
        if h < low:
            bars_above += 0; bars_below += 1     # whole bar below grid (bag held)
        elif l > high:
            bars_above += 1
        eq.append(cash + inv * c)

    # liquidate remaining inventory at segment end (re-center cost)
    last = float(seg.iloc[-1]["close"]) * (1 - SLIP_PCT)
    cash += inv * last * (1 - FEE_PCT)
    end_inv_value = inv * float(seg.iloc[-1]["close"])
    inv = 0.0
    return cash, eq, {"fills": fills, "realized": realized, "end_inv": end_inv_value,
                      "bars_below": bars_below, "bars_above": bars_above}


def walk_forward(df: pd.DataFrame, seg_bars: int, range_mult: float, levels: int) -> GridResult:
    """Re-center the grid each segment using the PRIOR window's realized high/low."""
    equity = 1.0
    curve = []
    total_fills = 0
    total_realized = 0.0
    end_inv = 0.0
    below = above = 0
    i = seg_bars
    while i + seg_bars <= len(df):
        prior = df.iloc[i - seg_bars:i]
        seg = df.iloc[i:i + seg_bars].reset_index(drop=True)
        pl, ph = float(prior["low"].min()), float(prior["high"].max())
        center = float(seg.iloc[0]["open"])
        half = (ph - pl) / 2 * range_mult
        low, high = center - half, center + half
        if low <= 0:
            low = center * 0.5
        final, eq, st = run_grid_segment(seg, low, high, levels)
        # scale segment (ran on BUDGET=1) onto running equity
        seg_ret = final / BUDGET
        for e in eq:
            curve.append(equity * e)
        equity *= seg_ret
        total_fills += st["fills"]
        total_realized += st["realized"] * (equity / seg_ret)  # rough, in equity units
        end_inv = st["end_inv"]
        below += st["bars_below"]; above += st["bars_above"]
        i += seg_bars

    e = pd.Series(curve) if curve else pd.Series([1.0])
    dd = e / e.cummax() - 1.0
    return GridResult(
        net_pct=(equity - 1.0) * 100.0,
        realized_pct=total_realized * 100.0,
        fills=total_fills,
        max_dd_pct=float(dd.min() * 100.0),
        end_inventory_frac=end_inv,
        bars_below=below, bars_above=above,
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=PAIR)
    p.add_argument("--bars", type=int, default=6000)
    args = p.parse_args()

    # MATCHED window: 1h@6000 ~= 4h@1500 ~= 250 days, so 1h vs 4h is a fair comparison
    data = {}
    for tf, label, nbars in (("60", "1h", args.bars), ("240", "4h", args.bars // 4)):
        data[label] = fetch_bybit(args.symbol, tf, nbars)
        d = data[label]
        days = (d.timestamp.iloc[-1] - d.timestamp.iloc[0]).total_seconds() / 86400
        print(f"{label}: bars={len(d)} (~{days:.0f}d) {d.timestamp.iloc[0]} -> {d.timestamp.iloc[-1]}")

    # segment length ~ 30 days in each timeframe's bars
    seg = {"1h": 24 * 30, "4h": 6 * 30}
    print("\ntf,range_mult,levels,net_pct,realized_pct,fills,max_dd_pct,buy_n_hold_pct")
    for label in ("1h", "4h"):
        d = data[label]
        bnh = (float(d.iloc[-1]["close"]) / float(d.iloc[0]["close"]) - 1) * 100
        for rm in (0.75, 1.0, 1.5, 2.0):
            for lv in (10, 20, 40):
                r = walk_forward(d, seg[label], rm, lv)
                print(f"{label},{rm},{lv},{r.net_pct:.2f},{r.realized_pct:.2f},"
                      f"{r.fills},{r.max_dd_pct:.2f},{bnh:.1f}")


if __name__ == "__main__":
    main()
