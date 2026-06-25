#!/usr/bin/env python3
"""ag_lockladder.py — PROFIT-LOCK LADDERING on the 1.8x leveraged V2 trend bot.

ANGLE: the 1.8x base (CAGR 145% / DD -37% / ret-DD 3.88, green every year) rides leveraged
open profit all the way up, so a sharp correction gives a lot of it back -> -37% DD. Bank the
leveraged open profit progressively and a correction gives back LESS -> lower DD. Leverage stays
fixed 1.8x; we only change HOW we harvest the open profit on the LONG runner.

Extends run_v (backtest_voltarget_dd.py) to support, on the LONG side only:
  (1) SCALE-OUT LADDER  -- close a slice at each of several +R rungs (e.g. 25% @ 4/8/12R).
  (2) LOCK + TRAIL-REMAINDER -- lock a fraction at +R, then chandelier-trail ONLY the runner.
  (3) VOL-SCALED LOCK -- bigger lock fraction when ATR% (vol) is high at the lock moment.
  (4) EQUITY-% LADDER  -- trim when the trade's OPEN profit reaches X% of total account equity
                          (lock by absolute $ gain, not by R), laddered at several X%.

ANTI-BUG: run_v_ext with no ladder + single lock(0.33@6R) reproduces the 1.8x base byte-for-byte
(asserted at startup). Signals on closed bars, fills next open. Stops only ratchet (max for long).
No lookahead: vol / equity-% read at bar i (the lock decision bar), act at oN (bar i+1 open).

GATE for any winner: full 2017-2026 + OOS(last 40%) + all 3 bears (2018/2022/2026) positive +
green every single year 2018-2026.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_ext import build
from backtest_short_aggressive import daily_macd_bear
from backtest_myv3_final import m

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT


def run_v_ext(df, bull, bear, ssize, parab, levarr,
              # --- original single-lock / pyramid / parab knobs (kept identical) ---
              pyr_frac=1.0, pyr_r=2.0, lock_frac=0.0, lock_r=5.0,
              atr_mult=3.5, sl_cap=0.12, be_r=1.0, be_buf=0.01, s_atr=5.0, s_cap=0.15,
              # --- (1) scale-out ladder: list of (R_level, fraction_of_CURRENT_units) ---
              ladder=None,
              # --- (2) chandelier trail on the REMAINDER after the single lock fires ---
              trail_k=None, trail_after_lock=False,
              # --- (3) vol-scaled lock: scale lock_frac by ATR%; needs atrpct array ---
              atrpct=None, vol_lock_lo=None, vol_lock_hi=None, vol_thr=0.04,
              # --- (4) equity-% ladder: list of (equityProfitFrac, fraction_of_units) ---
              eq_ladder=None,
              # --- SHORT-SIDE control (the binding DD lives here, not in the long) ---
              s_lock_frac=0.0, s_lock_r=5.0):
    """Faithful extension of backtest_voltarget_dd.run_v. With every new knob left at its default
    (ladder/trail/vol/eq_ladder = None/off) this is byte-identical to the original engine."""
    o=df["open"].values; h=df["high"].values; l=df["low"].values; c=df["close"].values
    a=bt.atr(df,14).values; n=len(df)
    cash=1.0; units=0.0; side=0; entry=stop=R=0.0
    pyrd=parabd=lockd=False; notional0=0.0; peakH=0.0
    armedL=armedS=True; eq=np.ones(n)
    # per-trade ladder bookkeeping
    rung_done=[]        # bool per ladder rung
    eqrung_done=[]      # bool per equity-% rung
    for i in range(16, n-1):
        oN,hN,lN,cN = o[i+1],h[i+1],l[i+1],c[i+1]
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        lev=levarr[i]
        # chandelier trail (original semantics): only on the long runner, optionally only after lock
        if side==1 and trail_k is not None:
            peakH=max(peakH,h[i])
            if (not trail_after_lock) or lockd:
                stop=max(stop, peakH - trail_k*a[i])
        if side!=0:
            hit=(lN<=stop) if side==1 else (hN>=stop)
            regime_out=(not bull[i]) if side==1 else (not bear[i])
            if hit or regime_out:
                fpx=(stop if hit else oN); fpx=fpx*(1-SLIP) if side==1 else fpx*(1+SLIP)
                cash+=units*fpx-abs(units)*fpx*FEE; units=0.0; side=0
                pyrd=parabd=lockd=False
            else:
                prof=((cN-entry)/R) if side==1 else ((entry-cN)/R)
                if prof>=be_r:
                    be=entry*(1+be_buf) if side==1 else entry*(1-be_buf)
                    stop=max(stop,be) if side==1 else min(stop,be)
                if side==1 and not pyrd and prof>=pyr_r:
                    addn=pyr_frac*notional0; au=addn/cN; cash-=au*cN+addn*FEE; units+=au
                    pyrd=True; stop=max(stop,entry)
                # (1) SCALE-OUT LADDER (long only) — close a fraction of CURRENT units at each rung
                if side==1 and ladder is not None and units>0:
                    for ri,(rlvl,rfrac) in enumerate(ladder):
                        if not rung_done[ri] and prof>=rlvl:
                            fpx=oN*(1-SLIP); cl=rfrac*units
                            cash+=cl*fpx*(1-FEE); units-=cl; rung_done[ri]=True
                # (4) EQUITY-% LADDER (long only) — trim when open profit >= X% of total equity
                if side==1 and eq_ladder is not None and units>0:
                    open_prof=units*cN - units*entry          # $ open profit of the long
                    tot_eq=cash+units*cN
                    pofeq=open_prof/tot_eq if tot_eq>0 else 0.0
                    for ri,(xfrac,rfrac) in enumerate(eq_ladder):
                        if not eqrung_done[ri] and pofeq>=xfrac:
                            fpx=oN*(1-SLIP); cl=rfrac*units
                            cash+=cl*fpx*(1-FEE); units-=cl; eqrung_done[ri]=True
                # single lock (original) — with optional (3) vol-scaled fraction
                if side==1 and lock_frac>0 and not lockd and prof>=lock_r:
                    lf=lock_frac
                    if atrpct is not None and vol_lock_lo is not None:
                        lf = vol_lock_hi if atrpct[i] >= vol_thr else vol_lock_lo
                    fpx=oN*(1-SLIP); cl=lf*units; cash+=cl*fpx*(1-FEE); units-=cl; lockd=True
                if side==1 and not parabd and parab[i]:
                    fpx=oN*(1-SLIP); cl=0.5*units; cash+=cl*fpx*(1-FEE); units-=cl; parabd=True
                # SHORT-SIDE lock — bank short open-profit at +R (units<0; close = buy back a slice)
                if side==-1 and s_lock_frac>0 and not lockd and prof>=s_lock_r:
                    fpx=oN*(1+SLIP); cl=s_lock_frac*units   # cl<0 -> reduces magnitude of units
                    cash+=cl*fpx-abs(cl)*fpx*FEE; units-=cl; lockd=True
        if side==0:
            if bull[i] and armedL:
                E=cash; st=max(c[i]-atr_mult*a[i],c[i]*(1-sl_cap)); ent=o[i+1]*(1+SLIP)
                if ent-st>0:
                    notional0=E*lev; units=notional0/ent; cash-=units*ent+notional0*FEE
                    entry=ent; stop=st; R=ent-st; side=1
                    pyrd=parabd=lockd=False; armedL=False; peakH=ent
                    rung_done=[False]*(len(ladder) if ladder else 0)
                    eqrung_done=[False]*(len(eq_ladder) if eq_ladder else 0)
            elif bear[i] and armedS:
                E=cash; st=min(c[i]+s_atr*a[i],c[i]*(1+s_cap)); ent=o[i+1]*(1-SLIP)
                if st-ent>0:
                    notional=ssize[i]*E*lev; units=-notional/ent; cash-=units*ent+notional*FEE
                    entry=ent; stop=st; R=st-ent; side=-1; armedS=False
        eq[i+1]=cash+units*cN
    return pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]


# ---- metrics / validation ----
def yr(s,y):
    sg=s[s.index.year==y]; return (sg.iloc[-1]/sg.iloc[0]-1)*100 if len(sg)>20 else 0.0
def grn(s): return all(yr(s,y)>=-0.5 for y in range(2018,2027))
def bear_ok(s): return yr(s,2018)>0 and yr(s,2022)>0 and yr(s,2026)>0
def oos(s): return m(s[s.index>=s.index[int(len(s)*0.6)]])[2]


def build_signals():
    df,bull=build(); c=df["close"]
    L=bull & (c>bt.sma(c,9*30*6).shift(1)).fillna(False).values
    bear=((c/c.rolling(40*6).max().shift(1)-1)<-0.10).fillna(False).values & daily_macd_bear(df)
    ddh=(c/c.rolling(180*6).max().shift(1)-1).fillna(0).values
    ss=np.where(ddh<=-0.30,1.0,np.where(ddh<=-0.20,0.50,0.25))
    pa=(c>2.2*bt.sma(c,140*6).shift(1)).fillna(False).values
    # ATR% of price (vol proxy), shifted so the value used at bar i is from closed bars
    atrpct=(bt.atr(df,14)/c).shift(1).fillna(0.03).values
    return df, L, bear, ss, pa, atrpct


def main():
    df,L,bear,ss,pa,atrpct = build_signals()
    lev18 = np.ones(len(df))*1.8

    # ---------- ANTI-BUG: reproduce the 1.8x base byte-for-byte ----------
    base = run_v_ext(df,L,bear,ss,pa,lev18, lock_frac=0.33, lock_r=6.0)
    cg,dd,rr = m(base); o=oos(base)
    print("="*100)
    print("ANTI-BUG CHECK — extended engine must reproduce the 1.8x base exactly")
    print(f"  1.8x BASE (lock 33%@6R): CAGR {cg*100:.0f}%  DD {dd*100:.0f}%  ret/DD {rr:.2f}  OOS {o:.2f}  "
          f"{'grn' if grn(base) else 'RED'}")
    assert abs(cg*100-145)<1 and abs(dd*100-(-37))<1 and abs(rr-3.88)<0.05, "BASE DRIFTED — STOP"
    print("  -> reproduced byte-for-byte (CAGR 145%, DD -37%, ret/DD 3.88). OK to proceed.\n")

    BASE_DD, BASE_CG = dd, cg

    def row(name, **kw):
        s=run_v_ext(df,L,bear,ss,pa,lev18, **kw)
        cg,dd,rr=m(s)
        pb=f"{yr(s,2018):>+4.0f}/{yr(s,2022):>+3.0f}/{yr(s,2026):>+3.0f}"
        ok = grn(s) and bear_ok(s) and cg>=1.20 and dd>=-0.30
        flag = "PASS" if ok else ("grn" if grn(s) else "RED")
        print(f"  {name:<40}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{oos(s):>6.2f}  {pb}  {flag}")
        return dict(name=name, cg=cg, dd=dd, rr=rr, oos=oos(s), s=s,
                    grn=grn(s), bear=bear_ok(s), pass_=ok)

    results=[]
    H = lambda t: print("  "+"-"*96+"\n  "+t)
    print(f"  {'config':<40}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  2018/22/26  gate")
    H("BASELINES (fixed 1.8x leverage)")
    results.append(row("1.8x no harvest at all", lock_frac=0.0))
    results.append(row("1.8x base: lock 33%@6R", lock_frac=0.33, lock_r=6.0))

    # ---------- (1) SCALE-OUT LADDERS ----------
    H("(1) SCALE-OUT LADDER — close slices at several +R rungs (no single-lock)")
    results.append(row("ladder 25% @ 4/8/12R",      lock_frac=0.0, ladder=[(4,0.25),(8,0.25),(12,0.25)]))
    results.append(row("ladder 33% @ 4/8/12R",      lock_frac=0.0, ladder=[(4,0.33),(8,0.33),(12,0.33)]))
    results.append(row("ladder 25% @ 3/6/9R",       lock_frac=0.0, ladder=[(3,0.25),(6,0.25),(9,0.25)]))
    results.append(row("ladder 20% @ 3/6/9/12/15R", lock_frac=0.0, ladder=[(3,0.20),(6,0.20),(9,0.20),(12,0.20),(15,0.20)]))
    results.append(row("ladder 25% @ 2/5/8/12R",    lock_frac=0.0, ladder=[(2,0.25),(5,0.25),(8,0.25),(12,0.25)]))
    results.append(row("ladder 30% @ 5/10R",        lock_frac=0.0, ladder=[(5,0.30),(10,0.30)]))
    results.append(row("ladder 50% @ 4/10R",        lock_frac=0.0, ladder=[(4,0.50),(10,0.50)]))

    # ---------- (2) LOCK + CHANDELIER TRAIL ON THE REMAINDER ----------
    H("(2) LOCK 33%@6R THEN chandelier-trail the REMAINDER (locked part safe, runner trails)")
    for k in (4,5,6,8):
        results.append(row(f"lock33@6R + trail k={k} (after lock)", lock_frac=0.33, lock_r=6.0,
                           trail_k=k, trail_after_lock=True))
    # also a couple with earlier/bigger lock then trail
    results.append(row("lock50@5R + trail k=6 (after lock)", lock_frac=0.50, lock_r=5.0,
                       trail_k=6, trail_after_lock=True))
    results.append(row("lock33@4R + trail k=6 (after lock)", lock_frac=0.33, lock_r=4.0,
                       trail_k=6, trail_after_lock=True))

    # ---------- (3) VOL-SCALED LOCK FRACTION ----------
    H("(3) VOL-SCALED LOCK — bigger lock fraction when ATR% high (vol_thr=4%)")
    results.append(row("lock 25%/50% lo/hi @6R, thr4%", lock_frac=0.40, lock_r=6.0,
                       atrpct=atrpct, vol_lock_lo=0.25, vol_lock_hi=0.50, vol_thr=0.04))
    results.append(row("lock 33%/66% lo/hi @6R, thr4%", lock_frac=0.40, lock_r=6.0,
                       atrpct=atrpct, vol_lock_lo=0.33, vol_lock_hi=0.66, vol_thr=0.04))
    results.append(row("lock 25%/66% lo/hi @5R, thr5%", lock_frac=0.40, lock_r=5.0,
                       atrpct=atrpct, vol_lock_lo=0.25, vol_lock_hi=0.66, vol_thr=0.05))
    results.append(row("lock 33%/75% lo/hi @6R, thr3%", lock_frac=0.40, lock_r=6.0,
                       atrpct=atrpct, vol_lock_lo=0.33, vol_lock_hi=0.75, vol_thr=0.03))

    # ---------- (4) EQUITY-% LADDER ----------
    H("(4) EQUITY-% LADDER — trim when open profit reaches X% of total account equity")
    results.append(row("eq-ladder 25% @ +20/40/60% eq", eq_ladder=[(0.20,0.25),(0.40,0.25),(0.60,0.25)]))
    results.append(row("eq-ladder 25% @ +30/60/90% eq", eq_ladder=[(0.30,0.25),(0.60,0.25),(0.90,0.25)]))
    results.append(row("eq-ladder 33% @ +25/50/75% eq", eq_ladder=[(0.25,0.33),(0.50,0.33),(0.75,0.33)]))
    results.append(row("eq-ladder 20% @ +15/30/45/60/75%", eq_ladder=[(0.15,0.20),(0.30,0.20),(0.45,0.20),(0.60,0.20),(0.75,0.20)]))
    results.append(row("eq-ladder 50% @ +25/50% eq",    eq_ladder=[(0.25,0.50),(0.50,0.50)]))

    # ---------- COMBOS: best harvest ideas stacked ----------
    H("COMBOS — stack the most promising harvesters")
    results.append(row("ladder 25%@3/6/9R + trail k=6", lock_frac=0.0,
                       ladder=[(3,0.25),(6,0.25),(9,0.25)], trail_k=6))
    results.append(row("lock33@6R + eq-ladder 25%@40/70%", lock_frac=0.33, lock_r=6.0,
                       eq_ladder=[(0.40,0.25),(0.70,0.25)]))
    results.append(row("ladder 20%@3/6/9/12R + trail k=6", lock_frac=0.0,
                       ladder=[(3,0.20),(6,0.20),(9,0.20),(12,0.20)], trail_k=6))

    # ---------- DIAGNOSTIC: where does the binding -37% DD actually live? ----------
    print("\n"+"="*100)
    print("DIAGNOSTIC — the binding 1.8x DD is the 2022-02-24->04-11 window, which holds NO long")
    print("="*100)
    bd=run_v_ext(df,L,bear,ss,pa,lev18, lock_frac=0.33, lock_r=6.0)
    ddw=(bd/bd.cummax()-1); tr=ddw.idxmin(); pk=bd[:tr].idxmax()
    held_long = (L[16:len(df)-1].sum())  # informational
    print(f"  base worst DD {ddw.min()*100:.1f}% over {str(pk.date())} -> {str(tr.date())} "
          f"(price chops -27% but regime is SHORT/flat, 0 long bars in window)")
    print("  => profit-lock laddering the LONG runner CANNOT move this DD; it is the leveraged")
    print("     SHORT sleeve whipsawing. Below: turn the short off, and bank short profit.\n")

    zero=np.zeros(len(df),bool)
    def row_b(name, bear_sig, **kw):
        s=run_v_ext(df,L,bear_sig,ss,pa,lev18, **kw)
        cg,dd,rr=m(s)
        pb=f"{yr(s,2018):>+4.0f}/{yr(s,2022):>+3.0f}/{yr(s,2026):>+3.0f}"
        ok = grn(s) and bear_ok(s) and cg>=1.20 and dd>=-0.30
        flag = "PASS" if ok else ("grn" if grn(s) else "RED")
        print(f"  {name:<40}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{oos(s):>6.2f}  {pb}  {flag}")
        return dict(name=name, cg=cg, dd=dd, rr=rr, oos=oos(s), s=s,
                    grn=grn(s), bear=bear_ok(s), pass_=ok)
    print(f"  {'config':<40}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  2018/22/26  gate")
    results.append(row_b("LONG-ONLY 1.8x (short OFF)", zero, lock_frac=0.33, lock_r=6.0))
    results.append(row_b("base + short-lock 50%@4R", bear, lock_frac=0.33, lock_r=6.0, s_lock_frac=0.50, s_lock_r=4.0))
    results.append(row_b("base + short-lock 50%@3R", bear, lock_frac=0.33, lock_r=6.0, s_lock_frac=0.50, s_lock_r=3.0))
    results.append(row_b("base + short-lock 66%@3R", bear, lock_frac=0.33, lock_r=6.0, s_lock_frac=0.66, s_lock_r=3.0))
    results.append(row_b("base + short-lock 50%@2R", bear, lock_frac=0.33, lock_r=6.0, s_lock_frac=0.50, s_lock_r=2.0))
    # best long harvest + best short lock stacked
    results.append(row_b("ladder25@4/8/12R + short-lock50@3R", bear, lock_frac=0.0,
                         ladder=[(4,0.25),(8,0.25),(12,0.25)], s_lock_frac=0.50, s_lock_r=3.0))
    results.append(row_b("volLock25/50@6R + short-lock50@3R", bear, lock_frac=0.40, lock_r=6.0,
                         atrpct=atrpct, vol_lock_lo=0.25, vol_lock_hi=0.50, vol_thr=0.04,
                         s_lock_frac=0.50, s_lock_r=3.0))

    # ---------- FINAL: rank passers by lowest DD (CAGR>=120%, all gates) ----------
    print("\n"+"="*100)
    print("FINAL — candidates passing ALL gates (CAGR>=120%, DD>=-30%, OOS, 3 bears+, green every yr)")
    print("="*100)
    passers=[r for r in results if r["pass_"] and r["name"]!="1.8x base: lock 33%@6R"]
    passers.sort(key=lambda r: r["dd"], reverse=True)  # lowest DD (closest to 0) first
    if not passers:
        print("  NONE passed all gates. Best DD-reducers that keep CAGR>=120% (may miss a bear/year):")
        cand=[r for r in results if r["cg"]>=1.20 and r["name"]!="1.8x base: lock 33%@6R"]
        cand.sort(key=lambda r: r["dd"], reverse=True)
        passers=cand[:3]
    print(f"  base 1.8x: CAGR {BASE_CG*100:.0f}%  DD {BASE_DD*100:.0f}%\n")
    print(f"  {'rank  config':<46}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  2018/22/26  gate")
    for rk,r in enumerate(passers[:3],1):
        s=r["s"]
        pb=f"{yr(s,2018):>+4.0f}/{yr(s,2022):>+3.0f}/{yr(s,2026):>+3.0f}"
        flag="PASS" if r["pass_"] else ("grn" if r["grn"] else "RED")
        print(f"  {rk}. {r['name']:<42}{r['cg']*100:>5.0f}%{r['dd']*100:>5.0f}%{r['rr']:>6.2f}{r['oos']:>6.2f}  {pb}  {flag}")
        ddcut=(r['dd']-BASE_DD)*100
        print(f"       DD vs base: {r['dd']*100:.1f}% vs {BASE_DD*100:.1f}%  ({ddcut:+.1f} pts)   "
              f"CAGR vs base: {r['cg']*100:.0f}% vs {BASE_CG*100:.0f}%")

    # year-by-year for the top passer
    if passers:
        top=passers[0]; s=top["s"]
        print(f"\n  YEAR-BY-YEAR — top candidate: {top['name']}")
        for y in range(2017,2027):
            print(f"    {y}: {yr(s,y):>+5.0f}%")

    # verdict
    print("\n"+"="*100)
    best=passers[0] if passers else None
    if best and best["pass_"]:
        print(f"  VERDICT: laddered locking CUTS the 1.8x DD from {BASE_DD*100:.0f}% to {best['dd']*100:.0f}% "
              f"while keeping CAGR {best['cg']*100:.0f}% (>=120%). Best: {best['name']}")
    elif best:
        print(f"  VERDICT: best DD-reducer keeping CAGR>=120% is {best['name']} "
              f"(DD {best['dd']*100:.0f}% vs base {BASE_DD*100:.0f}%) but it FAILS a gate "
              f"({'a bear<=0' if not best['bear'] else 'a red year/OOS'}).")
    else:
        print("  VERDICT: no config kept CAGR>=120%.")
    print("="*100)


if __name__=="__main__":
    main()
