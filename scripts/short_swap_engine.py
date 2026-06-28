#!/usr/bin/env python3
"""short_swap_engine.py — COPY of run_ec3 (config D engine) with a SWAPPABLE short instrument.

The LONG always trades BTC, the SIGNAL is always BTC's bear signal, and ALL sizing/stops/
trailing knobs are identical to config D. The ONLY thing that changes is WHICH coin the short
leg sells. The short instrument is loaded from the bt_helpers cache (bt.load(sym,"4h")):
SOL/BNB only exist there from 2021-01-01, so everything is aligned to the COMMON window where
BTC + the chosen short coin both have data (2021-2026). The shorted coin uses its OWN ATR for
its stop/trail. Honesty rules unchanged: closed-bar signals shift(1), next-bar fills, FEE+SLIP.

This is the apples-to-apples comparator for "short ETH vs short SOL vs short BNB" over 2021-2026.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_btclong_ethshort import load4h, dbull_map

FEE=bt.FEE_PCT; SLIP=bt.SLIP_PCT; BPD=6

_SYM={"eth":"ETHUSDT","sol":"SOLUSDT","bnb":"BNBUSDT","btc":"BTCUSDT"}


def run_swap(short_inst="eth", long_lev=None, lock_frac=0.33, lock_r=6.0, mf=12, ms=26, msig=9,
             confirm="macd", macd_tf="1d", drop_pct=0.10, drop_look=35,
             atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0, be_buf=0.01,
             s_atr=6.0, s_cap=0.20,
             s_lev=1.0, size_lo=0.5, size_mid=1.0, size_hi=1.0,
             spyr_k=0.0, spyr_frac=0.5,
             strail_k=3.5, strail_arm_R=1.0,
             s_reentry=False,
             blend=None):
    """blend: optional list of short coins to short equally (e.g. ['sol','eth']); overrides short_inst."""
    # --- BTC from engine load4h (full-history signal source); short coin(s) from bt.load cache ---
    btc,btcd=load4h("BTCUSDT")
    shorts = blend if blend else [short_inst]
    scoins = {}
    for sc in shorts:
        d = bt.load(_SYM[sc], "4h").copy()
        scoins[sc] = d
    # align BTC to the intersection of all short-coin timestamps (common 2021-2026 window)
    common = pd.Index(btc["timestamp"])
    for d in scoins.values():
        common = common.intersection(pd.Index(d["timestamp"]))
    btc = btc[btc["timestamp"].isin(common)].reset_index(drop=True)
    for sc in scoins:
        scoins[sc] = scoins[sc][scoins[sc]["timestamp"].isin(common)].reset_index(drop=True)

    bc=btc["close"]
    bull=((bt.ema(bc,50)>bt.ema(bc,200)).values & dbull_map(btc,btcd) & (bc>bt.sma(bc,9*30*BPD).shift(1)).fillna(False).values)
    parab=(bc>2.2*bt.sma(bc,140*BPD).shift(1)).fillna(False).values
    ts4=pd.to_datetime(btc["timestamp"])
    if macd_tf=="4h":
        ml4=bt.ema(bc,mf)-bt.ema(bc,ms); sig4=bt.ema(ml4,msig)
        macd_bear=(ml4<sig4).shift(1).fillna(False).values
    else:
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
    ssize=np.where(bdd<=-0.30,size_hi,np.where(bdd<=-0.20,size_mid,size_lo))
    if long_lev is None: long_lev=np.ones(len(btc))
    bo,bh,bl,bcv=[btc[k].values for k in ("open","high","low","close")]; ba=bt.atr(btc,14).values

    # short instrument arrays (single or equal-weight blend). For a blend we run independent
    # short legs sharing the same signal/sizing; each leg gets 1/k of the short notional.
    sc_names=list(scoins.keys()); k=len(sc_names)
    SO={};SH={};SL={};SC={};SA={}
    for sc in sc_names:
        d=scoins[sc]
        SO[sc],SH[sc],SL[sc],SC[sc]=[d[c].values for c in ("open","high","low","close")]
        SA[sc]=bt.atr(d,14).values

    n=len(btc); cash=1.0
    # long state
    lunits=0.0; lside=0; lentry=lstop=lR=0.0; pyrd=parabd=lockd=False; notional0=0.0
    armedL=True
    # short state (per-coin so a blend tracks each leg's stop/trail independently)
    sst={sc:dict(units=0.0,side=0,entry=0.0,stop=0.0,R=0.0,lowS=np.inf,n0s=0.0,spyrd=False) for sc in sc_names}
    armedS=True
    eq=np.ones(n)

    def mark(i):
        v=cash + (lunits*bcv[i] if lside==1 else 0.0)
        for sc in sc_names:
            st=sst[sc]
            if st["side"]==-1: v+=st["units"]*SC[sc][i]
        return v

    for i in range(16,n-1):
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        covered_this_bar=False
        # ---- LONG (BTC) ----
        if lside==1:
            lN=bl[i+1]; oN=bo[i+1]; cN=bcv[i+1]
            if lN<=lstop:
                fpx=lstop*(1-SLIP); cash+=lunits*fpx-abs(lunits)*fpx*FEE; lunits=0.0; lside=0; pyrd=parabd=lockd=False
            elif not bull[i]:
                fpx=oN*(1-SLIP); cash+=lunits*fpx-abs(lunits)*fpx*FEE; lunits=0.0; lside=0; pyrd=parabd=lockd=False
            else:
                prof=(cN-lentry)/lR
                if prof>=be_r: lstop=max(lstop,lentry*(1+be_buf))
                if not pyrd and prof>=pyr_r:
                    addn=pyr_frac*notional0; au=addn/cN; cash-=au*cN+addn*FEE; lunits+=au; pyrd=True; lstop=max(lstop,lentry)
                if lock_frac>0 and not lockd and prof>=lock_r:
                    fpx=oN*(1-SLIP); cl=lock_frac*lunits; cash+=cl*fpx*(1-FEE); lunits-=cl; lockd=True
                if not parabd and parab[i]:
                    fpx=oN*(1-SLIP); cl=0.5*lunits; cash+=cl*fpx*(1-FEE); lunits-=cl; parabd=True
        # ---- SHORT legs (each coin) ----
        for sc in sc_names:
            st=sst[sc]
            if st["side"]!=-1: continue
            so,sh,sl_,scv,sa=SO[sc],SH[sc],SL[sc],SC[sc],SA[sc]
            if sl_[i]<st["lowS"]: st["lowS"]=sl_[i]
            hN=sh[i+1]; oN=so[i+1]; cN=scv[i+1]
            tstop=st["stop"]
            if strail_k>0:
                prof_c=(st["entry"]-scv[i])/st["R"]
                if prof_c>=strail_arm_R:
                    cand=st["lowS"]+strail_k*sa[i]
                    tstop=min(st["stop"],cand)
            eff_stop=tstop
            if hN>=eff_stop:
                fpx=eff_stop*(1+SLIP); cash+=st["units"]*fpx-abs(st["units"])*fpx*FEE
                st["units"]=0.0; st["side"]=0; covered_this_bar=True; st["stop"]=eff_stop
            elif not bear[i]:
                fpx=oN*(1+SLIP); cash+=st["units"]*fpx-abs(st["units"])*fpx*FEE
                st["units"]=0.0; st["side"]=0; covered_this_bar=True
            else:
                st["stop"]=eff_stop
                prof=(st["entry"]-cN)/st["R"]
                if prof>=be_r: st["stop"]=min(st["stop"],st["entry"]*(1-be_buf))
                if spyr_k>0 and not st["spyrd"]:
                    fall=(st["entry"]-cN)/st["R"]
                    if fall>=spyr_k:
                        addn=spyr_frac*st["n0s"]; au=addn/cN
                        cash+=au*cN-addn*FEE; st["units"]-=au; st["spyrd"]=True; st["stop"]=min(st["stop"],st["entry"])
        # ---- ENTRIES (single position at a time, like config D) ----
        any_short = any(sst[sc]["side"]==-1 for sc in sc_names)
        if lside==0 and not any_short:
            allowS = armedS or (s_reentry and not covered_this_bar)
            if bull[i] and armedL:
                E=cash; oN=bo[i+1]; stp=max(bcv[i]-atr_mult*ba[i],bcv[i]*(1-sl_cap)); ent=oN*(1+SLIP)
                if ent-stp>0:
                    notional0=E*long_lev[i]; lunits=notional0/ent; cash-=lunits*ent+notional0*FEE
                    lentry=ent; lstop=stp; lR=ent-stp; lside=1; pyrd=parabd=lockd=False; armedL=False
            elif bear[i] and allowS:
                E=cash
                for sc in sc_names:
                    so,scv,sa=SO[sc],SC[sc],SA[sc]
                    oN=so[i+1]; stp=min(scv[i]+s_atr*sa[i],scv[i]*(1+s_cap)); ent=oN*(1-SLIP)
                    if stp-ent>0:
                        notional=(ssize[i]*s_lev*E)/k; u=-notional/ent; cash-=u*ent+notional*FEE
                        st=sst[sc]; st["entry"]=ent; st["stop"]=stp; st["R"]=stp-ent; st["side"]=-1
                        st["units"]=u; st["n0s"]=notional; st["spyrd"]=False; st["lowS"]=np.inf
                if any(sst[sc]["side"]==-1 for sc in sc_names): armedS=False
        eq[i+1]=mark(i+1)
    return pd.Series(eq,index=pd.to_datetime(btc["timestamp"])).iloc[16:]
