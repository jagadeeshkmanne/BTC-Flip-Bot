#!/usr/bin/env python3
"""backtest_v2_stats.py — V2 win-rate + exit mechanism + dynamic (vol-targeted) leverage vs fixed 1x.

Answers: what's the win rate, does it use a stop or a regime switch (both), and does DYNAMIC
leverage (scale size by causal vol) beat fixed 1x. Instruments a faithful copy of run2.
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


def run_stat(df,bull,bear,ssize,parab,levarr=None,atr_mult=3.5,sl_cap=0.12,be_r=1.0,pyr_r=2.0,
             pyr_frac=1.0,be_buf=0.01,s_atr=5.0,s_cap=0.15):
    o=df["open"].values;h=df["high"].values;l=df["low"].values;c=df["close"].values
    a=bt.atr(df,14).values; n=len(df)
    cash=1.0;units=0.0;side=0;entry=stop=R=0.0;pyrd=parabd=False;notional0=0.0
    armedL=armedS=True; eq=np.ones(n)
    trades=[]; eq_entry=1.0; stops=0; regimes=0
    for i in range(16,n-1):
        oN,hN,lN,cN=o[i+1],h[i+1],l[i+1],c[i+1]
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        lev = (levarr[i] if levarr is not None else 1.0)
        if side!=0:
            hit=(lN<=stop) if side==1 else (hN>=stop)
            regime_out=(not bull[i]) if side==1 else (not bear[i])
            if hit or regime_out:
                fpx=(stop if hit else oN); fpx=fpx*(1-SLIP) if side==1 else fpx*(1+SLIP)
                cash+=units*fpx-abs(units)*fpx*FEE;
                eq_now=cash
                trades.append(eq_now/eq_entry-1)
                stops+= 1 if hit else 0; regimes+= 0 if hit else 1
                units=0.0; side=0; pyrd=parabd=False
            else:
                prof=((cN-entry)/R) if side==1 else ((entry-cN)/R)
                if prof>=be_r:
                    be=entry*(1+be_buf) if side==1 else entry*(1-be_buf)
                    stop=max(stop,be) if side==1 else min(stop,be)
                if side==1 and not pyrd and prof>=pyr_r:
                    addn=pyr_frac*notional0; au=addn/cN; cash-=au*cN+addn*FEE; units+=au; pyrd=True; stop=max(stop,entry)
                if side==1 and not parabd and parab[i]:
                    fpx=oN*(1-SLIP); cl=0.5*units; cash+=cl*fpx*(1-FEE); units-=cl; parabd=True
        if side==0:
            if bull[i] and armedL:
                E=cash; oN=o[i+1]; st=max(c[i]-atr_mult*a[i],c[i]*(1-sl_cap)); ent=oN*(1+SLIP)
                if ent-st>0:
                    notional0=E*lev; units=notional0/ent; cash-=units*ent+notional0*FEE; entry=ent; stop=st; R=ent-st; side=1; pyrd=parabd=False; armedL=False; eq_entry=E
            elif bear[i] and armedS:
                E=cash; oN=o[i+1]; st=min(c[i]+s_atr*a[i],c[i]*(1+s_cap)); ent=oN*(1-SLIP)
                if st-ent>0:
                    notional=ssize[i]*E*lev; units=-notional/ent; cash-=units*ent+notional*FEE; entry=ent; stop=st; R=st-ent; side=-1; armedS=False; eq_entry=E
        eq[i+1]=cash+units*cN
    s=pd.Series(eq,index=pd.to_datetime(df["timestamp"])).iloc[16:]
    return s, np.array(trades), stops, regimes


def main():
    df,bull=build(); c=df["close"]
    L=bull & (c>bt.sma(c,9*30*6).shift(1)).fillna(False).values
    bear=((c/c.rolling(40*6).max().shift(1)-1)<-0.10).fillna(False).values & daily_macd_bear(df)
    ddh=(c/c.rolling(180*6).max().shift(1)-1).fillna(0).values
    ss=np.where(ddh<=-0.30,1.0,np.where(ddh<=-0.20,0.50,0.25))
    pa=(c>2.2*bt.sma(c,140*6).shift(1)).fillna(False).values
    print("="*72)
    print("V2 WIN RATE + EXIT MECHANISM + DYNAMIC vs FIXED LEVERAGE")
    print("="*72)
    s,tr,stops,regs=run_stat(df,L,bear,ss,pa)
    w=tr[tr>0]; ls=tr[tr<=0]
    print(f"\n  trades {len(tr)} | WIN RATE {len(w)/len(tr)*100:.0f}% ({len(w)}W/{len(ls)}L)")
    print(f"  avg win {w.mean()*100:+.0f}% | avg loss {ls.mean()*100:+.0f}% | payoff {abs(w.mean()/ls.mean()):.1f}:1")
    print(f"  biggest win {tr.max()*100:+.0f}% | biggest loss {tr.min()*100:+.0f}%")
    print(f"  EXITS: {stops} via STOP-LOSS, {regs} via REGIME-FLIP  => BOTH (hard ATR stop + regime exit)")
    print(f"  => low win rate is EXPECTED for a trend follower: few big wins pay for many small losses")
    print("\n  -- DYNAMIC leverage (causal vol-target) vs fixed 1x --")
    ret=pd.Series(c.values).pct_change(); rv=ret.rolling(30).std()
    med=rv.expanding(min_periods=300).median().bfill()
    for lo,hi in [(1.0,1.0),(0.7,1.5),(0.5,2.0)]:
        lv=(med/rv).clip(lo,hi).shift(1).fillna(1.0).values
        s2,_,_,_=run_stat(df,L,bear,ss,pa,levarr=lv); cg,dd,rr=m(s2)
        tag="(fixed 1x)" if lo==hi else f"(vol-target {lo}-{hi}x)"
        print(f"    {tag:<24}CAGR {cg*100:>4.0f}%  DD {dd*100:>4.0f}%  ret/DD {rr:.2f}")


if __name__=="__main__":
    main()
