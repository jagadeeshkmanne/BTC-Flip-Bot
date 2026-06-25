#!/usr/bin/env python3
"""backtest_entry_filters.py — test ENTRY confirmation filters on the deployed conviction base.

Copy of run_ec with an extra LONG-entry gate `efilt` (computed shift(1), no lookahead).
Goal: skip chop/whipsaw long entries (red-month source) WITHOUT missing big trends.
WIN = improves ret/DD AND every year 2018..2026 >= -1% AND holds/improves OOS(last40%).
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_btclong_ethshort import load4h, dbull_map
from backtest_myv3_shorts import m

FEE=bt.FEE_PCT; SLIP=bt.SLIP_PCT; BPD=6


def _deployed_llev(btc):
    """Reproduce btcv2_eq conviction leverage 1.0 + 1.5*conv (shift(1), no lookahead)."""
    c=btc["close"]
    adx=bt.adx(btc,14).shift(1).fillna(0).values
    egap=((bt.ema(c,50)-bt.ema(c,200))/bt.ema(c,200)).shift(1).fillna(0).values
    conv=np.clip(adx/35.0,0,1)*0.5+np.clip(egap/0.12,0,1)*0.5
    return 1.0+1.5*conv


def run_ef(efilt_fn=None, cooldown=0,
           lock_frac=0.33, lock_r=6.0, mf=12, ms=26, msig=9,
           macd_tf="1d", drop_pct=0.10, drop_look=40,
           atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0, be_buf=0.01,
           s_atr=5.0, s_cap=0.15):
    """Deployed base (short ETH + conviction lev). efilt_fn(btc,btcd,ts4)->bool array gates LONG entry.
    cooldown = bars to wait after a long stop-out before re-entering."""
    btc,btcd=load4h("BTCUSDT"); eth,ethd=load4h("ETHUSDT")
    common=pd.Index(btc["timestamp"]).intersection(pd.Index(eth["timestamp"]))
    btc=btc[btc["timestamp"].isin(common)].reset_index(drop=True); eth=eth[eth["timestamp"].isin(common)].reset_index(drop=True)
    bc=btc["close"]; ec=eth["close"]
    long_lev=_deployed_llev(btc)
    bull=((bt.ema(bc,50)>bt.ema(bc,200)).values & dbull_map(btc,btcd) & (bc>bt.sma(bc,9*30*BPD).shift(1)).fillna(False).values)
    parab=(bc>2.2*bt.sma(bc,140*BPD).shift(1)).fillna(False).values
    ts4=pd.to_datetime(btc["timestamp"])
    mld=bt.ema(btcd["close"],mf)-bt.ema(btcd["close"],ms); sigd=bt.ema(mld,msig)
    dmb=(mld<sigd).shift(1).fillna(False)
    macd_bear=pd.merge_asof(pd.DataFrame({"ts":ts4}).sort_values("ts"),
        pd.DataFrame({"ts":pd.to_datetime(btcd["timestamp"]),"b":dmb.values}).sort_values("ts"),
        on="ts",direction="backward")["b"].fillna(False).astype(bool).values
    drop_arr=((bc/bc.rolling(drop_look*BPD).max().shift(1)-1)<-drop_pct).fillna(False).values
    bear=drop_arr & macd_bear
    bdd=(bc/bc.rolling(180*BPD).max().shift(1)-1).fillna(0).values
    ssize=np.where(bdd<=-0.30,1.0,np.where(bdd<=-0.20,0.50,0.25))

    # ENTRY FILTER (all shift(1), past-only)
    efilt=np.ones(len(btc),bool) if efilt_fn is None else efilt_fn(btc,btcd,ts4)

    bo,bh,bl,bcv=[btc[k].values for k in ("open","high","low","close")]; ba=bt.atr(btc,14).values
    so,sh,scv=[eth[k].values for k in ("open","high","close")]; sa=bt.atr(eth,14).values
    n=len(btc); cash=1.0; units=0.0; side=0; entry=stop=R=0.0; pyrd=parabd=lockd=False; notional0=0.0
    armedL=armedS=True; eq=np.ones(n); cd_until=-1
    def mpx(i): return bcv[i] if side==1 else (scv[i] if side==-1 else bcv[i])
    for i in range(16,n-1):
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        if side==1:
            lN=bl[i+1]; oN=bo[i+1]; cN=bcv[i+1]
            if lN<=stop:
                fpx=stop*(1-SLIP); cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0; pyrd=parabd=lockd=False
                cd_until=i+cooldown
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
            if bull[i] and armedL and efilt[i] and i>cd_until:
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
def grn(s): return all(yr(s,y)>=-1.0 for y in range(2018,2027))


# ---- entry filters (shift(1) everywhere) ----
def f_adx(thr):
    def g(btc,btcd,ts4):
        return (bt.adx(btc,14).shift(1).fillna(0).values>=thr)
    return g

def f_rsi(thr):
    def g(btc,btcd,ts4):
        return (bt.rsi(btc["close"],14).shift(1).fillna(50).values>=thr)
    return g

def f_roc(look,thr):
    def g(btc,btcd,ts4):
        c=btc["close"]; return ((c/c.shift(look)-1).shift(1).fillna(0).values>=thr)
    return g

def f_fastema(p):
    def g(btc,btcd,ts4):
        c=btc["close"]; return (c.shift(1)>bt.ema(c,p).shift(1)).fillna(False).values
    return g

def f_vol_below(look,q):
    """realized vol percentile-rank below q (only enter in calm/normal vol)."""
    def g(btc,btcd,ts4):
        c=btc["close"]; rv=c.pct_change().rolling(look).std()
        rank=rv.rolling(180*BPD,min_periods=30*BPD).rank(pct=True)
        return (rank.shift(1).fillna(0).values<=q)
    return g

def f_vol_above(look,q):
    def g(btc,btcd,ts4):
        c=btc["close"]; rv=c.pct_change().rolling(look).std()
        rank=rv.rolling(180*BPD,min_periods=30*BPD).rank(pct=True)
        return (rank.shift(1).fillna(1).values>=q)
    return g

def f_weekly(fast,slow):
    """weekly EMA trend confirm (resample to 1W, EMA cross, merge_asof backward, shift)."""
    def g(btc,btcd,ts4):
        w=btc.assign(ts=pd.to_datetime(btc["timestamp"])).set_index("ts")["close"].resample("1W").last().dropna()
        wb=(bt.ema(w,fast)>bt.ema(w,slow)).shift(1).fillna(False)
        return pd.merge_asof(pd.DataFrame({"ts":ts4}).sort_values("ts"),
            pd.DataFrame({"ts":wb.index,"b":wb.values}).sort_values("ts"),
            on="ts",direction="backward")["b"].fillna(False).astype(bool).values
    return g

def combine(*fns):
    def g(btc,btcd,ts4):
        out=np.ones(len(btc),bool)
        for f in fns: out=out & f(btc,btcd,ts4)
        return out
    return g


def report(name,s,base):
    cg,dd,rr=m(s); o=m(s[s.index>=s.index[int(len(s)*0.6)]])
    bcg,bdd,brr=base
    flag="WIN" if (rr>brr and grn(s) and o[2]>=brr*0.98) else ("grn" if grn(s) else "RED")
    print(f"  {name:<34}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{o[2]:>6.2f}  {flag}")
    return rr,o[2],grn(s)


def main():
    base=run_ef(None)
    bcg,bdd,brr=m(base); bo=m(base[base.index>=base.index[int(len(base)*0.6)]])
    print("="*70)
    print("ENTRY FILTERS on deployed base (short ETH + conviction)")
    print("="*70)
    print(f"  {'config':<34}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  flag")
    print(f"  {'BASE (no filter)':<34}{bcg*100:>5.0f}%{bdd*100:>5.0f}%{brr:>6.2f}{bo[2]:>6.2f}  base")
    print("  -- isolation --")
    tests=[
        ("ADX>=20 at entry",f_adx(20)),
        ("ADX>=25 at entry",f_adx(25)),
        ("ADX>=30 at entry",f_adx(30)),
        ("RSI>=50 at entry",f_rsi(50)),
        ("RSI>=55 at entry",f_rsi(55)),
        ("ROC(30b)>=0",f_roc(30,0.0)),
        ("ROC(60b)>=0",f_roc(60,0.0)),
        ("close>EMA20",f_fastema(20)),
        ("close>EMA34",f_fastema(34)),
        ("vol-rank<=0.7 (calm)",f_vol_below(6,0.7)),
        ("vol-rank<=0.5 (calm)",f_vol_below(6,0.5)),
        ("vol-rank>=0.3",f_vol_above(6,0.3)),
        ("weekly EMA10>20",f_weekly(10,20)),
        ("weekly EMA5>13",f_weekly(5,13)),
    ]
    for nm,fn in tests:
        report(nm,run_ef(fn),(bcg,bdd,brr))
    print("  -- cooldown (no efilt) --")
    for k in (6,12,18,30):
        report(f"cooldown {k} bars",run_ef(None,cooldown=k),(bcg,bdd,brr))


if __name__=="__main__":
    main()
