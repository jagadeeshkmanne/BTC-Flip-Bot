#!/usr/bin/env python3
"""ftune_long_ext.py — fine-tune the LONG side of my-V3 on FULL 2017-2026 data (incl. 2018 bear).

Long gate = bull AND (optional macro filter: price > SMA(N months)).
We use backtest_myv3_final.run() (params: atr_mult, sl_cap, be_r, pyr_r, pyr_frac) with
allow_short=False, passing `bull & macro` as the bull arg.

Sweep (joint):
  macro SMA: OFF, N in {6,8,9,10,12} months   (macro = c > sma(c, N*30*6).shift(1))
  atr_mult {3,3.5,4,4.5}, sl_cap {0.08,0.12}, be_r {1.0,1.5}, pyr_r {2,3}, pyr_frac {0.5,1.0,1.5}

For each config report: CAGR, maxDD, ret/DD, OOS ret/DD, per-bear returns (2018/2021/2022/2024/
2025/2026), #trades.

Anti-bug: no-macro long-only must reproduce CAGR ~78% / DD -43% / ret/DD 1.83. SMA uses .shift(1)
(no lookahead). Distrust ret/DD>5. Stops only ratchet up (engine enforces this).

GOAL: most ROBUST long-only — high ret/DD, 2018 >= 0, minimal DD for the return, green/flat each
year. Also check whether the macro lookback is a PLATEAU (8/9/10 all work => robust) or a
KNIFE-EDGE (only 9 works => overfit).
"""
from __future__ import annotations
import os, sys, itertools
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_ext import build
from backtest_myv3_final import run, m

BPD = 6  # 4h bars per day
BEAR_YEARS = (2018, 2021, 2022, 2024, 2025, 2026)


def run_traced(df, gate, **kw):
    """Re-run the SAME engine but also count long entries. Mirrors backtest_myv3_final.run()."""
    FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT
    bull = np.asarray(gate, bool)
    bear = np.zeros(len(df), bool)
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    a = bt.atr(df, 14).values; n = len(df)
    lev = kw.get("lev", 1.0)
    pyr_r = kw.get("pyr_r", 2.0); pyr_frac = kw.get("pyr_frac", 1.0)
    be_r = kw.get("be_r", 1.0); atr_mult = kw.get("atr_mult", 3.5)
    sl_cap = kw.get("sl_cap", 0.08); be_buf = kw.get("be_buf", 0.01)
    trail_k = kw.get("trail_k", None)
    cash = 1.0; units = 0.0; side = 0; entry = 0.0; stop = 0.0; R = 0.0; pyrd = False; notional0 = 0.0
    peakH = 0.0; armedL = True; armedS = True
    eq = np.ones(n); ntr = 0
    for i in range(16, n - 1):
        oN, hN, lN, cN = o[i+1], h[i+1], l[i+1], c[i+1]
        if not bull[i]: armedL = True
        if not bear[i]: armedS = True
        if side != 0:
            if side == 1 and trail_k is not None:
                peakH = max(peakH, h[i]); stop = max(stop, peakH - trail_k*a[i])
            hit = (lN <= stop) if side == 1 else (hN >= stop)
            regime_out = (not bull[i]) if side == 1 else (not bear[i])
            if hit:
                fpx = stop*(1-SLIP) if side == 1 else stop*(1+SLIP)
                cash += units*fpx - abs(units)*fpx*FEE; units = 0.0; side = 0; pyrd = False
            elif regime_out:
                fpx = oN*(1-SLIP) if side == 1 else oN*(1+SLIP)
                cash += units*fpx - abs(units)*fpx*FEE; units = 0.0; side = 0; pyrd = False
            else:
                prof = ((cN-entry)/R) if side == 1 else ((entry-cN)/R)
                if prof >= be_r:
                    be = entry*(1+be_buf) if side == 1 else entry*(1-be_buf)
                    stop = max(stop, be) if side == 1 else min(stop, be)
                pr = pyr_r
                if pr is not None and not pyrd and prof >= pr:
                    addn = pyr_frac*notional0
                    add_units = (addn/cN) if side == 1 else (-addn/cN)
                    cash -= add_units*cN + addn*FEE; units += add_units; pyrd = True
                    stop = max(stop, entry) if side == 1 else min(stop, entry)
        if side == 0:
            go = 0
            if bull[i] and armedL: go = 1
            if go != 0:
                E = cash
                st = max(c[i]-atr_mult*a[i], c[i]*(1-sl_cap)); entry = oN*(1+SLIP); sz = lev
                ok = (entry-st > 0)
                if ok:
                    notional0 = sz*E
                    units = go*notional0/entry; cash = E - units*entry - notional0*FEE
                    stop = st; R = abs(entry-st); side = go; pyrd = False; peakH = entry
                    armedL = False; ntr += 1
        eq[i+1] = cash + units*cN
    s = pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]
    return s, ntr


def yr(s, y):
    seg = s[s.index.year == y]
    return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg) > 20 else 0.0


def oos_rdd(s):
    o = s[s.index >= s.index[int(len(s)*0.6)]]
    return m(o)[2]


def macro_gate(c, bull, N):
    if N is None:
        return bull
    macro = (c > bt.sma(c, N*30*BPD).shift(1)).values
    return bull & macro


def main():
    df, bull = build()
    c = df["close"]
    zeros = np.zeros(len(df), bool)

    # ---- ANTI-BUG sanity: no-macro long-only default must be 78% / -43% / 1.83 ----
    s0 = run(df, bull, zeros, allow_long=True, allow_short=False)
    cg, dd, rr = m(s0)
    print("="*140)
    print("ANTI-BUG SANITY — no-macro long-only DEFAULT (atr3.5,sl.08,be1,pyr2,frac1)")
    print(f"  CAGR {cg*100:.0f}%  DD {dd*100:.0f}%  ret/DD {rr:.2f}   (expect 78% / -43% / 1.83)")
    ok = abs(cg*100-78) <= 1 and abs(dd*100+43) <= 1 and abs(rr-1.83) <= 0.03
    print(f"  SANITY {'PASS' if ok else 'FAIL'}")
    print("="*140)

    macro_opts = [None, 6, 8, 9, 10, 12]
    atr_opts = [3.0, 3.5, 4.0, 4.5]
    slcap_opts = [0.08, 0.12]
    ber_opts = [1.0, 1.5]
    pyrr_opts = [2.0, 3.0]
    pyrf_opts = [0.5, 1.0, 1.5]

    rows = []
    combos = list(itertools.product(macro_opts, atr_opts, slcap_opts, ber_opts, pyrr_opts, pyrf_opts))
    print(f"\nSweeping {len(combos)} configs ...\n")
    for N, atr, slcap, ber, pyrr, pyrf in combos:
        gate = macro_gate(c, bull, N)
        kw = dict(atr_mult=atr, sl_cap=slcap, be_r=ber, pyr_r=pyrr, pyr_frac=pyrf, be_buf=0.01)
        s, ntr = run_traced(df, gate, **kw)
        cg, dd, rr = m(s)
        o = oos_rdd(s)
        bears = {y: yr(s, y) for y in BEAR_YEARS}
        rows.append(dict(N=("OFF" if N is None else N), atr=atr, slcap=slcap, ber=ber,
                         pyrr=pyrr, pyrf=pyrf, cagr=cg*100, dd=dd*100, rdd=rr, oos=o,
                         ntr=ntr, **{f"y{y}": bears[y] for y in BEAR_YEARS},
                         s=s))
    R = pd.DataFrame(rows)

    # ---- VERIFY traced engine matches run() on a couple of configs ----
    for N, atr in [(None, 3.5), (9, 3.5)]:
        gate = macro_gate(c, bull, N)
        sref = run(df, gate, zeros, allow_long=True, allow_short=False,
                   atr_mult=atr, sl_cap=0.08, be_r=1.0, pyr_r=2.0, pyr_frac=1.0, be_buf=0.01)
        strc, _ = run_traced(df, gate, atr_mult=atr)
        diff = float((sref - strc).abs().max())
        print(f"  [trace-check N={N} atr{atr}] max|run - run_traced| = {diff:.2e}  "
              f"{'OK' if diff < 1e-9 else 'MISMATCH'}")
    print()

    # ---- ROBUSTNESS HEADER PRINT ----
    cols = ["N", "atr", "slcap", "ber", "pyrr", "pyrf", "cagr", "dd", "rdd", "oos", "ntr"] \
        + [f"y{y}" for y in BEAR_YEARS]
    hdr = (f"{'N':>4}{'atr':>5}{'slc':>6}{'beR':>5}{'pyR':>5}{'pyF':>5}"
           f"{'CAGR':>7}{'DD':>6}{'r/DD':>6}{'OOS':>6}{'tr':>5}"
           f"{'2018':>7}{'2021':>7}{'2022':>7}{'2024':>7}{'2025':>7}{'2026':>7}")

    def prow(r):
        return (f"{str(r['N']):>4}{r['atr']:>5}{r['slcap']:>6}{r['ber']:>5}{r['pyrr']:>5}{r['pyrf']:>5}"
                f"{r['cagr']:>6.0f}%{r['dd']:>5.0f}%{r['rdd']:>6.2f}{r['oos']:>6.2f}{r['ntr']:>5}"
                f"{r['y2018']:>+6.0f}%{r['y2021']:>+6.0f}%{r['y2022']:>+6.0f}%"
                f"{r['y2024']:>+6.0f}%{r['y2025']:>+6.0f}%{r['y2026']:>+6.0f}%")

    # ---- TOP by ret/DD (all configs), flag suspicious ----
    print("="*140)
    print("TOP 20 by ret/DD (ALL configs):")
    print(hdr)
    top = R.sort_values("rdd", ascending=False).head(20)
    for _, r in top.iterrows():
        flag = "  <-- ret/DD>5 SUSPECT" if r["rdd"] > 5 else ""
        print(prow(r) + flag)

    # ---- ROBUST filter: 2018>=0 AND every bear-year >= -2% (green/flat) ----
    yr_cols = [f"y{y}" for y in BEAR_YEARS]
    robust = R[(R["y2018"] >= 0) & (R[yr_cols].min(axis=1) >= -2.0) & (R["rdd"] <= 5.0)]
    print("\n" + "="*140)
    print(f"ROBUST configs (2018>=0 AND all bear-years>=-2% AND ret/DD<=5):  {len(robust)} found")
    print("TOP 15 ROBUST by ret/DD:")
    print(hdr)
    for _, r in robust.sort_values("rdd", ascending=False).head(15).iterrows():
        print(prow(r))

    # ---- BEST 3 ROBUST: full metrics + year-by-year for winner ----
    best3 = robust.sort_values("rdd", ascending=False).head(3)
    print("\n" + "="*140)
    print("BEST 3 ROBUST LONG CONFIGS (full):")
    print(hdr)
    for _, r in best3.iterrows():
        print(prow(r))

    if len(best3):
        w = best3.iloc[0]
        print("\n  WINNER year-by-year:")
        yrs = sorted(set(w["s"].index.year))
        print("   " + "  ".join(f"{y}:{yr(w['s'], y):+.0f}%" for y in yrs))

    # ---- MACRO-LOOKBACK PLATEAU vs KNIFE-EDGE ----
    # Hold other params at the WINNER's (or default) and sweep N to see if 8/9/10 cluster.
    print("\n" + "="*140)
    print("MACRO-LOOKBACK ROBUSTNESS (plateau vs knife-edge):")
    # (a) at default params
    print("  At DEFAULT params (atr3.5,sl.08,be1,pyr2,frac1):")
    print(f"    {'N':>5}{'CAGR':>7}{'DD':>6}{'r/DD':>6}{'OOS':>6}{'2018':>7}{'tr':>5}")
    for N in [None, 6, 7, 8, 9, 10, 11, 12]:
        gate = macro_gate(c, bull, N)
        s, ntr = run_traced(df, gate, atr_mult=3.5, sl_cap=0.08, be_r=1.0, pyr_r=2.0, pyr_frac=1.0)
        cg, dd, rr = m(s)
        print(f"    {str('OFF' if N is None else N):>5}{cg*100:>6.0f}%{dd*100:>5.0f}%"
              f"{rr:>6.2f}{oos_rdd(s):>6.2f}{yr(s,2018):>+6.0f}%{ntr:>5}")
    # (b) at the winner's non-N params
    if len(best3):
        w = best3.iloc[0]
        if w["N"] != "OFF":
            print(f"  At WINNER's params (atr{w['atr']},sl{w['slcap']},be{w['ber']},"
                  f"pyr{w['pyrr']},frac{w['pyrf']}):")
            print(f"    {'N':>5}{'CAGR':>7}{'DD':>6}{'r/DD':>6}{'OOS':>6}{'2018':>7}{'tr':>5}")
            for N in [None, 6, 7, 8, 9, 10, 11, 12]:
                gate = macro_gate(c, bull, N)
                s, ntr = run_traced(df, gate, atr_mult=w["atr"], sl_cap=w["slcap"],
                                    be_r=w["ber"], pyr_r=w["pyrr"], pyr_frac=w["pyrf"])
                cg, dd, rr = m(s)
                print(f"    {str('OFF' if N is None else N):>5}{cg*100:>6.0f}%{dd*100:>5.0f}%"
                      f"{rr:>6.2f}{oos_rdd(s):>6.2f}{yr(s,2018):>+6.0f}%{ntr:>5}")

    print(f"\n  Full sweep: {len(R)} configs evaluated.")


if __name__ == "__main__":
    main()
