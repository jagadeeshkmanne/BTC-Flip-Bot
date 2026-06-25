#!/usr/bin/env python3
"""backtest_myv3_trail.py — explore TRAILING-STOP exits on top of the my-V3 engine.

Base engine = scripts/backtest_myv3.py (verified: pyr_r=2,frac=1,be_r=1,atr_mult=3,sl_cap=0.08
-> CAGR 73%, DD -31%, ret/DD 2.35, OOS 3.01). This file copies run() and adds a trailing stop
that can only RATCHET UP for a long. Three trail types:
  1. ATR chandelier : trail = highest_high_since_entry - k*ATR(14)
  2. percent        : trail = peak_close * (1 - x)
  3. donchian       : trail = lowest_low of last N bars (rolling, prior bars only)

Honesty rules (same as base):
  - signals/trail computed on bars UP TO AND INCLUDING bar i (current closed bar)
  - fills on the NEXT bar (i+1): stop fills intrabar at stop*(1-SLIP) if low<=stop; regime
    flip fills at next open
  - trail stop NEVER moves down (max() against existing sl)
  - structural SL + break-even + pyramid all kept intact
  - mark equity = cash + units*price
  - regime-flip exit is OPTIONAL (use_regime_exit) so we can test trail-only.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3 import regime, m

FEE=bt.FEE_PCT; SLIP=bt.SLIP_PCT


def run(df, bull, pyr_r=2.0, pyr_frac=1.0, be_r=1.0, atr_mult=3.0, sl_cap=0.08,
        trail=None, k=3.0, x=0.10, N=20, use_regime_exit=True):
    """trail in {None,'atr','pct','don'}. k=ATR mult, x=pct (frac), N=donchian lookback.
    The trail is evaluated as of the CURRENT bar i (no lookahead), applied as the stop for the
    fill on bar i+1. It only raises the stop (ratchet)."""
    o=df["open"].values;h=df["high"].values;l=df["low"].values;c=df["close"].values
    a=bt.atr(df,14).values;n=len(df)
    # rolling donchian low of last N bars (prior bars, shifted so bar i sees lows of i-N..i-1)
    don_low=df["low"].rolling(N).min().shift(1).values if trail=="don" else None
    cash=1.0;units=0.0;in_pos=False;entry=0.0;sl=0.0;R=0.0;pyrd=False;armed=True;E_entry=1.0
    hh=0.0; peak_c=0.0                      # highest high / peak close since entry
    eq=np.ones(n); ntr=0
    for i in range(16,n-1):
        oN,lN,cN=o[i+1],l[i+1],c[i+1]
        if not bull[i]: armed=True
        if in_pos:
            # update running extremes with the CURRENT closed bar i (then set trail for i+1 fill)
            hh=max(hh,h[i]); peak_c=max(peak_c,c[i])
            if trail=="atr":
                t=hh-k*a[i];          sl=max(sl,t)
            elif trail=="pct":
                t=peak_c*(1-x);       sl=max(sl,t)
            elif trail=="don":
                dl=don_low[i]
                if dl==dl: sl=max(sl,dl)      # nan-safe
            if lN<=sl:                                  # stop (structural or trail) hit intrabar
                fpx=sl*(1-SLIP); cash+=units*fpx*(1-FEE); units=0.0; in_pos=False; pyrd=False; ntr+=1
            elif use_regime_exit and not bull[i]:       # regime flip -> exit next open
                fpx=oN*(1-SLIP); cash+=units*fpx*(1-FEE); units=0.0; in_pos=False; pyrd=False; ntr+=1
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
                hh=h[i]; peak_c=c[i]; ntr+=1
        eq[i+1]=cash+units*cN
    s=pd.Series(eq,index=pd.to_datetime(df["timestamp"])).iloc[16:]
    return s, ntr


def met(s):
    cg,dd,rr=m(s)
    cut=s.index[int(len(s)*0.6)]; oc,od,orr=m(s[s.index>=cut])
    return cg,dd,rr,orr


def row(name,s,ntr):
    cg,dd,rr,orr=met(s)
    flag=""
    if rr>5 or orr>6: flag=" <-- SUSPECT"
    print(f"  {name:<34}{cg*100:>6.0f}%{dd*100:>5.0f}%{rr:>6.2f}{orr:>9.2f}{ntr:>7}{flag}")
    return cg,dd,rr,orr,ntr


def main():
    df,bull=regime()
    base=dict(pyr_r=2.0,pyr_frac=1.0,be_r=1.0,atr_mult=3.0,sl_cap=0.08)
    print("="*86)
    print("MY-V3 TRAILING-STOP EXPLORATION (BTC 4h, 1x base, pyramid ON, structural SL intact)")
    print("="*86)
    print(f"  {'config':<34}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS rDD':>9}{'#tr':>7}")

    # reference: base with no trail, regime-flip exit
    s,nt=run(df,bull,**base,trail=None); row("BASE (no trail, regime exit)",s,nt)
    print("  "+"-"*82)

    results={}
    # 1. ATR chandelier
    print("  [1] ATR chandelier trail (regime exit ON)")
    best_atr=None
    for k in (2,3,4,5,6):
        s,nt=run(df,bull,**base,trail="atr",k=k)
        r=row(f"  atr k={k}",s,nt)
        if best_atr is None or r[2]>best_atr[1][2]: best_atr=(k,r,s)
    # 2. percent
    print("  [2] Percent trail (regime exit ON)")
    best_pct=None
    for x in (0.05,0.08,0.12,0.15,0.20):
        s,nt=run(df,bull,**base,trail="pct",x=x)
        r=row(f"  pct x={x*100:.0f}%",s,nt)
        if best_pct is None or r[2]>best_pct[1][2]: best_pct=(x,r,s)
    # 3. donchian
    print("  [3] Donchian-low trail (regime exit ON)")
    best_don=None
    for N in (10,20,30):
        s,nt=run(df,bull,**base,trail="don",N=N)
        r=row(f"  don N={N}",s,nt)
        if best_don is None or r[2]>best_don[1][2]: best_don=(N,r,s)

    # 4. best of each type, trail-only (regime exit OFF) vs ON
    print("  "+"-"*82)
    print("  [4] BEST of each type: regime exit OFF (trail-only) vs ON")
    bk,br,_=best_atr; s,nt=run(df,bull,**base,trail="atr",k=bk,use_regime_exit=False)
    row(f"  atr k={bk} TRAIL-ONLY",s,nt)
    s,nt=run(df,bull,**base,trail="atr",k=bk,use_regime_exit=True); row(f"  atr k={bk} +regime",s,nt)
    bx,br,_=best_pct; s,nt=run(df,bull,**base,trail="pct",x=bx,use_regime_exit=False)
    row(f"  pct x={bx*100:.0f}% TRAIL-ONLY",s,nt)
    s,nt=run(df,bull,**base,trail="pct",x=bx,use_regime_exit=True); row(f"  pct x={bx*100:.0f}% +regime",s,nt)
    bN,br,_=best_don; s,nt=run(df,bull,**base,trail="don",N=bN,use_regime_exit=False)
    row(f"  don N={bN} TRAIL-ONLY",s,nt)
    s,nt=run(df,bull,**base,trail="don",N=bN,use_regime_exit=True); row(f"  don N={bN} +regime",s,nt)

    # ---- pick the single best config across everything by ret/DD (with sane OOS) ----
    print("="*86)
    cands=[]
    for k in (2,3,4,5,6):
        for ex in (True,False):
            s,nt=run(df,bull,**base,trail="atr",k=k,use_regime_exit=ex); cands.append((f"atr k={k} {'+reg' if ex else 'trail-only'}",s,nt))
    for x in (0.05,0.08,0.12,0.15,0.20):
        for ex in (True,False):
            s,nt=run(df,bull,**base,trail="pct",x=x,use_regime_exit=ex); cands.append((f"pct x={x*100:.0f}% {'+reg' if ex else 'trail-only'}",s,nt))
    for N in (10,20,30):
        for ex in (True,False):
            s,nt=run(df,bull,**base,trail="don",N=N,use_regime_exit=ex); cands.append((f"don N={N} {'+reg' if ex else 'trail-only'}",s,nt))
    scored=[]
    for nm,s,nt in cands:
        cg,dd,rr,orr=met(s); scored.append((rr,orr,cg,dd,nt,nm))
    scored.sort(reverse=True)
    print("  TOP 5 by ret/DD:")
    for rr,orr,cg,dd,nt,nm in scored[:5]:
        flag=" <-- SUSPECT" if rr>5 or orr>6 else ""
        print(f"    {nm:<24} CAGR {cg*100:>5.0f}%  DD {dd*100:>5.0f}%  r/DD {rr:>5.2f}  OOS {orr:>5.2f}  #tr {nt}{flag}")

    best_nm=scored[0][5]
    print("="*86)
    print(f"  YEAR-BY-YEAR for BEST: {best_nm}")
    # rebuild best
    def rebuild(nm):
        if nm.startswith("atr"):
            k=float(nm.split("k=")[1].split()[0]); ex="trail-only" not in nm
            return run(df,bull,**base,trail="atr",k=k,use_regime_exit=ex)
        if nm.startswith("pct"):
            x=float(nm.split("x=")[1].split("%")[0])/100; ex="trail-only" not in nm
            return run(df,bull,**base,trail="pct",x=x,use_regime_exit=ex)
        N=int(nm.split("N=")[1].split()[0]); ex="trail-only" not in nm
        return run(df,bull,**base,trail="don",N=N,use_regime_exit=ex)
    s,nt=rebuild(best_nm)
    cg,dd,rr,orr=met(s)
    print(f"  full: CAGR {cg*100:.0f}%  DD {dd*100:.0f}%  ret/DD {rr:.2f}  OOS {orr:.2f}  #tr {nt}")
    print(f"  BASE: CAGR 73%  DD -31%  ret/DD 2.35  OOS 3.01")
    print(f"  {'year':>6}{'return':>9}{'maxDD':>8}")
    yrs=sorted(set(s.index.year))
    for y in yrs:
        sy=s[s.index.year==y]
        if len(sy)<2: continue
        ret=sy.iloc[-1]/sy.iloc[0]-1
        ddy=(sy/sy.cummax()-1).min()
        print(f"  {y:>6}{ret*100:>8.0f}%{ddy*100:>7.0f}%")


if __name__=="__main__":
    main()
