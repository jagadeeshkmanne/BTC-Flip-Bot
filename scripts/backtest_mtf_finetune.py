#!/usr/bin/env python3
"""backtest_mtf_finetune.py — fine-tune the MTF Regime Agreement, validated by COMPLETE walk-forward.

Base rule (untuned): LONG when (4h EMA(f4)>EMA(s4)) AND (prior-day EMA(fd)>EMA(sd)), else FLAT.
Sweeps the 4 EMA periods. Honest validation = WALK-FORWARD: re-optimise (f4,s4,fd,sd) on each
rolling TRAIN window, trade the next TEST window with it, stitch ALL test segments into one
continuous out-of-sample equity. Compares against the canonical 50/200-50/200 baseline so we
know whether tuning actually adds value or just overfits.
"""
from __future__ import annotations
import os, sys, itertools
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

F4 = [20, 30, 50]; S4 = [100, 150, 200]
FD = [20, 30, 50]; SD = [100, 150, 200]
COMBOS = [(a, b, c, d) for a in F4 for b in S4 for c in FD for d in SD if a < b and c < d]


def ret_series(df4, sig):
    held = pd.Series(np.asarray(sig, float), index=df4.index).shift(1).fillna(0)
    oo = (df4["open"].shift(-1) / df4["open"] - 1).fillna(0)
    turn = held.diff().abs().fillna(held.abs())
    return (held * oo - turn * bt.COST).values


def build_all(coin):
    """Precompute the net-return series for every (f4,s4,fd,sd) combo on this coin."""
    df4 = bt.load(coin, "4h"); c4 = df4["close"]
    dfd = bt.load(coin, "1d"); cd = dfd["close"]
    ts4 = pd.to_datetime(df4["timestamp"])
    tsd = pd.to_datetime(dfd["timestamp"])
    # map each 4h bar -> index of most recent daily bar at/ before it (merge_asof backward)
    didx = pd.merge_asof(pd.DataFrame({"ts": ts4}).sort_values("ts"),
                         pd.DataFrame({"ts": tsd, "j": np.arange(len(tsd))}).sort_values("ts"),
                         on="ts", direction="backward")["j"].fillna(0).astype(int).values
    daily_cross = {(fd, sd): (bt.ema(cd, fd) > bt.ema(cd, sd)).shift(1).fillna(False).astype(bool).values
                   for fd in FD for sd in SD if fd < sd}
    trend4 = {(f4, s4): (bt.ema(c4, f4) > bt.ema(c4, s4)).values for f4 in F4 for s4 in S4 if f4 < s4}
    series = {}
    for (f4, s4, fd, sd) in COMBOS:
        dtr = daily_cross[(fd, sd)][didx]
        sig = (trend4[(f4, s4)] & dtr).astype(float)
        series[(f4, s4, fd, sd)] = ret_series(df4, sig)
    return df4, series


def rdd(r):
    eq = np.cumprod(1 + np.nan_to_num(np.asarray(r)))
    if len(eq) < 5 or eq[-1] <= 0:
        return -1.0
    dd = ((eq / np.maximum.accumulate(eq)) - 1).min()
    return (eq[-1] - 1) / abs(dd) if dd < -1e-9 else 0.0


def metrics_arr(r, bpy=6 * 365.25):
    eq = np.cumprod(1 + np.nan_to_num(r))
    if eq[-1] <= 0:
        return -1, -1, -1
    dd = ((eq / np.maximum.accumulate(eq)) - 1).min()
    cagr = eq[-1] ** (1 / max(len(r) / bpy, 1e-9)) - 1
    return cagr, dd, (cagr / abs(dd) if dd < -1e-9 else 0.0)


def walk_forward(series, train=4000, test=1000):
    n = len(next(iter(series.values()))); out = np.zeros(n); mask = np.zeros(n, bool)
    start = train; picks = []
    while start + test <= n:
        best, bp = -9, None
        for p, r in series.items():
            sc = rdd(r[start - train:start])
            if sc > best:
                best, bp = sc, p
        out[start:start + test] = series[bp][start:start + test]; mask[start:start + test] = True
        picks.append(bp); start += test
    return out[mask], picks, mask


def main():
    print("=" * 90)
    print("MTF REGIME AGREEMENT — fine-tune via COMPLETE walk-forward (4h exec, daily regime gate)")
    print("=" * 90)
    for coin in ("BTCUSDT", "ETHUSDT", "BNBUSDT"):
        df4, series = build_all(coin)
        idx = pd.to_datetime(df4["timestamp"])
        canon = series[(50, 200, 50, 200)]
        # canonical full + OOS(40%)
        cut = int(len(canon) * 0.6)
        cf = metrics_arr(canon); co = metrics_arr(canon[cut:])
        # complete walk-forward (re-optimized) vs FIXED canonical over the identical window
        wf, picks, mask = walk_forward(series)
        wfm = metrics_arr(wf)
        canon_same = metrics_arr(canon[mask])   # fixed 50/200 over the exact same OOS bars
        bh = (df4["close"] / df4["close"].iloc[0]).values
        bhret = np.diff(bh, prepend=bh[0]) / np.maximum(bh[0], 1e-9)  # rough
        bh_full = metrics_arr((df4["open"].shift(-1)/df4["open"]-1).fillna(0).values)
        from collections import Counter
        top = Counter(picks).most_common(3)
        print(f"\n  ── {coin[:-4]} ──")
        print(f"    canonical 50/200-50/200 : full ret/DD {cf[2]:.2f} (CAGR {cf[0]*100:.0f}%, DD {cf[1]*100:.0f}%) | OOS(40%) ret/DD {co[2]:.2f}")
        print(f"    >> over identical WF window, head-to-head:")
        print(f"         FIXED canonical 50/200 : ret/DD {canon_same[2]:.2f} (CAGR {canon_same[0]*100:.0f}%)")
        print(f"         RE-TUNED each fold     : ret/DD {wfm[2]:.2f} (CAGR {wfm[0]*100:.0f}%)   <- tuning {'HELPS' if wfm[2]>canon_same[2] else 'HURTS'}")
        print(f"    buy & hold              : full ret/DD {bh_full[2]:.2f} (CAGR {bh_full[0]*100:.0f}%, DD {bh_full[1]*100:.0f}%)")
        print(f"    most-picked params (f4/s4/fd/sd): " + ", ".join(f"{p}×{k}" for p, k in top))
        # year-by-year for canonical
        yr = idx.dt.year.values
        ys = []
        for y in sorted(set(yr)):
            m = yr == y
            if m.sum() < 100: continue
            ny = (np.prod(1 + np.nan_to_num(canon[m])) - 1) * 100
            ys.append(f"{y}:{ny:+.0f}%")
        print(f"    canonical year-by-year  : " + " ".join(ys))


def mask_pct(wf, series):
    n = len(next(iter(series.values())))
    return round(len(wf) / n * 100)


if __name__ == "__main__":
    main()
