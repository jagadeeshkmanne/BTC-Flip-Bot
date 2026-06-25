#!/usr/bin/env python3
"""short_deepen_engine.py — COPY of run_ec2 with short-side DEEPENING knobs added,
one mechanism per knob, all OFF by default (so run_ec3 with defaults == run_ec2 / CONFIG C).

DEEPENING KNOBS (all causal, closed-bar signals shift(1), next-bar fills, FEE/SLIP):
  spyr_k    : SHORT PYRAMID. if >0, when short and price has fallen spyr_k*R further from
              entry (and not yet pyramided), ADD spyr_frac*notional to the short and move
              stop to entry. (long side already pyramids; short never has.)
  spyr_frac : add size for the short pyramid (e.g. 0.5)
  strail_k  : TRAILING SHORT EXIT. if >0, trail a stop at (lowest-low-since-entry + strail_k*ATR)
              using ONLY past lows (lowS tracked through bar i, applied to bar i+1). Armed only
              after the short reaches strail_arm_R profit (so it rides sustained bears further).
              Tightens stop (never loosens). Works alongside the existing 'bear ends' exit.
  strail_arm_R : profit in R required before the trail arms (e.g. 1.0)
  s_reentry : SHORT RE-ENTRY. if True, after covering, allow immediate re-short on the same
              bar's signal even if armedS would block it, PROVIDED still below 35d-high gate
              AND daily MACD still bearish (both already in `bear`, so we just don't require
              armedS). Guarded so we never re-short on the SAME bar we covered via 'bear ends'.
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


def run_ec3(short_inst="eth", long_lev=None, lock_frac=0.33, lock_r=6.0, mf=12, ms=26, msig=9,
            confirm="macd", macd_tf="1d", drop_pct=0.10, drop_look=40,
            atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0, be_buf=0.01,
            s_atr=5.0, s_cap=0.15,
            s_lev=1.0, size_lo=0.25, size_mid=0.50, size_hi=1.0,
            # --- deepening knobs (default OFF -> identical to run_ec2) ---
            spyr_k=0.0, spyr_frac=0.5,
            strail_k=0.0, strail_arm_R=1.0,
            s_reentry=False):
    btc,btcd=load4h("BTCUSDT"); eth,ethd=load4h("ETHUSDT")
    common=pd.Index(btc["timestamp"]).intersection(pd.Index(eth["timestamp"]))
    btc=btc[btc["timestamp"].isin(common)].reset_index(drop=True); eth=eth[eth["timestamp"].isin(common)].reset_index(drop=True)
    bc=btc["close"]; ec=eth["close"]
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
    if short_inst=="eth":
        so,sh,sl_,scv=[eth[k].values for k in ("open","high","low","close")]; sa=bt.atr(eth,14).values
    else:
        so,sh,sl_,scv=bo,bh,bl,bcv; sa=ba
    n=len(btc); cash=1.0; units=0.0; side=0; entry=stop=R=0.0; pyrd=parabd=lockd=False; notional0=0.0
    spyrd=False; lowS=np.inf   # short pyramid flag, lowest-low since short entry
    armedL=armedS=True; eq=np.ones(n)
    def mpx(i): return bcv[i] if side==1 else (scv[i] if side==-1 else bcv[i])
    for i in range(16,n-1):
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        covered_this_bar=False
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
            # update lowest-low using ONLY closed bar i (causal); applies to next-bar trail
            if sl_[i]<lowS: lowS=sl_[i]
            hN=sh[i+1]; oN=so[i+1]; cN=scv[i+1]
            # compute trailing stop from data known at bar i (lowS through i, ATR at i)
            tstop=stop
            if strail_k>0:
                prof_c=(entry-scv[i])/R
                if prof_c>=strail_arm_R:
                    cand=lowS+strail_k*sa[i]
                    tstop=min(stop,cand)   # tighten only
            eff_stop=tstop
            if hN>=eff_stop:
                fpx=eff_stop*(1+SLIP); cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0; covered_this_bar=True
                stop=eff_stop
            elif not bear[i]:
                fpx=oN*(1+SLIP); cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0; covered_this_bar=True
            else:
                stop=eff_stop
                prof=(entry-cN)/R
                if prof>=be_r: stop=min(stop,entry*(1-be_buf))
                # SHORT PYRAMID: add to short on a further k*R fall, move stop to entry
                if spyr_k>0 and not spyrd:
                    fall=(entry-cN)/R
                    if fall>=spyr_k:
                        addn=spyr_frac*notional0s; au=addn/cN
                        cash+=au*cN-addn*FEE   # selling more (short): receive proceeds, pay fee
                        units-=au; spyrd=True; stop=min(stop,entry)
        if side==0:
            allowS = armedS or (s_reentry and not covered_this_bar)
            if bull[i] and armedL:
                E=cash; oN=bo[i+1]; st=max(bcv[i]-atr_mult*ba[i],bcv[i]*(1-sl_cap)); ent=oN*(1+SLIP)
                if ent-st>0:
                    notional0=E*long_lev[i]; units=notional0/ent; cash-=units*ent+notional0*FEE; entry=ent; stop=st; R=ent-st; side=1; pyrd=parabd=lockd=False; armedL=False
            elif bear[i] and allowS:
                E=cash; oN=so[i+1]; st=min(scv[i]+s_atr*sa[i],scv[i]*(1+s_cap)); ent=oN*(1-SLIP)
                if st-ent>0:
                    notional=ssize[i]*s_lev*E; units=-notional/ent; cash-=units*ent+notional*FEE
                    entry=ent; stop=st; R=st-ent; side=-1; armedS=False
                    notional0s=notional; spyrd=False; lowS=np.inf
        eq[i+1]=cash+units*mpx(i+1)
    return pd.Series(eq,index=pd.to_datetime(btc["timestamp"])).iloc[16:]
