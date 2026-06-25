#!/usr/bin/env python3
"""backtest_demand_zone_years.py — year-by-year P&L of the 4h demand-zone (long-only) vs buy&hold.

Slices BTC 4h history by calendar year, runs the honest engine fresh each year, and reports
net% / maxDD / trades / win% alongside that year's buy&hold. Shows which years carry it.
"""
from __future__ import annotations
import os, sys
import pandas as pd

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "scripts"))
from backtest_demand_zone import backtest, buy_hold

CACHE = os.path.join(HERE, "data/cache")
BASE = dict(left=3, right=3, zone_frac=0.6, stop_buf_atr=0.5, min_rr=1.5)
VARIANTS = {
    "LONG-only":  dict(allow_long=True, allow_short=False),
    "SHORT-only": dict(allow_long=False, allow_short=True),
    "LONG+SHORT": dict(allow_long=True, allow_short=True),
}


def main():
    df = pd.read_csv(os.path.join(CACHE, "BTCUSDT_4h_2019_binance.csv"), parse_dates=["timestamp"])
    df["year"] = df["timestamp"].dt.year
    years = [y for y in sorted(df["year"].unique()) if len(df[df["year"] == y]) >= 200]
    print("=" * 86)
    print("DEMAND-ZONE 4h — year by year (BTC): net% per direction + buy&hold")
    print("=" * 86)
    print(f"  {'year':<6}{'B&H%':>8}   {'LONG%':>8}{'SHORT%':>8}{'L+S%':>8}   {'Lwin%':>7}{'Swin%':>7}")
    comp = {k: 1.0 for k in VARIANTS}; bh_comp = 1.0; green = {k: 0 for k in VARIANTS}
    for yr in years:
        seg = df[df["year"] == yr].reset_index(drop=True)
        bh = buy_hold(seg)[0]; bh_comp *= (1 + bh / 100)
        res = {k: backtest(seg, **BASE, **v) for k, v in VARIANTS.items()}
        for k in VARIANTS:
            comp[k] *= (1 + res[k]["net"] / 100); green[k] += int(res[k]["net"] > 0)
        print(f"  {yr:<6}{bh:>8.1f}   {res['LONG-only']['net']:>8.1f}{res['SHORT-only']['net']:>8.1f}"
              f"{res['LONG+SHORT']['net']:>8.1f}   {res['LONG-only']['wr']:>7.0f}{res['SHORT-only']['wr']:>7.0f}")
    print("-" * 86)
    n = len(years)
    print(f"  {'compounded':<6}{(bh_comp-1)*100:>8.1f}   "
          f"{(comp['LONG-only']-1)*100:>8.1f}{(comp['SHORT-only']-1)*100:>8.1f}{(comp['LONG+SHORT']-1)*100:>8.1f}")
    print(f"  green years (of {n}):        {green['LONG-only']:>8d}{green['SHORT-only']:>8d}{green['LONG+SHORT']:>8d}")


if __name__ == "__main__":
    main()
