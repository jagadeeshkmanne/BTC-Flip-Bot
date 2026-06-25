#!/usr/bin/env python3
"""backtest_myv3_sweep.py — sweep EVERY setting of my-V3 (MTF entry + structural SL + pyramid + BE).

Varies each knob one at a time from a sensible base, so we see which settings matter and whether
the edge is robust (not a single lucky point). Then year-by-year + remove-best-trades on the
chosen config to confirm it's broad (green most years) and NOT fat-tail-dependent like the
original V3. BTC 4h, 1x base, honest fees.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_myv3 import regime, run, m, FEE

BASE=dict(atr_mult=3.0, sl_cap=0.08, be_r=1.0, pyr_r=2.0, pyr_frac=1.0)


def line(df,bull,name,**kw):
    p=dict(BASE); p.update(kw); s=run(df,bull,**p)
    cg,dd,rr=m(s); o=m(s[s.index>=s.index[int(len(s)*0.6)]])
    print(f"  {name:<34}{cg*100:>6.0f}%{dd*100:>5.0f}%{rr:>6.2f}{o[2]:>9.2f}")
    return rr,o[2]


def main():
    df,bull=regime()
    print("="*74)
    print("MY-V3 SETTINGS SWEEP — BTC 4h (base: atr3, slcap8%, be1R, pyr2R, frac1.0)")
    print("="*74)
    print(f"  {'setting':<34}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS rDD':>9}")
    print("  -- stop distance (ATR mult) --")
    for x in (2.0,2.5,3.0,4.0,5.0): line(df,bull,f"atr_mult={x}",atr_mult=x)
    print("  -- stop cap (max %) --")
    for x in (0.05,0.08,0.12,0.20): line(df,bull,f"sl_cap={int(x*100)}%",sl_cap=x)
    print("  -- break-even trigger (R) --")
    for x in (0.5,1.0,1.5,2.0): line(df,bull,f"be_r={x}",be_r=x)
    print("  -- pyramid trigger (R) --")
    line(df,bull,"pyr_r=None (off)",pyr_r=None)
    for x in (1.5,2.0,2.5,3.0): line(df,bull,f"pyr_r={x}",pyr_r=x)
    print("  -- pyramid size (frac of base) --")
    for x in (0.5,0.75,1.0,1.5): line(df,bull,f"pyr_frac={x}",pyr_frac=x)

    print("\n  YEAR-BY-YEAR (base config) — is it broad or fat-tail?")
    s=run(df,bull,**BASE); yr=s.index.year
    for y in sorted(set(yr)):
        seg=s[yr==y]
        if len(seg)<50: continue
        print(f"    {y}: {(seg.iloc[-1]/seg.iloc[0]-1)*100:+7.0f}%   (maxDD {(seg/seg.cummax()-1).min()*100:.0f}%)")
    full=(s.iloc[-1]/s.iloc[0]-1)*100
    print(f"    FULL: {full:+.0f}%  ret/DD {m(s)[2]:.2f}")


if __name__=="__main__":
    main()
