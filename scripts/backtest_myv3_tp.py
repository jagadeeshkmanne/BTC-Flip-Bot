#!/usr/bin/env python3
"""backtest_myv3_tp.py — explore TAKE-PROFIT / SCALE-OUT exits on top of my-V3 (pyramid ON).

Base (pyr_r=2, pyr_frac=1, be_r=1, atr_mult=3, sl_cap=0.08): CAGR 73%, DD -31%, r/DD 2.35, OOS 3.01.

This copies my-V3's honest cash/units engine and adds PARTIAL CLOSES:
  - Single partial TP: close frac F of units when price reaches +R_tp*R, rest runs on stop/regime.
  - Optional: after the TP fires, move remaining stop to break-even (entry).
  - Scale-out ladder: 1/3 at 3R, 1/3 at 6R, 1/3 on stop/regime.
  - Hard full TP at +R_tp*R (close everything).

Honesty (matches base):
  - Signals on CLOSED bar i, structural-stop / TP evaluated on bar i+1's REAL low/high.
  - STOP-FIRST on a straddle (we check stop before TP), same as base's lN<=sl first.
  - Stop fill = sl*(1-SLIP); TP fill = level*(1-SLIP) (we are SELLING, slippage hurts).
  - Partial close: cash += units_closed*fpx*(1-FEE); units -= units_closed. No lookahead.
  - mark equity = cash + units*close.
  - Pyramid adds borrowed notional at +pyr_r*R (cash negative), exactly like base.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
import backtest_myv3 as v3

FEE=bt.FEE_PCT; SLIP=bt.SLIP_PCT


def run_tp(df, bull, pyr_r=2.0, pyr_frac=1.0, be_r=1.0, atr_mult=3.0, sl_cap=0.08,
           tp_levels=None, be_after_tp=False, full_tp_r=None):
    """tp_levels: list of (R_multiple, frac_of_ORIGINAL_units) partial-close rungs, ascending R.
                  frac is fraction of the units held at INITIAL entry (so they sum to <=1 for the
                  base leg). Each rung fires once when bar high reaches entry+R*R.
       be_after_tp: after ANY tp rung fires, raise remaining stop to break-even (entry).
       full_tp_r:  if set, close ALL remaining units when high reaches entry+full_tp_r*R (overrides
                   the let-it-run behaviour). Mutually used as the 'hard full TP' test."""
    o=df["open"].values;h=df["high"].values;l=df["low"].values;c=df["close"].values
    a=bt.atr(df,14).values;n=len(df)
    cash=1.0;units=0.0;in_pos=False;entry=0.0;sl=0.0;R=0.0;pyrd=False;armed=True;E_entry=1.0
    base_units=0.0           # units bought at initial entry (TP fracs are of this)
    tp_fired=None            # boolean list, one per tp rung
    eq=np.ones(n)
    ntr=0; wins=0   # full-trade count + win-rate (a trade = entry -> full flat)
    for i in range(16,n-1):
        oN,hN,lN,cN=o[i+1],h[i+1],l[i+1],c[i+1]
        if not bull[i]: armed=True
        if in_pos:
            exited=False
            if lN<=sl:                                  # stop hit intrabar (STOP-FIRST)
                fpx=sl*(1-SLIP); cash+=units*fpx*(1-FEE); units=0.0; in_pos=False; pyrd=False
                exited=True
            elif not bull[i]:                           # regime flip -> exit next open
                fpx=oN*(1-SLIP); cash+=units*fpx*(1-FEE); units=0.0; in_pos=False; pyrd=False
                exited=True
            else:
                # --- hard full TP: close everything at the level ---
                if full_tp_r is not None and units>0 and hN>=entry+full_tp_r*R:
                    fpx=(entry+full_tp_r*R)*(1-SLIP); cash+=units*fpx*(1-FEE); units=0.0
                    in_pos=False; pyrd=False; exited=True
                else:
                    # --- partial TP rungs (each once, ascending) ---
                    if tp_levels is not None:
                        for j,(Rmul,frac) in enumerate(tp_levels):
                            if not tp_fired[j] and hN>=entry+Rmul*R:
                                u_close=min(frac*base_units, units)
                                if u_close>0:
                                    fpx=(entry+Rmul*R)*(1-SLIP)
                                    cash+=u_close*fpx*(1-FEE); units-=u_close
                                tp_fired[j]=True
                                if be_after_tp: sl=max(sl,entry)
                    # break-even / pyramid (same as base, on close)
                    prof=(cN-entry)/R if R>0 else 0
                    if prof>=be_r: sl=max(sl,entry)
                    if pyr_r is not None and not pyrd and prof>=pyr_r:
                        addn=pyr_frac*E_entry
                        cash-=addn*(1+FEE); units+=addn/cN; pyrd=True; sl=max(sl,entry)
            # if a partial-TP path drained all units without an explicit exit, flatten
            if in_pos and units<=1e-12:
                in_pos=False; pyrd=False; exited=True
            if exited:
                ntr+=1
                if cash>E_entry: wins+=1   # cash now fully realized vs equity at entry
        if not in_pos and bull[i] and armed:
            stop=max(c[i]-atr_mult*a[i], c[i]*(1-sl_cap))
            if c[i]-stop>0:
                E=cash; entry=oN*(1+SLIP); R=entry-stop; sl=stop
                units=E/entry; cash-=E*(1+FEE); E_entry=E; in_pos=True; pyrd=False; armed=False
                base_units=units; tp_fired=[False]*(len(tp_levels) if tp_levels else 0)
        eq[i+1]=cash+units*cN
    s=pd.Series(eq,index=pd.to_datetime(df["timestamp"])).iloc[16:]
    wr = (wins/ntr*100) if ntr else 0.0
    return s, ntr, wr


def metr(s):
    cg,dd,rr=v3.m(s)
    cut=s.index[int(len(s)*0.6)]
    _,_,orr=v3.m(s[s.index>=cut])
    return cg,dd,rr,orr


def yearly(s):
    out={}
    for yr,grp in s.groupby(s.index.year):
        ret=grp.iloc[-1]/grp.iloc[0]-1
        dd=(grp/grp.cummax()-1).min()
        out[yr]=(ret,dd)
    return out


def row(name,s,ntr,wr,base_rr=2.35,base_orr=3.01):
    cg,dd,rr,orr=metr(s)
    flag=""
    if rr>5 or orr>6: flag=" <-- SUSPICIOUS, recheck accounting"
    beat = "BEAT" if (rr>base_rr and orr>base_orr) else ("mixed" if (rr>base_rr or orr>base_orr) else "worse")
    print(f"  {name:<46}{cg*100:>6.0f}%{dd*100:>5.0f}%{rr:>6.2f}{orr:>8.2f}{ntr:>6}{wr:>6.0f}%  {beat}{flag}")
    return cg,dd,rr,orr


def main():
    df,bull=v3.regime()
    base=dict(pyr_r=2.0,pyr_frac=1.0,be_r=1.0,atr_mult=3.0,sl_cap=0.08)

    print("="*100)
    print("my-V3 + TAKE-PROFIT / SCALE-OUT exits (BTC 4h, pyramid ON). Base = pyr2/frac1.")
    print("="*100)
    # sanity: my run_tp with NO tp should equal base exactly
    s0,n0,w0=run_tp(df,bull,**base)
    sb=v3.run(df,bull,**base)
    print(f"  [sanity] run_tp(no-tp) == base equity:  maxabsdiff={np.abs(s0.values-sb.values).max():.2e}")
    print(f"  {'config':<46}{'CAGR':>7}{'DD':>6}{'r/DD':>6}{'OOS':>8}{'#tr':>6}{'win':>6}")
    cg,dd,rr,orr=row("BASE (no TP)",s0,n0,w0)
    base_rr,base_orr=rr,orr

    results={}
    # ---- 1. single partial TP, let rest run (no BE move) ----
    print("  --- 1. single partial TP @ R_tp, frac F, rest runs (stop unchanged) ---")
    for Rtp in (2,3,4,6,8):
        for F in (0.25,0.33,0.50):
            s,nt,wr=run_tp(df,bull,tp_levels=[(Rtp,F)],be_after_tp=False,**base)
            nm=f"TP {Rtp}R close {int(F*100)}%"
            results[nm]=row(nm,s,nt,wr,base_rr,base_orr)

    # ---- 2. single partial TP + move remaining stop to BE after TP fires ----
    print("  --- 2. single partial TP @ R_tp, frac F, + stop->BE after TP ---")
    for Rtp in (2,3,4,6,8):
        for F in (0.25,0.33,0.50):
            s,nt,wr=run_tp(df,bull,tp_levels=[(Rtp,F)],be_after_tp=True,**base)
            nm=f"TP {Rtp}R close {int(F*100)}% +BE"
            results[nm]=row(nm,s,nt,wr,base_rr,base_orr)

    # ---- 3. scale-out ladder: 1/3 @3R, 1/3 @6R, 1/3 stop/regime ----
    print("  --- 3. scale-out ladder 1/3@3R, 1/3@6R, 1/3 runs ---")
    for be in (False,True):
        s,nt,wr=run_tp(df,bull,tp_levels=[(3,1/3),(6,1/3)],be_after_tp=be,**base)
        nm=f"ladder 3R/6R{' +BE' if be else ''}"
        results[nm]=row(nm,s,nt,wr,base_rr,base_orr)

    # ---- 4. hard full TP (close everything) ----
    print("  --- 4. hard full TP @ R_tp (cap winners) ---")
    for Rtp in (4,6,8,10):
        s,nt,wr=run_tp(df,bull,full_tp_r=Rtp,**base)
        nm=f"HARD full TP {Rtp}R"
        results[nm]=row(nm,s,nt,wr,base_rr,base_orr)

    # ---- best config: year-by-year ----
    # pick best by ret/DD subject to OOS>=base_orr and not suspicious
    def score(kv):
        nm,(c,d,r,o)=kv
        if r>5 or o>6: return -1
        return r if o>=base_orr else r*0.5
    cand={k:v for k,v in results.items()}
    best=max(cand.items(), key=score)
    nm=best[0]
    print("="*100)
    print(f"BEST candidate by r/DD (OOS-gated): {nm}  -> CAGR {best[1][0]*100:.0f}% DD {best[1][1]*100:.0f}% r/DD {best[1][2]:.2f} OOS {best[1][3]:.2f}")
    # rebuild that config's series for year-by-year
    bestfn=_rebuild(nm,df,bull,base)
    print(f"  base r/DD 2.35 / OOS 3.01.  {'BEATS base' if (best[1][2]>2.35 and best[1][3]>3.01) else 'does NOT beat base on both'}")
    print(f"  {'year':<8}{'return':>9}{'maxDD':>8}")
    for yr,(ret,dd) in yearly(bestfn).items():
        print(f"  {yr:<8}{ret*100:>8.0f}%{dd*100:>7.0f}%")
    print(f"  {'BASE yearly for comparison':}")
    for yr,(ret,dd) in yearly(sb).items():
        print(f"  {yr:<8}{ret*100:>8.0f}%{dd*100:>7.0f}%")


def _rebuild(nm,df,bull,base):
    if nm=="BASE (no TP)": return run_tp(df,bull,**base)[0]
    if nm.startswith("ladder"):
        be="+BE" in nm
        return run_tp(df,bull,tp_levels=[(3,1/3),(6,1/3)],be_after_tp=be,**base)[0]
    if nm.startswith("HARD"):
        Rtp=int(nm.split()[3].replace("R",""))
        return run_tp(df,bull,full_tp_r=Rtp,**base)[0]
    # "TP {R}R close {F}% [+BE]"
    parts=nm.split()
    Rtp=int(parts[1].replace("R",""))
    F=int(parts[3].replace("%",""))/100
    be="+BE" in nm
    # snap F to the tested set
    F={25:0.25,33:0.33,50:0.50}.get(int(round(F*100)),F)
    return run_tp(df,bull,tp_levels=[(Rtp,F)],be_after_tp=be,**base)[0]


if __name__=="__main__":
    main()
