#!/usr/bin/env python3
"""backtest_mtf_short_finetune.py — fine-tune the SHORT side with its OWN parameters (BTC 4h).

The long works with EMA50/200. Maybe shorts need DIFFERENT (faster?) params. Sweep short-side
4h+daily EMA periods, walk-forward each, and answer honestly:
  (1) Does ANY tuned short make money standalone (beat cash = 0 on walk-forward)?
  (2) Does long(canonical 50/200) + tuned-short beat long-only?
Also tries faster shorts, a price<dailyEMA200 gate, and a quick-exit short. Honest taker fees.
"""
from __future__ import annotations
import os, sys, itertools
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt


def setup():
    df4 = bt.load("BTCUSDT", "4h"); c4 = df4["close"]
    dfd = bt.load("BTCUSDT", "1d"); cd = dfd["close"]
    ts4 = pd.to_datetime(df4["timestamp"]); tsd = pd.to_datetime(dfd["timestamp"])
    didx = pd.merge_asof(pd.DataFrame({"ts": ts4}).sort_values("ts"),
                         pd.DataFrame({"ts": tsd, "j": np.arange(len(tsd))}).sort_values("ts"),
                         on="ts", direction="backward")["j"].fillna(0).astype(int).values
    return df4, c4, cd, didx


def daily_bool(cd, expr, didx):
    return expr.shift(1).fillna(False).astype(bool).values[didx]


def ret_of(df4, pos):
    held = pd.Series(np.asarray(pos, float), index=df4.index).shift(1).fillna(0)
    oo = (df4["open"].shift(-1) / df4["open"] - 1).fillna(0)
    turn = held.diff().abs().fillna(held.abs())
    return (held * oo - turn * bt.COST).values


def rdd(r):
    eq = np.cumprod(1 + np.nan_to_num(np.asarray(r)))
    if len(eq) < 5 or eq[-1] <= 0: return -1.0
    dd = ((eq / np.maximum.accumulate(eq)) - 1).min()
    return (eq[-1] - 1) / abs(dd) if dd < -1e-9 else 0.0


def total(r):
    return (np.prod(1 + np.nan_to_num(np.asarray(r))) - 1) * 100


def wf(series, train=4000, test=1000):
    n = len(next(iter(series.values()))); out = np.zeros(n); mask = np.zeros(n, bool); s = train
    while s + test <= n:
        best, bp = -9, None
        for p, r in series.items():
            sc = rdd(r[s-train:s])
            if sc > best: best, bp = sc, p
        out[s:s+test] = series[bp][s:s+test]; mask[s:s+test] = True; s += test
    return out[mask]


def main():
    df4, c4, cd, didx = setup()
    print("=" * 80)
    print("FINE-TUNING THE SHORT SIDE (BTC 4h) — does any tuned short beat CASH?")
    print("=" * 80)

    # short-only return series for every param combo (both TFs bearish)
    short_series = {}
    for fa, sa in [(10,50),(20,50),(10,100),(20,100),(30,100),(20,200),(50,200),(30,150)]:
        f_dn = (bt.ema(c4, fa) < bt.ema(c4, sa)).values
        for fd, sd in [(20,100),(50,100),(20,200),(50,200)]:
            d_dn = daily_bool(cd, bt.ema(cd, fd) < bt.ema(cd, sd), didx)
            pos = -(f_dn & d_dn).astype(float)
            short_series[(fa,sa,fd,sd)] = ret_of(df4, pos)
    # price-below-daily-EMA200 gated short
    d_below200 = daily_bool(cd, cd < bt.ema(cd, 200), didx)
    for fa, sa in [(20,50),(10,50),(20,100)]:
        f_dn = (bt.ema(c4, fa) < bt.ema(c4, sa)).values
        short_series[("px200",fa,sa,0)] = ret_of(df4, -(f_dn & d_below200).astype(float))

    # walk-forward the best short (re-opt each fold)
    wf_short = wf(short_series)
    print(f"\n  WALK-FORWARD best-tuned SHORT-ONLY:")
    print(f"    total return {total(wf_short):+.0f}%   ret/DD {rdd(wf_short):.2f}")
    print(f"    (cash = 0% return; if this is negative, every tuned short LOSES money)")
    # best single short by full-sample for reference
    best_full = max(short_series.items(), key=lambda kv: total(kv[1]))
    print(f"    best short by FULL total return: {best_full[0]} -> {total(best_full[1]):+.0f}% (full-sample, cherry-picked)")

    # does long(canonical) + tuned short beat long-only?
    d_up = daily_bool(cd, bt.ema(cd,50) > bt.ema(cd,200), didx)
    long_pos = ((bt.ema(c4,50) > bt.ema(c4,200)).values & d_up).astype(float)
    long_r = ret_of(df4, long_pos)
    # build long+short combos: long fixed, short param swept
    ls_series = {}
    for k, sr in short_series.items():
        # combine: long where long_pos=1, short where short fires (short series already -1 net returns)
        f_dn_d_dn = (sr != 0).astype(float)  # bars the short is active
        # reconstruct combined position return: long_r when long, short return when short
        comb = np.where(long_pos.astype(bool)[:len(long_r)], long_r, 0) + np.where(~long_pos.astype(bool)[:len(sr)], sr, 0)
        ls_series[k] = comb
    wf_ls = wf(ls_series)
    print(f"\n  long-only (canonical 50/200): WF total {total(wf(dict(a=long_r))):+.0f}%  ret/DD {rdd(wf(dict(a=long_r))):.2f}")
    print(f"  long + TUNED short (WF re-opt short): WF total {total(wf_ls):+.0f}%  ret/DD {rdd(wf_ls):.2f}")
    print(f"\n  year-by-year short-only total% (best full-sample short {best_full[0]}):")
    yr = pd.to_datetime(df4['timestamp']).dt.year.values
    sr = best_full[1]
    for y in sorted(set(yr)):
        m = yr == y
        if m.sum() < 100: continue
        print(f"    {y}: {total(sr[m]):+.0f}%")


if __name__ == "__main__":
    main()
