#!/usr/bin/env python3
"""backtest_v2_eth_conviction.py — verify ETH-short stacks with conviction leverage before deploying.

Single-position engine: LONG BTC (conviction lev 1-2.5x + pyramid + lock33@6R + parabolic),
SHORT either BTC or ETH on BTC's bear signal (BTC bear-depth sizing, 1x, no leverage). Compares
short-BTC (current live) vs short-ETH. Must reproduce the deployed conviction config on short-BTC.
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


def run_ec(short_inst="btc", long_lev=None, lock_frac=0.33, lock_r=6.0, mf=12, ms=26, msig=9, confirm="macd",
           macd_tf="1d", drop_pct=0.10, drop_look=40,
           atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0, be_buf=0.01, s_atr=5.0, s_cap=0.15):
    btc,btcd=load4h("BTCUSDT"); eth,ethd=load4h("ETHUSDT")
    common=pd.Index(btc["timestamp"]).intersection(pd.Index(eth["timestamp"]))
    btc=btc[btc["timestamp"].isin(common)].reset_index(drop=True); eth=eth[eth["timestamp"].isin(common)].reset_index(drop=True)
    bc=btc["close"]; ec=eth["close"]
    bull=((bt.ema(bc,50)>bt.ema(bc,200)).values & dbull_map(btc,btcd) & (bc>bt.sma(bc,9*30*BPD).shift(1)).fillna(False).values)
    parab=(bc>2.2*bt.sma(bc,140*BPD).shift(1)).fillna(False).values
    # parametrized daily MACD bear (+ optional daily ADX-DI down confirm)
    ts4=pd.to_datetime(btc["timestamp"])
    if macd_tf=="4h":                              # MACD on the 4h chart TF (faster)
        ml4=bt.ema(bc,mf)-bt.ema(bc,ms); sig4=bt.ema(ml4,msig)
        macd_bear=(ml4<sig4).shift(1).fillna(False).values
    else:                                          # MACD on daily (current/live)
        mld=bt.ema(btcd["close"],mf)-bt.ema(btcd["close"],ms); sigd=bt.ema(mld,msig)
        dmb=(mld<sigd).shift(1).fillna(False)
        macd_bear=pd.merge_asof(pd.DataFrame({"ts":ts4}).sort_values("ts"),
            pd.DataFrame({"ts":pd.to_datetime(btcd["timestamp"]),"b":dmb.values}).sort_values("ts"),
            on="ts",direction="backward")["b"].fillna(False).astype(bool).values
    deb=(bt.ema(btcd["close"],50)<bt.ema(btcd["close"],200)).shift(1).fillna(False)
    ema_bear=pd.merge_asof(pd.DataFrame({"ts":ts4}).sort_values("ts"),
        pd.DataFrame({"ts":pd.to_datetime(btcd["timestamp"]),"b":deb.values}).sort_values("ts"),
        on="ts",direction="backward")["b"].fillna(False).astype(bool).values
    drop_arr=((bc/bc.rolling(drop_look*BPD).max().shift(1)-1)<-drop_pct).fillna(False).values
    conf={"macd":macd_bear,"ema":ema_bear,"both":macd_bear&ema_bear,
          "either":macd_bear|ema_bear,"none":np.ones(len(bc),bool)}[confirm]
    bear=drop_arr & conf
    bdd=(bc/bc.rolling(180*BPD).max().shift(1)-1).fillna(0).values
    ssize=np.where(bdd<=-0.30,1.0,np.where(bdd<=-0.20,0.50,0.25))
    if long_lev is None: long_lev=np.ones(len(btc))
    bo,bh,bl,bcv=[btc[k].values for k in ("open","high","low","close")]; ba=bt.atr(btc,14).values
    if short_inst=="eth":
        so,sh,scv=[eth[k].values for k in ("open","high","close")]; sa=bt.atr(eth,14).values
    else:
        so,sh,scv=bo,bh,bcv; sa=ba
    n=len(btc); cash=1.0; units=0.0; side=0; entry=stop=R=0.0; pyrd=parabd=lockd=False; notional0=0.0
    armedL=armedS=True; eq=np.ones(n)
    def mpx(i): return bcv[i] if side==1 else (scv[i] if side==-1 else bcv[i])
    for i in range(16,n-1):
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        if side==1:
            lN=bl[i+1]; oN=bo[i+1]; cN=bcv[i+1]
            if lN<=stop:
                fpx=stop*(1-SLIP); cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0; pyrd=parabd=lockd=False
            elif not bull[i]:
                fpx=oN*(1-SLIP); cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0; pyrd=parabd=lockd=False
            else:
                prof=(cN-entry)/R
                if prof>=be_r: stop=max(stop,entry*(1+be_buf))
                if not pyrd and prof>=pyr_r:
                    addn=pyr_frac*notional0; au=addn/cN; cash-=au*cN+addn*FEE; units+=au; pyrd=True; stop=max(stop,entry)
                if lock_frac>0 and not lockd and prof>=lock_r:
                    fpx=oN*(1-SLIP); cl=lock_frac*units; cash+=cl*fpx*(1-FEE); units-=cl; lockd=True
                if not parabd and parab[i]:
                    fpx=oN*(1-SLIP); cl=0.5*units; cash+=cl*fpx*(1-FEE); units-=cl; parabd=True
        elif side==-1:
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
                E=cash; oN=bo[i+1]; st=max(bcv[i]-atr_mult*ba[i],bcv[i]*(1-sl_cap)); ent=oN*(1+SLIP)
                if ent-st>0:
                    notional0=E*long_lev[i]; units=notional0/ent; cash-=units*ent+notional0*FEE; entry=ent; stop=st; R=ent-st; side=1; pyrd=parabd=lockd=False; armedL=False
            elif bear[i] and armedS:
                E=cash; oN=so[i+1]; st=min(scv[i]+s_atr*sa[i],scv[i]*(1+s_cap)); ent=oN*(1-SLIP)
                if st-ent>0:
                    notional=ssize[i]*E; units=-notional/ent; cash-=units*ent+notional*FEE; entry=ent; stop=st; R=st-ent; side=-1; armedS=False
        eq[i+1]=cash+units*mpx(i+1)
    return pd.Series(eq,index=pd.to_datetime(btc["timestamp"])).iloc[16:]


def yr(s,y):
    sg=s[s.index.year==y]; return (sg.iloc[-1]/sg.iloc[0]-1)*100 if len(sg)>20 else 0
def grn(s): return all(yr(s,y)>=-0.5 for y in range(2018,2027))


def main():
    btc,_=load4h("BTCUSDT"); eth,_=load4h("ETHUSDT")
    common=pd.Index(btc["timestamp"]).intersection(pd.Index(eth["timestamp"]))
    btc=btc[btc["timestamp"].isin(common)].reset_index(drop=True); c=btc["close"]
    adx=bt.adx(btc,14).shift(1).fillna(0).values
    egap=((bt.ema(c,50)-bt.ema(c,200))/bt.ema(c,200)).shift(1).fillna(0).values
    conv=np.clip(adx/35.0,0,1)*0.5+np.clip(egap/0.12,0,1)*0.5
    llev=1.0+(2.5-1.0)*conv
    print("="*84)
    print("ETH-SHORT + CONVICTION LEVERAGE — does it stack? (full 2017-2026)")
    print("="*84)
    print(f"  {'config':<40}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  2018/22/26")
    for name,si,lv in [("short BTC, 1x (plain)","btc",None),
                       ("short ETH, 1x (plain)","eth",None),
                       ("short BTC + conviction (=DEPLOYED)","btc",llev),
                       ("short ETH + conviction (candidate)","eth",llev)]:
        s=run_ec(si,long_lev=lv); cg,dd,rr=m(s); o=m(s[s.index>=s.index[int(len(s)*0.6)]])[2]
        print(f"  {name:<40}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{o:>6.2f}  {yr(s,2018):>+4.0f}/{yr(s,2022):>+3.0f}/{yr(s,2026):>+3.0f}  {'grn' if grn(s) else 'RED'}")


if __name__=="__main__":
    main()
