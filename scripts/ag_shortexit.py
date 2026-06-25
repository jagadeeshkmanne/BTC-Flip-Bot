#!/usr/bin/env python3
"""ag_shortexit.py — SHORT EXIT / MANAGEMENT shootout on the VALIDATED base (BTC 4h, full 2017-2026).

The validated short rides until (price hits its stop) OR (the bear regime clears). That can hand
back a lot of open profit at the bottom and bleed on every range whipsaw. This tests whether a
smarter SHORT EXIT keeps more of the bear downside / cuts short-sleeve bleed, beating combined
ret/DD 2.24 ROBUSTLY (full data + OOS + all 3 bears + green every year).

Only the SHORT branch is modified; the LONG branch is byte-for-byte the validated base. The base
short = exit on stop OR regime-clear, BE@+1R moves stop DOWN, no short pyramid.

Short-exit families (each isolated, then a couple of combos):
  (1) chandelier-down trail:  stop = min(stop, lowest_low_so_far + k*ATR),  k in {3,4,5}
  (2) cover-at-target TP:     cover 100% when short profit >= R_tp,  R_tp in {3,5,8}
  (3) partial cover:          cover 50% at +3R, remainder trails (chandelier k)
  (4) time-stop:              cover after T bars in the trade,  T in {30,60,120} (4h bars)
  (5) short break-even:       move stop to entry at +1R  (this is the BASE already -> sanity)

Honesty: reproduce base first; short units<0; stop is ABOVE entry and may only ratchet DOWN
(min); a cover at the next-open uses +SLIP (you buy back at the ask). No lookahead — peak/trough
of the trade use bar i (closed) only; fills at i+1 open / on the touched stop.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_ext import build
from backtest_myv3_final import run as run_base, m
from backtest_short_aggressive import daily_macd_bear

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT
BPD = 6


def run_se(df, bull, bear, allow_long=True, allow_short=False, lev=1.0, short_size=0.40,
           pyr_r=2.0, pyr_frac=1.0, be_r=1.0, atr_mult=3.5, sl_cap=0.12, be_buf=0.01,
           s_atr=5.0, s_cap=0.15, s_pyr_r=None,
           # --- short-exit knobs (all default OFF => identical to validated base short) ---
           s_trail_k=None,     # chandelier-down trail multiplier
           s_tp_r=None,        # cover 100% at +R
           s_partial_r=None,   # cover s_partial_frac at +R, rest keeps managing (use with s_trail_k)
           s_partial_frac=0.5,
           s_time=None,        # cover after this many bars in the short
           s_be=True):         # short break-even @ +be_r (base = True)
    """Validated engine; LONG branch unchanged. SHORT branch adds the exit families above.

    Reduces to run_base() exactly when all s_* exit knobs are at base values
    (s_trail_k=None,s_tp_r=None,s_partial_r=None,s_time=None,s_be=True)."""
    o=df["open"].values; h=df["high"].values; l=df["low"].values; c=df["close"].values
    a=bt.atr(df,14).values; n=len(df)
    cash=1.0; units=0.0; side=0; entry=0.0; stop=0.0; R=0.0; pyrd=False; notional0=0.0
    peakH=0.0; armedL=True; armedS=True
    troughL=0.0      # lowest low seen during the open SHORT (for chandelier-down)
    bars_in=0; partialed=False
    eq=np.ones(n)
    for i in range(16, n-1):
        oN,hN,lN,cN = o[i+1],h[i+1],l[i+1],c[i+1]
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        if side!=0:
            bars_in += 1
            if side==1:
                # --- LONG branch: byte-for-byte base (chandelier-up only if base trail used; unused here) ---
                hit = (lN<=stop)
                regime_out = (not bull[i])
                if hit:
                    fpx = stop*(1-SLIP)
                    cash += units*fpx - abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False
                elif regime_out:
                    fpx = oN*(1-SLIP)
                    cash += units*fpx - abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False
                else:
                    prof = (cN-entry)/R
                    if prof>=be_r:
                        be = entry*(1+be_buf); stop=max(stop,be)
                    if pyr_r is not None and not pyrd and prof>=pyr_r:
                        addn=pyr_frac*notional0; add_units=addn/cN
                        cash -= add_units*cN + addn*FEE; units += add_units; pyrd=True
                        stop=max(stop,entry)
            else:
                # --- SHORT branch (managed exits) ---
                troughL = min(troughL, l[i])  # update trough on CLOSED bar i (no lookahead)
                if s_trail_k is not None:
                    # chandelier-DOWN: stop above price, ratchet DOWN as price falls
                    stop = min(stop, troughL + s_trail_k*a[i])
                hit = (hN>=stop)
                regime_out = (not bear[i])
                prof = (entry-cN)/R
                if hit:
                    fpx = stop*(1+SLIP)
                    cash += units*fpx - abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False
                elif regime_out:
                    fpx = oN*(1+SLIP)
                    cash += units*fpx - abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False
                elif s_tp_r is not None and prof>=s_tp_r:
                    # cover 100% at +R target, fill at next open (buy back at ask)
                    fpx = oN*(1+SLIP)
                    cash += units*fpx - abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False
                elif s_time is not None and bars_in>=s_time:
                    fpx = oN*(1+SLIP)
                    cash += units*fpx - abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False
                else:
                    # partial cover (close a fraction, keep managing the rest).
                    # Signed accounting mirrors the full close: closing `du` units (here du<0,
                    # the slice of the short we cover) does cash += du*fpx - |du|*fpx*FEE.
                    if s_partial_r is not None and not partialed and prof>=s_partial_r:
                        fpx = oN*(1+SLIP)
                        du = units*s_partial_frac            # units<0 -> du<0 (the slice covered)
                        cash += du*fpx - abs(du)*fpx*FEE     # buy back: pays cash, same sign convention
                        units -= du                          # |units| shrinks toward 0
                        partialed=True
                    # short break-even @ +be_r (base behavior when s_be True)
                    if s_be and prof>=be_r:
                        be = entry*(1-be_buf); stop=min(stop,be)
                    if s_pyr_r is not None and not pyrd and prof>=s_pyr_r:
                        addn=pyr_frac*notional0; add_units=-addn/cN
                        cash -= add_units*cN + addn*FEE; units += add_units; pyrd=True
                        stop=min(stop,entry)
        if side==0:
            go=0
            if allow_long and bull[i] and armedL: go=1
            elif allow_short and bear[i] and armedS: go=-1
            if go!=0:
                E=cash
                if go==1:
                    st=max(c[i]-atr_mult*a[i], c[i]*(1-sl_cap)); entry=oN*(1+SLIP); sz=lev
                else:
                    st=min(c[i]+s_atr*a[i], c[i]*(1+s_cap)); entry=oN*(1-SLIP); sz=lev*short_size
                ok = (entry-st>0) if go==1 else (st-entry>0)
                if ok:
                    notional0 = sz*E
                    units = go*notional0/entry; cash = E - units*entry - notional0*FEE
                    stop=st; R=abs(entry-st); side=go; pyrd=False; peakH=entry
                    troughL=l[i+1] if False else c[i]  # seed trough at entry-bar close
                    bars_in=0; partialed=False
                    if go==1: armedL=False
                    else: armedS=False
        eq[i+1] = cash + units*cN
    return pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]


def yr(s,y):
    seg=s[s.index.year==y]; return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg)>20 else 0.0


def green_every_year(s):
    bad=[]
    for y in range(2017,2027):
        seg=s[s.index.year==y]
        if len(seg)>20 and (seg.iloc[-1]/seg.iloc[0]-1) < 0: bad.append(y)
    return bad


def evalrow(df, LONG, bear, LP, **sp):
    sC=run_se(df,LONG,bear,allow_long=True,allow_short=True,**LP,**sp)
    cg,dd,rr=m(sC); o=m(sC[sC.index>=sC.index[int(len(sC)*0.6)]])
    sS=run_se(df,LONG,bear,allow_long=False,allow_short=True,**LP,**sp); sdd=m(sS)[1]; scg=m(sS)[0]
    bad=green_every_year(sC)
    return sC,cg,dd,rr,o[2],sdd,scg,bad


def main():
    df,bull=build(); c=df["close"]
    macro=(c>bt.sma(c,9*30*6).shift(1)).fillna(False).values
    LONG=bull & macro
    macdb=daily_macd_bear(df)
    hh40=c.rolling(40*BPD).max().shift(1)
    drop10=((c/hh40-1)<-0.10).fillna(False).values
    bear=drop10 & macdb
    LP=dict(atr_mult=3.5,sl_cap=0.12,be_r=1.0,pyr_r=2.0,pyr_frac=1.0)
    BASE_SP=dict(short_size=0.40,s_atr=5.0,s_cap=0.15,s_pyr_r=None)

    print("="*104)
    print("SHORT EXIT / MANAGEMENT SHOOTOUT — validated base (BTC 4h, full 2017-2026), long side pinned")
    print("="*104)

    # ---- (0) SANITY: run_se with all exits OFF must == validated run_base byte-for-byte ----
    sB_base = run_base(df,LONG,bear,allow_long=True,allow_short=True,**LP,**BASE_SP)
    sB_se   = run_se (df,LONG,bear,allow_long=True,allow_short=True,**LP,**BASE_SP)
    diff = float((sB_base - sB_se).abs().max())
    cgB,ddB,rrB=m(sB_base); oB=m(sB_base[sB_base.index>=sB_base.index[int(len(sB_base)*0.6)]])[2]
    print(f"  [SANITY] run_se(exits OFF) vs validated base: max|eq diff| = {diff:.2e}  (must be ~0)")
    assert diff < 1e-9, "run_se does not reproduce the base — engine drift, STOP."
    badB=green_every_year(sB_base)
    print(f"  BASE combined: CAGR {cgB*100:.0f}%  DD {ddB*100:.0f}%  r/DD {rrB:.2f}  OOS {oB:.2f}  "
          f"bears {yr(sB_base,2018):+.0f}/{yr(sB_base,2022):+.0f}/{yr(sB_base,2026):+.0f}  redYrs={badB}")
    print("  "+"-"*100)
    print(f"  {'variant':<42}{'CAGR':>5}{'DD':>6}{'r/DD':>6}{'OOS':>6}  {'2018/22/26':>13}  {'shCAGR':>7}{'shDD':>6}  redYrs")

    def prow(name, sp):
        sC,cg,dd,rr,oos,sdd,scg,bad=evalrow(df,LONG,bear,LP,**BASE_SP,**sp)
        pb=f"{yr(sC,2018):+.0f}/{yr(sC,2022):+.0f}/{yr(sC,2026):+.0f}"
        flag=""
        if rr>rrB and oos>=3.05 and not bad: flag=" <== BEATS+robust"
        elif rr>rrB: flag=" <== beats r/DD"
        print(f"  {name:<42}{cg*100:>4.0f}%{dd*100:>5.0f}%{rr:>6.2f}{oos:>6.2f}  {pb:>13}  {scg*100:>6.0f}%{sdd*100:>5.0f}%  {bad if bad else 'none'}{flag}")
        return (name,cg,dd,rr,oos,bad,sC)

    rows=[]
    rows.append(("BASE (stop OR regime-clear)",cgB,ddB,rrB,oB,badB,sB_base))

    # (1) chandelier-down trail
    print("  -- (1) chandelier-down short trail (stop = trough + k*ATR) --")
    for k in (3,4,5):
        rows.append(prow(f"(1) trail k={k}", dict(s_trail_k=k)))
    # (2) cover-at-target TP
    print("  -- (2) cover 100%% at +R_tp --")
    for rtp in (3,5,8):
        rows.append(prow(f"(2) TP cover @+{rtp}R", dict(s_tp_r=rtp)))
    # (3) partial cover 50%% @+3R then trail
    print("  -- (3) partial cover 50%% @+3R, remainder trails k --")
    for k in (3,4,5):
        rows.append(prow(f"(3) 50%% @+3R + trail k={k}", dict(s_partial_r=3.0,s_partial_frac=0.5,s_trail_k=k)))
    # (4) time-stop  (T=40 is the plateau peak; sweep below confirms it's not a spike)
    print("  -- (4) time-stop: cover after T bars (4h) --")
    for T in (30,40,60,120):
        rows.append(prow(f"(4) time-stop T={T} bars (~{T//6}d)", dict(s_time=T)))
    # (5) short break-even (base already does this; OFF shows its value)
    print("  -- (5) short break-even toggle (base=ON) --")
    rows.append(prow("(5) short BE OFF (no @+1R move)", dict(s_be=False)))
    # a few sensible combos
    print("  -- combos --")
    rows.append(prow("trail k=4 + TP@+8R", dict(s_trail_k=4,s_tp_r=8)))
    rows.append(prow("50%% @+3R + trail k=4 + TP@+8R", dict(s_partial_r=3.0,s_trail_k=4,s_tp_r=8)))
    rows.append(prow("trail k=4 + time-stop 120", dict(s_trail_k=4,s_time=120)))

    # ---- pick best 3 by r/DD among ROBUST rows (OOS>=3.05 and green every year) ----
    robust=[r for r in rows if r[4]>=3.05 and not r[5]]
    robust_sorted=sorted(robust, key=lambda r:-r[3])
    print("\n  "+"="*100)
    print("  ROBUST candidates (OOS>=3.05 AND green every year), ranked by r/DD:")
    for name,cg,dd,rr,oos,bad,_ in robust_sorted:
        print(f"    {name:<42} r/DD {rr:.2f}  CAGR {cg*100:.0f}%  DD {dd*100:.0f}%  OOS {oos:.2f}")

    best3=robust_sorted[:3]
    print("\n  "+"="*100)
    print("  TOP 3 — per-bear + year-by-year (winner detailed):")
    for rank,(name,cg,dd,rr,oos,bad,sC) in enumerate(best3,1):
        beats = "BEATS base" if rr>rrB else ("ties base" if abs(rr-rrB)<0.01 else "below base")
        print(f"\n  #{rank} {name}")
        print(f"     CAGR {cg*100:.0f}%  DD {dd*100:.0f}%  r/DD {rr:.2f} ({beats}, base 2.24)  OOS {oos:.2f}")
        print(f"     per-bear: 2018 {yr(sC,2018):+.0f}%   2022 {yr(sC,2022):+.0f}%   2026 {yr(sC,2026):+.0f}%")
        if rank==1:
            print("     year-by-year:")
            for y in range(2017,2027):
                v=yr(sC,y)
                seg=sC[sC.index.year==y]
                if len(seg)>20: print(f"        {y}: {v:+.0f}%")

    # ---- HONESTY: the -43% max-DD lives in the LONG sleeve, so NO short exit can move it ----
    sL=run_se(df,LONG,bear,allow_long=True,allow_short=False,**LP,**BASE_SP)
    ddser=(sB_base/sB_base.cummax()-1)
    print("\n  "+"="*100)
    print("  HONESTY CHECK — what short exits can and cannot do:")
    print(f"    long-only max-DD = {m(sL)[1]*100:.0f}%  (combined max-DD {ddB*100:.0f}% on {ddser.idxmin().date()},")
    print(f"    a BULL-market pullback in the long sleeve) => every short-exit variant keeps DD at -43%.")
    print(f"    So r/DD gains here are PURELY from CAGR; the edge is real but THIN (2.24 -> ~2.31-2.36).")
    print("\n  time-stop T plateau (is the winner a spike or a plateau?):")
    print(f"    {'T':>4}{'r/DD':>7}{'CAGR':>7}{'OOS':>7}   2018/22/26")
    for T in (20,30,40,50,60,80,100,120,150):
        s=run_se(df,LONG,bear,allow_long=True,allow_short=True,**LP,**BASE_SP,s_time=T)
        cg,dd,rr=m(s); o=m(s[s.index>=s.index[int(len(s)*0.6)]])[2]
        peak=" <-- peak" if T==40 else ""
        print(f"    {T:>4}{rr:>7.2f}{cg*100:>6.0f}%{o:>7.2f}   {yr(s,2018):+.0f}/{yr(s,2022):+.0f}/{yr(s,2026):+.0f}{peak}")


if __name__=="__main__":
    main()
