#!/usr/bin/env python3
"""Best current 1H BTCUSDT candidate: short-only Donchian breakdown.

Goal: evaluate the strategy as a trader would, with CAGR, drawdown, PF, and
risk-per-trade sizing. This is research-only and does not deploy anything.

Rules:
  - 1H candles
  - short only
  - trend filter: EMA50 < EMA200
  - entry signal: close breaks below prior 55-bar low
  - filters: ADX14 >= 25 and ATR/close >= 0.15%
  - enter next 1H open
  - stop = entry + 2 ATR
  - target = 3R
  - max hold = 96 hours
  - cooldown = 6 hours
  - fees/slippage included
  - same-bar stop/target collision counts as stop
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import pandas as pd


FEE_PCT = 0.00055
SLIP_PCT = 0.0005


@dataclass
class Trade:
    entry_i: int
    exit_i: int
    entry: float
    stop: float
    target: float
    exit: float
    reason: str
    r_after_cost: float


def load_ohlcv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="raise")
    return df


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = ((up > down) & (up > 0)) * up.clip(lower=0)
    minus_dm = ((down > up) & (down > 0)) * down.clip(lower=0)
    a = atr(df, n)
    plus_di = 100 * ema(plus_dm, n) / a
    minus_di = 100 * ema(minus_dm, n) / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
    return ema(dx, n)


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["atr"] = atr(out, 14)
    out["atr_pct"] = out["atr"] / out["close"]
    out["ema50"] = ema(out["close"], 50)
    out["ema200"] = ema(out["close"], 200)
    out["adx14"] = adx(out, 14)
    out["prior_55_low"] = out["low"].rolling(55).min().shift(1)
    return out


def short_pct_return(entry: float, exit_px: float) -> float:
    entry_fill = entry * (1 - SLIP_PCT)
    exit_fill = exit_px * (1 + SLIP_PCT)
    gross = (entry_fill - exit_fill) / entry_fill
    return gross - 2 * FEE_PCT


def collect_trades(df: pd.DataFrame) -> list[Trade]:
    trades: list[Trade] = []
    next_i = 240
    for i in range(240, len(df) - 1):
        if i < next_i:
            continue
        row = df.iloc[i]
        signal = (
            row["ema50"] < row["ema200"]
            and row["close"] < row["prior_55_low"]
            and row["adx14"] >= 25
            and row["atr_pct"] >= 0.0015
        )
        if not signal:
            continue

        entry_i = i + 1
        entry = float(df.loc[entry_i, "open"])
        stop = entry + 2.0 * float(row["atr"])
        target = entry - 3.0 * (stop - entry)
        exit_i = min(len(df) - 1, entry_i + 96)
        exit_px = float(df.loc[exit_i, "close"])
        reason = "time"
        for j in range(entry_i, exit_i + 1):
            hi = float(df.loc[j, "high"])
            lo = float(df.loc[j, "low"])
            if hi >= stop:
                exit_i = j
                exit_px = stop
                reason = "stop"
                break
            if lo <= target:
                exit_i = j
                exit_px = target
                reason = "target"
                break

        risk_pct = (stop - entry) / entry
        r_after_cost = short_pct_return(entry, exit_px) / risk_pct
        trades.append(Trade(entry_i, exit_i, entry, stop, target, exit_px, reason, r_after_cost))
        next_i = exit_i + 6
    return trades


def metrics(df: pd.DataFrame, trades: list[Trade], risk_per_trade: float) -> dict[str, float]:
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    wins = 0
    for t in trades:
        pnl = equity * risk_per_trade * t.r_after_cost
        if equity + pnl <= 0:
            return {
                "trades": float(len(trades)),
                "win_rate": 0.0,
                "pf": 0.0,
                "net_pct": -100.0,
                "cagr_pct": -100.0,
                "max_dd_pct": 100.0,
                "targets": float(sum(1 for x in trades if x.reason == "target")),
                "stops": float(sum(1 for x in trades if x.reason == "stop")),
                "times": float(sum(1 for x in trades if x.reason == "time")),
            }
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        if pnl > 0:
            gross_profit += pnl
            wins += 1
        else:
            gross_loss -= pnl

    days = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).total_seconds() / 86400
    cagr = (equity ** (365 / days) - 1) * 100 if equity > 0 and days > 0 else -100.0
    return {
        "trades": float(len(trades)),
        "win_rate": wins / len(trades) if trades else 0.0,
        "pf": gross_profit / gross_loss if gross_loss else (float("inf") if gross_profit else 0.0),
        "net_pct": (equity - 1) * 100,
        "cagr_pct": cagr,
        "max_dd_pct": max_dd * 100,
        "targets": float(sum(1 for x in trades if x.reason == "target")),
        "stops": float(sum(1 for x in trades if x.reason == "stop")),
        "times": float(sum(1 for x in trades if x.reason == "time")),
    }


def print_row(label: str, risk: float, m: dict[str, float]) -> None:
    mar = m["cagr_pct"] / m["max_dd_pct"] if m["max_dd_pct"] > 0 else 0.0
    print(
        f"{label:5s} risk={risk*100:5.1f}% trades={m['trades']:4.0f} "
        f"win={m['win_rate']*100:5.1f}% PF={m['pf']:.2f} "
        f"net={m['net_pct']:8.1f}% CAGR={m['cagr_pct']:8.1f}% "
        f"DD={m['max_dd_pct']:6.1f}% MAR={mar:5.2f} "
        f"T/S/time={m['targets']:.0f}/{m['stops']:.0f}/{m['times']:.0f}"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/cache/BTCUSDT_1h_12000_bybit.csv")
    ap.add_argument("--risk", type=float, default=0.05, help="single risk-per-trade run, e.g. 0.05")
    args = ap.parse_args()

    raw = load_ohlcv(args.csv)
    split = int(len(raw) * 0.6)
    datasets = {
        "FULL": add_features(raw),
        "IS": add_features(raw.iloc[:split].reset_index(drop=True)),
        "OOS": add_features(raw.iloc[split:].reset_index(drop=True)),
    }
    print(f"data: {raw.timestamp.iloc[0]} -> {raw.timestamp.iloc[-1]} bars={len(raw)} tf=1h")
    print("strategy: short Donchian 55 breakdown, EMA50<EMA200, ADX>=25, ATR%>=0.15, stop=2ATR, target=3R")
    print()
    for risk in (0.01, 0.02, 0.03, 0.05, 0.075, 0.10):
        for label, df in datasets.items():
            print_row(label, risk, metrics(df, collect_trades(df), risk))
        print()
    print("Selected risk run:")
    for label, df in datasets.items():
        print_row(label, args.risk, metrics(df, collect_trades(df), args.risk))


if __name__ == "__main__":
    main()
