#!/usr/bin/env python3
"""backtest_grid_vs_myv3.py — why a grid (many trades) loses to my-V3 (few trades) on BTC.

A neutral grid harvests range chop: buy each -step% dip, sell each +step% rip, bounded inventory.
It makes hundreds of trades — but on a TRENDING asset it accumulates a losing bag in crashes and
sells winners too early in rallies. Compares to my-V3 (trend, few trades). Honest fees. BTC 4h.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3 import regime, run as myv3_run, m

COST = bt.FEE_PCT + bt.SLIP_PCT


def grid(df, step=0.02, n_levels=20):
    """Neutral grid: deploy up to full capital across n_levels; buy dips, sell rips."""
    c = df["close"].values; n = len(df)
    unit = 1.0 / n_levels
    cash = 1.0; inv = 0.0; ref = c[0]; trades = 0
    eq = np.ones(n)
    for i in range(1, n):
        p = c[i]
        # buy the dip
        while p <= ref*(1-step) and cash >= unit:
            inv += (unit*(1-COST))/p; cash -= unit; ref = ref*(1-step); trades += 1
        # sell the rip
        while p >= ref*(1+step) and inv*p >= unit:
            inv -= unit/p; cash += unit*(1-COST); ref = ref*(1+step); trades += 1
        eq[i] = cash + inv*p
    return pd.Series(eq, index=pd.to_datetime(df["timestamp"])), trades


def yr(s,y):
    seg=s[s.index.year==y]; return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg)>20 else 0.0


def main():
    df, bull = regime()
    print("="*78)
    print("GRID (many trades) vs my-V3 (few trades) — BTC 4h, honest fees")
    print("="*78)
    print(f"  {'strategy':<34}{'CAGR':>7}{'DD':>6}{'r/DD':>6}{'trades':>8}")
    rows=[]
    for step in (0.01, 0.02, 0.03):
        s,t = grid(df, step=step); cg,dd,rr = m(s)
        print(f"  {'grid '+str(int(step*100))+'% step':<34}{cg*100:>6.0f}%{dd*100:>5.0f}%{rr:>6.2f}{t:>8}")
        rows.append(("grid "+str(int(step*100))+"%", s))
    sv = myv3_run(df, bull, atr_mult=3.5, be_r=1.0, pyr_r=2.0, pyr_frac=1.0)
    cg,dd,rr = m(sv)
    print(f"  {'my-V3 (trend, tuned long)':<34}{cg*100:>6.0f}%{dd*100:>5.0f}%{rr:>6.2f}{'~22':>8}")
    rows.append(("my-V3", sv))
    print("\n  YEAR-BY-YEAR return% (note 2022 crash + 2020/21 trend):")
    print(f"    {'year':<6}{'grid 2%':>10}{'my-V3':>10}")
    g2 = grid(df, step=0.02)[0]
    for y in range(2020,2027):
        print(f"    {y:<6}{yr(g2,y):>+9.0f}%{yr(sv,y):>+9.0f}%")


if __name__=="__main__":
    main()
