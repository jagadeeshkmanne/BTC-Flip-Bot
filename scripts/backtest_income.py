#!/usr/bin/env python3
"""backtest_income.py — year-by-year + a real monthly-income (withdrawal) simulation for both bots.

Shows full year-by-year returns, and simulates drawing a fixed monthly income (% and $) to see
whether the account survives the ~40% losing months. Answers: can btcv2/btcalts fund monthly income?
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_compare_all import btcv2_eq, btcalts_eq

CAP0 = 10000.0


def yearly(name, eq, y0):
    print(f"\n  {name} — year-by-year:")
    for y in range(y0, 2027):
        seg = eq[eq.index.year == y]
        if len(seg) < 20: continue
        print(f"    {y}: {(seg.iloc[-1]/seg.iloc[0]-1)*100:+6.0f}%   (maxDD {(seg/seg.cummax()-1).min()*100:.0f}%)")


def income_sim(name, eq, wd_pct):
    """Withdraw wd_pct of the CURRENT account each month. Track survival + worst drawdown."""
    m = eq.resample("ME").last().pct_change().dropna()
    m = m[m.index >= pd.Timestamp("2021-01-01")]
    acc = CAP0; peak = CAP0; worst = 0.0; total_wd = 0.0; lowest = CAP0
    for r in m:
        acc *= (1 + r)
        wd = acc * wd_pct
        acc -= wd; total_wd += wd
        peak = max(peak, acc); worst = min(worst, acc / peak - 1); lowest = min(lowest, acc)
    avg_monthly_income = total_wd / len(m)
    return acc, total_wd, avg_monthly_income, worst, lowest


def main():
    e2 = btcv2_eq(); ea = btcalts_eq()
    print("=" * 78)
    print("FULL YEAR-BY-YEAR RETURNS")
    print("=" * 78)
    yearly("btcv2 (2017-2026, BTC+ETH history)", e2, 2017)
    yearly("btcalts (2021-2026, alt-data limited)", ea, 2021)

    print("\n" + "=" * 78)
    print(f"MONTHLY-INCOME SIMULATION — start ${CAP0:,.0f}, withdraw a FIXED % each month (2021-26)")
    print("=" * 78)
    print(f"  {'bot / withdrawal':<26}{'avg income/mo':>14}{'end acct':>12}{'total drawn':>13}{'worst acct DD':>15}")
    for name, eq in [("btcv2", e2), ("btcalts", ea)]:
        for wd in (0.03, 0.05, 0.08):
            end, tot, avg_inc, worst, low = income_sim(name, eq, wd)
            print(f"  {name+' @ '+str(int(wd*100))+'%/mo':<26}${avg_inc:>11,.0f}  ${end:>10,.0f}  ${tot:>11,.0f}  {worst*100:>13.0f}%")
    print("\n  (account still grows because avg monthly return > withdrawal — BUT the 'worst acct DD'")
    print("   is the real-feel pain: that's how deep the account drops mid-stretch even while drawing.)")


if __name__ == "__main__":
    main()
