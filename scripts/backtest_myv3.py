#!/usr/bin/env python3
"""backtest_myv3.py — my own honest version of V3's logic, built on the validated MTF edge.

Takes V3's GOOD ideas (MTF trend, structural SL, pyramiding into winners, break-even) but uses
the VALIDATED MTF entry (not the engulfing stack, which I showed hurts). Tests whether V3's
pyramiding/SL framework adds real edge over the plain MTF Regime — walk-forward, 1x, honest fees.

  Entry  : MTF bull = (4h EMA50>200) AND (prior-day EMA50>200).
  SL     : entry - atr_mult*ATR, capped at sl_cap%. Defines R (initial risk).
  Pyramid: at +pyr_r * R, add pyr_frac of position, move SL to entry + 0.5R (lock profit).
  BE     : at +be_r * R, move SL to entry.
  Exit   : SL hit (intrabar) OR regime flips bearish.
Compares to MTF Regime (loose regime-flip exit). BTC 4h.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

FEE=bt.FEE_PCT; SLIP=bt.SLIP_PCT


def regime():
    df=pd.read_csv(os.path.join(bt.CACHE,"BTCUSDT_1h_binance_volume.csv"),parse_dates=["timestamp"])
    df=df.set_index("timestamp").resample("4h").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna().reset_index()
    dd=pd.read_csv(os.path.join(bt.CACHE,"BTCUSDT_1h_binance_volume.csv"),parse_dates=["timestamp"]).set_index("timestamp").resample("1D").agg({"close":"last"}).dropna().reset_index()
    d_up=(bt.ema(dd["close"],50)>bt.ema(dd["close"],200)).shift(1).fillna(False).astype(bool)
    ts=pd.to_datetime(df["timestamp"])
    dmap=pd.merge_asof(pd.DataFrame({"ts":ts}).sort_values("ts"),
        pd.DataFrame({"ts":pd.to_datetime(dd["timestamp"]),"b":d_up.values}).sort_values("ts"),
        on="ts",direction="backward")["b"].fillna(False).astype(bool).values
    f_up=(bt.ema(df["close"],50)>bt.ema(df["close"],200)).values
    return df, (f_up & dmap)


def run(df,bull,pyr_r=None,pyr_frac=0.5,be_r=1.0,atr_mult=3.0,sl_cap=0.08):
    """Honest cash/units engine: 1x base position; pyramid BORROWS to add exposure (cash goes
    negative), so the extra units genuinely gain AND lose. Mark equity = cash + units*price."""
    o=df["open"].values;h=df["high"].values;l=df["low"].values;c=df["close"].values
    a=bt.atr(df,14).values;n=len(df)
    cash=1.0;units=0.0;in_pos=False;entry=0.0;sl=0.0;R=0.0;pyrd=False;armed=True;E_entry=1.0
    eq=np.ones(n)
    for i in range(16,n-1):
        oN,lN,cN=o[i+1],l[i+1],c[i+1]
        if not bull[i]: armed=True
        if in_pos:
            if lN<=sl:                                  # stop hit intrabar
                fpx=sl*(1-SLIP); cash+=units*fpx*(1-FEE); units=0.0; in_pos=False; pyrd=False
            elif not bull[i]:                           # regime flip -> exit next open
                fpx=oN*(1-SLIP); cash+=units*fpx*(1-FEE); units=0.0; in_pos=False; pyrd=False
            else:
                prof=(cN-entry)/R if R>0 else 0
                if prof>=be_r: sl=max(sl,entry)         # break-even
                if pyr_r is not None and not pyrd and prof>=pyr_r:
                    addn=pyr_frac*E_entry               # add % of original notional (borrowed)
                    cash-=addn*(1+FEE); units+=addn/cN; pyrd=True; sl=max(sl,entry)
        if not in_pos and bull[i] and armed:
            stop=max(c[i]-atr_mult*a[i], c[i]*(1-sl_cap))
            if c[i]-stop>0:
                E=cash; entry=oN*(1+SLIP); R=entry-stop; sl=stop
                units=E/entry; cash-=E*(1+FEE); E_entry=E; in_pos=True; pyrd=False; armed=False
        eq[i+1]=cash+units*cN
    s=pd.Series(eq,index=pd.to_datetime(df["timestamp"])).iloc[16:]
    return s


def m(s):
    yrs=max((s.index[-1]-s.index[0]).days/365.25,1e-9)
    cg=(s.iloc[-1]/s.iloc[0])**(1/yrs)-1 if s.iloc[-1]>0 else -1
    dd=(s/s.cummax()-1).min()
    return cg,dd,(cg/abs(dd) if dd<-1e-9 else 0)


def main():
    df,bull=regime()
    print("="*82)
    print("MY OWN V3 — MTF entry + structural SL + pyramiding + break-even (BTC 4h, 1x)")
    print("="*82)
    print(f"  {'config':<40}{'CAGR':>7}{'DD':>6}{'r/DD':>6}   {'OOS rDD':>8}")
    cfgs=[
        ("MTF Regime baseline (loose exit)", dict(pyr_r=None, atr_mult=999, sl_cap=0.99)),  # effectively regime-flip only
        ("tight SL only (no pyramid)", dict(pyr_r=None)),
        ("+ pyramid at 2R", dict(pyr_r=2.0)),
        ("+ pyramid at 3R", dict(pyr_r=3.0)),
        ("+ pyramid at 3R, frac 1.0", dict(pyr_r=3.0, pyr_frac=1.0)),
    ]
    for name,kw in cfgs:
        s=run(df,bull,**kw); cg,dd,rr=m(s)
        cut=s.index[int(len(s)*0.6)]; oc,od,orr=m(s[s.index>=cut])
        print(f"  {name:<40}{cg*100:>6.0f}%{dd*100:>5.0f}%{rr:>6.2f}   {orr:>8.2f}")
    # the validated plain MTF Regime (regime-flip exit, no SL) for reference
    from backtest_mtf_trademgmt import regime as reg2, run as run2, m as m2
    df2,b2=reg2(); s2=run2(df2,b2,be=0.10); c2=m2(s2); o2=m2(s2[s2.index>=s2.index[int(len(s2)*0.6)]])
    print(f"  {'[ref] validated MTF Regime +10%BE':<40}{c2[0]*100:>6.0f}%{c2[1]*100:>5.0f}%{c2[2]:>6.2f}   {o2[2]:>8.2f}")


if __name__=="__main__":
    main()
