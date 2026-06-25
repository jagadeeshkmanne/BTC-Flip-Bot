#!/usr/bin/env python3
"""backtest_voltarget_dd.py — reduce the DD on the vol-target (0.7-1.5x) V2 + pyramid sweep on it.

The dynamic-leverage config is 136% CAGR / -39% DD / ret/DD 3.45. Can we cut the -39% DD?
Levers tried: leverage ceiling, pyramid frac/gap (sweep), lock-partial-profit at +R. Reports
CAGR/DD/ret/DD + per-bear + green-every-year for each. Faithful copy of the V2 engine.
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


def run_v(df,bull,bear,ssize,parab,levarr,pyr_frac=1.0,pyr_r=2.0,lock_frac=0.0,lock_r=5.0,
          trail_k=None,trail_after=0.0,atr_mult=3.5,sl_cap=0.12,be_r=1.0,be_buf=0.01,s_atr=5.0,s_cap=0.15):
    o=df["open"].values;h=df["high"].values;l=df["low"].values;c=df["close"].values
    a=bt.atr(df,14).values; n=len(df)
    cash=1.0;units=0.0;side=0;entry=stop=R=0.0;pyrd=parabd=lockd=False;notional0=0.0;peakH=0.0
    armedL=armedS=True; eq=np.ones(n)
    for i in range(16,n-1):
        oN,hN,lN,cN=o[i+1],h[i+1],l[i+1],c[i+1]
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        lev=levarr[i]
        if side==1 and trail_k is not None:
            peakH=max(peakH,h[i])
            if (peakH-entry)/R >= trail_after:                # only trail after +trail_after R
                stop=max(stop,peakH-trail_k*a[i])
        if side!=0:
            hit=(lN<=stop) if side==1 else (hN>=stop)
            regime_out=(not bull[i]) if side==1 else (not bear[i])
            if hit or regime_out:
                fpx=(stop if hit else oN); fpx=fpx*(1-SLIP) if side==1 else fpx*(1+SLIP)
                cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0; pyrd=parabd=lockd=False
            else:
                prof=((cN-entry)/R) if side==1 else ((entry-cN)/R)
                if prof>=be_r:
                    be=entry*(1+be_buf) if side==1 else entry*(1-be_buf)
                    stop=max(stop,be) if side==1 else min(stop,be)
                if side==1 and not pyrd and prof>=pyr_r:
                    addn=pyr_frac*notional0; au=addn/cN; cash-=au*cN+addn*FEE; units+=au; pyrd=True; stop=max(stop,entry)
                if side==1 and lock_frac>0 and not lockd and prof>=lock_r:
                    fpx=oN*(1-SLIP); cl=lock_frac*units; cash+=cl*fpx*(1-FEE); units-=cl; lockd=True
                if side==1 and not parabd and parab[i]:
                    fpx=oN*(1-SLIP); cl=0.5*units; cash+=cl*fpx*(1-FEE); units-=cl; parabd=True
        if side==0:
            if bull[i] and armedL:
                E=cash; st=max(c[i]-atr_mult*a[i],c[i]*(1-sl_cap)); ent=o[i+1]*(1+SLIP)
                if ent-st>0:
                    notional0=E*lev; units=notional0/ent; cash-=units*ent+notional0*FEE; entry=ent; stop=st; R=ent-st; side=1; pyrd=parabd=lockd=False; armedL=False; peakH=ent
            elif bear[i] and armedS:
                E=cash; st=min(c[i]+s_atr*a[i],c[i]*(1+s_cap)); ent=o[i+1]*(1-SLIP)
                if st-ent>0:
                    notional=ssize[i]*E*lev; units=-notional/ent; cash-=units*ent+notional*FEE; entry=ent; stop=st; R=st-ent; side=-1; armedS=False
        eq[i+1]=cash+units*cN
    return pd.Series(eq,index=pd.to_datetime(df["timestamp"])).iloc[16:]


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
    def lv(lo,hi): return (med/rv).clip(lo,hi).shift(1).fillna(1.0).values
    one=np.ones(len(df))

    def row(name,levarr,**kw):
        s=run_v(df,L,bear,ss,pa,levarr,**kw); cg,dd,rr=m(s)
        o=m(s[s.index>=s.index[int(len(s)*0.6)]])[2]
        print(f"  {name:<38}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{o:>6.2f}  {yr(s,2018):>+4.0f}/{yr(s,2022):>+3.0f}/{yr(s,2026):>+3.0f}  {'grn' if grn(s) else 'RED'}")

    print("="*92)
    print("REDUCE DD ON VOL-TARGET V2 + PYRAMID SWEEP — full 2017-2026")
    print("="*92)
    print(f"  {'config':<38}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  2018/22/26")
    print("  -- baselines --")
    row("fixed 1x (V2)",one)
    row("vol-target 0.7-1.5x",lv(0.7,1.5))
    print("  -- lower the leverage CEILING --")
    row("vol-target 0.7-1.2x",lv(0.7,1.2))
    row("vol-target 0.8-1.3x",lv(0.8,1.3))
    row("vol-target 0.6-1.4x",lv(0.6,1.4))
    print("  -- vol-target 0.7-1.5x + PYRAMID sweep --")
    row("  pyr OFF (frac 0)",lv(0.7,1.5),pyr_frac=0.0)
    row("  pyr frac 0.5",lv(0.7,1.5),pyr_frac=0.5)
    row("  pyr frac 0.5 @ 3R",lv(0.7,1.5),pyr_frac=0.5,pyr_r=3.0)
    row("  pyr frac 1.0 @ 3R",lv(0.7,1.5),pyr_frac=1.0,pyr_r=3.0)
    print("  -- vol-target 0.7-1.5x + LOCK partial profit (DD reducer) --")
    row("  lock 33% @ +5R",lv(0.7,1.5),lock_frac=0.33,lock_r=5.0)
    row("  lock 50% @ +6R",lv(0.7,1.5),lock_frac=0.50,lock_r=6.0)
    row("  lock 50% @ +4R",lv(0.7,1.5),lock_frac=0.50,lock_r=4.0)
    print("  -- combine: lower ceiling + pyr 0.5 + lock --")
    row("  0.7-1.2x + pyr0.5 + lock33@5R",lv(0.7,1.2),pyr_frac=0.5,lock_frac=0.33,lock_r=5.0)


if __name__=="__main__":
    main()
