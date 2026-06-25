#!/usr/bin/env python3
"""backtest_portfolio.py — combined btcv2 + btcalts portfolio: overall CAGR/DD + best allocation.

Both are BTC-trend-driven (correlated), but on different instruments. Tests splitting capital
across them (daily-rebalanced constant weights) to see if the combination beats either alone.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_compare_all import btcv2_eq, btcalts_eq

CAP0 = 5000.0


def main():
    e1 = btcv2_eq().resample("1D").last().dropna()
    e2 = btcalts_eq().resample("1D").last().dropna()
    idx = e1.index.intersection(e2.index)
    idx = idx[idx >= pd.Timestamp("2021-01-01")]
    r1 = e1.reindex(idx).pct_change().fillna(0)
    r2 = e2.reindex(idx).pct_change().fillna(0)
    corr = r1.corr(r2)
    yrs = (idx[-1] - idx[0]).days / 365.25
    print("=" * 76)
    print(f"PORTFOLIO: btcv2 + btcalts — combined CAGR/DD ({idx[0].date()}..{idx[-1].date()}, {yrs:.1f}y)")
    print("=" * 76)
    print(f"  return correlation between the two bots: {corr:.2f}  (high = move together)")
    print(f"  {'allocation (btcv2/btcalts)':<30}{'CAGR':>7}{'DD':>7}{'ret/DD':>8}{'$5k->':>14}")
    best = None
    for w in [1.0, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.0]:
        rc = w * r1 + (1 - w) * r2
        ec = (1 + rc).cumprod() * CAP0
        cg = (ec.iloc[-1] / CAP0) ** (1 / yrs) - 1
        dd = (ec / ec.cummax() - 1).min()
        rr = cg / abs(dd) if dd < -1e-9 else 0
        lbl = f"{int(w*100)}/{int((1-w)*100)}"
        if w == 1.0: lbl += " (btcv2 only)"
        elif w == 0.0: lbl += " (btcalts only)"
        if best is None or rr > best[1]: best = (lbl, rr, cg, dd)
        print(f"  {lbl:<30}{cg*100:>6.0f}%{dd*100:>6.0f}%{rr:>8.2f}{ec.iloc[-1]:>13,.0f}")
    print(f"\n  BEST risk-adjusted allocation: {best[0]} -> CAGR {best[2]*100:.0f}%, DD {best[3]*100:.0f}%, ret/DD {best[1]:.2f}")


if __name__ == "__main__":
    main()
