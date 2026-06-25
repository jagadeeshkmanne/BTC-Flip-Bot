#!/usr/bin/env python3
"""backtest_v2_tp.py — trade count/year for V2 + fine-tune TP (long TP, short TP/time-stop) on BTC.

Extends the V2 engine with: long take-profit (close frac at +R), short time-stop (cover after T bars),
short take-profit (cover at +R). Counts trades/year. Tests whether any TP beats V2 (ret/DD 3.34).
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


def run3(df, bull, bear, ssize, parab_trig, use_parab=True, use_bd=True,
         long_tp_r=None, long_tp_frac=0.5, short_tp_r=None, short_ts=None,
         atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0, be_buf=0.01, s_atr=5.0, s_cap=0.15):
    o=df["open"].values;h=df["high"].values;l=df["low"].values;c=df["close"].values
    a=bt.atr(df,14).values; n=len(df)
    cash=1.0;units=0.0;side=0;entry=0.0;stop=0.0;R=0.0;pyrd=False;notional0=0.0;parab=False;ltp=False;sbar=0
    armedL=True;armedS=True; eq=np.ones(n); nL=0; nS=0
    for i in range(16,n-1):
        oN,hN,lN,cN=o[i+1],h[i+1],l[i+1],c[i+1]
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        if side!=0:
            hit=(lN<=stop) if side==1 else (hN>=stop)
            regime_out=(not bull[i]) if side==1 else (not bear[i])
            ts_hit = (side==-1 and short_ts is not None and (i-sbar)>=short_ts)
            if hit or ts_hit:
                fpx=(stop if hit else oN); fpx=fpx*(1-SLIP) if side==1 else fpx*(1+SLIP)
                cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False; parab=False; ltp=False
            elif regime_out:
                fpx=oN*(1-SLIP) if side==1 else oN*(1+SLIP)
                cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False; parab=False; ltp=False
            else:
                prof=((cN-entry)/R) if side==1 else ((entry-cN)/R)
                if prof>=be_r:
                    be=entry*(1+be_buf) if side==1 else entry*(1-be_buf)
                    stop=max(stop,be) if side==1 else min(stop,be)
                if side==1 and not pyrd and prof>=pyr_r:
                    addn=pyr_frac*notional0; au=addn/cN; cash-=au*cN+addn*FEE; units+=au; pyrd=True; stop=max(stop,entry)
                # long TP
                if side==1 and long_tp_r is not None and not ltp and prof>=long_tp_r:
                    fpx=oN*(1-SLIP); cl=long_tp_frac*units; cash+=cl*fpx*(1-FEE); units-=cl; ltp=True
                # short TP (cover at +R)
                if side==-1 and short_tp_r is not None and prof>=short_tp_r:
                    fpx=oN*(1+SLIP); cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0
                # parabolic de-risk (long)
                if use_parab and side==1 and not parab and parab_trig[i]:
                    fpx=oN*(1-SLIP); cl=0.5*units; cash+=cl*fpx*(1-FEE); units-=cl; parab=True
        if side==0:
            go=0
            if bull[i] and armedL: go=1
            elif bear[i] and armedS: go=-1
            if go!=0:
                E=cash
                if go==1:
                    st=max(c[i]-atr_mult*a[i], c[i]*(1-sl_cap)); entry=oN*(1+SLIP); sz=1.0
                else:
                    st=min(c[i]+s_atr*a[i], c[i]*(1+s_cap)); entry=oN*(1-SLIP); sz=(ssize[i] if use_bd else 0.40)
                ok=(entry-st>0) if go==1 else (st-entry>0)
                if ok:
                    notional0=sz*E; units=go*notional0/entry; cash=E-units*entry-notional0*FEE
                    stop=st; R=abs(entry-st); side=go; pyrd=False; parab=False; ltp=False; sbar=i
                    if go==1: armedL=False; nL+=1
                    else: armedS=False; nS+=1
        eq[i+1]=cash+units*cN
    return pd.Series(eq,index=pd.to_datetime(df["timestamp"])).iloc[16:], nL, nS


def main():
    df,bull=build(); c=df["close"]
    LONG=bull&(c>bt.sma(c,9*30*BPD).shift(1)).fillna(False).values
    bear=((c/c.rolling(40*BPD).max().shift(1)-1)<-0.10).fillna(False).values & daily_macd_bear(df)
    ddh=(c/c.rolling(180*BPD).max().shift(1)-1).fillna(0).values
    ssize=np.where(ddh<=-0.30,1.0,np.where(ddh<=-0.20,0.50,0.25))
    parab=(c>2.2*bt.sma(c,140*BPD).shift(1)).fillna(False).values
    yrs=(pd.to_datetime(df["timestamp"]).iloc[-1]-pd.to_datetime(df["timestamp"]).iloc[16]).days/365.25
    print("="*86)
    print("V2 TRADE COUNT + TP FINE-TUNE (BTC 2017-2026)")
    print("="*86)
    print(f"  {'config':<34}{'CAGR':>6}{'DD':>6}{'r/DD':>6}{'OOS':>6}{'L+S':>7}{'tr/yr':>7}")
    tests=[
        ("V2 (no TP)", {}),
        ("+ long TP 50% @6R", dict(long_tp_r=6.0,long_tp_frac=0.5)),
        ("+ long TP 25% @8R", dict(long_tp_r=8.0,long_tp_frac=0.25)),
        ("+ short TP @5R (full cover)", dict(short_tp_r=5.0)),
        ("+ short TP @8R", dict(short_tp_r=8.0)),
        ("+ short time-stop T=60", dict(short_ts=60)),
        ("+ short time-stop T=90", dict(short_ts=90)),
    ]
    for name,kw in tests:
        s,nL,nS=run3(df,LONG,bear,ssize,parab,**kw); cg,dd,rr=m(s); o=m(s[s.index>=s.index[int(len(s)*0.6)]])
        print(f"  {name:<34}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{o[2]:>6.2f}{nL+nS:>7}{(nL+nS)/yrs:>7.1f}")
    print(f"\n  ({yrs:.1f} years of data)")


if __name__=="__main__":
    main()
