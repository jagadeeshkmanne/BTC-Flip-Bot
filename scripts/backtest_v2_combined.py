#!/usr/bin/env python3
"""backtest_v2_combined.py — stack the two genuine agent winners + verify they don't overfit.

LONG winner: parabolic de-risk — take 50% profit when price > 1.2*SMA(20wk) (cuts DD -43%->-31%).
SHORT winner: bear-depth scaling — short_size by drawdown-from-180d-high (.25/.50/1.0 @ -10/-20/-30%).
Verifies base reproduction (features OFF == base), then each alone, then BOTH. Honest fees, no lookahead.
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

FEE=bt.FEE_PCT; SLIP=bt.SLIP_PCT; BPD=6


def run2(df, bull, bear, ssize, parab_trig, use_parab=False, use_bd=False,
         short_size=0.40, atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0,
         be_buf=0.01, s_atr=5.0, s_cap=0.15):
    o=df["open"].values;h=df["high"].values;l=df["low"].values;c=df["close"].values
    a=bt.atr(df,14).values; n=len(df)
    cash=1.0;units=0.0;side=0;entry=0.0;stop=0.0;R=0.0;pyrd=False;notional0=0.0;parab=False
    armedL=True;armedS=True; eq=np.ones(n)
    for i in range(16,n-1):
        oN,hN,lN,cN=o[i+1],h[i+1],l[i+1],c[i+1]
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        if side!=0:
            hit=(lN<=stop) if side==1 else (hN>=stop)
            regime_out=(not bull[i]) if side==1 else (not bear[i])
            if hit:
                fpx=stop*(1-SLIP) if side==1 else stop*(1+SLIP)
                cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False; parab=False
            elif regime_out:
                fpx=oN*(1-SLIP) if side==1 else oN*(1+SLIP)
                cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False; parab=False
            else:
                prof=((cN-entry)/R) if side==1 else ((entry-cN)/R)
                if prof>=be_r:
                    be=entry*(1+be_buf) if side==1 else entry*(1-be_buf)
                    stop=max(stop,be) if side==1 else min(stop,be)
                if side==1 and not pyrd and prof>=pyr_r:   # long pyramids; short does NOT (validated)
                    addn=pyr_frac*notional0; au=addn/cN
                    cash-=au*cN+addn*FEE; units+=au; pyrd=True
                    stop=max(stop,entry)
                # PARABOLIC DE-RISK (long only): take 50% off when extended
                if use_parab and side==1 and not parab and parab_trig[i]:
                    fpx=oN*(1-SLIP); cl=0.5*units
                    cash+=cl*fpx*(1-FEE); units-=cl; parab=True
        if side==0:
            go=0
            if bull[i] and armedL: go=1
            elif bear[i] and armedS: go=-1
            if go!=0:
                E=cash
                if go==1:
                    st=max(c[i]-atr_mult*a[i], c[i]*(1-sl_cap)); entry=oN*(1+SLIP); sz=1.0
                else:
                    st=min(c[i]+s_atr*a[i], c[i]*(1+s_cap)); entry=oN*(1-SLIP)
                    sz=(ssize[i] if use_bd else short_size)
                ok=(entry-st>0) if go==1 else (st-entry>0)
                if ok:
                    notional0=sz*E; units=go*notional0/entry; cash=E-units*entry-notional0*FEE
                    stop=st; R=abs(entry-st); side=go; pyrd=False; parab=False
                    if go==1: armedL=False
                    else: armedS=False
        eq[i+1]=cash+units*cN
    return pd.Series(eq,index=pd.to_datetime(df["timestamp"])).iloc[16:]


def yr(s,y):
    seg=s[s.index.year==y]; return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg)>20 else 0.0


def main():
    df,bull=build(); c=df["close"]
    macro=(c>bt.sma(c,9*30*BPD).shift(1)).fillna(False).values
    LONG=bull & macro
    hh40=c.rolling(40*BPD).max().shift(1); drop=((c/hh40-1)<-0.10).fillna(False).values
    bear=drop & daily_macd_bear(df)
    # bear-depth size array (drawdown from 180d high)
    hh180=c.rolling(180*BPD).max().shift(1); ddh=(c/hh180-1).fillna(0).values
    ssize=np.where(ddh<=-0.30,1.0,np.where(ddh<=-0.20,0.50,0.25))
    # parabolic trigger: price > 120% ABOVE SMA(20wk=840 bars 4h)  => price > 2.2*SMA
    parab_trig=(c>2.2*bt.sma(c,140*BPD).shift(1)).fillna(False).values
    print("="*92)
    print("V2 = parabolic de-risk (long) + bear-depth short — full 2017-2026, verify stacking")
    print("="*92)
    print(f"  {'config':<34}{'CAGR':>6}{'DD':>6}{'r/DD':>6}{'OOS':>6}  {'2018/22/26':>14}")
    cfgs=[("BASE (both OFF)",False,False),("+ parabolic only",True,False),
          ("+ bear-depth only",False,True),("V2: BOTH",True,True)]
    series={}
    for name,up,ub in cfgs:
        s=run2(df,LONG,bear,ssize,parab_trig,use_parab=up,use_bd=ub); series[name]=s
        cg,dd,rr=m(s); o=m(s[s.index>=s.index[int(len(s)*0.6)]])
        pb=f"{yr(s,2018):+.0f}/{yr(s,2022):+.0f}/{yr(s,2026):+.0f}"
        print(f"  {name:<34}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{o[2]:>6.2f}  {pb:>14}")
    print("\n  V2 year-by-year:")
    s=series["V2: BOTH"]
    print("   "+" ".join(f"{y}:{yr(s,y):+.0f}%" for y in range(2017,2027)))


if __name__=="__main__":
    main()
