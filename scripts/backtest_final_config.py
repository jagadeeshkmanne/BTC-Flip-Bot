#!/usr/bin/env python3
"""backtest_final_config.py — FINAL recommended V2 config: full year-by-year + compounded $ from $5000.

Recommended (best-returns): vol-target/fixed-1.8x leverage + lock-33%-profit-@+6R on V2.
Prints complete metrics, year-by-year return% AND end-of-year equity (compounding from $5,000),
and the honest caveats.
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
from backtest_voltarget_dd import run_v

CAP0=5000.0


def full_report(s, label):
    s=s/s.iloc[0]*CAP0   # scale to $5000 start
    r=s.pct_change().fillna(0)
    yrs=(s.index[-1]-s.index[0]).days/365.25
    cagr=(s.iloc[-1]/CAP0)**(1/yrs)-1
    dd=(s/s.cummax()-1); maxdd=dd.min()
    sharpe=r.mean()/r.std()*np.sqrt(6*365.25) if r.std()>0 else 0
    print("="*72)
    print(f"  {label}")
    print("="*72)
    print(f"  Start ${CAP0:,.0f}  ->  FINAL ${s.iloc[-1]:,.0f}   ({s.iloc[-1]/CAP0:,.0f}x)")
    print(f"  CAGR {cagr*100:.0f}%   maxDD {maxdd*100:.0f}%   ret/DD {cagr/abs(maxdd):.2f}   Sharpe {sharpe:.2f}")
    print(f"  {'year':<6}{'return':>9}{'end-of-year $':>18}{'maxDD':>8}")
    for y in range(2017,2027):
        seg=s[s.index.year==y]
        if len(seg)<20:
            print(f"  {y:<6}{'flat':>9}{seg.iloc[-1] if len(seg) else CAP0:>18,.0f}"); continue
        ret=(seg.iloc[-1]/seg.iloc[0]-1)*100
        d=(seg/seg.cummax()-1).min()*100
        print(f"  {y:<6}{ret:>+8.0f}%{seg.iloc[-1]:>18,.0f}{d:>7.0f}%")


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
    LK=dict(lock_frac=0.33,lock_r=6.0)
    # best-returns (your pick): fixed 1.8x
    full_report(run_v(df,L,bear,ss,pa,one*1.8,**LK),
                "BEST-RETURNS: 1.8x leverage (=60% at 3x) + lock 33% @ +6R")
    print()
    full_report(run_v(df,L,bear,ss,pa,vt,**LK),
                "BEST-RATIO: vol-target 0.7-1.5x + lock 33% @ +6R")


if __name__=="__main__":
    main()
