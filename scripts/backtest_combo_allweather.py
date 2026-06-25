#!/usr/bin/env python3
"""backtest_combo_allweather.py — fill the MTF Regime's flat years (2022/2025/2026) by combining
it with the btcalts long/short engine (which profits in BTC-bears via alt shorts).

Portfolio = w*MTF-Regime(BTC 4h long/flat) + (1-w)*btcalts-L/S(BTC signal -> eqw ETH/BNB/SOL,
long/short, vol-scaled). Daily-rebalanced. Goal: GREEN every year, including bears. Honest fees.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_mtf_trademgmt import regime as mtf_regime, run as mtf_run

ALTS = ["ETHUSDT", "BNBUSDT", "SOLUSDT"]
FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT


def mtf_daily_returns():
    df4, bull = mtf_regime()
    s = mtf_run(df4, bull, lev=1.0, be=0.10)          # MTF Regime + 10% break-even, 1x
    d = s.resample("1D").last().dropna()
    return d.pct_change().dropna()


def btcalts_daily_returns(F=32, S=800):
    btc = bt.load("BTCUSDT", "1h")
    frames = {a: bt.load(a, "1h") for a in ALTS}
    start = max([btc["timestamp"].iloc[0]] + [frames[a]["timestamp"].iloc[0] for a in ALTS])
    btc = btc[btc["timestamp"] >= start].reset_index(drop=True)
    frames = {a: frames[a][frames[a]["timestamp"] >= start].reset_index(drop=True) for a in ALTS}
    n = min([len(btc)] + [len(frames[a]) for a in ALTS])
    btc = btc.iloc[:n].reset_index(drop=True); frames = {a: frames[a].iloc[:n].reset_index(drop=True) for a in ALTS}
    base = (bt.ema(btc["close"], F) > bt.ema(btc["close"], S)).astype(float).values * 2 - 1  # long/short
    rv = btc["close"].pct_change().rolling(24).std().bfill().values
    pos = base * np.clip(np.nanmedian(rv) / (rv + 1e-9), 0.2, 1.0)
    sig = pd.Series(pos, index=btc.index)
    rr = []
    for a in ALTS:
        held = sig.shift(1).fillna(0).values
        oo = (frames[a]["open"].shift(-1) / frames[a]["open"] - 1).fillna(0).values
        turn = np.abs(np.diff(held, prepend=0.0))
        rr.append(held * oo - turn * (FEE + SLIP))
    r = np.mean(rr, axis=0)
    s = pd.Series(np.cumprod(1 + np.nan_to_num(r)), index=pd.to_datetime(btc["timestamp"]))
    d = s.resample("1D").last().dropna()
    return d.pct_change().dropna()


def stats(r):
    eq = (1 + r).cumprod()
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    return cagr, dd, (cagr / abs(dd) if dd < -1e-9 else 0.0), eq


def main():
    mtf = mtf_daily_returns(); alts = btcalts_daily_returns()
    df = pd.DataFrame({"mtf": mtf, "alts": alts}).dropna()
    print("=" * 80)
    print("ALL-WEATHER COMBO — MTF Regime (bull engine) + btcalts L/S (bear engine)")
    print("=" * 80)
    print(f"  common window: {df.index[0].date()} -> {df.index[-1].date()}")
    print(f"  {'mix':<22}{'CAGR':>8}{'maxDD':>8}{'ret/DD':>8}")
    mixes = [("100% MTF only", 1.0), ("100% btcalts only", 0.0),
             ("70% MTF / 30% alts", 0.7), ("60% MTF / 40% alts", 0.6), ("50/50", 0.5)]
    series = {}
    for name, w in mixes:
        r = w * df["mtf"] + (1 - w) * df["alts"]
        cg, dd, rr, eq = stats(r); series[name] = r
        print(f"  {name:<22}{cg*100:>7.0f}%{dd*100:>7.0f}%{rr:>8.2f}")
    print("\n  YEAR-BY-YEAR net% (does the combo stay GREEN every year?):")
    print(f"    {'mix':<22}" + "".join(f"{y:>8}" for y in range(2021, 2027)))
    for name in ("100% MTF only", "60% MTF / 40% alts", "50/50"):
        r = series[name]; row = f"    {name:<22}"
        for y in range(2021, 2027):
            ry = r[[t.year == y for t in r.index]]
            if len(ry) < 50: row += f"{'-':>8}"; continue
            row += f"{(np.prod(1 + ry) - 1) * 100:>+7.0f}%"
        print(row)


if __name__ == "__main__":
    main()
