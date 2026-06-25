#!/usr/bin/env python3
"""backtest_v2_export.py — export the deployed V2 config's backtest (year + month-by-month) to JSON.

Deployed config: conviction 1.0-2.5x long BTC + SHORT ETH on BTC's signal + lock 33%@6R.
Writes data/btcv2/backtest.json (summary + year x month grid) for the dashboard.
"""
from __future__ import annotations
import os, sys, json
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_v2_eth_conviction import run_ec
from backtest_btclong_ethshort import load4h

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "btcv2", "backtest.json")


def main():
    btc, _ = load4h("BTCUSDT"); eth, _ = load4h("ETHUSDT")
    common = pd.Index(btc["timestamp"]).intersection(pd.Index(eth["timestamp"]))
    btc = btc[btc["timestamp"].isin(common)].reset_index(drop=True); c = btc["close"]
    adx = bt.adx(btc, 14).shift(1).fillna(0).values
    egap = ((bt.ema(c, 50) - bt.ema(c, 200)) / bt.ema(c, 200)).shift(1).fillna(0).values
    conv = np.clip(adx / 35.0, 0, 1) * 0.5 + np.clip(egap / 0.12, 0, 1) * 0.5
    llev = 1.0 + (2.5 - 1.0) * conv
    s = run_ec("eth", long_lev=llev)
    s = s / s.iloc[0] * 5000.0
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    cagr = (s.iloc[-1] / 5000.0) ** (1 / yrs) - 1
    maxdd = (s / s.cummax() - 1).min()
    r = s.pct_change().fillna(0); sharpe = r.mean() / r.std() * np.sqrt(6 * 365.25)
    years = []
    for y in range(2017, 2027):
        seg = s[s.index.year == y]
        if len(seg) < 20: continue
        years.append({"year": y, "ret_pct": round((seg.iloc[-1] / seg.iloc[0] - 1) * 100),
                      "end_usd": round(seg.iloc[-1]), "maxdd_pct": round((seg / seg.cummax() - 1).min() * 100)})
    monthly = s.resample("ME").last().pct_change().dropna() * 100
    grid = {}
    for ts, val in monthly.items():
        grid.setdefault(str(ts.year), {})[ts.month] = round(float(val))
    out = {
        "config": "V2 conviction 1.0-2.5x long BTC + short ETH on BTC's signal + lock33@6R (4h)",
        "window": "2017-08 .. 2026-06 (full, 3 bears)",
        "start_usd": 5000, "final_usd": round(s.iloc[-1]),
        "cagr_pct": round(cagr * 100), "maxdd_pct": round(maxdd * 100),
        "ret_dd": round(cagr / abs(maxdd), 2), "sharpe": round(sharpe, 2),
        "caveat": "Heavily optimized on this data; early-years compounding reflects BTC/ETH's gone-forever 2018-2020 growth. Expect ~half this CAGR forward, with real -36% drawdowns. [PAPER]",
        "years": years, "monthly": grid,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f: json.dump(out, f, indent=2)
    print(f"wrote backtest.json: {out['cagr_pct']}% CAGR, {out['maxdd_pct']}% DD, ret/DD {out['ret_dd']}, ${out['final_usd']:,} final, {len(years)} yrs")


if __name__ == "__main__":
    main()
