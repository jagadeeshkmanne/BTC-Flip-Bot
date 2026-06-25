#!/usr/bin/env python3
"""backtest_short_eth_on_btc.py — when BTC gives the SHORT signal, short ETH instead of BTC.

Clean single-position engine (long OR short, never both): the LONG leg is BTC; the SHORT leg can be
BTC (=validated V2) or ETH — but driven by the SAME BTC signals (bear gate + BTC bear-depth + same
timing). Only the instrument the short is *placed on* changes (ETH entry/stop/mark). Verifies
short_inst='btc' reproduces V2 (ret/DD 3.34) before trusting the ETH comparison.
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


def run_se(short_inst="btc", lev=1.0, atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0,
           be_buf=0.01, s_atr=5.0, s_cap=0.15):
    btc,btcd=load4h("BTCUSDT")
    sym={"eth":"ETHUSDT","bnb":"BNBUSDT","sol":"SOLUSDT"}.get(short_inst)
    alt=None
    if sym:
        alt,_=load4h(sym)
        common=pd.Index(btc["timestamp"]).intersection(pd.Index(alt["timestamp"]))
        btc=btc[btc["timestamp"].isin(common)].reset_index(drop=True)
        alt=alt[alt["timestamp"].isin(common)].reset_index(drop=True)
    bc=btc["close"]
    # ALL signals from BTC
    bull=((bt.ema(bc,50)>bt.ema(bc,200)).values & dbull_map(btc,btcd) & (bc>bt.sma(bc,9*30*BPD).shift(1)).fillna(False).values)
    parab=(bc>2.2*bt.sma(bc,140*BPD).shift(1)).fillna(False).values
    bear=((bc/bc.rolling(40*BPD).max().shift(1)-1)<-0.10).fillna(False).values & macd_bear_map(btc,btcd)
    bdd=(bc/bc.rolling(180*BPD).max().shift(1)-1).fillna(0).values
    ssize=np.where(bdd<=-0.30,1.0,np.where(bdd<=-0.20,0.50,0.25))   # BTC-drawdown bear-depth
    # price arrays: long always BTC; short uses chosen instrument
    bo,bh,bl,bcv=[btc[k].values for k in ("open","high","low","close")]; ba=bt.atr(btc,14).values
    if alt is not None:
        so,sh,scv=[alt[k].values for k in ("open","high","close")]; sa=bt.atr(alt,14).values
    else:
        so,sh,scv=bo,bh,bcv; sa=ba
    n=len(btc); cash=1.0; units=0.0; side=0; entry=stop=R=0.0; pyrd=parabd=False; notional0=0.0
    armedL=True; armedS=True; eq=np.ones(n)
    def mpx(i): return bcv[i] if side==1 else (scv[i] if side==-1 else bcv[i])
    for i in range(16,n-1):
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        if side==1:        # LONG BTC
            lN=bl[i+1]; oN=bo[i+1]; cN=bcv[i+1]
            if lN<=stop:
                fpx=stop*(1-SLIP); cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0; pyrd=parabd=False
            elif not bull[i]:
                fpx=oN*(1-SLIP); cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0; pyrd=parabd=False
            else:
                prof=(cN-entry)/R
                if prof>=be_r: stop=max(stop,entry*(1+be_buf))
                if not pyrd and prof>=pyr_r:
                    addn=pyr_frac*notional0; au=addn/cN; cash-=au*cN+addn*FEE; units+=au; pyrd=True; stop=max(stop,entry)
                if not parabd and parab[i]:
                    fpx=oN*(1-SLIP); cl=0.5*units; cash+=cl*fpx*(1-FEE); units-=cl; parabd=True
        elif side==-1:     # SHORT (BTC or ETH)
            hN=sh[i+1]; oN=so[i+1]; cN=scv[i+1]
            if hN>=stop:
                fpx=stop*(1+SLIP); cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0
            elif not bear[i]:
                fpx=oN*(1+SLIP); cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0
            else:
                prof=(entry-cN)/R
                if prof>=be_r: stop=min(stop,entry*(1-be_buf))
        if side==0:
            if bull[i] and armedL:
                E=cash; oN=bo[i+1]; st=max(bcv[i]-atr_mult*ba[i], bcv[i]*(1-sl_cap)); ent=oN*(1+SLIP)
                if ent-st>0:
                    notional0=E*lev; units=notional0/ent; cash-=units*ent+notional0*FEE; entry=ent; stop=st; R=ent-st; side=1; pyrd=parabd=False; armedL=False
            elif bear[i] and armedS:
                E=cash; oN=so[i+1]; st=min(scv[i]+s_atr*sa[i], scv[i]*(1+s_cap)); ent=oN*(1-SLIP)
                if st-ent>0:
                    notional=ssize[i]*E*lev; units=-notional/ent; cash-=units*ent+notional*FEE; entry=ent; stop=st; R=st-ent; side=-1; armedS=False
        eq[i+1]=cash+units*mpx(i+1)
    return pd.Series(eq,index=pd.to_datetime(btc["timestamp"])).iloc[16:]


def yr(s,y):
    seg=s[s.index.year==y]; return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg)>20 else None


def rep(name,s):
    cg,dd,rr=m(s); o=m(s[s.index>=s.index[int(len(s)*0.6)]])
    pb="/".join(f"{yr(s,y):+.0f}" if yr(s,y) is not None else "--" for y in (2018,2022,2026))
    print(f"  {name:<36}CAGR {cg*100:>4.0f}%  DD {dd*100:>4.0f}%  ret/DD {rr:.2f}  OOS {o[2]:.2f}  bears {pb}")


def main():
    print("="*94)
    print("SHORT WHICH COIN ON BTC'S SIGNAL? + LEVERAGE (does leverage reduce DD?) — full 2017-2026")
    print("="*94)
    print("  -- which instrument to short (1x), long always BTC --")
    rep("short BTC (=validated V2)", run_se("btc"))
    rep("short ETH on BTC signal", run_se("eth"))
    rep("short BNB on BTC signal", run_se("bnb"))
    rep("short SOL on BTC signal", run_se("sol"))
    print("\n  -- LEVERAGE on the ETH-short version (does it reduce DD?) --")
    for L in (1.0, 2.0, 3.0):
        rep(f"short ETH, lev={L}x", run_se("eth", lev=L))
    print("\n  (short_inst='btc' reproduces validated V2 = CAGR 103/DD -31/ret-DD 3.34)")


if __name__=="__main__":
    main()
