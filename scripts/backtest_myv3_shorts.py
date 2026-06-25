#!/usr/bin/env python3
"""backtest_myv3_shorts.py — add a SHORT engine to my-V3 and test honestly over full history.

Mirrors the long machinery on the short side: short the BEAR regime (4h EMA50<200 AND prior-day
EMA50<200), structural stop = entry + atr_mult*ATR (capped), break-even at +be_r*R, pyramid at
+pyr_r*R (borrowed), exit on stop (intrabar high>=stop) or regime no longer bear (next open).
Unified signed cash/units engine. Compares LONG-only (base) vs LONG+SHORT vs SHORT-only.
Sanity gate: long-only path must reproduce the known base (CAGR 73%, DD -31%).
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT


def regimes():
    raw = pd.read_csv(os.path.join(bt.CACHE, "BTCUSDT_1h_binance_volume.csv"), parse_dates=["timestamp"])
    df = raw.set_index("timestamp").resample("4h").agg(
        {"open":"first","high":"max","low":"min","close":"last"}).dropna().reset_index()
    dd = raw.set_index("timestamp").resample("1D").agg({"close":"last"}).dropna().reset_index()
    d_up = (bt.ema(dd["close"],50) > bt.ema(dd["close"],200)).shift(1).fillna(False).astype(bool)
    d_dn = (bt.ema(dd["close"],50) < bt.ema(dd["close"],200)).shift(1).fillna(False).astype(bool)
    ts = pd.to_datetime(df["timestamp"])
    def mp(src):
        return pd.merge_asof(pd.DataFrame({"ts":ts}).sort_values("ts"),
            pd.DataFrame({"ts":pd.to_datetime(dd["timestamp"]),"b":src.values}).sort_values("ts"),
            on="ts", direction="backward")["b"].fillna(False).astype(bool).values
    f_up = (bt.ema(df["close"],50) > bt.ema(df["close"],200)).values
    f_dn = (bt.ema(df["close"],50) < bt.ema(df["close"],200)).values
    bull = f_up & mp(d_up)
    bear = f_dn & mp(d_dn)
    return df, bull, bear


def run(df, bull, bear, allow_long=True, allow_short=False,
        pyr_r=2.0, pyr_frac=1.0, be_r=1.0, atr_mult=3.0, sl_cap=0.08, be_buf=0.005):
    o=df["open"].values; h=df["high"].values; l=df["low"].values; c=df["close"].values
    a=bt.atr(df,14).values; n=len(df)
    cash=1.0; units=0.0; side=0; entry=0.0; stop=0.0; R=0.0; pyrd=False; E_entry=1.0
    armedL=True; armedS=True
    eq=np.ones(n)
    for i in range(16, n-1):
        oN,hN,lN,cN = o[i+1],h[i+1],l[i+1],c[i+1]
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        if side!=0:
            # stop hit intrabar
            hit = (lN<=stop) if side==1 else (hN>=stop)
            regime_out = (not bull[i]) if side==1 else (not bear[i])
            if hit:
                fpx = stop*(1-SLIP) if side==1 else stop*(1+SLIP)
                cash += units*fpx - abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False
            elif regime_out:
                fpx = oN*(1-SLIP) if side==1 else oN*(1+SLIP)
                cash += units*fpx - abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False
            else:
                prof = ((cN-entry)/R) if side==1 else ((entry-cN)/R)
                if prof>=be_r:
                    be = entry*(1+be_buf) if side==1 else entry*(1-be_buf)
                    stop = max(stop,be) if side==1 else min(stop,be)
                if pyr_r is not None and not pyrd and prof>=pyr_r:
                    addn = pyr_frac*E_entry
                    add_units = (addn/cN) if side==1 else (-addn/cN)
                    cash -= add_units*cN + addn*FEE; units += add_units; pyrd=True
                    stop = max(stop,entry) if side==1 else min(stop,entry)
        if side==0:
            go=0
            if allow_long and bull[i] and armedL: go=1
            elif allow_short and bear[i] and armedS: go=-1
            if go!=0:
                E=cash
                if go==1:
                    st=max(c[i]-atr_mult*a[i], c[i]*(1-sl_cap)); entry=oN*(1+SLIP)
                    if entry-st<=0: st=None
                else:
                    st=min(c[i]+atr_mult*a[i], c[i]*(1+sl_cap)); entry=oN*(1-SLIP)
                    if st-entry<=0: st=None
                if st is not None:
                    units = go*E/entry; cash = E - units*entry - E*FEE
                    stop=st; R=abs(entry-st); side=go; E_entry=E; pyrd=False
                    if go==1: armedL=False
                    else: armedS=False
        eq[i+1] = cash + units*cN
    return pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]


def m(s):
    yrs=max((s.index[-1]-s.index[0]).days/365.25,1e-9)
    cg=(s.iloc[-1]/s.iloc[0])**(1/yrs)-1 if s.iloc[-1]>0 else -1
    dd=(s/s.cummax()-1).min()
    return cg,dd,(cg/abs(dd) if dd<-1e-9 else 0)


def main():
    df,bull,bear=regimes()
    print("="*76)
    print("MY-V3 + SHORTS — does shorting the bear regime add edge or fragility? (BTC 4h)")
    print("="*76)
    print(f"  {'config':<28}{'CAGR':>7}{'DD':>6}{'r/DD':>6}{'OOSr/DD':>9}")
    configs=[
        ("LONG-only (base)", dict(allow_long=True, allow_short=False)),
        ("LONG + SHORT", dict(allow_long=True, allow_short=True)),
        ("SHORT-only", dict(allow_long=False, allow_short=True)),
    ]
    series={}
    for name,kw in configs:
        s=run(df,bull,bear,**kw); series[name]=s; cg,dd,rr=m(s)
        o=m(s[s.index>=s.index[int(len(s)*0.6)]])
        print(f"  {name:<28}{cg*100:>6.0f}%{dd*100:>5.0f}%{rr:>6.2f}{o[2]:>9.2f}")
    print("\n  YEAR-BY-YEAR (return%):  LONG-only  |  LONG+SHORT  |  SHORT-only")
    yr=series["LONG-only (base)"].index.year
    for y in sorted(set(yr)):
        row=[]
        for name in ("LONG-only (base)","LONG + SHORT","SHORT-only"):
            seg=series[name][series[name].index.year==y]
            row.append(f"{(seg.iloc[-1]/seg.iloc[0]-1)*100:+6.0f}%" if len(seg)>20 else "    --")
        print(f"    {y}:   {row[0]}      {row[1]}      {row[2]}")


if __name__=="__main__":
    main()
