#!/usr/bin/env python3
"""backtest_scalp_maker.py — can a SCALP bot work if we fix the COST structure (maker fees)?

Fresh angle: every prior test used TAKER fees (0.055%/side + 0.05% slip = 0.21% round-trip).
A real scalp bot uses LIMIT/maker orders: ~0.01-0.02%/side, near-zero slip (you set the price),
sometimes a rebate. That drops round-trip cost ~5-10x. So the honest question is whether any
scalp signal has a POSITIVE gross edge that survives maker fees (even if it dies on taker).

For each scalp signal on BTC 5m/15m, report net CAGR at three cost levels:
  GROSS  : 0 fee, 0 slip       (is there ANY raw edge in the signal?)
  MAKER  : 0.015%/side, 0.005% slip  (best-case limit-order scalping)
  TAKER  : 0.055%/side, 0.05% slip    (what we've been testing)
Plus avg gross %/trade vs the maker round-trip cost (~0.04%). If gross/trade < cost, hopeless.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

COSTS = {"GROSS": (0.0, 0.0), "MAKER": (0.00015, 0.00005), "TAKER": (0.00055, 0.0005)}


def run(df, pos, fee, slip):
    held = pd.Series(np.asarray(pos, float), index=df.index).shift(1).fillna(0)
    oo = (df["open"].shift(-1) / df["open"] - 1).fillna(0)
    turn = held.diff().abs().fillna(held.abs())
    r = held * oo - turn * (fee + slip)
    eq = (1 + r).cumprod(); eq.index = pd.to_datetime(df["timestamp"])
    n = int((turn > 0).sum())
    # avg gross % per trade (per round-trip): total gross return spread over trades
    gross = (held * oo).sum()
    per_trade = gross / max(n, 1) * 100
    return eq, n, per_trade


def signals(df):
    c = df["close"]
    out = {}
    out["ema9/21"] = (bt.ema(c, 9) > bt.ema(c, 21)).astype(float) * 2 - 1
    out["ema20/50"] = (bt.ema(c, 20) > bt.ema(c, 50)).astype(float) * 2 - 1
    out["ema50/200"] = (bt.ema(c, 50) > bt.ema(c, 200)).astype(float) * 2 - 1
    # RSI mean-reversion (scalp the bounce)
    r = bt.rsi(c, 14); rev = np.zeros(len(df)); s = 0
    rv = r.values
    for i in range(len(df)):
        if rv[i] < 30: s = 1
        elif rv[i] > 70: s = -1
        rev[i] = s
    out["rsi_revert"] = pd.Series(rev, index=df.index)
    # momentum sign (ROC)
    out["roc12"] = np.sign(c / c.shift(12) - 1)
    # micro-breakout (donchian 20)
    up = df["high"].rolling(20).max().shift(1); dn = df["low"].rolling(20).min().shift(1)
    bo = np.where(c > up, 1.0, np.where(c < dn, -1.0, np.nan))
    out["donch20"] = pd.Series(bo, index=df.index).ffill().fillna(0)
    # bollinger reversion
    lo, mid, hi = bt.bbands(c, 20, 2)
    bb = np.where(c < lo, 1.0, np.where(c > hi, -1.0, np.nan))
    out["bb_revert"] = pd.Series(bb, index=df.index).ffill().fillna(0)
    return out


def main():
    for tf in ("5m", "15m"):
        df = bt.load("BTCUSDT", tf)
        span = f"{df['timestamp'].iloc[0].date()}->{df['timestamp'].iloc[-1].date()}"
        print("\n" + "=" * 92)
        print(f"BTC {tf} SCALP — net CAGR at GROSS / MAKER / TAKER fees ({len(df)} bars, {span})")
        print("=" * 92)
        print(f"  {'signal':<12}{'trades':>8}{'%/trade':>9}{'GROSS CAGR':>12}{'MAKER CAGR':>12}{'TAKER CAGR':>12}")
        sigs = signals(df)
        rows = []
        for name, pos in sigs.items():
            cells = {}
            n = pt = 0
            for lvl, (fee, slip) in COSTS.items():
                eq, n, pt = run(df, pos, fee, slip)
                cells[lvl] = bt.metrics(eq)[0] * 100
            rows.append((name, n, pt, cells))
        rows.sort(key=lambda x: x[3]["MAKER"], reverse=True)
        for name, n, pt, cells in rows:
            print(f"  {name:<12}{n:>8}{pt:>8.3f}%{cells['GROSS']:>11.0f}%{cells['MAKER']:>11.0f}%{cells['TAKER']:>11.0f}%")
        print(f"  (maker round-trip cost ~0.04%; a signal needs %/trade > that AND positive GROSS to have a prayer)")


if __name__ == "__main__":
    main()
