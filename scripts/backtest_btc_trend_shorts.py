#!/usr/bin/env python3
"""backtest_btc_trend_shorts.py — should the BTC trend bot add SHORTS? Honest WF + year-by-year.

The live trend_btc bot is LONG/FLAT (sits in cash in downtrends). Tests whether enabling the
SHORT side helps on BTC, across 4h/1d, for two rule families:
  ema_cross   : EMA(f)>EMA(s) long ; <s short
  live_rule   : EMA13>EMA20 & close>EMA200 long ; EMA13<EMA20 & close<EMA200 short ; else flat
Variants: long/flat vs long/short. Walk-forward (re-opt each fold) + year-by-year.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt


def sig_cross(df, f, s, shorts):
    up = bt.ema(df["close"], f) > bt.ema(df["close"], s)
    return (up.astype(float) * 2 - 1) if shorts else up.astype(float)


def sig_live(df, shorts):
    e13, e20, e200 = bt.ema(df["close"], 13), bt.ema(df["close"], 20), bt.ema(df["close"], 200)
    longc = (e13 > e20) & (df["close"] > e200)
    pos = longc.astype(float)
    if shorts:
        shortc = (e13 < e20) & (df["close"] < e200)
        pos = pos - shortc.astype(float)
    return pos


def retdd(r):
    """Date-free ret/DD: total return / |maxDD| (scale-free, fine for ranking & WF)."""
    eq = np.cumprod(1 + np.nan_to_num(np.asarray(r)))
    if len(eq) < 5 or eq[-1] <= 0: return -1.0
    dd = ((eq / np.maximum.accumulate(eq)) - 1).min()
    tot = eq[-1] - 1
    return tot / abs(dd) if dd < -1e-9 else 0.0


def wf(series_by_param, train, test):
    params = list(series_by_param); n = len(next(iter(series_by_param.values())))
    out = np.zeros(n); mask = np.zeros(n, bool); start = train
    while start + test <= n:
        best, bp = -9, None
        for p in params:
            r = retdd(series_by_param[p][start-train:start])
            if r > best: best, bp = r, p
        out[start:start+test] = series_by_param[bp][start:start+test]; mask[start:start+test] = True
        start += test
    return retdd(out[mask])


def ret_series(df, pos):
    held = pd.Series(np.asarray(pos, float), index=df.index).shift(1).fillna(0)
    oo = (df["open"].shift(-1) / df["open"] - 1).fillna(0)
    turn = held.diff().abs().fillna(held.abs())
    return (held * oo - turn * bt.COST).values


def main():
    for tf, (tr, te) in (("4h", (2000, 500)), ("1d", (500, 120))):
        df = bt.load("BTCUSDT", tf)
        yr = pd.to_datetime(df["timestamp"]).dt.year.values
        print("\n" + "=" * 78)
        print(f"BTC {tf} — does adding SHORTS to the trend bot help? (walk-forward)")
        print("=" * 78)
        # walk-forward: ema_cross family, long/flat vs long/short
        for shorts in (False, True):
            grid = {(f, s): ret_series(df, sig_cross(df, f, s, shorts))
                    for f in (8, 13, 20, 50) for s in (100, 150, 200) if f < s}
            r = wf(grid, tr, te)
            print(f"  ema_cross  {'long/SHORT' if shorts else 'long/flat ':10}  WF ret/DD {r:5.2f}")
        for shorts in (False, True):
            r = wf({"live": ret_series(df, sig_live(df, shorts))}, tr, te)
            print(f"  live_rule  {'long/SHORT' if shorts else 'long/flat ':10}  WF ret/DD {r:5.2f}")
        # year-by-year for live_rule long/flat vs long/short
        lf = ret_series(df, sig_live(df, False)); ls = ret_series(df, sig_live(df, True))
        print("  year-by-year (live_rule)   long/flat   long/SHORT")
        for y in sorted(set(yr)):
            m = yr == y
            if m.sum() < 50: continue
            nlf = (np.prod(1+np.nan_to_num(lf[m]))-1)*100
            nls = (np.prod(1+np.nan_to_num(ls[m]))-1)*100
            print(f"    {y}{nlf:>13.1f}%{nls:>12.1f}%")


if __name__ == "__main__":
    main()
