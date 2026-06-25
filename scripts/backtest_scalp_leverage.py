#!/usr/bin/env python3
"""backtest_scalp_leverage.py — ema20/50 15m scalp at 1x/2x/3x, with REAL liquidation + drawdown.

The signal: EMA20>EMA50 -> long else short (reverse, always-in-market), BTC 15m. Reports CAGR,
maxDD, and liquidation count at leverage 1/2/3 and fee levels GROSS/MAKER/TAKER. Liquidation
modeled intrabar: a position is wiped when the adverse move from entry reaches ~(1/lev - maint).
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

MAINT = 0.005
COSTS = {"GROSS": (0.0, 0.0), "MAKER": (0.00015, 0.00005), "TAKER": (0.00055, 0.0005)}


def run(df, lev, fee, slip):
    c = df["close"].values; o = df["open"].values; h = df["high"].values; l = df["low"].values
    up = (bt.ema(df["close"], 20) > bt.ema(df["close"], 50)).values
    n = len(df); bal = 1.0; side = 0; entry = 0.0
    eq = np.ones(n); liqs = 0; start = 52
    for i in range(start, n - 1):
        oN, hN, lN, cN = o[i+1], h[i+1], l[i+1], c[i+1]
        want = 1 if up[i] else -1
        # intrabar liquidation on open position
        if side != 0 and bal > 0:
            frac = 1.0/lev - MAINT
            liq = entry*(1-frac) if side == 1 else entry*(1+frac)
            if (side == 1 and lN <= liq) or (side == -1 and hN >= liq):
                bal = 0.0; liqs += 1; side = 0
        # flip on signal change (fill next open), fee scales with leverage
        if side != want and bal > 0:
            if side != 0:
                fpx = oN*(1 - slip*side)
                ret = lev*side*(fpx/entry - 1); bal *= (1+ret)*(1 - fee*lev)
            if bal > 0:
                side = want; entry = oN*(1 + slip*side); bal *= (1 - fee*lev)
        eq[i+1] = max(bal*(1 + lev*side*(cN/entry - 1)), 0.0) if side != 0 and bal > 0 else bal
        if eq[i+1] <= 0:
            eq[i+1:] = 0.0; break
    s = pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[start:]
    yrs = max((s.index[-1]-s.index[0]).days/365.25, 1e-9)
    cagr = (s.iloc[-1]/s.iloc[0])**(1/yrs)-1 if s.iloc[-1] > 0 else -1.0
    dd = (s/s.cummax()-1).min()
    return cagr*100, dd*100, liqs, s.iloc[-1]


def main():
    df = bt.load("BTCUSDT", "15m")
    print("=" * 84)
    print("BTC 15m  EMA20/50 reverse scalp — leverage x liquidation x fees")
    print("=" * 84)
    for lvl, (fee, slip) in COSTS.items():
        print(f"\n  [{lvl} fees]  {'lev':<5}{'CAGR':>9}{'maxDD':>9}{'liquidations':>14}{'final $1->':>12}")
        for lev in (1.0, 2.0, 3.0):
            cagr, dd, liq, fin = run(df, lev, fee, slip)
            tag = "  WIPED" if fin <= 1e-6 else ""
            print(f"  {'':5}{lev:<5.0f}{cagr:>8.0f}%{dd:>8.0f}%{liq:>14}{fin:>11.3f}{tag}")


if __name__ == "__main__":
    main()
