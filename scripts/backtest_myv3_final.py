#!/usr/bin/env python3
"""backtest_myv3_final.py — synthesize the agent winners + test leverage 1-3x + trailing.

Combines: tuned LONG (atr_mult=3.5, be_buf=0.01) + additive SLOW-GATED SHORT (price<SMA(12mo),
short_size=0.25, s_atr=4, s_cap=0.12, short pyramid ON). Adds a global `lev` multiplier and an
optional chandelier trail to answer: does 3x help? does trailing reduce DD or just clip winners?
Unified signed cash/units engine; lev=1 + short off must reproduce the my-V3 long base.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_shorts import regimes, m

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT


def run(df, bull, bear, allow_long=True, allow_short=False, lev=1.0, short_size=0.25,
        pyr_r=2.0, pyr_frac=1.0, be_r=1.0, atr_mult=3.5, sl_cap=0.08, be_buf=0.01,
        s_atr=4.0, s_cap=0.12, s_pyr_r=2.0, trail_k=None):
    o=df["open"].values; h=df["high"].values; l=df["low"].values; c=df["close"].values
    a=bt.atr(df,14).values; n=len(df)
    cash=1.0; units=0.0; side=0; entry=0.0; stop=0.0; R=0.0; pyrd=False; notional0=0.0
    peakH=0.0; armedL=True; armedS=True
    eq=np.ones(n)
    for i in range(16, n-1):
        oN,hN,lN,cN = o[i+1],h[i+1],l[i+1],c[i+1]
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        if side!=0:
            if side==1 and trail_k is not None:
                peakH=max(peakH,h[i]); stop=max(stop, peakH - trail_k*a[i])
            hit = (lN<=stop) if side==1 else (hN>=stop)
            regime_out = (not bull[i]) if side==1 else (not bear[i])
            if hit:
                fpx = stop*(1-SLIP) if side==1 else stop*(1+SLIP)
                cash += units*fpx - abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False
            elif regime_out:
                fpx = oN*(1-SLIP) if side==1 else oN*(1+SLIP)
                cash += units*fpx - abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False
            else:
                prof = ((cN-entry)/R) if side==1 else ((entry-cN)/R)
                if prof>=be_r:
                    be = entry*(1+be_buf) if side==1 else entry*(1-be_buf)
                    stop = max(stop,be) if side==1 else min(stop,be)
                pr = s_pyr_r if side==-1 else pyr_r
                if pr is not None and not pyrd and prof>=pr:
                    addn = pyr_frac*notional0
                    add_units = (addn/cN) if side==1 else (-addn/cN)
                    cash -= add_units*cN + addn*FEE; units += add_units; pyrd=True
                    stop = max(stop,entry) if side==1 else min(stop,entry)
        if side==0:
            go=0
            if allow_long and bull[i] and armedL: go=1
            elif allow_short and bear[i] and armedS: go=-1
            if go!=0:
                E=cash
                if go==1:
                    st=max(c[i]-atr_mult*a[i], c[i]*(1-sl_cap)); entry=oN*(1+SLIP); sz=lev
                else:
                    st=min(c[i]+s_atr*a[i], c[i]*(1+s_cap)); entry=oN*(1-SLIP); sz=lev*short_size
                ok = (entry-st>0) if go==1 else (st-entry>0)
                if ok:
                    notional0 = sz*E
                    units = go*notional0/entry; cash = E - units*entry - notional0*FEE
                    stop=st; R=abs(entry-st); side=go; pyrd=False; peakH=entry
                    if go==1: armedL=False
                    else: armedS=False
        eq[i+1] = cash + units*cN
    return pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]


def yr(s,y):
    seg=s[s.index.year==y]; return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg)>20 else 0.0


def show(s,label,nyears=False):
    cg,dd,rr=m(s); o=m(s[s.index>=s.index[int(len(s)*0.6)]])
    print(f"  {label:<40}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{o[2]:>7.2f}{yr(s,2022):>6.0f}%{yr(s,2026):>6.0f}%")


def main():
    df,bull,bear_d=regimes()
    c=df["close"]; bear_slow=(c < bt.sma(c,12*30*6).shift(1)).fillna(False).values
    print("="*92)
    print("FINAL SYNTHESIS — tuned long + slow-gated short, leverage & trailing tests (BTC 4h)")
    print("="*92)
    print(f"  {'config':<40}{'CAGR':>6}{'DD':>6}{'r/DD':>6}{'OOS':>7}{'2022':>6}{'2026':>7}")
    # sanity: lev1 long-only with OLD base params == known base
    show(run(df,bull,bear_d,allow_long=True,allow_short=False,lev=1,atr_mult=3.0,be_buf=0.005), "[sanity] long-only OLD base (expect 2.38)")
    print("  "+"-"*90)
    show(run(df,bull,bear_d,allow_long=True,allow_short=False,atr_mult=3.5,be_buf=0.01), "tuned LONG-only (atr3.5,bebuf1%)")
    show(run(df,bull,bear_slow,allow_long=True,allow_short=True), "COMBINED long+slowshort (lev1)")
    print("  -- leverage on the combined config --")
    for L in (1.0,1.5,2.0,3.0):
        show(run(df,bull,bear_slow,allow_long=True,allow_short=True,lev=L), f"COMBINED lev={L}x")
    print("  -- trailing stop on the combined config (does it reduce DD?) --")
    for k in (3,4,5):
        show(run(df,bull,bear_slow,allow_long=True,allow_short=True,trail_k=k), f"COMBINED + chandelier trail k={k}")
    print("\n  YEAR-BY-YEAR — COMBINED (lev1):")
    s=run(df,bull,bear_slow,allow_long=True,allow_short=True)
    for y in range(2020,2027): print(f"    {y}: {yr(s,y):+.0f}%")


if __name__=="__main__":
    main()
