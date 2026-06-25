#!/usr/bin/env python3
"""backtest_myv3_ext.py — extend BTC to 2017 (adds 2018 bear) + analyze ALL bear drops + tune short gate.

(1) Analyze BTC's real drawdown structure: how many bears at -10/-20/-30/-50%, sustained vs sharp.
(2) Re-validate my-V3 combined on full 2017-2026 (3 major bears: 2018, 2022, 2026).
(3) Short-gate shootout on 3 bears: slow SMA gates vs FAST drop-from-high gates (the user's idea:
    short when down X% over N days) — does shorting shorter drops add profit or just whipsaw?
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_final import run, m

EXT = os.path.join(bt.CACHE, "BTCUSDT_1h_binance_ext.csv")
BPD = 6


def build():
    raw = pd.read_csv(EXT, parse_dates=["timestamp"])
    df = raw.set_index("timestamp").resample("4h").agg(
        {"open":"first","high":"max","low":"min","close":"last"}).dropna().reset_index()
    dd = raw.set_index("timestamp").resample("1D").agg({"close":"last"}).dropna().reset_index()
    d_up = (bt.ema(dd["close"],50) > bt.ema(dd["close"],200)).shift(1).fillna(False).astype(bool)
    ts = pd.to_datetime(df["timestamp"])
    dmap = pd.merge_asof(pd.DataFrame({"ts":ts}).sort_values("ts"),
        pd.DataFrame({"ts":pd.to_datetime(dd["timestamp"]),"b":d_up.values}).sort_values("ts"),
        on="ts", direction="backward")["b"].fillna(False).astype(bool).values
    f_up = (bt.ema(df["close"],50) > bt.ema(df["close"],200)).values
    bull = f_up & dmap
    return df, bull


def analyze_bears(df):
    c = df["close"]; ts = pd.to_datetime(df["timestamp"])
    peak = c.cummax(); ddown = (c/peak - 1)
    print("  BTC drawdown episodes (peak-to-trough, 2017-2026):")
    in_dd=False; start=trough=0; peakv=0
    eps=[]
    for i in range(len(c)):
        if not in_dd and ddown.iloc[i] < -0.10:
            in_dd=True; start=i; trough=i; peakv=peak.iloc[i]
        elif in_dd:
            if c.iloc[i] < c.iloc[trough]: trough=i
            if c.iloc[i] >= peakv*0.999:  # recovered
                eps.append((ts.iloc[start], ts.iloc[trough], (c.iloc[trough]/peakv-1)*100, (ts.iloc[trough]-ts.iloc[start]).days))
                in_dd=False
    if in_dd:
        eps.append((ts.iloc[start], ts.iloc[trough], (c.iloc[trough]/peakv-1)*100, (ts.iloc[trough]-ts.iloc[start]).days))
    for s,t,depth,dur in eps:
        tag = "MAJOR" if depth<-30 else ("medium" if depth<-20 else "minor")
        print(f"    {s.date()} -> trough {t.date()}  {depth:6.0f}%  ({dur:4d}d to trough)  {tag}")
    print(f"  => {len(eps)} drops >10%; {sum(1 for e in eps if e[2]<-30)} major (>30%), {sum(1 for e in eps if -30<=e[2]<-20)} medium, {sum(1 for e in eps if -20<=e[2]<-10)} minor")


def yr(s,y):
    seg=s[s.index.year==y]; return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg)>20 else 0.0


def show(df,bull,bear,label):
    allow_s = bear is not None
    if bear is None: bear = np.zeros(len(df), dtype=bool)
    s=run(df,bull,bear,allow_long=True,allow_short=allow_s)
    cg,dd,rr=m(s); o=m(s[s.index>=s.index[int(len(s)*0.6)]])
    yrs="  ".join(f"{y}:{yr(s,y):+.0f}%" for y in (2018,2022,2026))
    print(f"  {label:<34}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{o[2]:>6.2f}   {yrs}")
    return s


def main():
    df,bull=build()
    c=df["close"]
    print("="*94)
    print("BTC EXTENDED 2017-2026 — bear analysis + short-gate shootout (3 bears: 2018/2022/2026)")
    print("="*94)
    analyze_bears(df)
    print("\n  STRATEGY on extended data (long + various short gates):")
    print(f"  {'config':<34}{'CAGR':>5}{'DD':>6}{'r/DD':>6}{'OOS':>6}   2018/2022/2026")
    show(df,bull,None,"LONG-ONLY")
    # slow gates
    for mo in (6,12,18):
        bear=(c < bt.sma(c,mo*30*BPD).shift(1)).fillna(False).values
        show(df,bull,bear,f"+ short SMA({mo}mo)")
    # FAST drop-from-high gates (user's idea: down X% over N days)
    for X,N in ((0.10,30),(0.15,30),(0.20,40),(0.10,14)):
        hh=c.rolling(N*BPD).max().shift(1); bear=((c/hh-1) < -X).fillna(False).values
        show(df,bull,bear,f"+ short >{int(X*100)}% off {N}d-high")
    # daily-EMA (the whipsawy one) for reference
    dd=pd.read_csv(EXT,parse_dates=["timestamp"]).set_index("timestamp").resample("1D").agg({"close":"last"}).dropna().reset_index()
    d_dn=(bt.ema(dd["close"],50)<bt.ema(dd["close"],200)).shift(1).fillna(False).astype(bool)
    ts=pd.to_datetime(df["timestamp"])
    bear_d=pd.merge_asof(pd.DataFrame({"ts":ts}).sort_values("ts"),pd.DataFrame({"ts":pd.to_datetime(dd["timestamp"]),"b":d_dn.values}).sort_values("ts"),on="ts",direction="backward")["b"].fillna(False).values & (bt.ema(c,50)<bt.ema(c,200)).values
    show(df,bull,bear_d,"+ short daily-EMA50<200 (fast)")


if __name__=="__main__":
    main()
