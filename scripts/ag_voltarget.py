#!/usr/bin/env python3
"""ag_voltarget.py — VOLATILITY-TARGETED SIZING on the validated my-V3 combined base.

ANGLE: scale position notional inversely to realized vol so per-trade risk is ~constant —
smaller size when vol is high (chop/blowoff), bigger in calm trends. Goal: smoother equity /
lower DD at similar CAGR. Beat ret/DD 2.24 and ideally cut the -43% DD.

VALIDATED BASE (reproduced byte-for-byte first; see repro at bottom of CAGR/DD/OOS print):
  LONG = bull & (price>SMA(9mo)); atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0
  SHORT = (drop>10%/40d) & (daily MACD<sig); short_size=0.40, s_atr=5.0, s_cap=0.15, s_pyr_r=None
  Combined full 2017-2026: CAGR 96%, DD -43%, ret/DD 2.24, OOS 3.05, green every year.

ANTI-LOOKAHEAD: realized vol rv = rolling std of 4h log-returns. The size multiplier is
  target_size = clip(vol_target / rv_causal, lo, hi)
where rv is computed on CLOSED bars and the whole signal is .shift(1) before use at bar i
(entry fills at bar i+1 open, so this is fully causal — no full-sample stats anywhere).
vol_target itself is a fixed CONSTANT (a chosen annualized-vol budget expressed in 4h-return
units), NOT derived from the sample, so there is no full-sample-median lookahead.

We also realize and report PEAK LEVERAGE actually used (max notional/equity at entry).
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_ext import build, BPD
from backtest_myv3_shorts import m

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT
EXT = os.path.join(bt.CACHE, "BTCUSDT_1h_binance_ext.csv")


# ----- signals (task base) -----
def signals(df, bull):
    c = df["close"]
    sma9 = bt.sma(c, 9*30*BPD).shift(1)
    long_sig = (bull & (c > sma9)).fillna(False).values
    raw = pd.read_csv(EXT, parse_dates=["timestamp"])
    dd = raw.set_index("timestamp").resample("1D").agg({"close":"last"}).dropna().reset_index()
    e12 = bt.ema(dd["close"],12); e26 = bt.ema(dd["close"],26)
    macd = e12 - e26; sig = bt.ema(macd,9)
    macd_dn = (macd < sig).shift(1).fillna(False).astype(bool)
    ts = pd.to_datetime(df["timestamp"])
    macd_dn_4h = pd.merge_asof(pd.DataFrame({"ts":ts}).sort_values("ts"),
        pd.DataFrame({"ts":pd.to_datetime(dd["timestamp"]),"b":macd_dn.values}).sort_values("ts"),
        on="ts", direction="backward")["b"].fillna(False).astype(bool).values
    hh40 = c.rolling(40*BPD).max().shift(1)
    drop10 = ((c/hh40 - 1) < -0.10).fillna(False).values
    short_sig = drop10 & macd_dn_4h
    return long_sig, short_sig


# ----- causal realized-vol size multiplier -----
def vol_size(df, lookback, vol_target, lo, hi):
    """Per-bar size multiplier, CAUSAL. rv = rolling std of 4h log-returns over `lookback`,
    then shift(1) so bar i only sees data through bar i-1. vol_target is a fixed constant."""
    c = df["close"]
    ret = np.log(c / c.shift(1))
    rv = ret.rolling(lookback).std()
    mult = (vol_target / rv).clip(lo, hi)
    # shift to be strictly causal at decision bar i; before warmup default to 1.0 (neutral)
    mult = mult.shift(1).fillna(1.0).values
    return mult


def vol_size_neutral(df, lookback, lo, hi):
    """CAUSAL vol-targeting that is risk-NEUTRAL in budget: the multiplier is the causal vol
    ratio re-centered so its running (expanding) mean stays ~1.0. This removes the hidden
    leverage that a fixed vol_target injects, isolating the SHAPE of vol-targeting (cut size in
    high vol, add in low vol) at MATCHED average exposure. Re-centering uses an EXPANDING mean
    of PAST values only (shift 1) — never the full sample, so no lookahead."""
    c = df["close"]
    ret = np.log(c / c.shift(1))
    rv = ret.rolling(lookback).std()
    inv = (1.0 / rv)                      # bigger when calm, smaller when wild
    # causal normalizer: expanding mean of inv using only past bars
    norm = inv.shift(1).expanding(min_periods=lookback).mean()
    mult = (inv / norm).clip(lo, hi)
    mult = mult.shift(1).fillna(1.0).values
    return mult


# ----- engine: my-V3 run() with a per-bar size multiplier on entry notional -----
def run_vt(df, bull, bear, size_long=None, size_short=None,
           allow_long=True, allow_short=True,
           short_size=0.40, pyr_r=2.0, pyr_frac=1.0, be_r=1.0,
           atr_mult=3.5, sl_cap=0.12, be_buf=0.01,
           s_atr=5.0, s_cap=0.15, s_pyr_r=None):
    o=df["open"].values; h=df["high"].values; l=df["low"].values; c=df["close"].values
    a=bt.atr(df,14).values; n=len(df)
    if size_long is None: size_long = np.ones(n)
    if size_short is None: size_short = np.ones(n)
    cash=1.0; units=0.0; side=0; entry=0.0; stop=0.0; R=0.0; pyrd=False; notional0=0.0
    armedL=True; armedS=True
    eq=np.ones(n); peak_lev=0.0
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
                    st=max(c[i]-atr_mult*a[i], c[i]*(1-sl_cap)); entry=oN*(1+SLIP); sz=size_long[i]
                else:
                    st=min(c[i]+s_atr*a[i], c[i]*(1+s_cap)); entry=oN*(1-SLIP); sz=short_size*size_short[i]
                ok = (entry-st>0) if go==1 else (st-entry>0)
                if ok:
                    notional0 = sz*E
                    units = go*notional0/entry; cash = E - units*entry - notional0*FEE
                    stop=st; R=abs(entry-st); side=go; pyrd=False
                    lev_now = abs(notional0)/E if E>0 else 0.0
                    if lev_now>peak_lev: peak_lev=lev_now
                    if go==1: armedL=False
                    else: armedS=False
        eq[i+1] = cash + units*cN
    s = pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]
    return s, peak_lev


def yr(s,y):
    seg=s[s.index.year==y]; return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg)>20 else 0.0


def evalcfg(df, long_sig, short_sig, size_long, size_short):
    s,plev = run_vt(df, long_sig, short_sig, size_long=size_long, size_short=size_short)
    cg,ddn,rr = m(s)
    o = m(s[s.index>=s.index[int(len(s)*0.6)]])
    return s, cg, ddn, rr, o[2], plev


def avg_entry_size(df, long_sig, sizes):
    """Avg size multiplier actually applied AT LONG-ENTRY bars (transparency on exposure)."""
    idx = np.where(np.asarray(long_sig))[0]
    idx = idx[(idx>=16)&(idx<len(df)-1)]
    return float(np.mean(sizes[idx])) if len(idx) else 1.0


def main():
    df, bull = build()
    long_sig, short_sig = signals(df, bull)
    n = len(df)
    ones = np.ones(n)

    print("="*108)
    print("VOLATILITY-TARGETED SIZING on my-V3 combined (BTC 4h, 2017-2026). Causal rolling-std vol, no lookahead.")
    print("="*108)

    # --- reproduce base (size=1 everywhere) ---
    s0,cg0,dd0,rr0,oo0,pl0 = evalcfg(df, long_sig, short_sig, ones, ones)
    print(f"  {'config':<46}{'CAGR':>6}{'DD':>7}{'r/DD':>7}{'OOS':>7}{'pkLev':>7}")
    print("  "+"-"*100)
    print(f"  {'BASE (size=1, reproduce)':<46}{cg0*100:>5.0f}%{dd0*100:>6.0f}%{rr0:>7.2f}{oo0:>7.2f}{pl0:>7.2f}")
    assert abs(cg0-0.96)<0.02 and abs(dd0+0.43)<0.02, "BASE did not reproduce!"
    print("  -- base reproduced (CAGR~96%, DD~-43%, r/DD~2.24, OOS~3.05) --\n")

    # --- SWEEP ---
    # NOTE ON HONESTY: a FIXED vol_target makes the avg multiplier drift >1 -> that is just
    # LEVERAGE, which trivially raises CAGR (and DD). The real question for vol-targeting is
    # whether reshaping size by vol cuts DD at MATCHED average exposure. So the primary sweep
    # uses the risk-NEUTRAL re-centered multiplier (avg entry size ~1.0). We ALSO run the fixed
    # vol_target version but report avg entry size so its leverage is explicit and discounted.
    results = []
    lookbacks = [30, 60, 90, 120, 180]
    bounds = [(0.5,1.5), (0.5,2.0), (0.3,2.0), (0.3,1.5), (0.7,1.3), (0.6,1.6)]

    print(f"  SWEEP A — risk-NEUTRAL vol-targeting (avg entry size ~1.0; isolates DD shape):")
    print(f"  {'lb  clip      side':<34}{'avgSz':>6}{'CAGR':>6}{'DD':>7}{'r/DD':>7}{'OOS':>7}{'pkLev':>7}")
    print("  "+"-"*100)
    for lb in lookbacks:
        for (lo,hi) in bounds:
            sl = vol_size_neutral(df, lb, lo, hi)
            for both in (False, True):
                ss = sl if both else ones
                s,cg,ddn,rr,oo,pl = evalcfg(df, long_sig, short_sig, sl, ss)
                asz = avg_entry_size(df, long_sig, sl)
                results.append(dict(lb=lb,vt=None,lo=lo,hi=hi,both=both,neutral=True,
                                    cg=cg,dd=ddn,rr=rr,oos=oo,plev=pl,avgsz=asz,s=s))

    # SWEEP B — fixed vol_target (carries leverage; reported but discounted)
    vts = [0.020, 0.025, 0.030, 0.035, 0.040]
    for lb in lookbacks:
        for vt in vts:
            for (lo,hi) in bounds:
                sl = vol_size(df, lb, vt, lo, hi)
                for both in (False, True):
                    ss = sl if both else ones
                    s,cg,ddn,rr,oo,pl = evalcfg(df, long_sig, short_sig, sl, ss)
                    asz = avg_entry_size(df, long_sig, sl)
                    results.append(dict(lb=lb,vt=vt,lo=lo,hi=hi,both=both,neutral=False,
                                        cg=cg,dd=ddn,rr=rr,oos=oo,plev=pl,avgsz=asz,s=s))

    # GATE: green every year (2017-2026) AND OOS>0.
    def green(s):
        return all(yr(s,y) > -1.0 for y in range(2017,2027))

    valid = [r for r in results if green(r["s"]) and r["oos"]>0]

    # PRIMARY ranking: the honest vol-target win = LOWER DD at avg size <=~1.05 (matched exposure).
    matched = [r for r in valid if r["avgsz"] <= 1.05 and r["plev"] <= 1.6]
    matched.sort(key=lambda r: (r["dd"], r["rr"]), reverse=True)  # least-negative DD first

    print(f"\n  Top 8 MATCHED-EXPOSURE configs (avg entry size<=1.05, pkLev<=1.6), ranked by lowest DD:")
    print(f"  {'lb  clip      side  mode':<40}{'avgSz':>6}{'CAGR':>6}{'DD':>7}{'r/DD':>7}{'OOS':>7}{'pkLev':>7}")
    print("  "+"-"*100)
    for r in matched[:8]:
        side = "both" if r["both"] else "long"
        mode = "neut" if r["neutral"] else f"vt{r['vt']:.3f}"
        lab = f"{r['lb']:<4}[{r['lo']},{r['hi']}]  {side:<4} {mode}"
        f1 = " DD<base" if r["dd"]>dd0 else ""
        f2 = " r/DD>base" if r["rr"]>rr0 else ""
        print(f"  {lab:<40}{r['avgsz']:>6.2f}{r['cg']*100:>5.0f}%{r['dd']*100:>6.0f}%{r['rr']:>7.2f}{r['oos']:>7.2f}{r['plev']:>7.2f}{f1}{f2}")

    # SECONDARY ranking: best ret/DD overall (incl. leveraged) — full transparency on avg size.
    by_rr = sorted(valid, key=lambda r:(round(r["rr"],3),-r["dd"]), reverse=True)
    print(f"\n  Top 8 by ret/DD overall (incl. leveraged — avgSz shows the real exposure):")
    print(f"  {'lb  clip      side  mode':<40}{'avgSz':>6}{'CAGR':>6}{'DD':>7}{'r/DD':>7}{'OOS':>7}{'pkLev':>7}")
    print("  "+"-"*100)
    for r in by_rr[:8]:
        side = "both" if r["both"] else "long"
        mode = "neut" if r["neutral"] else f"vt{r['vt']:.3f}"
        lab = f"{r['lb']:<4}[{r['lo']},{r['hi']}]  {side:<4} {mode}"
        print(f"  {lab:<40}{r['avgsz']:>6.2f}{r['cg']*100:>5.0f}%{r['dd']*100:>6.0f}%{r['rr']:>7.2f}{r['oos']:>7.2f}{r['plev']:>7.2f}")

    # BEST 3 = honest matched-exposure winners (fall back to ret/DD if none cut DD)
    valid = matched if matched else by_rr

    best3 = valid[:3]
    print("\n"+"="*108)
    print("  BEST 3 CONFIGS — full breakdown")
    print("="*108)
    for rank,r in enumerate(best3,1):
        side = "both sides" if r["both"] else "long only"
        mode = "risk-neutral (avg size ~1)" if r["neutral"] else f"fixed vol_target={r['vt']}"
        s = r["s"]
        print(f"\n  #{rank}  lookback={r['lb']}  clip=[{r['lo']},{r['hi']}]  {mode}  applied to {side}")
        print(f"      CAGR {r['cg']*100:.0f}%   DD {r['dd']*100:.0f}%   ret/DD {r['rr']:.2f}   OOS {r['oos']:.2f}   avg entry size {r['avgsz']:.2f}   peak realized lev {r['plev']:.2f}x")
        # per-bear
        bears = {"2018 bear":(2018,2019),"2022 bear":(2022,2023),"2026 bear":(2026,2027)}
        bd=[]
        for nm,(y0,y1) in bears.items():
            seg = s[(s.index.year>=y0)&(s.index.year<y1)]
            if len(seg)>10:
                d=(seg/seg.cummax()-1).min(); tot=(seg.iloc[-1]/seg.iloc[0]-1)
                bd.append(f"{nm}: ret {tot*100:+.0f}% / DD {d*100:.0f}%")
        print("      bears -> " + " | ".join(bd))
        ys = "  ".join(f"{y}:{yr(s,y):+.0f}%" for y in range(2017,2027))
        print("      years -> " + ys)

    # base bears + years for comparison
    print("\n  --- BASE for comparison ---")
    bears = {"2018 bear":(2018,2019),"2022 bear":(2022,2023),"2026 bear":(2026,2027)}
    bd=[]
    for nm,(y0,y1) in bears.items():
        seg=s0[(s0.index.year>=y0)&(s0.index.year<y1)]
        d=(seg/seg.cummax()-1).min(); tot=(seg.iloc[-1]/seg.iloc[0]-1)
        bd.append(f"{nm}: ret {tot*100:+.0f}% / DD {d*100:.0f}%")
    print(f"      CAGR {cg0*100:.0f}%  DD {dd0*100:.0f}%  ret/DD {rr0:.2f}  OOS {oo0:.2f}  peakLev {pl0:.2f}x")
    print("      bears -> " + " | ".join(bd))
    print("      years -> " + "  ".join(f"{y}:{yr(s0,y):+.0f}%" for y in range(2017,2027)))

    # verdict
    print("\n"+"="*108)
    b = best3[0]
    beats_rr = b["rr"] > rr0
    cuts_dd  = b["dd"] > dd0   # less negative = smaller DD
    print(f"  VERDICT (matched-exposure honest test): best avg-size {b['avgsz']:.2f}, pkLev {b['plev']:.2f}x")
    print(f"           ret/DD = {b['rr']:.2f} vs base {rr0:.2f}  -> {'BEATS' if beats_rr else 'does NOT beat'} base ret/DD")
    print(f"           DD     = {b['dd']*100:.0f}% vs base {dd0*100:.0f}%  -> DD {'IMPROVED' if cuts_dd else 'NOT improved'} at matched exposure")
    # also report the best LEVERAGED ret/DD for completeness
    lev_best = max(valid+by_rr if False else by_rr, key=lambda r:r["rr"])
    print(f"  NOTE: best ret/DD WITH leverage = {lev_best['rr']:.2f} (CAGR {lev_best['cg']*100:.0f}%, DD {lev_best['dd']*100:.0f}%, "
          f"avgSz {lev_best['avgsz']:.2f}, pkLev {lev_best['plev']:.2f}x) — that is mostly leverage, not vol-shape.")
    print("="*108)


if __name__=="__main__":
    main()
