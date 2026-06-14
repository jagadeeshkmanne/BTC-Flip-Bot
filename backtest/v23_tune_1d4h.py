#!/usr/bin/env python3
"""v23_tune_1d4h.py — disciplined IS/OOS parameter tuning of the v2.3 regime
router on the only viable combo (1d regime / 4h exec, PF>1 baseline).

Sweeps RSI thresholds x gap x SL x TP x HTF-RSI confirmation. Split:
  IS  = ..2024-01-01   OOS = 2024-01-01..
A config only counts as ROBUST if PF>1 in BOTH halves (the repo's standard —
in-sample-only winners are overfit noise). 2x leverage for net%.
"""
from __future__ import annotations
import os, sys, itertools
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v23_timeframe_sweep import run_combo

SPLIT = pd.Timestamp("2024-01-01")
RTF, ETF, LEV = "1d", "4h", 2.0
GRID = dict(
    rsi=[(25, 75), (30, 70), (35, 65)],
    gap_mult=[0.5, 1.0, 1.5],
    sl_mult=[0.7, 1.0, 1.5],
    tp_mult=[0.7, 1.0, 1.5],
    htf_rsi=[None, 50],
)


def metrics(**kw):
    full = run_combo(RTF, ETF, lev=LEV, **kw)
    is_ = run_combo(RTF, ETF, lev=LEV, t1=SPLIT, **kw)
    oos = run_combo(RTF, ETF, lev=LEV, t0=SPLIT, **kw)
    return full, is_, oos


def main():
    rows = []
    combos = list(itertools.product(*GRID.values()))
    print(f"sweeping {len(combos)} configs on {RTF}/{ETF} (IS/OOS split {SPLIT.date()})…",
          file=sys.stderr)
    for vals in combos:
        kw = dict(zip(GRID.keys(), vals))
        (os_, ob) = kw.pop("rsi"); kw["rsi_os"], kw["rsi_ob"] = os_, ob
        full, is_, oos = metrics(**kw)
        if full.get("trades", 0) < 30 or is_.get("trades", 0) < 10 or oos.get("trades", 0) < 10:
            continue
        rows.append(dict(rsi=f"{os_}/{ob}", gap=kw["gap_mult"], sl=kw["sl_mult"],
                         tp=kw["tp_mult"], htf=kw["htf_rsi"] or "-",
                         is_pf=is_["pf"], is_net=is_["net"], oos_pf=oos["pf"],
                         oos_net=oos["net"], full_pf=full["pf"], n=full["trades"]))
    df = pd.DataFrame(rows)
    robust = df[(df.is_pf > 1) & (df.oos_pf > 1)].sort_values("oos_pf", ascending=False)
    print(f"\n{len(df)} valid configs | {len(robust)} ROBUST (PF>1 in BOTH halves)\n")
    print("=== top 12 by OOS PF (ROBUST only) ===")
    hdr = f"{'rsi':<7}{'gap':>5}{'sl':>5}{'tp':>5}{'htf':>5}{'IS_pf':>7}{'IS%':>7}{'OOS_pf':>8}{'OOS%':>7}{'N':>6}"
    print(hdr); print("-"*len(hdr))
    for _, r in robust.head(12).iterrows():
        print(f"{r.rsi:<7}{r.gap:>5}{r.sl:>5}{r.tp:>5}{str(r.htf):>5}{r.is_pf:>7.2f}"
              f"{r.is_net:>+7.0f}{r.oos_pf:>8.2f}{r.oos_net:>+7.0f}{r.n:>6}")
    print("\n=== baseline (RSI30/70, all mults 1.0, no HTF) for reference ===")
    b = df[(df.rsi=="30/70")&(df.gap==1.0)&(df.sl==1.0)&(df.tp==1.0)&(df.htf=="-")]
    if len(b):
        r=b.iloc[0]; print(f"  IS_pf {r.is_pf:.2f} ({r.is_net:+.0f}%) | OOS_pf {r.oos_pf:.2f} ({r.oos_net:+.0f}%) | N {r.n}")
    print(f"\nBest OOS PF overall: {df.oos_pf.max():.2f} | median OOS PF: {df.oos_pf.median():.2f}")


if __name__ == "__main__":
    main()
