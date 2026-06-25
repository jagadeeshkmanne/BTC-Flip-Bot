#!/usr/bin/env python3
"""Research-only weekly-range hedge grid for BTCUSDT.

Concept:
  - Use the prior rolling week to define a causal high/low range.
  - At each weekly reset, build arithmetic grid lines inside that range.
  - Below current price: long grid entries, each closes one grid step higher.
  - Above current price: short grid entries, each closes one grid step lower.
  - Long and short legs are independent, so both can exist over time.

This is not a deployment bot. It is an honest first-pass backtest with real OHLC
path fills, fees, slippage, mark-to-market open legs, and weekly reset losses.
"""
from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass

import pandas as pd
import requests


PAIR = "BTCUSDT"
BYBIT_BASE = "https://api.bybit.com"
FEE_PCT = 0.00055
SLIP_PCT = 0.0005
INTERVALS = {"15m": "15", "1h": "60", "4h": "240"}


def fetch_bybit(symbol: str, interval: str, bars: int, cache_dir: str = "data/cache") -> pd.DataFrame:
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"{symbol}_{interval}_{bars}_bybit.csv")
    if os.path.exists(cache_path):
        cached = pd.read_csv(cache_path, parse_dates=["timestamp"])
        if len(cached) >= bars:
            return cached.tail(bars).reset_index(drop=True)

    rows: list[list[str]] = []
    end_ms: int | None = None
    while len(rows) < bars:
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": INTERVALS[interval],
            "limit": min(1000, bars - len(rows)),
        }
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
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
            "open": [float(x[1]) for x in rows],
            "high": [float(x[2]) for x in rows],
            "low": [float(x[3]) for x in rows],
            "close": [float(x[4]) for x in rows],
            "volume": [float(x[5]) for x in rows],
        }
    ).reset_index(drop=True)
    df.to_csv(cache_path, index=False)
    return df


@dataclass
class Leg:
    side: int
    entry: float
    target: float
    qty: float


def path_points(row: pd.Series) -> list[float]:
    o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    return [o, l, h, c] if c >= o else [o, h, l, c]


def crossed(a: float, b: float, level: float) -> bool:
    return min(a, b) <= level <= max(a, b)


def metric_curve(curve: list[float]) -> tuple[float, float]:
    s = pd.Series(curve)
    dd = s / s.cummax() - 1.0
    return (s.iloc[-1] - 1.0) * 100.0, float(dd.min() * 100.0)


def backtest(
    df: pd.DataFrame,
    *,
    lookback_bars: int,
    reset_bars: int,
    levels: int,
    range_mult: float,
    order_pct: float,
    max_legs_side: int,
    close_on_reset: bool,
) -> dict:
    cash = 1.0
    legs: list[Leg] = []
    curve: list[float] = []
    timestamps: list[pd.Timestamp] = []
    realized = 0.0
    fills = 0
    closes = 0
    resets = 0
    grid_low = grid_high = step = 0.0
    long_entries: set[float] = set()
    short_entries: set[float] = set()
    next_reset = lookback_bars

    def equity(mark: float) -> float:
        open_pnl = 0.0
        for leg in legs:
            if leg.side == 1:
                open_pnl += leg.qty * (mark - leg.entry)
            else:
                open_pnl += leg.qty * (leg.entry - mark)
        return cash + open_pnl

    def reset_grid(i: int, px: float) -> None:
        nonlocal cash, legs, grid_low, grid_high, step, long_entries, short_entries, resets
        if close_on_reset and legs:
            for leg in legs:
                fill = px * (1 - SLIP_PCT if leg.side == 1 else 1 + SLIP_PCT)
                pnl = leg.qty * ((fill - leg.entry) if leg.side == 1 else (leg.entry - fill))
                fee = leg.qty * fill * FEE_PCT
                cash += pnl - fee
            legs = []
        prior = df.iloc[i - lookback_bars : i]
        low = float(prior["low"].min())
        high = float(prior["high"].max())
        half = (high - low) * 0.5 * range_mult
        center = px
        grid_low = max(center - half, center * 0.5)
        grid_high = center + half
        step = (grid_high - grid_low) / levels
        lines = [grid_low + k * step for k in range(levels + 1)]
        long_entries = {x for x in lines if x < px}
        short_entries = {x for x in lines if x > px}
        resets += 1

    def can_open(side: int, level: float) -> bool:
        side_legs = [leg for leg in legs if leg.side == side]
        if len(side_legs) >= max_legs_side:
            return False
        return all(abs(leg.entry - level) > step * 0.1 or leg.side != side for leg in legs)

    for i in range(lookback_bars, len(df)):
        row = df.iloc[i]
        if i >= next_reset:
            reset_grid(i, float(row["open"]))
            next_reset = i + reset_bars

        pts = path_points(row)
        for a, b in zip(pts, pts[1:]):
            # Close profitable legs first when their TP is touched.
            remaining: list[Leg] = []
            for leg in legs:
                if crossed(a, b, leg.target):
                    fill = leg.target * (1 - SLIP_PCT if leg.side == 1 else 1 + SLIP_PCT)
                    pnl = leg.qty * ((fill - leg.entry) if leg.side == 1 else (leg.entry - fill))
                    fee = leg.qty * fill * FEE_PCT
                    cash += pnl - fee
                    realized += pnl - fee
                    closes += 1
                else:
                    remaining.append(leg)
            legs = remaining

            # Open long legs below current path, TP one grid step higher.
            for level in sorted(long_entries, reverse=True):
                if crossed(a, b, level) and can_open(1, level):
                    fill = level * (1 + SLIP_PCT)
                    qty = (equity(fill) * order_pct) / fill
                    fee = qty * fill * FEE_PCT
                    cash -= fee
                    legs.append(Leg(1, fill, level + step, qty))
                    fills += 1

            # Open short legs above current path, TP one grid step lower.
            for level in sorted(short_entries):
                if crossed(a, b, level) and can_open(-1, level):
                    fill = level * (1 - SLIP_PCT)
                    qty = (equity(fill) * order_pct) / fill
                    fee = qty * fill * FEE_PCT
                    cash -= fee
                    legs.append(Leg(-1, fill, level - step, qty))
                    fills += 1

        mark = float(row["close"])
        curve.append(equity(mark))
        timestamps.append(pd.Timestamp(row["timestamp"]))

    if legs:
        px = float(df.iloc[-1]["close"])
        for leg in legs:
            fill = px * (1 - SLIP_PCT if leg.side == 1 else 1 + SLIP_PCT)
            pnl = leg.qty * ((fill - leg.entry) if leg.side == 1 else (leg.entry - fill))
            fee = leg.qty * fill * FEE_PCT
            cash += pnl - fee
        curve[-1] = cash
        legs = []

    net, dd = metric_curve(curve)
    monthly = pd.Series(curve, index=pd.to_datetime(timestamps)).resample("ME").last().pct_change().dropna() * 100
    return {
        "net_pct": net,
        "max_dd_pct": dd,
        "fills": fills,
        "closes": closes,
        "realized_pct": realized * 100,
        "resets": resets,
        "monthly": monthly,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=PAIR)
    p.add_argument("--interval", choices=sorted(INTERVALS), default="1h")
    p.add_argument("--bars", type=int, default=12000)
    p.add_argument("--levels", type=int, default=24)
    p.add_argument("--range-mult", type=float, default=1.0)
    p.add_argument("--order-pct", type=float, default=0.015)
    p.add_argument("--max-legs-side", type=int, default=8)
    p.add_argument("--close-on-reset", action="store_true")
    p.add_argument("--sweep", action="store_true")
    args = p.parse_args()

    df = fetch_bybit(args.symbol, args.interval, args.bars)
    bars_per_day = {"15m": 96, "1h": 24, "4h": 6}[args.interval]
    lookback = bars_per_day * 7
    reset = bars_per_day * 7
    print(f"{args.symbol} {args.interval} bars={len(df)} {df.timestamp.iloc[0]} -> {df.timestamp.iloc[-1]}")

    if args.sweep:
        print("levels,range_mult,order_pct,max_legs_side,close_on_reset,net_pct,max_dd_pct,fills,closes,realized_pct")
        for levels in (16, 24, 32):
            for rm in (0.75, 1.0, 1.25):
                for op in (0.01, 0.015, 0.02):
                    for max_legs in (5, 8, 12):
                        for cor in (False, True):
                            r = backtest(
                                df,
                                lookback_bars=lookback,
                                reset_bars=reset,
                                levels=levels,
                                range_mult=rm,
                                order_pct=op,
                                max_legs_side=max_legs,
                                close_on_reset=cor,
                            )
                            print(
                                f"{levels},{rm},{op},{max_legs},{int(cor)},"
                                f"{r['net_pct']:.2f},{r['max_dd_pct']:.2f},{r['fills']},{r['closes']},{r['realized_pct']:.2f}"
                            )
        return

    r = backtest(
        df,
        lookback_bars=lookback,
        reset_bars=reset,
        levels=args.levels,
        range_mult=args.range_mult,
        order_pct=args.order_pct,
        max_legs_side=args.max_legs_side,
        close_on_reset=args.close_on_reset,
    )
    print("net_pct,max_dd_pct,fills,closes,realized_pct,resets")
    print(f"{r['net_pct']:.2f},{r['max_dd_pct']:.2f},{r['fills']},{r['closes']},{r['realized_pct']:.2f},{r['resets']}")
    print()
    print("month,profit_pct")
    for idx, val in r["monthly"].items():
        print(f"{idx.strftime('%Y-%m')},{float(val):.2f}")


if __name__ == "__main__":
    main()
