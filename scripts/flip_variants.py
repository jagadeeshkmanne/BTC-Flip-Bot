#!/usr/bin/env python3
"""flip_variants.py — test whether the bot should FLIP long<->short directly instead of
closing to flat and re-entering on separate gates.

Engine is a COPY of short_deepen_engine.run_ec3 (config D), identical sizing/stops/trailing.
ONLY the transition logic changes, controlled by `flip`:

  flip="A"  : baseline config D. Long exits to FLAT on bull-gate fail; short opens ONLY on its
              own gate (drop>10% AND daily MACD bear). Flat gap allowed.
  flip="B"  : flip-to-short on long break. When the long exits because bull gate failed, open a
              SHORT on ETH immediately (config D short sizing/stop/trail), bypassing the
              drop/MACD short gate. Short still exits on its own rules. (Symmetric: when a short
              covers on bear-clear and bull is true, go long immediately.)
  flip="C"  : full always-in. Never flat. bull true -> long BTC; bull false -> short ETH
              (bypass the drop/MACD short gate entirely). Pure reversal.
  flip="D2" : flip only when short-gate already met. When the long exits AND the short gate
              (drop>10% AND MACD bear) is ALREADY true at that bar, enter the short immediately
              (no flat gap); otherwise behave like baseline.

All variants: closed-bar signals (shift(1) already baked into bull/bear), next-bar fills,
FEE+SLIP kept, no lookahead.
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


def run_flip(flip="A", short_inst="eth", long_lev=None, lock_frac=0.33, lock_r=6.0,
             mf=12, ms=26, msig=9, confirm="macd", macd_tf="1d", drop_pct=0.10, drop_look=40,
             atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0, be_buf=0.01,
             s_atr=5.0, s_cap=0.15,
             s_lev=1.0, size_lo=0.25, size_mid=0.50, size_hi=1.0,
             spyr_k=0.0, spyr_frac=0.5, strail_k=0.0, strail_arm_R=1.0, s_reentry=False):
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
    bear=drop_arr & conf            # the OWN short gate (drop>pct AND MACD bear)
    bdd=(bc/bc.rolling(180*BPD).max().shift(1)-1).fillna(0).values
    ssize=np.where(bdd<=-0.30,size_hi,np.where(bdd<=-0.20,size_mid,size_lo))
    if long_lev is None: long_lev=np.ones(len(btc))
    bo,bh,bl,bcv=[btc[k].values for k in ("open","high","low","close")]; ba=bt.atr(btc,14).values
    if short_inst=="eth":
        so,sh,sl_,scv=[eth[k].values for k in ("open","high","low","close")]; sa=bt.atr(eth,14).values
    else:
        so,sh,sl_,scv=bo,bh,bl,bcv; sa=ba
    n=len(btc); cash=1.0; units=0.0; side=0; entry=stop=R=0.0; pyrd=parabd=lockd=False; notional0=0.0
    spyrd=False; lowS=np.inf; notional0s=0.0
    armedL=armedS=True; eq=np.ones(n)
    def mpx(i): return bcv[i] if side==1 else (scv[i] if side==-1 else bcv[i])

    def open_short(i):
        # open a config-D short at next-bar open; returns local state via nonlocal
        nonlocal cash,units,entry,stop,R,side,armedS,notional0s,spyrd,lowS
        E=cash; oN=so[i+1]; st=min(scv[i]+s_atr*sa[i],scv[i]*(1+s_cap)); ent=oN*(1-SLIP)
        if st-ent>0:
            notional=ssize[i]*s_lev*E; units=-notional/ent; cash-=units*ent+notional*FEE
            entry=ent; stop=st; R=st-ent; side=-1; armedS=False
            notional0s=notional; spyrd=False; lowS=np.inf
            return True
        return False

    def open_long(i):
        nonlocal cash,units,entry,stop,R,side,armedL,notional0,pyrd,parabd,lockd
        E=cash; oN=bo[i+1]; st=max(bcv[i]-atr_mult*ba[i],bcv[i]*(1-sl_cap)); ent=oN*(1+SLIP)
        if ent-st>0:
            notional0=E*long_lev[i]; units=notional0/ent; cash-=units*ent+notional0*FEE
            entry=ent; stop=st; R=ent-st; side=1; pyrd=parabd=lockd=False; armedL=False
            return True
        return False

    for i in range(16,n-1):
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        covered_this_bar=False
        long_broke=False       # long exited THIS bar because bull gate failed (trend flip down)
        short_cleared=False    # short covered THIS bar because bear gate cleared
        if side==1:
            lN=bl[i+1]; oN=bo[i+1]; cN=bcv[i+1]
            if lN<=stop:
                fpx=stop*(1-SLIP); cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0; pyrd=parabd=lockd=False
            elif not bull[i]:
                fpx=oN*(1-SLIP); cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0; pyrd=parabd=lockd=False
                long_broke=True
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
            if sl_[i]<lowS: lowS=sl_[i]
            hN=sh[i+1]; oN=so[i+1]; cN=scv[i+1]
            tstop=stop
            if strail_k>0:
                prof_c=(entry-scv[i])/R
                if prof_c>=strail_arm_R:
                    cand=lowS+strail_k*sa[i]
                    tstop=min(stop,cand)
            eff_stop=tstop
            if hN>=eff_stop:
                fpx=eff_stop*(1+SLIP); cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0; covered_this_bar=True
                stop=eff_stop
            elif not bear[i]:
                fpx=oN*(1+SLIP); cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0; covered_this_bar=True
                short_cleared=True
            else:
                stop=eff_stop
                prof=(entry-cN)/R
                if prof>=be_r: stop=min(stop,entry*(1-be_buf))
                if spyr_k>0 and not spyrd:
                    fall=(entry-cN)/R
                    if fall>=spyr_k:
                        addn=spyr_frac*notional0s; au=addn/cN
                        cash+=au*cN-addn*FEE; units-=au; spyrd=True; stop=min(stop,entry)

        # ---- TRANSITION LOGIC ----
        if side==0:
            if flip=="C":
                # always-in: bull -> long, else short (bypass own short gate)
                if bull[i]:
                    if armedL: open_long(i)
                else:
                    open_short(i)   # no gate, no armed requirement (pure reversal)
            elif flip=="B":
                # flip-to-short the moment the uptrend broke this bar (bypass drop/MACD gate)
                if long_broke:
                    if not open_short(i):
                        # short couldn't open; fall back to normal logic next bars
                        pass
                elif short_cleared and bull[i] and armedL:
                    open_long(i)    # symmetric flip back to long
                else:
                    # normal baseline entries
                    allowS = armedS or (s_reentry and not covered_this_bar)
                    if bull[i] and armedL:
                        open_long(i)
                    elif bear[i] and allowS:
                        open_short(i)
            elif flip=="D2":
                # flip only when the OWN short gate is already true at this bar (no flat gap)
                if long_broke and bear[i]:
                    open_short(i)
                else:
                    allowS = armedS or (s_reentry and not covered_this_bar)
                    if bull[i] and armedL:
                        open_long(i)
                    elif bear[i] and allowS:
                        open_short(i)
            else:  # flip=="A" baseline
                allowS = armedS or (s_reentry and not covered_this_bar)
                if bull[i] and armedL:
                    open_long(i)
                elif bear[i] and allowS:
                    open_short(i)
        eq[i+1]=cash+units*mpx(i+1)
    return pd.Series(eq,index=pd.to_datetime(btc["timestamp"])).iloc[16:]


def D_args():
    return dict(short_inst="eth", size_lo=0.5, size_mid=1.0, size_hi=1.0,
                drop_look=35, s_atr=6.0, s_cap=0.20, strail_k=3.5, strail_arm_R=1.0)


def llev_arr():
    btc,_=load4h("BTCUSDT"); eth,_=load4h("ETHUSDT")
    common=pd.Index(btc["timestamp"]).intersection(pd.Index(eth["timestamp"]))
    b=btc[btc["timestamp"].isin(common)].reset_index(drop=True); c=b["close"]
    adx=bt.adx(b,14).shift(1).fillna(0).values
    egap=((bt.ema(c,50)-bt.ema(c,200))/bt.ema(c,200)).shift(1).fillna(0).values
    conv=np.clip(adx/35.0,0,1)*0.5+np.clip(egap/0.12,0,1)*0.5
    return 1.0+1.5*conv


if __name__=="__main__":
    from backtest_v2_eth_conviction import m, yr, grn
    llev=llev_arr()
    print("="*96)
    print("FLIP VARIANTS — long=BTC, short=ETH, config D sizing/stops/trail; ONLY transition logic changes")
    print("="*96)
    print(f"  {'variant':<34}{'CAGR':>6}{'DD':>7}{'r/DD':>6}{'OOS':>6}{'2018':>7}{'2022':>7}{'2026':>7}{'minYr':>7}  grn")
    rows={}
    for name,flip in [("A baseline (close-to-flat) =D","A"),
                      ("B flip-to-short on long break","B"),
                      ("C full always-in flip","C"),
                      ("D2 flip only when gate ready","D2")]:
        s=run_flip(flip=flip, long_lev=llev, **D_args())
        rows[flip]=s
        cg,dd,rr=m(s)
        o=m(s[s.index>=s.index[int(len(s)*0.6)]])[2]
        mn=min(yr(s,y) for y in range(2018,2027))
        print(f"  {name:<34}{cg*100:>5.0f}%{dd*100:>6.1f}%{rr:>6.2f}{o:>6.2f}"
              f"{yr(s,2018):>+6.0f}%{yr(s,2022):>+6.0f}%{yr(s,2026):>+6.0f}%{mn:>+6.0f}%  {'grn' if grn(s) else 'RED'}")
    print("\n  FULL YEAR-BY-YEAR return%:")
    print(f"    {'variant':<8}" + "".join(f"{y:>8}" for y in range(2018,2027)))
    for flip in ["A","B","C","D2"]:
        s=rows[flip]
        print(f"    {flip:<8}" + "".join(f"{yr(s,y):>+7.0f}%" for y in range(2018,2027)))
