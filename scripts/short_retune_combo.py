#!/usr/bin/env python3
"""short_retune_combo.py — joint combo of marginally-positive moves + perturbation hard-verify."""
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from short_deepen_engine import run_ec3
from backtest_v2_eth_conviction import yr
from backtest_myv3_final import m
from short_retune_sweep import LLEV, D


def stats(**kw):
    args = dict(D); args.update(kw)
    s = run_ec3(**args)
    cg, dd, rr = m(s)
    oos = m(s[s.index >= s.index[int(len(s) * 0.6)]])[2]
    ys = {y: yr(s, y) for y in range(2018, 2027)}
    green = all(v >= -1.0 for v in ys.values())
    return dict(cg=cg, dd=dd, rr=rr, oos=oos, ys=ys, green=green)


def fmt(label, r):
    flag = "grn" if r["green"] else "RED"
    print(f"  {label:<34}CAGR {r['cg']*100:>5.0f}%  DD {r['dd']*100:>5.0f}%  r/DD {r['rr']:>5.2f}  "
          f"OOS {r['oos']:>5.2f}  2018 {r['ys'][2018]:>+5.0f}  2022 {r['ys'][2022]:>+4.0f}  {flag}")


def main():
    base = stats()
    print("CONFIG D baseline:")
    fmt("D", base)

    # best combo of marginally-positive single moves
    combo = dict(s_atr=5.0, strail_k=3.0, size_lo=0.5, size_mid=0.75, size_hi=1.0)
    print("\nBEST JOINT COMBO (s_atr=5, strail_k=3.0, size 0.5/0.75/1.0):")
    c = stats(**combo)
    fmt("combo", c)
    print("    full year-by-year: " + "  ".join(f"{y}:{c['ys'][y]:+.0f}%" for y in range(2018, 2027)))

    # also the more-aggressive size combo that lifted CAGR
    combo2 = dict(s_atr=5.0, strail_k=3.0, size_lo=0.5, size_mid=1.0, size_hi=1.25)
    print("\nALT COMBO (s_atr=5, strail_k=3.0, size 0.5/1.0/1.25):")
    c2 = stats(**combo2)
    fmt("combo2", c2)

    # ---- perturbation: nudge EVERY changed param +/-1 step ----
    print("\n" + "=" * 90)
    print("PERTURBATION of best combo — nudge each changed param +/-1 step")
    print("=" * 90)
    grid = {
        "s_atr": [4.0, 6.0],          # +/- around 5
        "strail_k": [2.5, 3.5],       # +/- around 3.0
        "size_mid": [0.5, 1.0],       # +/- around 0.75 (step from sweep)
    }
    worst = None
    for k, vals in grid.items():
        for v in vals:
            kw = dict(combo); kw[k] = v
            r = stats(**kw)
            fmt(f"{k}={v}", r)
            if worst is None or r["rr"] < worst[1]:
                worst = (f"{k}={v}", r["rr"], r)
    print(f"\nWORST NEIGHBOR: {worst[0]}  ->  r/DD {worst[1]:.2f}  (D=5.61)")


if __name__ == "__main__":
    main()
