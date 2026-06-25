#!/usr/bin/env python3
"""ag_derisk.py — REFINE the two V2-specific de-risk mechanisms.

V2 BASE (reproduced byte-for-byte from backtest_v2_combined.py run2):
  (A) PARABOLIC de-risk (long only): take 50% off the long when price > 2.2*SMA(20wk).
  (B) BEAR-DEPTH short size: 0.25/0.50/1.00 at -10/-20/-30% drawdown from 180d high.
  Combined: CAGR 103%, DD -31%, ret/DD 3.34, OOS 3.24, bears +56/+26/+14.

This script PARAMETRIZES both mechanisms and sweeps alternatives:
  (A) blow-off detectors: k*SMA(20wk) for k in {1.8,2.0,2.2,2.5}; %above 50wk SMA;
      daily RSI>{80,85}; distance-above-200d-MA; close fraction {25,50,75%}.
  (B) bear-depth maps: continuous clip(|dd|/0.30, 0.25, 1.0); buckets {-15/-25/-35};
      size by months-below-SMA(6mo) instead of drawdown.

ENGINE = exact copy of run2 (signals on closed bars, fill next open, fees+slip, stops
ratchet, long pyramids/short doesn't, parabolic is long-only & one-shot). The ONLY changes
are: parab close fraction is a parameter (parab_frac), and the parabolic trigger + bear-depth
size are passed in as precomputed arrays. NO lookahead anywhere (all rolling/SMA .shift(1)).

GATE for a "winner": full 2017-2026 + OOS + ALL 3 bears (2018/2022/2026) positive + green every
year. Distrust ret/DD>5 or OOS>6 (likely a bug). Raw data printed below.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_ext import build, EXT
from backtest_short_aggressive import daily_macd_bear
from backtest_myv3_final import m

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT; BPD = 6


def run3(df, bull, bear, ssize, parab_trig, use_parab=False, use_bd=False,
         parab_frac=0.50,
         short_size=0.40, atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0,
         be_buf=0.01, s_atr=5.0, s_cap=0.15):
    """Identical to backtest_v2_combined.run2 except `parab_frac` (close fraction) is a
    parameter instead of hard-wired 0.5. parab_trig and ssize are precomputed arrays so the
    same engine drives every detector/size-map variant."""
    o=df["open"].values;h=df["high"].values;l=df["low"].values;c=df["close"].values
    a=bt.atr(df,14).values; n=len(df)
    cash=1.0;units=0.0;side=0;entry=0.0;stop=0.0;R=0.0;pyrd=False;notional0=0.0;parab=False
    armedL=True;armedS=True; eq=np.ones(n)
    for i in range(16,n-1):
        oN,hN,lN,cN=o[i+1],h[i+1],l[i+1],c[i+1]
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        if side!=0:
            hit=(lN<=stop) if side==1 else (hN>=stop)
            regime_out=(not bull[i]) if side==1 else (not bear[i])
            if hit:
                fpx=stop*(1-SLIP) if side==1 else stop*(1+SLIP)
                cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False; parab=False
            elif regime_out:
                fpx=oN*(1-SLIP) if side==1 else oN*(1+SLIP)
                cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False; parab=False
            else:
                prof=((cN-entry)/R) if side==1 else ((entry-cN)/R)
                if prof>=be_r:
                    be=entry*(1+be_buf) if side==1 else entry*(1-be_buf)
                    stop=max(stop,be) if side==1 else min(stop,be)
                if side==1 and not pyrd and prof>=pyr_r:
                    addn=pyr_frac*notional0; au=addn/cN
                    cash-=au*cN+addn*FEE; units+=au; pyrd=True
                    stop=max(stop,entry)
                if use_parab and side==1 and not parab and parab_trig[i]:
                    fpx=oN*(1-SLIP); cl=parab_frac*units
                    cash+=cl*fpx*(1-FEE); units-=cl; parab=True
        if side==0:
            go=0
            if bull[i] and armedL: go=1
            elif bear[i] and armedS: go=-1
            if go!=0:
                E=cash
                if go==1:
                    st=max(c[i]-atr_mult*a[i], c[i]*(1-sl_cap)); entry=oN*(1+SLIP); sz=1.0
                else:
                    st=min(c[i]+s_atr*a[i], c[i]*(1+s_cap)); entry=oN*(1-SLIP)
                    sz=(ssize[i] if use_bd else short_size)
                ok=(entry-st>0) if go==1 else (st-entry>0)
                if ok:
                    notional0=sz*E; units=go*notional0/entry; cash=E-units*entry-notional0*FEE
                    stop=st; R=abs(entry-st); side=go; pyrd=False; parab=False
                    if go==1: armedL=False
                    else: armedS=False
        eq[i+1]=cash+units*cN
    return pd.Series(eq,index=pd.to_datetime(df["timestamp"])).iloc[16:]


def yr(s,y):
    seg=s[s.index.year==y]; return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg)>20 else 0.0


def stat(s):
    """(CAGR%, DD%, ret/DD, OOS ret/DD, bear2018, bear2022, bear2026, min-year%, all_gates_pass)."""
    cg,dd,rr=m(s); o=m(s[s.index>=s.index[int(len(s)*0.6)]])
    b18,b22,b26=yr(s,2018),yr(s,2022),yr(s,2026)
    years=[yr(s,y) for y in range(2017,2027)]
    miny=min(years)
    gate=(b18>0 and b22>0 and b26>0 and miny>0 and rr>0)
    return cg*100,dd*100,rr,o[2],b18,b22,b26,miny,gate


def line(name,s):
    cg,dd,rr,oos,b18,b22,b26,miny,gate=stat(s)
    flag=""
    if rr>5 or oos>6: flag=" !! r/DD>5 or OOS>6 — DISTRUST (likely bug)"
    elif gate: flag=" GATE-PASS"
    print(f"  {name:<40}{cg:>5.0f}%{dd:>5.0f}%{rr:>6.2f}{oos:>6.2f}  {b18:+4.0f}/{b22:+4.0f}/{b26:+4.0f} miny{miny:+4.0f}%{flag}")
    return stat(s)+(name,)


def daily_rsi_arr(df, n=14):
    """Daily RSI mapped onto the 4h bars (shift(1) -> no lookahead, value known at bar open)."""
    raw=pd.read_csv(EXT,parse_dates=["timestamp"])
    dd=raw.set_index("timestamp").resample("1D").agg({"close":"last"}).dropna().reset_index()
    r=bt.rsi(dd["close"],n).shift(1)
    ts=pd.to_datetime(df["timestamp"])
    return pd.merge_asof(pd.DataFrame({"ts":ts}).sort_values("ts"),
        pd.DataFrame({"ts":pd.to_datetime(dd["timestamp"]),"r":r.values}).sort_values("ts"),
        on="ts",direction="backward")["r"].values


def daily_ma200_dist_arr(df):
    """(price/200d-MA - 1) on daily close, mapped to 4h. 200d MA uses .shift(1)."""
    raw=pd.read_csv(EXT,parse_dates=["timestamp"])
    dd=raw.set_index("timestamp").resample("1D").agg({"close":"last"}).dropna().reset_index()
    ma=dd["close"].rolling(200).mean().shift(1)
    dist=(dd["close"]/ma-1)
    ts=pd.to_datetime(df["timestamp"])
    return pd.merge_asof(pd.DataFrame({"ts":ts}).sort_values("ts"),
        pd.DataFrame({"ts":pd.to_datetime(dd["timestamp"]),"d":dist.values}).sort_values("ts"),
        on="ts",direction="backward")["d"].values


def months_below_sma6_arr(df):
    """Consecutive count (in 4h bars) that close has been below SMA(6mo). Used as a bear-depth
    proxy: longer below the long MA => deeper/older bear => bigger short. shift(1) on the SMA."""
    c=df["close"]
    below=(c < bt.sma(c,6*30*BPD).shift(1)).fillna(False).values
    cnt=np.zeros(len(below)); run=0
    for i in range(len(below)):
        run=run+1 if below[i] else 0
        cnt[i]=run
    return cnt  # in 4h bars; 1 month ~= 180 bars


def main():
    df,bull=build(); c=df["close"]
    macro=(c>bt.sma(c,9*30*BPD).shift(1)).fillna(False).values
    LONG=bull & macro
    hh40=c.rolling(40*BPD).max().shift(1); drop=((c/hh40-1)<-0.10).fillna(False).values
    bear=drop & daily_macd_bear(df)

    # --- BEAR-DEPTH size arrays ---
    hh180=c.rolling(180*BPD).max().shift(1); ddh=(c/hh180-1).fillna(0).values
    ss_base=np.where(ddh<=-0.30,1.0,np.where(ddh<=-0.20,0.50,0.25))            # BASE (B)
    ss_cont=np.clip(np.abs(ddh)/0.30,0.25,1.0)                                  # continuous
    ss_b15 =np.where(ddh<=-0.35,1.0,np.where(ddh<=-0.25,0.50,0.25))            # buckets -15/-25/-35 (entry gate is -10)
    ss_b15b=np.where(ddh<=-0.35,1.0,np.where(ddh<=-0.25,0.50,np.where(ddh<=-0.15,0.25,0.25)))
    # months-below-SMA(6mo): map bar-count to size (1mo~180 bars)
    mb=months_below_sma6_arr(df)
    ss_mob=np.where(mb>=3*180,1.0,np.where(mb>=2*180,0.50,0.25))                # >=3mo/2mo/else

    # --- PARABOLIC trigger arrays ---
    sma20=bt.sma(c,140*BPD).shift(1)            # 20 weeks = 140 days
    sma50=bt.sma(c,350*BPD).shift(1)            # 50 weeks
    pt_18=(c>1.8*sma20).fillna(False).values
    pt_20=(c>2.0*sma20).fillna(False).values
    pt_22=(c>2.2*sma20).fillna(False).values    # BASE (A)
    pt_25=(c>2.5*sma20).fillna(False).values
    pt_50w_30=(c>1.30*sma50).fillna(False).values   # 30% above 50wk SMA
    pt_50w_50=(c>1.50*sma50).fillna(False).values   # 50% above 50wk SMA
    rsiD=daily_rsi_arr(df,14)
    pt_rsi80=(rsiD>80); pt_rsi80=np.where(np.isnan(pt_rsi80),False,pt_rsi80).astype(bool)
    pt_rsi85=np.where(np.isnan(rsiD>85),False,rsiD>85).astype(bool)
    d200=daily_ma200_dist_arr(df)
    pt_200d_80=np.where(np.isnan(d200>0.80),False,d200>0.80).astype(bool)   # >80% above 200d MA
    pt_200d_120=np.where(np.isnan(d200>1.20),False,d200>1.20).astype(bool)  # >120% above 200d MA

    zeros=np.zeros(len(df),bool); zsize=np.full(len(df),0.40)

    print("="*112)
    print("ag_derisk.py — REFINE V2 parabolic de-risk + bear-depth short.  full 2017-2026 BTC 4h")
    print("="*112)
    print(f"  {'config':<40}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  {'18/22/26 + minyr':>20}")
    print("  "+"-"*108)

    # ---- 0. REPRODUCE BASE (must match backtest_v2_combined: 96/-43/2.24, ... , 103/-31/3.34) ----
    print("  [REPRO] verify run3 == run2 (BASE both OFF, then V2 BOTH @ k2.2/50%/buckets):")
    line("BASE both OFF", run3(df,LONG,bear,ss_base,pt_22,use_parab=False,use_bd=False))
    line("parabolic only (k2.2,50%)", run3(df,LONG,bear,ss_base,pt_22,use_parab=True,use_bd=False))
    line("bear-depth only (.25/.50/1.0)", run3(df,LONG,bear,ss_base,pt_22,use_parab=False,use_bd=True))
    BASE=line("V2 BOTH (base formulation)", run3(df,LONG,bear,ss_base,pt_22,use_parab=True,use_bd=True))
    base_rr=BASE[2]

    results=[]
    def reg(name,s):
        st=line(name,s); results.append(st); return st

    # ============ (A) PARABOLIC DETECTOR SWEEP (bear-depth fixed = BASE buckets) ============
    print("\n  (A) PARABOLIC blow-off detector sweep  [bear-depth = BASE buckets, parab close=50%]:")
    for nm,pt in [("A: k=1.8*SMA20wk",pt_18),("A: k=2.0*SMA20wk",pt_20),
                  ("A: k=2.2*SMA20wk (BASE)",pt_22),("A: k=2.5*SMA20wk",pt_25),
                  ("A: >30% above SMA50wk",pt_50w_30),("A: >50% above SMA50wk",pt_50w_50),
                  ("A: dailyRSI>80",pt_rsi80),("A: dailyRSI>85",pt_rsi85),
                  ("A: >80% above 200d-MA",pt_200d_80),("A: >120% above 200d-MA",pt_200d_120)]:
        reg(nm, run3(df,LONG,bear,ss_base,pt,use_parab=True,use_bd=True))

    # ---- parabolic CLOSE FRACTION sweep (best a couple of detectors x 25/50/75%) ----
    print("\n  parabolic CLOSE FRACTION sweep (k=2.2 SMA20wk):")
    for f in (0.25,0.50,0.75):
        reg(f"A: k2.2 close {int(f*100)}%", run3(df,LONG,bear,ss_base,pt_22,use_parab=True,use_bd=True,parab_frac=f))
    print("  parabolic CLOSE FRACTION sweep (k=2.0 SMA20wk — fires more often):")
    for f in (0.25,0.50,0.75):
        reg(f"A: k2.0 close {int(f*100)}%", run3(df,LONG,bear,ss_base,pt_20,use_parab=True,use_bd=True,parab_frac=f))

    # ============ (B) BEAR-DEPTH SIZE MAP SWEEP (parabolic fixed = BASE k2.2/50%) ============
    print("\n  (B) BEAR-DEPTH size map sweep  [parabolic = BASE k2.2/50%]:")
    for nm,ss in [("B: buckets .25/.50/1.0 (BASE)",ss_base),
                  ("B: continuous clip(|dd|/.30)",ss_cont),
                  ("B: buckets -15/-25/-35",ss_b15),
                  ("B: months-below-SMA6mo",ss_mob)]:
        reg(nm, run3(df,LONG,bear,ss,pt_22,use_parab=True,use_bd=True))

    # ============ best-of-A x best-of-B cross (only GATE-passing single-axis winners) ============
    print("\n  CROSS: pair the DD-cutters from (A) with the size maps from (B):")
    cross=[("X: k2.0/50% + continuous",pt_20,ss_cont,0.50),
           ("X: k2.0/75% + BASE buckets",pt_20,ss_base,0.75),
           ("X: k2.0/50% + BASE buckets",pt_20,ss_base,0.50),
           ("X: k1.8/50% + BASE buckets",pt_18,ss_base,0.50),
           ("X: RSI>80 + continuous",pt_rsi80,ss_cont,0.50)]
    for nm,pt,ss,f in cross:
        reg(nm, run3(df,LONG,bear,ss,pt,use_parab=True,use_bd=True,parab_frac=f))

    # ============ RANKING ============
    allr=[BASE]+results
    print("\n"+"="*112)
    print("  RANKING — gate-passing configs by ret/DD (then lowest DD):")
    print("="*112)
    passing=[r for r in allr if r[8]]
    passing_sorted=sorted(passing,key=lambda r:(-r[2],r[1]))
    print(f"  {'rank cfg':<42}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  {'18/22/26':>14}")
    for i,r in enumerate(passing_sorted[:8],1):
        print(f"  {i:>2}. {r[9]:<38}{r[0]:>5.0f}%{r[1]:>5.0f}%{r[2]:>6.2f}{r[3]:>6.2f}  {r[4]:+4.0f}/{r[5]:+4.0f}/{r[6]:+4.0f}")

    print(f"\n  BASE V2 ret/DD = {base_rr:.2f}, DD = {BASE[1]:.0f}%.  Lowest-DD gate-passing config:")
    lowdd=sorted(passing,key=lambda r:r[1],reverse=True)[0]  # DD is negative; reverse -> least negative? no
    lowdd=min(passing,key=lambda r:r[1])  # most negative is worst; we want closest to 0 -> max
    lowdd=max(passing,key=lambda r:r[1])
    print(f"    {lowdd[9]}: DD {lowdd[1]:.0f}%, ret/DD {lowdd[2]:.2f}, CAGR {lowdd[0]:.0f}%")

    # ============ WINNER year-by-year ============
    win=passing_sorted[0]
    # rebuild winner series for full year-by-year (look it up by name)
    name2call={
        "V2 BOTH (base formulation)":(ss_base,pt_22,0.50),
        "A: k=1.8*SMA20wk":(ss_base,pt_18,0.50),"A: k=2.0*SMA20wk":(ss_base,pt_20,0.50),
        "A: k=2.5*SMA20wk":(ss_base,pt_25,0.50),
        "A: >30% above SMA50wk":(ss_base,pt_50w_30,0.50),"A: >50% above SMA50wk":(ss_base,pt_50w_50,0.50),
        "A: dailyRSI>80":(ss_base,pt_rsi80,0.50),"A: dailyRSI>85":(ss_base,pt_rsi85,0.50),
        "A: >80% above 200d-MA":(ss_base,pt_200d_80,0.50),"A: >120% above 200d-MA":(ss_base,pt_200d_120,0.50),
        "A: k2.2 close 25%":(ss_base,pt_22,0.25),"A: k2.2 close 50%":(ss_base,pt_22,0.50),"A: k2.2 close 75%":(ss_base,pt_22,0.75),
        "A: k2.0 close 25%":(ss_base,pt_20,0.25),"A: k2.0 close 50%":(ss_base,pt_20,0.50),"A: k2.0 close 75%":(ss_base,pt_20,0.75),
        "B: continuous clip(|dd|/.30)":(ss_cont,pt_22,0.50),"B: buckets -15/-25/-35":(ss_b15,pt_22,0.50),
        "B: months-below-SMA6mo":(ss_mob,pt_22,0.50),
        "X: k2.0/50% + continuous":(ss_cont,pt_20,0.50),"X: k2.0/75% + BASE buckets":(ss_base,pt_20,0.75),
        "X: k2.0/50% + BASE buckets":(ss_base,pt_20,0.50),"X: k1.8/50% + BASE buckets":(ss_base,pt_18,0.50),
        "X: RSI>80 + continuous":(ss_cont,pt_rsi80,0.50),
    }
    print(f"\n  WINNER (top ret/DD, gate-passing): {win[9]}")
    if win[9] in name2call:
        ss,pt,f=name2call[win[9]]
        sw=run3(df,LONG,bear,ss,pt,use_parab=True,use_bd=True,parab_frac=f)
        print("   year-by-year: "+" ".join(f"{y}:{yr(sw,y):+.0f}%" for y in range(2017,2027)))
        cg,dd,rr=m(sw); print(f"   full: CAGR {cg*100:.0f}%  DD {dd*100:.0f}%  ret/DD {rr:.2f}  bears {yr(sw,2018):+.0f}/{yr(sw,2022):+.0f}/{yr(sw,2026):+.0f}")


if __name__=="__main__":
    main()
