#!/usr/bin/env python3
"""short_antibottom.py — COPY of run_ec3 (config D engine) with ANTI-BOTTOM short-entry
filters added. ONLY the bear-ENTRY gate is touched; long side and all exits are identical
to run_ec3. All filters causal (shift(1) / past-only), next-bar fills, FEE/SLIP kept.

Anti-bottom gate parameters (all OFF by default -> identical to config D):
  ab_rsi_inst : "eth" or "btc" instrument whose RSI(14) gates the short
  ab_rsi_min  : skip the short if that RSI < ab_rsi_min (don't short the deeply oversold).
                None = off.
  ab_ext_inst : instrument for the EMA-extension gate
  ab_ext_ema  : EMA length (e.g. 20 or 50)
  ab_ext_max  : skip the short if (close/EMA - 1) < -ab_ext_max  (price already > X% below
                EMA = overextended down = bounce risk). None = off.
  ab_pullback : EMA length for PULLBACK entry. When set, after bear fires, only short once
                price has bounced back UP to within / above this EMA (short a lower-high).
                None = off.  Uses the short instrument's price + EMA, causal.
  ab_newlow_N : require a fresh confirmation: short only if the closed bar made a new N-bar
                low (momentum still down). None = off. Uses short instrument low.
  scale2_k    : 2-step SCALE-IN. short HALF (scale2_frac0) on the signal; add the rest only
                if price falls another scale2_k*ATR. Never adds beyond the first step / to a
                loser. 0 = off.
  scale2_frac0: initial fraction of target size shorted on signal (e.g. 0.5)

The bear-entry RSI/EMA/extension/newlow gates use the SHORT instrument (eth) unless an
inst override says btc; everything past-only (computed on closed bars, applied next bar).
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


def run_ab(short_inst="eth", long_lev=None, lock_frac=0.33, lock_r=6.0, mf=12, ms=26, msig=9,
           confirm="macd", macd_tf="1d", drop_pct=0.10, drop_look=40,
           atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0, be_buf=0.01,
           s_atr=5.0, s_cap=0.15,
           s_lev=1.0, size_lo=0.25, size_mid=0.50, size_hi=1.0,
           strail_k=0.0, strail_arm_R=1.0,
           # --- anti-bottom short-entry filters (default OFF -> identical to run_ec3) ---
           ab_rsi_inst="eth", ab_rsi_min=None,
           ab_ext_inst="eth", ab_ext_ema=20, ab_ext_max=None,
           ab_pullback=None, ab_pull_inst="eth",
           ab_newlow_N=None, ab_newlow_inst="eth",
           scale2_k=0.0, scale2_frac0=0.5):
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

    # ---- ANTI-BOTTOM gates (all causal: indicator on bar i decides the i->i+1 fill) ----
    def inst_close(name): return ec if name=="eth" else bc
    def inst_low(df_name): return (eth["low"] if df_name=="eth" else btc["low"])
    # RSI gate: pass if RSI(i) >= ab_rsi_min (computed on closed bars, shifted 1)
    rsi_pass=np.ones(len(bc),bool)
    if ab_rsi_min is not None:
        r=bt.rsi(inst_close(ab_rsi_inst),14).shift(1).fillna(50).values
        rsi_pass = r >= ab_rsi_min
    # extension gate: pass if (close/EMA-1) >= -ab_ext_max  (NOT too far below EMA)
    ext_pass=np.ones(len(bc),bool)
    if ab_ext_max is not None:
        cc=inst_close(ab_ext_inst); e=bt.ema(cc,ab_ext_ema)
        ext=((cc/e-1).shift(1).fillna(0)).values
        ext_pass = ext >= -ab_ext_max
    # new-low gate: pass if closed bar low is the min of trailing N closed bars
    newlow_pass=np.ones(len(bc),bool)
    if ab_newlow_N is not None:
        lo=inst_low(ab_newlow_inst)
        rollmin=lo.rolling(ab_newlow_N).min()
        newlow_pass=(lo<=rollmin).shift(1).fillna(False).values
    # pullback EMA (for pullback entry): short-instrument close vs EMA, closed-bar
    pull_ema=None
    if ab_pullback is not None:
        cc=inst_close(ab_pull_inst); pe=bt.ema(cc,ab_pullback)
        # "bounced back" = close >= EMA on the closed bar
        pull_ema=(cc>=pe).shift(1).fillna(False).values

    n=len(btc); cash=1.0; units=0.0; side=0; entry=stop=R=0.0; pyrd=parabd=lockd=False; notional0=0.0
    lowS=np.inf; sc2_done=False; sc2_target=0.0
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
                fpx=eff_stop*(1+SLIP); cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0
                stop=eff_stop
            elif not bear[i]:
                fpx=oN*(1+SLIP); cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0
            else:
                stop=eff_stop
                prof=(entry-cN)/R
                if prof>=be_r: stop=min(stop,entry*(1-be_buf))
                # 2-step SCALE-IN: add remainder only if price fell scale2_k*ATR further
                if scale2_k>0 and not sc2_done:
                    fall=(entry-cN)/R
                    if fall>=scale2_k:
                        addn=(1.0-scale2_frac0)*sc2_target; au=addn/cN
                        cash+=au*cN-addn*FEE  # short more: receive proceeds, pay fee
                        units-=au; sc2_done=True
        if side==0:
            if bull[i] and armedL:
                E=cash; oN=bo[i+1]; st=max(bcv[i]-atr_mult*ba[i],bcv[i]*(1-sl_cap)); ent=oN*(1+SLIP)
                if ent-st>0:
                    notional0=E*long_lev[i]; units=notional0/ent; cash-=units*ent+notional0*FEE; entry=ent; stop=st; R=ent-st; side=1; pyrd=parabd=lockd=False; armedL=False
            elif bear[i] and armedS:
                # ----- ANTI-BOTTOM gate evaluated at entry decision (bar i) -----
                gate = rsi_pass[i] and ext_pass[i] and newlow_pass[i]
                if ab_pullback is not None:
                    gate = gate and pull_ema[i]
                if gate:
                    E=cash; oN=so[i+1]; st=min(scv[i]+s_atr*sa[i],scv[i]*(1+s_cap)); ent=oN*(1-SLIP)
                    if st-ent>0:
                        full=ssize[i]*s_lev*E
                        if scale2_k>0:
                            sc2_target=full; sc2_done=False; notional=scale2_frac0*full
                        else:
                            notional=full
                        units=-notional/ent; cash-=units*ent+notional*FEE
                        entry=ent; stop=st; R=st-ent; side=-1; armedS=False
                        lowS=np.inf
        eq[i+1]=cash+units*mpx(i+1)
    return pd.Series(eq,index=pd.to_datetime(btc["timestamp"])).iloc[16:]
