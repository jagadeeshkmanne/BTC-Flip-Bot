#!/usr/bin/env python3
"""backtest_dynamic_short.py — dynamically short BTC *or* ETH, whichever a causal signal favors.

BTC long always. When the bear gate fires, the short sleeve picks the instrument to short via a
CAUSAL switch signal (can't see the future): relative momentum, ETH/BTC ratio trend, or bear depth.
Goal: capture ETH's bigger crashes when ETH is the weaker coin, BTC's cleaner short otherwise.
Compare to always-BTC (=V2, ret/DD 3.34) and always-ETH.
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


def run_dyn(switch, atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0, be_buf=0.01, s_atr=5.0, s_cap=0.15):
    btc,btcd=load4h("BTCUSDT"); eth,ethd=load4h("ETHUSDT")
    common=pd.Index(btc["timestamp"]).intersection(pd.Index(eth["timestamp"]))
    btc=btc[btc["timestamp"].isin(common)].reset_index(drop=True)
    eth=eth[eth["timestamp"].isin(common)].reset_index(drop=True)
    bc=btc["close"]; ec=eth["close"]
    bull=((bt.ema(bc,50)>bt.ema(bc,200)).values & dbull_map(btc,btcd) & (bc>bt.sma(bc,9*30*BPD).shift(1)).fillna(False).values)
    parab=(bc>2.2*bt.sma(bc,140*BPD).shift(1)).fillna(False).values
    # shared bear trigger: BTC down>10% from 40d high AND BTC daily MACD<sig
    bear=((bc/bc.rolling(40*BPD).max().shift(1)-1)<-0.10).fillna(False).values & macd_bear_map(btc,btcd)
    # per-instrument bear-depth size
    def ssz(c):
        ddh=(c/c.rolling(180*BPD).max().shift(1)-1).fillna(0).values
        return np.where(ddh<=-0.30,1.0,np.where(ddh<=-0.20,0.50,0.25))
    bss=ssz(bc); ess=ssz(ec)
    # causal switch signals -> True means short ETH, False means short BTC
    b20=(bc/bc.shift(120)-1); e20=(ec/ec.shift(120)-1)
    ratio=ec/bc; rma=bt.sma(ratio,50*BPD)
    btc_dd=(bc/bc.rolling(180*BPD).max()-1)
    sw={
        "btc": np.zeros(len(bc),bool),
        "eth": np.ones(len(bc),bool),
        "relmom": (e20<b20).shift(1).fillna(False).values,         # short whichever is weaker (ETH if ETH weaker)
        "ratio": (ratio<rma).shift(1).fillna(False).values,        # ETH underperforming BTC -> short ETH
        "depth": (btc_dd<-0.25).shift(1).fillna(False).values,     # deep bear -> short ETH (more downside)
    }[switch]
    bo,bh,bl,bcv=[btc[k].values for k in ("open","high","low","close")]; ba=bt.atr(btc,14).values
    eo,eh,el,ecv=[eth[k].values for k in ("open","high","low","close")]; ea=bt.atr(eth,14).values
    n=len(btc); cash=1.0; bu=0.0
    lside=0; lentry=lstop=lR=0.0; pyrd=parabd=False; armedL=True
    su=0.0; sinst=0; sentry=sstop=sR=0.0; armedS=True; lnot0=0.0   # sinst: 1=BTC,2=ETH
    eq=np.ones(n)
    def spx(i): return bcv[i] if sinst==1 else ecv[i]
    def equity(i): return cash + bu*bcv[i] + su*spx(i)
    for i in range(16,n-1):
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        # BTC long
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
                    addn=pyr_frac*lnot0; au=addn/cN; cash-=au*cN+addn*FEE; bu+=au; pyrd=True; lstop=max(lstop,lentry)
                if not parabd and parab[i]:
                    fpx=oN*(1-SLIP); cl=0.5*bu; cash+=cl*fpx*(1-FEE); bu-=cl; parabd=True
        # short (BTC or ETH)
        if sinst!=0:
            hi=(bh if sinst==1 else eh)[i+1]; oN=(bo if sinst==1 else eo)[i+1]; cN=spx(i+1)
            if hi>=sstop:
                fpx=sstop*(1+SLIP); cash+=su*fpx-abs(su)*fpx*FEE; su=0.0; sinst=0
            elif not bear[i]:
                fpx=oN*(1+SLIP); cash+=su*fpx-abs(su)*fpx*FEE; su=0.0; sinst=0
            else:
                prof=(sentry-cN)/sR
                if prof>=be_r: sstop=min(sstop,sentry*(1-be_buf))
        # BTC long entry
        if lside==0 and bull[i] and armedL:
            E=equity(i); oN=bo[i+1]; st=max(bcv[i]-atr_mult*ba[i], bcv[i]*(1-sl_cap)); ent=oN*(1+SLIP)
            if ent-st>0:
                bu=E/ent; cash-=bu*ent+E*FEE; lentry=ent; lstop=st; lR=ent-st; lside=1; pyrd=parabd=False; armedL=False; lnot0=E
        # short entry — pick instrument via switch
        if sinst==0 and bear[i] and armedS:
            use_eth=sw[i]; E=equity(i)
            o_,c_,a_,ss_= (eo,ecv,ea,ess) if use_eth else (bo,bcv,ba,bss)
            oN=o_[i+1]; st=min(c_[i]+s_atr*a_[i], c_[i]*(1+s_cap)); ent=oN*(1-SLIP)
            if st-ent>0:
                notional=0.40*ss_[i]*E; su=-notional/ent; cash-=su*ent+notional*FEE
                sentry=ent; sstop=st; sR=st-ent; sinst=(2 if use_eth else 1); armedS=False
        eq[i+1]=equity(i+1)
    return pd.Series(eq,index=pd.to_datetime(btc["timestamp"])).iloc[16:]


def rep(name,s):
    cg,dd,rr=m(s); o=m(s[s.index>=s.index[int(len(s)*0.6)]])
    print(f"  {name:<32}CAGR {cg*100:>4.0f}%  DD {dd*100:>4.0f}%  ret/DD {rr:.2f}  OOS {o[2]:.2f}")


def main():
    print("="*84)
    print("DYNAMIC SHORT SWITCH — short BTC or ETH per a causal signal (full 2017-2026)")
    print("="*84)
    rep("always BTC short (=V2)", run_dyn("btc"))
    rep("always ETH short", run_dyn("eth"))
    rep("switch: relative momentum", run_dyn("relmom"))
    rep("switch: ETH/BTC ratio trend", run_dyn("ratio"))
    rep("switch: bear depth (deep->ETH)", run_dyn("depth"))


if __name__=="__main__":
    main()
