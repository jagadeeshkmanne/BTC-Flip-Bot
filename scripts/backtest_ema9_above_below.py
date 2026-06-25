#!/usr/bin/env python3
"""Research-only BTC EMA9 above/below long-short backtest.

Rules:
  - if close > EMA9, target LONG
  - if close < EMA9, target SHORT
  - switch/enter on next candle open
  - optional "cross_only" mode enters only when price crosses EMA9
  - fees and slippage are included on every position change
  - reports full and chronological 60/40 OOS split
"""
from __future__ import annotations

import argparse

import pandas as pd


FEE_PCT = 0.00055
SLIP_PCT = 0.0005


def load_ohlcv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="raise")
    return df


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (
        df.set_index("timestamp")
        .resample(rule, label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def metrics(eq: list[float], df: pd.DataFrame) -> dict[str, float]:
    e = pd.Series(eq)
    if len(e) < 2:
        return {"net_pct": 0.0, "cagr_pct": 0.0, "max_dd_pct": 0.0}
    dd = e / e.cummax() - 1.0
    days = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).total_seconds() / 86400
    net = (e.iloc[-1] - 1.0) * 100
    cagr = (e.iloc[-1] ** (365 / days) - 1) * 100 if days > 0 and e.iloc[-1] > 0 else -100.0
    return {"net_pct": net, "cagr_pct": cagr, "max_dd_pct": float(dd.min() * 100)}


def backtest(df: pd.DataFrame, *, mode: str, long_only: bool = False, short_only: bool = False) -> dict[str, float]:
    d = df.copy()
    d["ema9"] = ema(d["close"], 9)
    above = d["close"] > d["ema9"]
    below = d["close"] < d["ema9"]
    if mode == "state":
        target = pd.Series(0, index=d.index)
        target[above] = 1
        target[below] = -1
    elif mode == "cross_only":
        target = pd.Series(0, index=d.index)
        prev_above = above.shift(1, fill_value=False)
        prev_below = below.shift(1, fill_value=False)
        target[(above) & (~prev_above)] = 1
        target[(below) & (~prev_below)] = -1
    else:
        raise ValueError(mode)

    if mode == "state":
        if long_only:
            target = target.clip(lower=0)
        if short_only:
            target = target.clip(upper=0)

    cash = 1.0
    side = 0
    entry = 0.0
    qty = 0.0
    eq: list[float] = []
    trades = wins = 0
    gross_profit = gross_loss = 0.0

    def close_position(px: float) -> None:
        nonlocal cash, side, entry, qty, trades, wins, gross_profit, gross_loss
        if side == 0:
            return
        fill = px * (1 - SLIP_PCT) if side == 1 else px * (1 + SLIP_PCT)
        pnl = qty * (fill - entry) if side == 1 else qty * (entry - fill)
        fee = qty * fill * FEE_PCT
        net = pnl - fee
        cash += net
        trades += 1
        wins += int(net > 0)
        if net > 0:
            gross_profit += net
        else:
            gross_loss -= net
        side = 0
        entry = 0.0
        qty = 0.0

    def open_position(new_side: int, px: float) -> None:
        nonlocal cash, side, entry, qty
        if new_side == 0:
            return
        fill = px * (1 + SLIP_PCT) if new_side == 1 else px * (1 - SLIP_PCT)
        notional = cash
        fee = notional * FEE_PCT
        qty = (notional - fee) / fill
        entry = fill
        side = new_side

    for i in range(20, len(d) - 1):
        desired = int(target.iloc[i])
        px = float(d.iloc[i + 1]["open"])
        if mode == "state":
            if desired != side:
                close_position(px)
                open_position(desired, px)
        else:
            if long_only:
                if desired == 1 and side == 0:
                    open_position(1, px)
                elif desired == -1 and side == 1:
                    close_position(px)
            elif short_only:
                if desired == -1 and side == 0:
                    open_position(-1, px)
                elif desired == 1 and side == -1:
                    close_position(px)
            elif desired != 0 and desired != side:
                close_position(px)
                open_position(desired, px)
        mark = float(d.iloc[i + 1]["close"])
        if side == 0:
            eq.append(cash)
        else:
            unreal = qty * (mark - entry) if side == 1 else qty * (entry - mark)
            eq.append(cash + unreal)

    close_position(float(d.iloc[-1]["close"]))
    eq.append(cash)
    m = metrics(eq, d)
    m.update(
        {
            "trades": float(trades),
            "win_rate": wins / trades if trades else 0.0,
            "pf": gross_profit / gross_loss if gross_loss else (float("inf") if gross_profit else 0.0),
        }
    )
    return m


def print_stats(name: str, stats: dict[str, float]) -> None:
    print(
        f"{name:24s} trades={stats['trades']:5.0f} win={stats['win_rate']*100:5.1f}% "
        f"PF={stats['pf']:.3f} net={stats['net_pct']:8.2f}% CAGR={stats['cagr_pct']:8.2f}% "
        f"DD={stats['max_dd_pct']:7.2f}%"
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["state", "cross_only"], default="state")
    args = p.parse_args()

    datasets = {
        "15m": load_ohlcv("data/cache/BTCUSDT_15m_16000_bybit.csv"),
        "1h": load_ohlcv("data/cache/BTCUSDT_1h_12000_bybit.csv"),
    }
    datasets["30m"] = resample_ohlcv(datasets["15m"], "30min")
    datasets["2h"] = resample_ohlcv(datasets["1h"], "2h")
    datasets["4h"] = resample_ohlcv(datasets["1h"], "4h")
    datasets["6h"] = resample_ohlcv(datasets["1h"], "6h")
    datasets["12h"] = resample_ohlcv(datasets["1h"], "12h")
    datasets["1d"] = resample_ohlcv(datasets["1h"], "1D")

    for label, df in datasets.items():
        split = int(len(df) * 0.6)
        oos = df.iloc[split:].reset_index(drop=True)
        print(f"\n{label}: {df.timestamp.iloc[0]} -> {df.timestamp.iloc[-1]} bars={len(df)} mode={args.mode}")
        for suffix, long_only, short_only in (
            ("long_short", False, False),
            ("long_only", True, False),
            ("short_only", False, True),
        ):
            print_stats(f"FULL {suffix}", backtest(df, mode=args.mode, long_only=long_only, short_only=short_only))
            print_stats(f"OOS  {suffix}", backtest(oos, mode=args.mode, long_only=long_only, short_only=short_only))


if __name__ == "__main__":
    main()
