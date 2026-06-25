#!/usr/bin/env python3
"""backtest_btc_altshorts.py — BTC long + alt shorts (ETH/BNB/SOL, single or diversified basket).

Tests the user's idea family: BTC for longs (clean trends) + alts for shorts (crash harder).
Single-alt shorts and a diversified 3-alt short basket (which may smooth the whipsaw). One shared
account, honest cash/units. Compares to BTC-only V2 (ret/DD 3.34, DD -31%).
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_final import m
from backtest_btclong_ethshort import load4h, macd_bear_map, dbull_map

FEE=bt.FEE_PCT; SLIP=bt.SLIP_PCT; BPD=6


def alt_short_arrays(sym):
    df,dd=load4h(sym); c=df["close"]
    bear=((c/c.rolling(40*BPD).max().shift(1)-1)<-0.10).fillna(False).values & macd_bear_map(df,dd)
    ddh=(c/c.rolling(180*BPD).max().shift(1)-1).fillna(0).values
    ss=np.where(ddh<=-0.30,1.0,np.where(ddh<=-0.20,0.50,0.25))
    return df, bear, ss


def run_combo(alt_specs, atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0, be_buf=0.01, s_atr=5.0, s_cap=0.15):
    btc,btcd=load4h("BTCUSDT")
    alts=[(sym,salloc,)+alt_short_arrays(sym) for sym,salloc in alt_specs]
    # common timestamps across BTC + all alts
    common=pd.Index(btc["timestamp"])
    for _,_,adf,_,_ in alts: common=common.intersection(pd.Index(adf["timestamp"]))
    btc=btc[btc["timestamp"].isin(common)].reset_index(drop=True)
    bc=btc["close"]
    bull=((bt.ema(bc,50)>bt.ema(bc,200)).values & dbull_map(btc,btcd) & (bc>bt.sma(bc,9*30*BPD).shift(1)).fillna(False).values)
    parab=(bc>2.2*bt.sma(bc,140*BPD).shift(1)).fillna(False).values
    A=[]
    for sym,salloc,adf,bear,ss in alts:
        adf2=adf[adf["timestamp"].isin(common)].reset_index(drop=True)
        idx=adf["timestamp"].isin(common).values
        A.append(dict(sym=sym,salloc=salloc,o=adf2["open"].values,h=adf2["high"].values,
                      c=adf2["close"].values,atr=bt.atr(adf2,14).values,bear=bear[idx],ss=ss[idx]))
    bo,bh,bl,bcv=[btc[k].values for k in ("open","high","low","close")]; ba=bt.atr(btc,14).values
    n=len(btc); cash=1.0; bu=0.0
    lside=0; lentry=lstop=lR=0.0; pyrd=parabd=False; armedL=True
    K=len(A); eu=[0.0]*K; sside=[0]*K; sentry=[0.0]*K; sstop=[0.0]*K; sR=[0.0]*K; armedS=[True]*K
    eq=np.ones(n)
    def equity(i): return cash + bu*bcv[i] + sum(eu[k]*A[k]["c"][i] for k in range(K))
    for i in range(16,n-1):
        if not bull[i]: armedL=True
        for k in range(K):
            if not A[k]["bear"][i]: armedS[k]=True
        # BTC long manage
        if lside==1:
            lN=bl[i+1]; oN=bo[i+1]; cN=bcv[i+1]
            if lN<=lstop:
                fpx=lstop*(1-SLIP); cash+=bu*fpx-abs(bu)*fpx*FEE; bu=0.0; lside=0; pyrd=parabd=False
            elif not bull[i]:
                fpx=oN*(1-SLIP); cash+=bu*fpx-abs(bu)*fpx*FEE; bu=0.0; lside=0; pyrd=parabd=False
            else:
                prof=(cN-lentry)/lR
                if prof>=be_r: lstop=max(lstop,lentry*(1+be_buf))
                if not pyrd and prof>=pyr_r:
                    addn=pyr_frac*(bu*cN); au=addn/cN; cash-=au*cN+addn*FEE; bu+=au; pyrd=True; lstop=max(lstop,lentry)
                if not parabd and parab[i]:
                    fpx=oN*(1-SLIP); cl=0.5*bu; cash+=cl*fpx*(1-FEE); bu-=cl; parabd=True
        # alt shorts manage
        for k in range(K):
            if sside[k]==-1:
                hN=A[k]["h"][i+1]; oN=A[k]["o"][i+1]; cN=A[k]["c"][i+1]
                if hN>=sstop[k]:
                    fpx=sstop[k]*(1+SLIP); cash+=eu[k]*fpx-abs(eu[k])*fpx*FEE; eu[k]=0.0; sside[k]=0
                elif not A[k]["bear"][i]:
                    fpx=oN*(1+SLIP); cash+=eu[k]*fpx-abs(eu[k])*fpx*FEE; eu[k]=0.0; sside[k]=0
                else:
                    prof=(sentry[k]-cN)/sR[k]
                    if prof>=be_r: sstop[k]=min(sstop[k],sentry[k]*(1-be_buf))
        # BTC long entry
        if lside==0 and bull[i] and armedL:
            E=equity(i); oN=bo[i+1]; st=max(bcv[i]-atr_mult*ba[i], bcv[i]*(1-sl_cap)); ent=oN*(1+SLIP)
            if ent-st>0:
                bu=E/ent; cash-=bu*ent+E*FEE; lentry=ent; lstop=st; lR=ent-st; lside=1; pyrd=parabd=False; armedL=False
        # alt short entries
        for k in range(K):
            if sside[k]==0 and A[k]["bear"][i] and armedS[k]:
                E=equity(i); oN=A[k]["o"][i+1]; st=min(A[k]["c"][i]+s_atr*A[k]["atr"][i], A[k]["c"][i]*(1+s_cap)); ent=oN*(1-SLIP)
                if st-ent>0:
                    notional=A[k]["salloc"]*A[k]["ss"][i]*E; eu[k]=-notional/ent; cash-=eu[k]*ent+notional*FEE
                    sentry[k]=ent; sstop[k]=st; sR[k]=st-ent; sside[k]=-1; armedS[k]=False
        eq[i+1]=equity(i+1)
    return pd.Series(eq,index=pd.to_datetime(btc["timestamp"])).iloc[16:]


def rep(name,s):
    cg,dd,rr=m(s); o=m(s[s.index>=s.index[int(len(s)*0.6)]])
    print(f"  {name:<34}CAGR {cg*100:>4.0f}%  DD {dd*100:>4.0f}%  ret/DD {rr:.2f}  OOS {o[2]:.2f}  ({s.index[0].year}-{s.index[-1].year})")


def main():
    print("="*92)
    print("BTC LONG + ALT SHORTS — single alts & diversified basket vs BTC-only V2")
    print("="*92)
    rep("BTC + ETH short", run_combo([("ETHUSDT",0.40)]))
    rep("BTC + BNB short", run_combo([("BNBUSDT",0.40)]))
    rep("BTC + SOL short", run_combo([("SOLUSDT",0.40)]))
    rep("BTC + ETH+BNB+SOL shorts", run_combo([("ETHUSDT",0.15),("BNBUSDT",0.15),("SOLUSDT",0.15)]))
    rep("BTC + ETH+BNB shorts (2017+)", run_combo([("ETHUSDT",0.20),("BNBUSDT",0.20)]))
    print("  " + "-"*88)
    # BTC-only V2 reference on the matching long window
    from backtest_v2_combined import run2, build
    from backtest_short_aggressive import daily_macd_bear
    df2,bull2=build(); c2=df2["close"]
    L2=bull2&(c2>bt.sma(c2,9*30*BPD).shift(1)).fillna(False).values
    be2=((c2/c2.rolling(40*BPD).max().shift(1)-1)<-0.10).fillna(False).values & daily_macd_bear(df2)
    dh2=(c2/c2.rolling(180*BPD).max().shift(1)-1).fillna(0).values; ss2=np.where(dh2<=-0.30,1.0,np.where(dh2<=-0.20,0.50,0.25))
    pa2=(c2>2.2*bt.sma(c2,140*BPD).shift(1)).fillna(False).values
    rep("BTC-only V2 (ref)", run2(df2,L2,be2,ss2,pa2,use_parab=True,use_bd=True))


if __name__=="__main__":
    main()
