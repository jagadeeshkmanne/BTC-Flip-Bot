#!/usr/bin/env python3
"""backtest_lock_leverage.py — leverage + lock + trailing sweep on the lock-33%@5R V2 config.

Tests the user's questions on the best config (vol-target + lock-33%@5R, ret/DD 3.80):
 - fixed leverage 1x..3x ("60% at 2x" = 1.2x effective, "60% at 3x" = 1.8x)
 - different lock fraction / R
 - trailing SL (chandelier, activated after +R)
Reports CAGR/DD/ret/DD/OOS + per-bear + green-every-year. Engine reproduces fixed-1x V2 exactly.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_ext import build
from backtest_short_aggressive import daily_macd_bear
from backtest_myv3_final import m
from backtest_voltarget_dd import run_v


def yr(s,y):
    sg=s[s.index.year==y]; return (sg.iloc[-1]/sg.iloc[0]-1)*100 if len(sg)>20 else 0
def grn(s): return all(yr(s,y)>=-0.5 for y in range(2018,2027))


def main():
    df,bull=build(); c=df["close"]
    L=bull & (c>bt.sma(c,9*30*6).shift(1)).fillna(False).values
    bear=((c/c.rolling(40*6).max().shift(1)-1)<-0.10).fillna(False).values & daily_macd_bear(df)
    ddh=(c/c.rolling(180*6).max().shift(1)-1).fillna(0).values
    ss=np.where(ddh<=-0.30,1.0,np.where(ddh<=-0.20,0.50,0.25))
    pa=(c>2.2*bt.sma(c,140*6).shift(1)).fillna(False).values
    ret=pd.Series(c.values).pct_change(); rv=ret.rolling(30).std()
    med=rv.expanding(min_periods=300).median().bfill()
    vt=(med/rv).clip(0.7,1.5).shift(1).fillna(1.0).values
    one=np.ones(len(df))
    def row(name,levarr,**kw):
        s=run_v(df,L,bear,ss,pa,levarr,**kw); cg,dd,rr=m(s)
        o=m(s[s.index>=s.index[int(len(s)*0.6)]])[2]
        print(f"  {name:<34}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{o:>6.2f}  {yr(s,2018):>+4.0f}/{yr(s,2022):>+3.0f}/{yr(s,2026):>+3.0f}  {'grn' if grn(s) else 'RED'}")

    print("="*92)
    print("LEVERAGE + LOCK + TRAILING sweep on lock-33%@5R V2 — full 2017-2026")
    print("="*92)
    print(f"  {'config':<34}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  2018/22/26")
    print("  -- FIXED leverage + lock33@5R ('60% at 2x'=1.2x, '60% at 3x'=1.8x) --")
    LK=dict(lock_frac=0.33,lock_r=5.0)
    row("1.0x",one,**LK)
    row("1.2x  (=60% at 2x)",one*1.2,**LK)
    row("1.5x",one*1.5,**LK)
    row("1.8x  (=60% at 3x)",one*1.8,**LK)
    row("2.0x",one*2.0,**LK)
    row("2.5x",one*2.5,**LK)
    row("3.0x",one*3.0,**LK)
    row("vol-target 0.7-1.5x",vt,**LK)
    print("  -- LOCK fraction / R sweep (on vol-target) --")
    for fr in (0.25,0.33,0.50):
        for lr in (4.0,5.0,6.0):
            row(f"lock {int(fr*100)}% @ +{lr:.0f}R",vt,lock_frac=fr,lock_r=lr)
    print("  -- TRAILING SL (chandelier, activated after +R) on vol-target+lock33@5R --")
    for tk in (3.0,4.0,5.0):
        row(f"trail {tk:.0f}xATR after +3R",vt,lock_frac=0.33,lock_r=5.0,trail_k=tk,trail_after=3.0)
    row("trail 4xATR after +5R",vt,lock_frac=0.33,lock_r=5.0,trail_k=4.0,trail_after=5.0)


if __name__=="__main__":
    main()
