#!/usr/bin/env python3
"""backtest_short_aggressive.py — can shorts make MORE (in range + bear) without wrecking DD?

Tests more-aggressive short ideas on full 2017-2026, pinned long side (macro filter). For each:
combined CAGR/DD/ret/DD/OOS + per-bear (2018/2022/2026) + SHORT-ONLY standalone (isolates the
short's real quality/DD). Honest about the up-drift headwind + range-market whipsaw.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_ext import build
from backtest_myv3_final import run, m

EXT = os.path.join(bt.CACHE, "BTCUSDT_1h_binance_ext.csv")
BPD = 6


def daily_macd_bear(df):
    raw = pd.read_csv(EXT, parse_dates=["timestamp"])
    dd = raw.set_index("timestamp").resample("1D").agg({"close":"last"}).dropna().reset_index()
    ml = bt.ema(dd["close"],12)-bt.ema(dd["close"],26); sig = bt.ema(ml,9)
    b = (ml<sig).shift(1).fillna(False)
    ts = pd.to_datetime(df["timestamp"])
    return pd.merge_asof(pd.DataFrame({"ts":ts}).sort_values("ts"),
        pd.DataFrame({"ts":pd.to_datetime(dd["timestamp"]),"b":b.values}).sort_values("ts"),
        on="ts", direction="backward")["b"].fillna(False).astype(bool).values


def yr(s,y):
    seg=s[s.index.year==y]; return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg)>20 else 0.0


def main():
    df,bull=build(); c=df["close"]
    macro=(c>bt.sma(c,9*30*6).shift(1)).fillna(False).values
    LONG=bull & macro
    macdb=daily_macd_bear(df)
    rsi=bt.rsi(c,14).values
    adx=bt.adx(df,14).values if hasattr(bt,'adx') else np.full(len(df),20.0)
    hh40=c.rolling(40*BPD).max().shift(1)
    drop10=((c/hh40-1)<-0.10).fillna(False).values
    drop7=((c/hh40-1)<-0.07).fillna(False).values
    drop5=((c/hh40-1)<-0.05).fillna(False).values
    LP=dict(atr_mult=3.5,sl_cap=0.12,be_r=1.0,pyr_r=2.0,pyr_frac=1.0)
    zero=np.zeros(len(df),bool)

    print("="*96)
    print("AGGRESSIVE SHORT VARIANTS — full 2017-2026, pinned long (macro filter)")
    print("="*96)
    sL=run(df,LONG,zero,allow_long=True,allow_short=False,**LP); cgl,ddl,rrl=m(sL)
    print(f"  {'variant':<40}{'CAGR':>5}{'DD':>6}{'r/DD':>6}{'OOS':>6}  {'2018/22/26':>14}  shortDD")
    print(f"  {'LONG-ONLY (no short)':<40}{cgl*100:>4.0f}%{ddl*100:>5.0f}%{rrl:>6.2f}{m(sL[sL.index>=sL.index[int(len(sL)*0.6)]])[2]:>6.2f}   (long only)")
    print("  "+"-"*92)

    variants=[
        ("BASE short: drop10 & MACD, sz0.40", drop10&macdb, dict(short_size=0.40,s_atr=5.0,s_cap=0.15,s_pyr_r=None)),
        ("bigger: sz0.70", drop10&macdb, dict(short_size=0.70,s_atr=5.0,s_cap=0.15,s_pyr_r=None)),
        ("bigger: sz1.00", drop10&macdb, dict(short_size=1.00,s_atr=5.0,s_cap=0.15,s_pyr_r=None)),
        ("more freq: drop7 & MACD sz0.40", drop7&macdb, dict(short_size=0.40,s_atr=5.0,s_cap=0.15,s_pyr_r=None)),
        ("more freq: drop5 & MACD sz0.40", drop5&macdb, dict(short_size=0.40,s_atr=5.0,s_cap=0.15,s_pyr_r=None)),
        ("any bear: MACD<sig only sz0.40", macdb, dict(short_size=0.40,s_atr=5.0,s_cap=0.15,s_pyr_r=None)),
        ("RANGE mean-rev: RSI>70 short sz0.25", (rsi>70), dict(short_size=0.25,s_atr=4.0,s_cap=0.10,s_pyr_r=None)),
        ("RANGE: RSI>72 & MACD<sig sz0.30", (rsi>72)&macdb, dict(short_size=0.30,s_atr=4.0,s_cap=0.12,s_pyr_r=None)),
        ("drop10&MACD + short pyramid@2R", drop10&macdb, dict(short_size=0.40,s_atr=5.0,s_cap=0.15,s_pyr_r=2.0)),
    ]
    for name,bear,sp in variants:
        sC=run(df,LONG,bear,allow_long=True,allow_short=True,**LP,**sp)
        cg,dd,rr=m(sC); o=m(sC[sC.index>=sC.index[int(len(sC)*0.6)]])
        sS=run(df,LONG,bear,allow_long=False,allow_short=True,**LP,**sp); sdd=m(sS)[1]
        pb=f"{yr(sC,2018):+.0f}/{yr(sC,2022):+.0f}/{yr(sC,2026):+.0f}"
        flag=" <==better" if rr>rrl else ""
        print(f"  {name:<40}{cg*100:>4.0f}%{dd*100:>5.0f}%{rr:>6.2f}{o[2]:>6.2f}  {pb:>14}  {sdd*100:>5.0f}%{flag}")
    print("\n  shortDD = drawdown of the SHORT sleeve standalone (how much it bleeds in up-markets)")


if __name__=="__main__":
    main()
