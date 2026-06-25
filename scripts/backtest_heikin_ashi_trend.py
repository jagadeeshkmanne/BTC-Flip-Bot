#!/usr/bin/env python3
"""Research-only Heikin Ashi filters for the current 4h BTC trend bot.

The key rule: Heikin Ashi is used only for signals. Fills, fees, and PnL use
real OHLC close prices so the averaged HA candles cannot create fake fills.
"""
from __future__ import annotations

import argparse
import math
import time
from dataclasses import dataclass

import pandas as pd
import requests


PAIR = "BTCUSDT"
BYBIT_BASE = "https://api.bybit.com"
FEE_PCT = 0.00055
SLIP_PCT = 0.0005


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def fetch_bybit_4h(symbol: str, bars: int) -> pd.DataFrame:
    rows: list[list[str]] = []
    end_ms: int | None = None
    while len(rows) < bars:
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": "240",
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
        oldest = min(int(x[0]) for x in batch)
        end_ms = oldest - 1
        time.sleep(0.08)

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
    )
    return df.reset_index(drop=True)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ema13"] = ema(out["close"], 13)
    out["ema20"] = ema(out["close"], 20)
    out["ema200"] = ema(out["close"], 200)

    ha_close = (out["open"] + out["high"] + out["low"] + out["close"]) / 4.0
    ha_open = [float((out.loc[0, "open"] + out.loc[0, "close"]) / 2.0)]
    for i in range(1, len(out)):
        ha_open.append((ha_open[-1] + float(ha_close.iloc[i - 1])) / 2.0)
    out["ha_open"] = ha_open
    out["ha_close"] = ha_close
    out["ha_high"] = pd.concat([out["high"], out["ha_open"], out["ha_close"]], axis=1).max(axis=1)
    out["ha_low"] = pd.concat([out["low"], out["ha_open"], out["ha_close"]], axis=1).min(axis=1)
    out["ha_green"] = out["ha_close"] > out["ha_open"]
    out["ha_red"] = out["ha_close"] < out["ha_open"]
    out["ha_body"] = (out["ha_close"] - out["ha_open"]).abs()
    out["ha_range"] = (out["ha_high"] - out["ha_low"]).replace(0, math.nan)
    out["ha_body_frac"] = out["ha_body"] / out["ha_range"]
    out["ha_no_lower_wick"] = out["ha_low"] >= pd.concat([out["ha_open"], out["ha_close"]], axis=1).min(axis=1) * 0.999999
    out["ha_no_upper_wick"] = out["ha_high"] <= pd.concat([out["ha_open"], out["ha_close"]], axis=1).max(axis=1) * 1.000001
    out["base_long"] = (out["ema13"] > out["ema20"]) & (out["close"] > out["ema200"])
    return out


@dataclass
class Result:
    name: str
    trades: int
    wins: int
    net_pct: float
    pf: float
    max_dd_pct: float
    exposure_pct: float


def run_strategy(df: pd.DataFrame, name: str, entry: pd.Series, exit_: pd.Series) -> Result:
    cash = 1.0
    qty = 0.0
    entry_cost = 0.0
    wins = 0
    trades = 0
    gross_profit = 0.0
    gross_loss = 0.0
    equity_curve = []
    exposed = 0

    for i in range(201, len(df) - 1):
        price = float(df.loc[i + 1, "open"])
        if qty == 0.0 and bool(entry.iloc[i]):
            fill = price * (1.0 + SLIP_PCT)
            fee = cash * FEE_PCT
            qty = (cash - fee) / fill
            entry_cost = cash
            cash = 0.0
        elif qty > 0.0 and bool(exit_.iloc[i]):
            fill = price * (1.0 - SLIP_PCT)
            proceeds = qty * fill
            fee = proceeds * FEE_PCT
            cash = proceeds - fee
            pnl = cash - entry_cost
            trades += 1
            wins += int(pnl > 0)
            if pnl > 0:
                gross_profit += pnl
            else:
                gross_loss += -pnl
            qty = 0.0
            entry_cost = 0.0

        mark = float(df.loc[i + 1, "close"])
        equity_curve.append(cash + qty * mark)
        exposed += int(qty > 0.0)

    if qty > 0.0:
        fill = float(df.iloc[-1]["close"]) * (1.0 - SLIP_PCT)
        proceeds = qty * fill
        fee = proceeds * FEE_PCT
        cash = proceeds - fee
        pnl = cash - entry_cost
        trades += 1
        wins += int(pnl > 0)
        if pnl > 0:
            gross_profit += pnl
        else:
            gross_loss += -pnl
        equity_curve.append(cash)

    eq = pd.Series(equity_curve)
    dd = eq / eq.cummax() - 1.0
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    return Result(
        name=name,
        trades=trades,
        wins=wins,
        net_pct=(cash - 1.0) * 100.0,
        pf=pf,
        max_dd_pct=float(dd.min() * 100.0),
        exposure_pct=exposed / max(len(equity_curve), 1) * 100.0,
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--bars", type=int, default=6000)
    p.add_argument("--symbol", default=PAIR)
    args = p.parse_args()

    df = add_indicators(fetch_bybit_4h(args.symbol, args.bars))
    base = df["base_long"]
    ha_green = df["ha_green"]
    ha_strong_green = df["ha_green"] & df["ha_no_lower_wick"]
    ha_red = df["ha_red"]
    ha_weak = df["ha_red"] | ((df["ha_green"]) & (df["ha_body_frac"] < 0.25))

    variants = [
        ("baseline_ema13_20_200", base, ~base),
        ("ha_confirm_entry_exit_on_base_flip", base & ha_green, ~base),
        ("ha_strong_confirm_exit_on_base_flip", base & ha_strong_green, ~base),
        ("ha_confirm_exit_on_ha_red_or_base_flip", base & ha_green, (~base) | ha_red),
        ("ha_strong_exit_on_weak_or_base_flip", base & ha_strong_green, (~base) | ha_weak),
    ]
    results = [run_strategy(df, name, entry, exit_) for name, entry, exit_ in variants]

    print(f"symbol={args.symbol} bars={len(df)} from={df.timestamp.iloc[0]} to={df.timestamp.iloc[-1]}")
    print("name,trades,win_rate,net_pct,pf,max_dd_pct,exposure_pct")
    for r in results:
        win_rate = r.wins / r.trades * 100.0 if r.trades else 0.0
        print(f"{r.name},{r.trades},{win_rate:.1f},{r.net_pct:.2f},{r.pf:.3f},{r.max_dd_pct:.2f},{r.exposure_pct:.1f}")

    print()
    print("period,name,trades,win_rate,net_pct,pf,max_dd_pct,exposure_pct")
    periods = [
        ("2023_2024", "2023-01-01", "2025-01-01"),
        ("2025", "2025-01-01", "2026-01-01"),
        ("2026_ytd", "2026-01-01", None),
    ]
    for label, start, end in periods:
        mask = df["timestamp"] >= pd.Timestamp(start)
        if end is not None:
            mask &= df["timestamp"] < pd.Timestamp(end)
        sub = df.loc[mask].reset_index(drop=True)
        if len(sub) < 260:
            continue
        sub_results = [
            run_strategy(sub, name, entry.loc[mask].reset_index(drop=True), exit_.loc[mask].reset_index(drop=True))
            for name, entry, exit_ in variants
        ]
        for r in sub_results:
            win_rate = r.wins / r.trades * 100.0 if r.trades else 0.0
            print(f"{label},{r.name},{r.trades},{win_rate:.1f},{r.net_pct:.2f},{r.pf:.3f},{r.max_dd_pct:.2f},{r.exposure_pct:.1f}")


if __name__ == "__main__":
    main()
