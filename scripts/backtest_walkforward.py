#!/usr/bin/env python3
"""backtest_walkforward.py — WALK-FORWARD validation of the session's candidates.

Single backtests gave contradictory numbers (overfit-prone). This re-optimises the EMA pair
on each rolling TRAIN window, then trades the NEXT (unseen) TEST window with it, stitches all
test segments into one continuous out-of-sample equity, and reports honest metrics. Same-coin
buy&hold over the identical stitched span is the benchmark.

Candidates:
  BTC 1h reverse           : single-coin BTC, EMA fast/slow long/short
  ETH 1h reverse           : single-coin ETH
  ETH own-sig (1h)         : ETH on its own slow trend (the surprise OOS performer)
  BTC-led ALT basket (1h)  : BTC's signal applied to eqw(ETH,BNB,SOL)
  4-coin basket 4h (LIVE)  : each coin on its OWN signal, eqw (what's deployed)

Selection metric on train = ret/DD. Honesty: open-to-open fills, fee 0.055%/side + 0.05%
slip on turnover. Data: Binance 1h (cached), 4h resampled.
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(symbol):
    for name in (f"{symbol}_1h_binance.csv", f"{symbol}_1h_binance_full.csv"):
        p = os.path.join(HERE, "data/cache", name)
        if os.path.exists(p):
            return pd.read_csv(p, parse_dates=["timestamp"])
    raise FileNotFoundError(symbol)


def to_tf(df, tf):
    if tf == "1h":
        return df.reset_index(drop=True)
    return df.set_index("timestamp").resample(tf).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def sig_reverse(df, f, s):
    return (ema(df["close"], f) > ema(df["close"], s)).astype(float) * 2 - 1


def apply_sig(sig, target):
    held = sig.shift(1).fillna(0).values
    oo = (target["open"].shift(-1) / target["open"] - 1).fillna(0).values
    turn = np.abs(np.diff(held, prepend=0.0))
    return held * oo - turn * (FEE_PCT + SLIP_PCT)


def rr(ret):
    """ret/DD from a return array."""
    eq = np.cumprod(1 + np.nan_to_num(ret))
    if eq[-1] <= 0:
        return -1.0
    peak = np.maximum.accumulate(eq)
    dd = (eq / peak - 1).min()
    yrs = len(ret) / (24 * 365.25)  # bars are 1h-equiv for selection; scale-free for ranking
    cagr = eq[-1] ** (1 / max(yrs, 1e-9)) - 1
    return cagr / abs(dd) if dd < -1e-9 else 0.0


def metrics(ret, bars_per_year):
    eq = np.cumprod(1 + np.nan_to_num(ret))
    if eq[-1] <= 0:
        return -1.0, -1.0, -1.0
    peak = np.maximum.accumulate(eq); dd = (eq / peak - 1).min()
    yrs = len(ret) / bars_per_year
    cagr = eq[-1] ** (1 / max(yrs, 1e-9)) - 1
    return cagr, dd, (cagr / abs(dd) if dd < -1e-9 else 0.0)


def walk_forward(ret_by_param, train, test):
    """ret_by_param: {param: full return np.array}. Returns stitched OOS return array + fold params."""
    params = list(ret_by_param.keys())
    n = len(next(iter(ret_by_param.values())))
    stitched = np.zeros(n); mask = np.zeros(n, bool); chosen = []
    start = train
    while start + test <= n:
        best_p, best_s = None, -1e9
        for p in params:
            s = rr(ret_by_param[p][start - train:start])
            if s > best_s:
                best_s, best_p = s, p
        stitched[start:start + test] = ret_by_param[best_p][start:start + test]
        mask[start:start + test] = True
        chosen.append(best_p)
        start += test
    return stitched[mask], chosen


def main():
    raw = {s: load(s) for s in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT")}
    common = max(df["timestamp"].iloc[0] for df in raw.values())
    raw = {s: df[df["timestamp"] >= common].reset_index(drop=True) for s, df in raw.items()}

    fasts1h = [20, 32, 50]; slows1h = [200, 400, 600, 800]
    fasts4h = [5, 8, 13]; slows4h = [100, 150, 200]
    BPY_1H = 24 * 365.25
    BPY_4H = 6 * 365.25

    # 1h frames
    f1 = {s: to_tf(df, "1h") for s, df in raw.items()}
    # 4h frames
    f4 = {s: to_tf(df, "4h") for s, df in raw.items()}

    candidates = []

    # single-coin reverse (BTC, ETH) on 1h
    for coin in ("BTCUSDT", "ETHUSDT"):
        rbp = {(f, s): apply_sig(sig_reverse(f1[coin], f, s), f1[coin]) for f in fasts1h for s in slows1h}
        candidates.append((f"{coin[:-4]} 1h reverse", rbp, 8000, 2000, BPY_1H, f1["BTCUSDT"]))

    # BTC-led alt basket on 1h
    alts1 = ["ETHUSDT", "BNBUSDT", "SOLUSDT"]
    rbp = {}
    for f in fasts1h:
        for s in slows1h:
            sig = sig_reverse(f1["BTCUSDT"], f, s)
            rbp[(f, s)] = np.mean([apply_sig(sig, f1[a]) for a in alts1], axis=0)
    candidates.append(("BTC-led ALT basket 1h", rbp, 8000, 2000, BPY_1H, f1["BTCUSDT"]))

    # 4-coin own-signal basket on 4h (LIVE strategy family)
    rbp = {}
    for f in fasts4h:
        for s in slows4h:
            rbp[(f, s)] = np.mean([apply_sig(sig_reverse(f4[c], f, s), f4[c])
                                   for c in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT")], axis=0)
    candidates.append(("4-coin basket 4h (LIVE)", rbp, 2000, 500, BPY_4H, f4["BTCUSDT"]))

    print("=" * 92)
    print("WALK-FORWARD (rolling re-optimisation; every bar is genuine out-of-sample)")
    print("=" * 92)
    print(f"  {'candidate':<26}{'WF CAGR':>9}{'WF maxDD':>10}{'WF ret/DD':>11}{'folds':>7}   {'vs B&H r/DD':>12}")
    for name, rbp, train, test, bpy, btc_ref in candidates:
        wf_ret, chosen = walk_forward(rbp, train, test)
        c, d, r = metrics(wf_ret, bpy)
        # buy&hold of BTC over the same stitched length
        n = len(next(iter(rbp.values())))
        bh = (btc_ref["open"].shift(-1) / btc_ref["open"] - 1).fillna(0).values
        bh_ret = bh[train:train + len(wf_ret)]
        bc, bd, br = metrics(bh_ret, bpy)
        print(f"  {name:<26}{c*100:>8.0f}%{d*100:>9.0f}%{r:>11.2f}{len(chosen):>7}   {br:>12.2f}")
        # show parameter stability
        from collections import Counter
        top = Counter(chosen).most_common(3)
        print(f"       chosen pairs: " + ", ".join(f"{p}×{k}" for p, k in top))

    print("\n  (B&H r/DD = BTC buy&hold over the identical out-of-sample span, same scale)")


if __name__ == "__main__":
    main()
