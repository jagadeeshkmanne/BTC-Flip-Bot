#!/usr/bin/env python3
"""backtest_v2_multicoin.py — run the full V2 strategy on ETH and BNB separately (2017-2026).

V2 = MTF long (4h+daily EMA50/200 + 9mo macro) + parabolic de-risk + bear-depth short.
Same exact rules as BTC, per-coin regime/gates. Does the edge generalize beyond BTC?
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_v2_combined import run2
from backtest_myv3_final import m

BPD = 6


def build_coin(sym):
    raw = pd.read_csv(os.path.join(bt.CACHE, f"{sym}_1h_binance_ext.csv"), parse_dates=["timestamp"])
    df = raw.set_index("timestamp").resample("4h").agg(
        {"open":"first","high":"max","low":"min","close":"last"}).dropna().reset_index()
    dd = raw.set_index("timestamp").resample("1D").agg({"close":"last"}).dropna().reset_index()
    ts = pd.to_datetime(df["timestamp"])
    # daily regime + MACD, causal
    d_up = (bt.ema(dd["close"],50) > bt.ema(dd["close"],200)).shift(1).fillna(False).astype(bool)
    ml = bt.ema(dd["close"],12)-bt.ema(dd["close"],26); sig = bt.ema(ml,9)
    d_macd = (ml<sig).shift(1).fillna(False)
    def mp(src):
        return pd.merge_asof(pd.DataFrame({"ts":ts}).sort_values("ts"),
            pd.DataFrame({"ts":pd.to_datetime(dd["timestamp"]),"b":src.values}).sort_values("ts"),
            on="ts", direction="backward")["b"].fillna(False).astype(bool).values
    c = df["close"]
    f_up = (bt.ema(c,50) > bt.ema(c,200)).values
    bull = f_up & mp(d_up)
    macro = (c > bt.sma(c, 9*30*BPD).shift(1)).fillna(False).values
    LONG = bull & macro
    drop = ((c/c.rolling(40*BPD).max().shift(1)-1) < -0.10).fillna(False).values
    bear = drop & mp(d_macd)
    ddh = (c/c.rolling(180*BPD).max().shift(1)-1).fillna(0).values
    ssize = np.where(ddh<=-0.30,1.0,np.where(ddh<=-0.20,0.50,0.25))
    parab = (c > 2.2*bt.sma(c, 140*BPD).shift(1)).fillna(False).values
    return df, LONG, bear, ssize, parab


def yr(s,y):
    seg=s[s.index.year==y]; return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg)>20 else None


def main():
    print("="*96)
    print("V2 on ETH and BNB separately (full 2017-2026) — does it generalize beyond BTC?")
    print("="*96)
    print(f"  {'coin / config':<30}{'CAGR':>6}{'DD':>6}{'r/DD':>6}{'OOS':>6}  {'2018/22/25/26':>16}")
    for sym in ("BTCUSDT","ETHUSDT","BNBUSDT"):
        try:
            df,LONG,bear,ssize,parab = build_coin(sym)
        except Exception as e:
            print(f"  {sym}: ERROR {str(e)[:40]}"); continue
        base = run2(df,LONG,bear,ssize,parab,use_parab=False,use_bd=False)
        v2   = run2(df,LONG,bear,ssize,parab,use_parab=True, use_bd=True)
        for tag,s in [("base",base),("V2",v2)]:
            cg,dd,rr=m(s); o=m(s[s.index>=s.index[int(len(s)*0.6)]])
            pb="/".join(f"{yr(s,y):+.0f}" if yr(s,y) is not None else "--" for y in (2018,2022,2025,2026))
            print(f"  {sym[:3]+' '+tag:<30}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{o[2]:>6.2f}  {pb:>16}")
        # V2 year-by-year
        yrs=" ".join(f"{y}:{yr(v2,y):+.0f}%" for y in range(2018,2027) if yr(v2,y) is not None)
        print(f"      {sym[:3]} V2 by year: {yrs}")
        print()


if __name__=="__main__":
    main()
