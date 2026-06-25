#!/usr/bin/env python3
"""backtest_v2_mae.py — how far does BTC dip AFTER a V2 long entry? (MAE) + test DCA-on-dip.

Measures, per long trade, the max drawdown from entry before it resolves (MAE), split by
winners vs losers. Then tests DCA (add on a -X% dip, averaging DOWN) vs the current pyramid
(add at +2R to a WINNER). Answers: is there a typical dip to DCA into, or do losers just stop out?
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


def run_mae(df,bull,bear,ssize,parab,dca_pct=None,dca_frac=1.0,use_pyr=True,
            atr_mult=3.5,sl_cap=0.12,be_r=1.0,pyr_r=2.0,pyr_frac=1.0,be_buf=0.01,s_atr=5.0,s_cap=0.15):
    o=df["open"].values;h=df["high"].values;l=df["low"].values;c=df["close"].values
    a=bt.atr(df,14).values; n=len(df)
    cash=1.0;units=0.0;side=0;entry=stop=R=0.0;pyrd=parabd=dcad=False;notional0=0.0
    low_since=1e18; eq_entry=1.0
    armedL=armedS=True; eq=np.ones(n); trades=[]   # (mae, ret, won, side)
    for i in range(16,n-1):
        oN,hN,lN,cN=o[i+1],h[i+1],l[i+1],c[i+1]
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        if side!=0:
            if side==1: low_since=min(low_since,lN)
            hit=(lN<=stop) if side==1 else (hN>=stop)
            regime_out=(not bull[i]) if side==1 else (not bear[i])
            if hit or regime_out:
                fpx=(stop if hit else oN); fpx=fpx*(1-SLIP) if side==1 else fpx*(1+SLIP)
                cash+=units*fpx-abs(units)*fpx*FEE
                if side==1:
                    mae=low_since/entry-1
                    trades.append((mae, cash/eq_entry-1, cash>eq_entry, 1))
                units=0.0; side=0; pyrd=parabd=dcad=False
            else:
                prof=((cN-entry)/R) if side==1 else ((entry-cN)/R)
                if prof>=be_r:
                    be=entry*(1+be_buf) if side==1 else entry*(1-be_buf)
                    stop=max(stop,be) if side==1 else min(stop,be)
                # DCA: add on a -X% dip from entry (averaging DOWN) — does NOT move stop up
                if side==1 and dca_pct is not None and not dcad and cN<=entry*(1-dca_pct):
                    addn=dca_frac*notional0; au=addn/cN; cash-=au*cN+addn*FEE
                    entry=(units*entry+au*cN)/(units+au); units+=au; dcad=True   # blended entry
                if side==1 and use_pyr and not pyrd and prof>=pyr_r:
                    addn=pyr_frac*notional0; au=addn/cN; cash-=au*cN+addn*FEE; units+=au; pyrd=True; stop=max(stop,entry)
                if side==1 and not parabd and parab[i]:
                    fpx=oN*(1-SLIP); cl=0.5*units; cash+=cl*fpx*(1-FEE); units-=cl; parabd=True
        if side==0:
            if bull[i] and armedL:
                E=cash; st=max(c[i]-atr_mult*a[i],c[i]*(1-sl_cap)); ent=o[i+1]*(1+SLIP)
                if ent-st>0:
                    notional0=E; units=E/ent; cash-=units*ent+E*FEE; entry=ent; stop=st; R=ent-st; side=1; pyrd=parabd=dcad=False; armedL=False; low_since=ent; eq_entry=E
            elif bear[i] and armedS:
                E=cash; st=min(c[i]+s_atr*a[i],c[i]*(1+s_cap)); ent=o[i+1]*(1-SLIP)
                if st-ent>0:
                    notional=ssize[i]*E; units=-notional/ent; cash-=units*ent+notional*FEE; entry=ent; stop=st; R=st-ent; side=-1; armedS=False
        eq[i+1]=cash+units*cN
    return pd.Series(eq,index=pd.to_datetime(df["timestamp"])).iloc[16:], trades


def main():
    df,bull=build(); c=df["close"]
    L=bull & (c>bt.sma(c,9*30*6).shift(1)).fillna(False).values
    bear=((c/c.rolling(40*6).max().shift(1)-1)<-0.10).fillna(False).values & daily_macd_bear(df)
    ddh=(c/c.rolling(180*6).max().shift(1)-1).fillna(0).values
    ss=np.where(ddh<=-0.30,1.0,np.where(ddh<=-0.20,0.50,0.25))
    pa=(c>2.2*bt.sma(c,140*6).shift(1)).fillna(False).values
    s,tr=run_mae(df,L,bear,ss,pa)
    mae=np.array([t[0] for t in tr]); won=np.array([t[2] for t in tr])
    print("="*78)
    print("HOW FAR DOES BTC DIP AFTER A V2 LONG ENTRY? (MAE) — full 2017-2026")
    print("="*78)
    print(f"  {len(tr)} long trades")
    print(f"  avg MAE (max dip from entry): {mae.mean()*100:.1f}%  | median {np.median(mae)*100:.1f}%")
    print(f"  WINNERS ({won.sum()}): avg dip {mae[won].mean()*100:.1f}%  (these dipped then recovered)")
    print(f"  LOSERS  ({(~won).sum()}): avg dip {mae[~won].mean()*100:.1f}%  (these dipped to the stop)")
    print("  dip-depth distribution (all trades):")
    for thr in (0.02,0.03,0.05,0.07,0.10):
        print(f"    dipped >{int(thr*100)}% below entry: {(mae<=-thr).mean()*100:.0f}% of trades  "
              f"(of those, {won[mae<=-thr].mean()*100:.0f}% still won)")
    print("\n  DCA-ON-DIP (add averaging DOWN at -X%) vs current pyramid (add to WINNER @2R):")
    print(f"    {'config':<36}{'CAGR':>6}{'DD':>6}{'r/DD':>6}")
    def row(name,**kw):
        s2,_=run_mae(df,L,bear,ss,pa,**kw); cg,dd,rr=m(s2)
        print(f"    {name:<36}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}")
    row("V2 (pyramid to winner @2R)")
    row("DCA add at -3% dip (no pyr)", dca_pct=0.03, use_pyr=False)
    row("DCA add at -5% dip (no pyr)", dca_pct=0.05, use_pyr=False)
    row("DCA at -3% + keep pyramid", dca_pct=0.03, use_pyr=True)
    row("DCA at -5% half-size + pyramid", dca_pct=0.05, dca_frac=0.5, use_pyr=True)


if __name__=="__main__":
    main()
