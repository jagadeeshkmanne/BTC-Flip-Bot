#!/usr/bin/env python3
"""backtest_mtf_shorts.py — does adding SHORTS to the MTF Regime help? (BTC + alts, year-by-year)

Long/flat base: LONG when (4h EMA50>200) AND (prior-day EMA50>200), else flat.
Short side (mirror): SHORT when (4h EMA50<200) AND (prior-day EMA50<200) — both TFs bearish.
Compares long/flat vs long/short vs short-only, full + OOS + year-by-year. Honest taker fees.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt


def regime(coin):
    df4 = bt.load(coin, "4h"); c4 = df4["close"]
    dfd = bt.load(coin, "1d"); cd = dfd["close"]
    ts4 = pd.to_datetime(df4["timestamp"]); tsd = pd.to_datetime(dfd["timestamp"])
    didx = pd.merge_asof(pd.DataFrame({"ts": ts4}).sort_values("ts"),
                         pd.DataFrame({"ts": tsd, "j": np.arange(len(tsd))}).sort_values("ts"),
                         on="ts", direction="backward")["j"].fillna(0).astype(int).values
    d_up = (bt.ema(cd, 50) > bt.ema(cd, 200)).shift(1).fillna(False).astype(bool).values[didx]
    f_up = (bt.ema(c4, 50) > bt.ema(c4, 200)).values
    bull = f_up & d_up
    bear = (~f_up) & (~d_up)
    return df4, bull, bear


def pos_of(bull, bear, mode):
    if mode == "long_flat":
        return bull.astype(float)
    if mode == "short_only":
        return -bear.astype(float)
    return bull.astype(float) - bear.astype(float)   # long_short


def evalp(df4, pos):
    held = pd.Series(np.asarray(pos, float), index=df4.index).shift(1).fillna(0)
    oo = (df4["open"].shift(-1) / df4["open"] - 1).fillna(0)
    turn = held.diff().abs().fillna(held.abs())
    r = (held * oo - turn * bt.COST)
    eq = (1 + r).cumprod(); eq.index = pd.to_datetime(df4["timestamp"])
    return eq, r.values


def met(eq):
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    return cagr, dd, (cagr / abs(dd) if dd < -1e-9 else 0.0)


def main():
    for coin in ("BTCUSDT", "ETHUSDT", "BNBUSDT"):
        df4, bull, bear = regime(coin)
        idx = pd.to_datetime(df4["timestamp"]); yr = idx.dt.year.values
        print("\n" + "=" * 80)
        print(f"MTF REGIME — long/flat vs long/short vs short-only  [{coin[:-4]} 4h]")
        print("=" * 80)
        print(f"  {'variant':<13}{'FULL CAGR':>10}{'DD':>6}{'r/DD':>6}   {'OOS CAGR':>9}{'OOS rDD':>8}")
        rmap = {}
        for mode in ("long_flat", "long_short", "short_only"):
            eq, r = evalp(df4, pos_of(bull, bear, mode)); rmap[mode] = r
            fc, fd, fr = met(eq); cut = eq.index[int(len(eq) * 0.6)]; oc, od, orr = met(eq[eq.index >= cut])
            print(f"  {mode:<13}{fc*100:>9.0f}%{fd*100:>5.0f}%{fr:>6.2f}   {oc*100:>8.0f}%{orr:>8.2f}")
        print(f"  year-by-year net%:  {'long/flat':>12}{'long/short':>12}{'short-only':>12}")
        for y in sorted(set(yr)):
            m = yr == y
            if m.sum() < 100: continue
            vals = [(np.prod(1 + np.nan_to_num(rmap[md][m])) - 1) * 100 for md in ("long_flat", "long_short", "short_only")]
            print(f"    {y}{vals[0]:>16.0f}%{vals[1]:>11.0f}%{vals[2]:>11.0f}%")


if __name__ == "__main__":
    main()
