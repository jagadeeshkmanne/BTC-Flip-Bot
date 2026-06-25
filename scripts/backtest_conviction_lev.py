#!/usr/bin/env python3
"""backtest_conviction_lev.py — leverage by SIGNAL STRENGTH (conviction): strong trend->3x, weak->1x.

User's idea: scale leverage by how strong the signal is. Conviction from ADX + EMA-separation
(causal). Long leverage = 1x..lev_max by conviction; short sleeve kept de-risked (the agents found
the -37% DD is the leveraged short whipsaw). Compares to fixed 1.8x + the agent winners.
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


def run_conv(df,bull,bear,ssize,parab,long_lev,short_lev=1.0,lock_frac=0.33,lock_r=6.0,
             atr_mult=3.5,sl_cap=0.12,be_r=1.0,pyr_r=2.0,pyr_frac=1.0,be_buf=0.01,s_atr=5.0,s_cap=0.15):
    o=df["open"].values;h=df["high"].values;l=df["low"].values;c=df["close"].values
    a=bt.atr(df,14).values; n=len(df)
    cash=1.0;units=0.0;side=0;entry=stop=R=0.0;pyrd=parabd=lockd=False;notional0=0.0
    armedL=armedS=True; eq=np.ones(n); plev=[]
    for i in range(16,n-1):
        oN,hN,lN,cN=o[i+1],h[i+1],l[i+1],c[i+1]
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
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
                    lv=long_lev[i]; notional0=E*lv; units=notional0/ent; cash-=units*ent+notional0*FEE; entry=ent; stop=st; R=ent-st; side=1; pyrd=parabd=lockd=False; armedL=False; plev.append(lv)
            elif bear[i] and armedS:
                E=cash; st=min(c[i]+s_atr*a[i],c[i]*(1+s_cap)); ent=o[i+1]*(1-SLIP)
                if st-ent>0:
                    notional=ssize[i]*E*short_lev; units=-notional/ent; cash-=units*ent+notional*FEE; entry=ent; stop=st; R=st-ent; side=-1; armedS=False
        eq[i+1]=cash+units*cN
    return pd.Series(eq,index=pd.to_datetime(df["timestamp"])).iloc[16:], (np.mean(plev) if plev else 0)


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
    # CONVICTION (causal): ADX trend strength + EMA50/200 separation
    adx=bt.adx(df,14).shift(1).fillna(0).values
    egap=((bt.ema(c,50)-bt.ema(c,200))/bt.ema(c,200)).shift(1).fillna(0).values
    conv=np.clip(adx/35.0,0,1)*0.5 + np.clip(egap/0.12,0,1)*0.5    # 0..1
    one=np.ones(len(df))
    def llev(lmin,lmax): return lmin+(lmax-lmin)*conv

    def row(name,longlev,short_lev=1.0):
        s,al=run_conv(df,L,bear,ss,pa,longlev,short_lev=short_lev); cg,dd,rr=m(s)
        o=m(s[s.index>=s.index[int(len(s)*0.6)]])[2]
        print(f"  {name:<38}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{o:>6.2f}  {yr(s,2018):>+4.0f}/{yr(s,2022):>+3.0f}/{yr(s,2026):>+3.0f}  avgL {al:.1f}  {'grn' if grn(s) else 'RED'}")

    print("="*96)
    print("CONVICTION LEVERAGE — strong signal->high lev, weak->low; short sleeve de-risked. 2017-2026")
    print("="*96)
    print(f"  {'config':<38}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  2018/22/26")
    print("  -- references --")
    row("fixed 1.8x (short 1.0x)",one*1.8,1.0)
    row("fixed 1.6x + short x0.75 (agent)",one*1.6,0.75)
    print("  -- conviction leverage 1x..max, short 1.0x --")
    row("conv 1.0-2.0x",llev(1.0,2.0),1.0)
    row("conv 1.0-2.5x",llev(1.0,2.5),1.0)
    row("conv 1.0-3.0x",llev(1.0,3.0),1.0)
    row("conv 1.2-3.0x",llev(1.2,3.0),1.0)
    print("  -- conviction leverage + de-risked short (x0.75) --")
    row("conv 1.0-2.5x + short x0.75",llev(1.0,2.5),0.75)
    row("conv 1.0-3.0x + short x0.75",llev(1.0,3.0),0.75)
    row("conv 1.2-3.0x + short x0.75",llev(1.2,3.0),0.75)
    row("conv 1.0-3.0x + short x0.5",llev(1.0,3.0),0.5)


if __name__=="__main__":
    main()
