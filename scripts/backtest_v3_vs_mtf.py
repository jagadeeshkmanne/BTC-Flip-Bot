#!/usr/bin/env python3
"""backtest_v3_vs_mtf.py — does V3's engulfing entry add edge, or is the MTF trend the whole edge?

Strips V3 to its CORE entry (MTF daily-bias + 4h-RSI confirm + engulfing + RSI21 + MACD + vol),
runs it LONG/FLAT at 1x (no leverage, no pyramid, no flip) on 4h, exit on opposite signal or
daily-regime flip. Compares full + walk-forward to the validated MTF Regime (daily+4h trend).
If V3-core <= MTF Regime, then "fine-tuning V3 on higher TF" just leads back to the MTF Regime.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_v3_recon import load, macd


def v3_core_pos(df, P):
    o=df["open"].values;c=df["close"].values;v=df["volume"].values;h=df["high"].values;l=df["low"].values
    rsi=bt.rsi(df["close"],P["rsi_len"]).values
    ml,ms=macd(df["close"]);ml=ml.values;ms=ms.values
    atr=bt.atr(df,20).values;atrma=pd.Series(atr).rolling(50).mean().values
    vsma=pd.Series(v).rolling(20).mean().values
    db=df["d_bull"].values;h4=df["h4r"].values;body=np.abs(c-o)
    n=len(df);pos=np.zeros(n);side=0
    for i in range(60,n):
        bel=c[i-1]<o[i-1] and c[i]>o[i] and c[i]>=o[i-1] and o[i]<=c[i-1] and body[i]>body[i-1]*P["eng"]
        hv=atr[i]>atrma[i];vok=v[i]>P["volr"]*vsma[i]
        lsig=rsi[i]>P["rsi_lmin"] and ml[i]>ms[i] and bel and hv and vok and db[i] and h4[i]>50
        # exit: daily regime flips bearish, or a bearish engulf signal
        bes=c[i-1]>o[i-1] and c[i]<o[i] and o[i]>=c[i-1] and c[i]<=o[i-1] and body[i]>body[i-1]*P["eng"]
        ssig=rsi[i]<P["rsi_smax"] and ml[i]<ms[i] and bes and hv and vok and (not db[i]) and h4[i]<50
        if side==0 and lsig: side=1
        elif side==1 and (ssig or not db[i]): side=0
        pos[i]=side
    return pd.Series(pos,index=df.index)


def mtf_regime_pos(df):
    c=df["close"]
    f_up=(bt.ema(c,50)>bt.ema(c,200)).values
    return pd.Series((f_up & df["d_bull"].values).astype(float),index=df.index)


def evalp(df,pos):
    held=pos.shift(1).fillna(0)
    oo=(df["open"].shift(-1)/df["open"]-1).fillna(0)
    turn=held.diff().abs().fillna(held.abs())
    r=(held*oo-turn*bt.COST)
    eq=(1+r).cumprod();eq.index=pd.to_datetime(df["timestamp"])
    return eq, int((turn>0).sum())


def m(eq):
    yrs=max((eq.index[-1]-eq.index[0]).days/365.25,1e-9)
    cg=(eq.iloc[-1]/eq.iloc[0])**(1/yrs)-1
    dd=(eq/eq.cummax()-1).min()
    return cg,dd,(cg/abs(dd) if dd<-1e-9 else 0)


def main():
    P=dict(rsi_len=21,rsi_lmin=45,rsi_smax=55,eng=1.0,volr=1.5)
    df=load("4h")
    print("="*78)
    print("V3 engulfing-MTF entry (1x long/flat) vs MTF Regime — BTC 4h")
    print("="*78)
    print(f"  {'strategy':<34}{'CAGR':>8}{'DD':>7}{'ret/DD':>8}{'trades':>8}   {'OOS rDD':>8}")
    for name, pos in [("V3 engulfing-MTF entry (1x L/F)", v3_core_pos(df,P)),
                      ("MTF Regime (daily+4h trend)", mtf_regime_pos(df))]:
        eq,nt=evalp(df,pos); cg,dd,rr=m(eq)
        cut=eq.index[int(len(eq)*0.6)]; oc,od,orr=m(eq[eq.index>=cut])
        print(f"  {name:<34}{cg*100:>7.0f}%{dd*100:>6.0f}%{rr:>8.2f}{nt:>8}   {orr:>8.2f}")
    print("\n  (both 1x long/flat, honest fees, same 4h data 2019-2026)")
    print("  Takeaway: if V3-entry <= MTF Regime, the engulfing/RSI/MACD/vol stack adds nothing")
    print("  over the plain MTF trend — and the MTF Regime IS the fine-tuned higher-TF version.")


if __name__=="__main__":
    main()
