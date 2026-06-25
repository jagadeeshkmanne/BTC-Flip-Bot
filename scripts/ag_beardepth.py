#!/usr/bin/env python3
"""ag_beardepth.py — BEAR-DEPTH SHORT SCALING on full BTC 2017-2026 (3 bears: 2018/2022/2026).

ANGLE: size the short by how DEEP / confirmed the bear is. Small on shallow -10% dips (avoid
whipsaw), large only in confirmed deep macro-bears (capture more downside). short_size becomes a
FUNCTION of bear depth measured AT ENTRY (closed bar i, no lookahead):
  - drawdown-from-trailing-high bucket  (deeper drop -> bigger short)
  - months below SMA(6mo)               (longer below -> bigger short)
  - how far price is below SMA(6mo)      (further below -> bigger short)

Engine is a byte-for-byte copy of backtest_myv3_final.run() with ONE change: short_size can be a
per-bar ARRAY (ssize[i]) read at the entry decision bar i. Longs unchanged (size=lev). For a flat
array (all 0.40 / all 1.0) it must reproduce the validated base / flat-1.0 numbers exactly.

VALIDATED BASE (full 2017-2026): LONG=bull&(price>SMA9mo) atr3.5/sl0.12/be1/pyr2R/frac1.0.
SHORT=(drop>10%/40d)&(daily MACD<sig) sz0.40/s_atr5/s_cap0.15/no-short-pyr.
 -> Combined CAGR 96% DD-43% r/DD 2.24 OOS 3.05 bears +26/+11/+8 shortDD -34%.
 -> Flat sz1.0: CAGR 108% r/DD 2.52 but shortDD -67% (whipsaws shallow dips). Beat this DD tradeoff.

GATE: full data + OOS + all 3 bears (2018/2022/2026) + green every year + combined r/DD > 2.24.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_ext import build
from backtest_myv3_final import m
from backtest_short_aggressive import daily_macd_bear

BPD = 6  # 4h bars per day
EXT = os.path.join(bt.CACHE, "BTCUSDT_1h_binance_ext.csv")
FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT


def run_depth(df, bull, bear, ssize, allow_long=True, allow_short=True, lev=1.0,
              pyr_r=2.0, pyr_frac=1.0, be_r=1.0, atr_mult=3.5, sl_cap=0.12, be_buf=0.01,
              s_atr=5.0, s_cap=0.15, s_pyr_r=None):
    """EXACT copy of backtest_myv3_final.run, except short size = ssize[i] (per-bar array) read at
    the entry-decision bar i (a CLOSED bar -> no lookahead). ssize can be a scalar too."""
    o=df["open"].values; h=df["high"].values; l=df["low"].values; c=df["close"].values
    a=bt.atr(df,14).values; n=len(df)
    if np.isscalar(ssize): ssize=np.full(n, float(ssize))
    else: ssize=np.asarray(ssize, float)
    cash=1.0; units=0.0; side=0; entry=0.0; stop=0.0; R=0.0; pyrd=False; notional0=0.0
    peakH=0.0; armedL=True; armedS=True
    eq=np.ones(n)
    for i in range(16, n-1):
        oN,hN,lN,cN = o[i+1],h[i+1],l[i+1],c[i+1]
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        if side!=0:
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
                pr = s_pyr_r if side==-1 else pyr_r
                if pr is not None and not pyrd and prof>=pr:
                    addn = pyr_frac*notional0
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
                    st=max(c[i]-atr_mult*a[i], c[i]*(1-sl_cap)); entry=oN*(1+SLIP); sz=lev
                else:
                    st=min(c[i]+s_atr*a[i], c[i]*(1+s_cap)); entry=oN*(1-SLIP); sz=lev*ssize[i]
                ok = (entry-st>0) if go==1 else (st-entry>0)
                if ok and sz>0:
                    notional0 = sz*E
                    units = go*notional0/entry; cash = E - units*entry - notional0*FEE
                    stop=st; R=abs(entry-st); side=go; pyrd=False; peakH=entry
                    if go==1: armedL=False
                    else: armedS=False
        eq[i+1] = cash + units*cN
    return pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]


def yr(s,y):
    seg=s[s.index.year==y]; return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg)>20 else 0.0


def bucket_size(metric, edges, sizes):
    """Step function: size[k] applied where metric is in bucket k (metric>=edges[k]). Vectorized,
    no lookahead (metric is already a closed/shifted array). edges descending, e.g.
    edges=[-0.10,-0.20,-0.30], sizes=[0.25,0.50,1.00] (and 0 below the first edge)."""
    out=np.zeros(len(metric))
    for e,s in zip(edges,sizes):
        out=np.where(metric<=e, s, out)
    return out


def main():
    df,bull=build(); c=df["close"]; n=len(df)
    macro=(c>bt.sma(c,9*30*BPD).shift(1)).fillna(False).values
    LONG=bull & macro
    macdb=daily_macd_bear(df)

    # ---- base short GATE (validated): drop>10% off 40d-high AND daily MACD<sig ----
    hh40=c.rolling(40*BPD).max().shift(1)
    drop40=(c/hh40-1)                              # how deep below 40d-high (<=0)
    bear=((drop40<-0.10).fillna(False).values) & macdb

    # ---- depth metrics (all closed-bar / shifted -> no lookahead) ----
    hh180=c.rolling(180*BPD).max().shift(1)
    dd180=(c/hh180-1).fillna(0.0).values          # drawdown from trailing 180d high
    drop40v=drop40.fillna(0.0).values
    sma6=bt.sma(c,6*30*BPD).shift(1)
    far6=(c/sma6-1).fillna(0.0).values            # how far below SMA(6mo)
    below6=(c<sma6).fillna(False).values
    runb=np.zeros(n); k=0
    for i in range(n):
        k=k+1 if below6[i] else 0; runb[i]=k
    mo_below=runb/(30*BPD)                         # consecutive months below SMA(6mo)

    SP=dict(s_atr=5.0,s_cap=0.15,s_pyr_r=None)
    sL=run_depth(df,LONG,bear,0.0,allow_short=False,**SP); cgl,ddl,rrl=m(sL)

    print("="*100)
    print("BEAR-DEPTH SHORT SCALING — full BTC 2017-2026 (3 bears 2018/2022/2026), pinned long")
    print("="*100)
    print(f"  {'variant':<46}{'CAGR':>5}{'DD':>6}{'r/DD':>6}{'OOS':>6}  {'2018/22/26':>14}  shortDD")
    print(f"  {'LONG-ONLY (no short)':<46}{cgl*100:>4.0f}%{ddl*100:>5.0f}%{rrl:>6.2f}"
          f"{m(sL[sL.index>=sL.index[int(len(sL)*0.6)]])[2]:>6.2f}   (long only)")
    print("  "+"-"*96)

    def line(name, ssize, ref=False):
        sC=run_depth(df,LONG,bear,ssize,allow_long=True,allow_short=True,**SP)
        cg,dd,rr=m(sC); oo=m(sC[sC.index>=sC.index[int(len(sC)*0.6)]])[2]
        sS=run_depth(df,LONG,bear,ssize,allow_long=False,allow_short=True,**SP); sdd=m(sS)[1]
        pb=f"{yr(sC,2018):+.0f}/{yr(sC,2022):+.0f}/{yr(sC,2026):+.0f}"
        green=all(yr(sC,y)>0 for y in range(2018,2027) if len(sC[sC.index.year==y])>20)
        flag=""
        if not ref:
            flag=" <==beats r/DD" if rr>2.24 else ""
            if green and rr>2.24: flag+=" GREEN"
        tag="(ref)" if ref else ""
        print(f"  {name:<46}{cg*100:>4.0f}%{dd*100:>5.0f}%{rr:>6.2f}{oo:>6.2f}  {pb:>14}  {sdd*100:>5.0f}%{flag} {tag}")
        return dict(name=name,cg=cg,dd=dd,rr=rr,oos=oo,sdd=sdd,green=green,
                    pb=(yr(sC,2018),yr(sC,2022),yr(sC,2026)),s=sC)

    res=[]
    # references (must reproduce backtest_short_aggressive exactly)
    line("FLAT sz0.40 (validated BASE)", 0.40, ref=True)
    line("FLAT sz0.70", 0.70, ref=True)
    line("FLAT sz1.00 (108%/2.52 but shortDD-67%)", 1.00, ref=True)
    print("  "+"-"*96)

    # ---- depth-scaled variants ----
    # A) drawdown-from-180d-high buckets
    A=[
      ("DD180 bkt .25/.50/1.0 @-10/-20/-30", bucket_size(dd180,[-0.10,-0.20,-0.30],[0.25,0.50,1.00])),
      ("DD180 bkt .25/.60/1.0 @-12/-25/-35", bucket_size(dd180,[-0.12,-0.25,-0.35],[0.25,0.60,1.00])),
      ("DD180 bkt .30/.70/1.2 @-10/-22/-32", bucket_size(dd180,[-0.10,-0.22,-0.32],[0.30,0.70,1.20])),
      ("DD180 bkt .20/.50/.90/1.4 4-step",   bucket_size(dd180,[-0.10,-0.20,-0.30,-0.45],[0.20,0.50,0.90,1.40])),
      ("DD180 bkt .15/.45/1.0 @-10/-22/-35", bucket_size(dd180,[-0.10,-0.22,-0.35],[0.15,0.45,1.00])),
    ]
    # B) drop-from-40d-high buckets (matches the gate's own metric)
    B=[
      ("DROP40 bkt .25/.55/1.0 @-10/-18/-28", bucket_size(drop40v,[-0.10,-0.18,-0.28],[0.25,0.55,1.00])),
      ("DROP40 bkt .30/.70/1.2 @-10/-20/-30", bucket_size(drop40v,[-0.10,-0.20,-0.30],[0.30,0.70,1.20])),
    ]
    # C) months below SMA(6mo)
    C=[
      ("MoBelow6 bkt .25/.55/1.0 @0/2/4mo",  bucket_size(mo_below,[0.0,2.0,4.0],[0.25,0.55,1.00])),
      ("MoBelow6 bkt .30/.70/1.2 @0/3/5mo",  bucket_size(mo_below,[0.0,3.0,5.0],[0.30,0.70,1.20])),
    ]
    # D) how far below SMA(6mo)
    D=[
      ("Far6 bkt .25/.55/1.0 @-3/-12/-22%",  bucket_size(far6,[-0.03,-0.12,-0.22],[0.25,0.55,1.00])),
      ("Far6 bkt .30/.70/1.2 @-5/-15/-25%",  bucket_size(far6,[-0.05,-0.15,-0.25],[0.30,0.70,1.20])),
    ]
    # E) smooth linear ramp on DD180: size = clip(base + slope*depth, lo, hi)
    def ramp(metric, base, slope, lo, hi):
        return np.clip(base + slope*(-metric), lo, hi)
    E=[
      ("DD180 ramp .2->1.3 (slope3.0)", ramp(dd180,0.20,3.0,0.20,1.30)),
      ("DD180 ramp .25->1.5 (slope4.0)", ramp(dd180,0.25,4.0,0.25,1.50)),
    ]

    print("  >>> A) drawdown-from-180d-high buckets")
    for nm,sz in A: res.append(line(nm,sz))
    print("  >>> B) drop-from-40d-high buckets")
    for nm,sz in B: res.append(line(nm,sz))
    print("  >>> C) months-below-SMA(6mo) buckets")
    for nm,sz in C: res.append(line(nm,sz))
    print("  >>> D) how-far-below-SMA(6mo) buckets")
    for nm,sz in D: res.append(line(nm,sz))
    print("  >>> E) smooth linear ramp on DD180")
    for nm,sz in E: res.append(line(nm,sz))

    # ---- pick best 3: must pass GATE (r/DD>2.24, all bears>0, green every yr), rank by r/DD ----
    print("\n"+"="*100)
    valid=[r for r in res if r['rr']>2.24 and r['green'] and min(r['pb'])>0]
    valid.sort(key=lambda r:-r['rr'])
    print(f"  PASS GATE (r/DD>{2.24}, all 3 bears >0, green every year): {len(valid)} variants")
    best=valid[:3]
    print("  BEST 3 (gate-passing, by r/DD):")
    for r in best:
        print(f"    {r['name']:<46} CAGR {r['cg']*100:.0f}%  DD {r['dd']*100:.0f}%  r/DD {r['rr']:.2f}  "
              f"OOS {r['oos']:.2f}  shortDD {r['sdd']*100:.0f}%")

    print("\n  PER-BEAR + YEAR-BY-YEAR for the BEST 3:")
    for r in best:
        print(f"\n  >>> {r['name']}")
        print(f"      combined: CAGR {r['cg']*100:.0f}%  DD {r['dd']*100:.0f}%  r/DD {r['rr']:.2f}  "
              f"OOS {r['oos']:.2f}  | SHORT-SLEEVE-standalone DD {r['sdd']*100:.0f}%")
        print(f"      bears  2018:{r['pb'][0]:+.0f}%  2022:{r['pb'][1]:+.0f}%  2026:{r['pb'][2]:+.0f}%  "
              f"(BASE +26/+11/+8)")
        yrs="  ".join(f"{y}:{yr(r['s'],y):+.0f}%" for y in range(2017,2027))
        print(f"      year-by-year: {yrs}")
        better = (r['rr']>2.24 and min(r['pb'])>0)
        print(f"      vs BASE (96%/-43%/2.24/+26/+11/+8): "
              f"{'BEATS r/DD' if r['rr']>2.24 else 'below r/DD'}; "
              f"bears {'all >= base' if (r['pb'][0]>=26 and r['pb'][1]>=11 and r['pb'][2]>=8) else 'mixed vs base'}; "
              f"shortDD {r['sdd']*100:.0f}% vs base -34% / flat1.0 -67%")


if __name__=="__main__":
    main()
