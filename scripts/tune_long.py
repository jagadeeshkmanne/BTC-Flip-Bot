#!/usr/bin/env python3
"""tune_long.py — optimize the LONG side of my-V3 (BTC 4h) for ROBUSTNESS.

Extends backtest_myv3.run() with a be_buf param (BE moves stop to entry*(1+be_buf)
instead of exactly entry). Verifies the known base byte-for-byte, then coordinate-
descends over the joint sweep, scoring each candidate on:
  CAGR, maxDD, ret/DD, OOS ret/DD (last 40% slice), 3-fold ret/DD, year-by-year.

Honesty rules (unchanged from backtest_myv3): signals on closed bars, fills next open,
fee+slip both sides, stops ratchet UP only, pyramid BORROWS (cash goes negative),
no lookahead.
"""
from __future__ import annotations
import os, sys, itertools
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3 import regime

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT


def run(df, bull, pyr_r=2.0, pyr_frac=1.0, be_r=1.0, atr_mult=3.0, sl_cap=0.08, be_buf=0.005):
    """Honest cash/units engine: 1x base position; pyramid BORROWS to add exposure
    (cash goes negative). Mark equity = cash + units*price.

    EXTENSION vs backtest_myv3.run(): break-even moves the stop to entry*(1+be_buf)
    instead of exactly entry. All other mechanics identical.
    """
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    a = bt.atr(df, 14).values; n = len(df)
    cash = 1.0; units = 0.0; in_pos = False; entry = 0.0; sl = 0.0; R = 0.0
    pyrd = False; armed = True; E_entry = 1.0
    eq = np.ones(n)
    for i in range(16, n - 1):
        oN, lN, cN = o[i + 1], l[i + 1], c[i + 1]
        if not bull[i]: armed = True
        if in_pos:
            if lN <= sl:                                    # stop hit intrabar
                fpx = sl * (1 - SLIP); cash += units * fpx * (1 - FEE); units = 0.0; in_pos = False; pyrd = False
            elif not bull[i]:                               # regime flip -> exit next open
                fpx = oN * (1 - SLIP); cash += units * fpx * (1 - FEE); units = 0.0; in_pos = False; pyrd = False
            else:
                prof = (cN - entry) / R if R > 0 else 0
                if prof >= be_r: sl = max(sl, entry * (1 + be_buf))   # break-even (+buffer)
                if pyr_r is not None and not pyrd and prof >= pyr_r:
                    addn = pyr_frac * E_entry                # add % of original notional (borrowed)
                    cash -= addn * (1 + FEE); units += addn / cN; pyrd = True; sl = max(sl, entry * (1 + be_buf))
        if not in_pos and bull[i] and armed:
            stop = max(c[i] - atr_mult * a[i], c[i] * (1 - sl_cap))
            if c[i] - stop > 0:
                E = cash; entry = oN * (1 + SLIP); R = entry - stop; sl = stop
                units = E / entry; cash -= E * (1 + FEE); E_entry = E; in_pos = True; pyrd = False; armed = False
        eq[i + 1] = cash + units * cN
    s = pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]
    return s


def m(s):
    yrs = max((s.index[-1] - s.index[0]).days / 365.25, 1e-9)
    cg = (s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1 if s.iloc[-1] > 0 else -1
    dd = (s / s.cummax() - 1).min()
    return cg, dd, (cg / abs(dd) if dd < -1e-9 else 0)


def folds(s, k=3):
    """ret/DD of each of k equal-time slices."""
    cuts = [s.index[min(int(len(s) * j / k), len(s) - 1)] for j in range(k + 1)]
    cuts[-1] = s.index[-1] + pd.Timedelta(seconds=1)
    out = []
    for j in range(k):
        sl = s[(s.index >= cuts[j]) & (s.index < cuts[j + 1])]
        out.append(m(sl)[2] if len(sl) > 10 else 0.0)
    return out


def yearly(s):
    """{year: ret%} normalizing each calendar year to its own start."""
    out = {}
    for y, g in s.groupby(s.index.year):
        if len(g) > 1:
            out[y] = (g.iloc[-1] / g.iloc[0] - 1) * 100
    return out


def oos(s, frac=0.6):
    cut = s.index[int(len(s) * frac)]
    return m(s[s.index >= cut])


def score(df, bull, **kw):
    s = run(df, bull, **kw)
    cg, dd, rr = m(s)
    _, _, orr = oos(s)
    f = folds(s)
    yr = yearly(s)
    return dict(cg=cg, dd=dd, rr=rr, orr=orr, folds=f, yr=yr,
                worst_fold=min(f), green=all(v >= -0.01 for v in yr.values()))


# ---------------- main ----------------
GRID = dict(atr_mult=[2.5, 3, 3.5, 4, 4.5], sl_cap=[0.05, 0.08, 0.12],
            be_r=[0.5, 1, 1.5, 2], be_buf=[0, 0.005, 0.01],
            pyr_r=[1.5, 2, 2.5, 3], pyr_frac=[0.5, 0.75, 1.0, 1.5])
BASE = dict(atr_mult=3, sl_cap=0.08, be_r=1.0, pyr_r=2.0, pyr_frac=1.0, be_buf=0.005)


def fmt(tag, r):
    fs = "/".join(f"{x:.2f}" for x in r["folds"])
    return (f"{tag:<52} CAGR {r['cg']*100:>5.0f}%  DD {r['dd']*100:>5.0f}%  "
            f"r/DD {r['rr']:>5.2f}  OOS {r['orr']:>5.2f}  folds {fs}  "
            f"worst {r['worst_fold']:>5.2f}  green {r['green']}")


def main():
    df, bull = regime()

    # ---- VERIFY: reproduce known base ----
    print("=" * 110)
    print("VERIFY base (atr=3,sl_cap=.08,be_r=1,pyr_r=2,pyr_frac=1,be_buf=.005) -> expect CAGR~74% DD~-31% r/DD~2.38 OOS~3.0")
    rb = score(df, bull, **BASE)
    print(fmt("BASE", rb))
    print("=" * 110)

    # ---- COORDINATE DESCENT (2 passes) ----
    cur = dict(BASE)
    order = ["atr_mult", "sl_cap", "be_r", "pyr_r", "pyr_frac", "be_buf"]
    results = {}  # tuple(config) -> score, dedup

    def key(cfg):
        return tuple(cfg[k] for k in order)

    def evalc(cfg):
        kk = key(cfg)
        if kk not in results:
            results[kk] = score(df, bull, **cfg)
        return results[kk]

    def rank_metric(r):
        # robustness: penalize if not green, reward min(OOS, worst_fold, ret/DD)
        if not r["green"]:
            return -1
        return min(r["rr"], r["orr"], r["worst_fold"] + 0.3)

    for p in range(3):
        print(f"\n----- PASS {p+1} -----")
        for axis in order:
            best_v = cur[axis]; best_r = rank_metric(evalc(cur)); best_sc = evalc(cur)
            line = []
            for v in GRID[axis]:
                cfg = dict(cur); cfg[axis] = v
                r = evalc(cfg); mr = rank_metric(r)
                line.append((v, mr, r))
                if mr > best_r + 1e-9:
                    best_r = mr; best_v = v; best_sc = r
            cur[axis] = best_v
            tag = ",".join(f"{a}={cur[a]}" for a in order)
            print(f"  axis {axis:<9} -> {best_v}   [{tag}]  score={best_r:.2f}  "
                  f"r/DD {best_sc['rr']:.2f} OOS {best_sc['orr']:.2f} worstfold {best_sc['worst_fold']:.2f}")
    print(f"\nCOORD-DESCENT WINNER: {cur}")
    print(fmt("WINNER", evalc(cur)))

    # ---- PLATEAU CHECK around winner + collect top configs ----
    # evaluate +/- one neighbor on every axis for the winner
    print("\n----- PLATEAU CHECK (winner +/- 1 step per axis) -----")
    for axis in order:
        vals = GRID[axis]; idx = vals.index(cur[axis]) if cur[axis] in vals else None
        for d in (-1, 1):
            if idx is None: continue
            j = idx + d
            if 0 <= j < len(vals):
                cfg = dict(cur); cfg[axis] = vals[j]
                print(fmt(f"  {axis}={vals[j]}", evalc(cfg)))

    # ---- rank ALL evaluated configs by robustness for top-3 ----
    print("\n----- TOP CONFIGS BY ROBUSTNESS (green, then min(OOS,worst_fold,r/DD)) -----")
    scored = []
    for kk, r in results.items():
        cfg = dict(zip(order, kk))
        scored.append((rank_metric(r), cfg, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    for sc, cfg, r in scored[:8]:
        tag = ",".join(f"{a}={cfg[a]}" for a in order)
        print(fmt(tag, r))

    # ---- year-by-year for the winner ----
    print("\n----- YEAR-BY-YEAR (winner) -----")
    rw = evalc(cur)
    for y in sorted(rw["yr"]):
        print(f"  {y}: {rw['yr'][y]:>7.1f}%")
    print(f"\nBASE for comparison: r/DD {rb['rr']:.2f}  OOS {rb['orr']:.2f}  folds {rb['folds']}  green {rb['green']}")


if __name__ == "__main__":
    main()
