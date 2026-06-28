#!/usr/bin/env python3
"""btcv2_monthly.py — month-by-month reality of the LIVE btcv2 (config D) equity curve.
Answers: does it actually deliver ~10%/month, how consistent, worst month, hold length?
"""
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from short_deepen_engine import run_ec3
from backtest_btclong_ethshort import load4h


def build_llev():
    btc, _ = load4h("BTCUSDT"); eth, _ = load4h("ETHUSDT")
    common = pd.Index(btc["timestamp"]).intersection(pd.Index(eth["timestamp"]))
    b = btc[btc["timestamp"].isin(common)].reset_index(drop=True); c = b["close"]
    adx = bt.adx(b, 14).shift(1).fillna(0).values
    egap = ((bt.ema(c, 50) - bt.ema(c, 200)) / bt.ema(c, 200)).shift(1).fillna(0).values
    conv = np.clip(adx / 35.0, 0, 1) * 0.5 + np.clip(egap / 0.12, 0, 1) * 0.5
    return 1.0 + 1.5 * conv


eq = run_ec3("eth", long_lev=build_llev(), size_lo=0.5, size_mid=1.0, size_hi=1.0,
             drop_look=35, s_atr=6.0, s_cap=0.20, strail_k=3.5, strail_arm_R=1.0)

m = eq.resample("ME").last().pct_change().dropna()
print(f"Months total: {len(m)}")
print(f"Average month:   {m.mean():>7.1%}   (target = +10%)")
print(f"Median month:    {m.median():>7.1%}")
print(f"Positive months: {(m > 0).mean():>7.1%}")
print(f"Months >= +10%:  {(m >= 0.10).mean():>7.1%}")
print(f"Best month:      {m.max():>7.1%}")
print(f"Worst month:     {m.min():>7.1%}")
print(f"Std of monthly:  {m.std():>7.1%}   (volatility of the income)")
print()
# distribution buckets
buckets = [(-1, -0.2), (-0.2, -0.1), (-0.1, 0), (0, 0.1), (0.1, 0.2), (0.2, 1)]
print("Monthly return distribution:")
for lo, hi in buckets:
    cnt = ((m > lo) & (m <= hi)).sum()
    bar = "#" * cnt
    print(f"  {lo:>5.0%}..{hi:>4.0%}: {cnt:>3}  {bar}")
print()
# how many consecutive red months in worst stretch
sign = (m > 0).astype(int).values
worst_red = cur = 0
for s in sign:
    cur = 0 if s else cur + 1
    worst_red = max(worst_red, cur)
print(f"Longest losing streak: {worst_red} months in a row")
# recent / OOS half
oos = m[m.index >= m.index[int(len(m)*0.6)]]
print(f"\nOOS half (recent {len(oos)} months): avg {oos.mean():.1%}, "
      f"positive {(oos>0).mean():.0%}, worst {oos.min():.1%}")
