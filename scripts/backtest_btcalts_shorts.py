#!/usr/bin/env python3
"""backtest_btcalts_shorts.py — does adding SHORTS to the BTC-led alt basket help?

Thesis: BTC falls -> alts fall MORE, so short the alts when BTC's trend is down. Compares
LONG-ONLY vs LONG+SHORT (both vol-scaled, BTC slow-trend signal -> eqw ETH/BNB/SOL):
  - full-period CAGR / maxDD / ret/DD (fixed BTC 32/800) -> real drawdown of each
  - year-by-year net%
  - WALK-FORWARD (re-optimise BTC EMA each fold) -> honest risk-adjusted verdict
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALTS = ["ETHUSDT", "BNBUSDT", "SOLUSDT"]


def load(sym):
    for nm in (f"{sym}_1h_binance.csv", f"{sym}_1h_binance_full.csv"):
        p = os.path.join(HERE, "data/cache", nm)
        if os.path.exists(p):
            return pd.read_csv(p, parse_dates=["timestamp"])
    raise FileNotFoundError(sym)


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def alt_rets(btc, frames, F, S, longonly, volscale=True):
    base = (ema(btc["close"], F) > ema(btc["close"], S)).astype(float).values
    pos = base if longonly else (base * 2 - 1)
    if volscale:
        rv = btc["close"].pct_change().rolling(24).std().bfill().values
        pos = pos * np.clip(np.nanmedian(rv) / (rv + 1e-9), 0.2, 1.0)
    sig = pd.Series(pos, index=btc.index)
    rr = []
    for a in ALTS:
        held = sig.shift(1).fillna(0).values
        oo = (frames[a]["open"].shift(-1) / frames[a]["open"] - 1).fillna(0).values
        turn = np.abs(np.diff(held, prepend=0.0))
        rr.append(held * oo - turn * (FEE_PCT + SLIP_PCT))
    return np.mean(rr, axis=0)


def met(r, bpy=24 * 365.25):
    eq = np.cumprod(1 + np.nan_to_num(r))
    if eq[-1] <= 0:
        return -1, -1, -1
    dd = ((eq / np.maximum.accumulate(eq)) - 1).min()
    cagr = eq[-1] ** (1 / max(len(r) / bpy, 1e-9)) - 1
    return cagr, dd, (cagr / abs(dd) if dd < -1e-9 else 0.0)


def walk_forward(btc, frames, longonly, train=8000, test=2000):
    grid = [(f, s) for f in (20, 32, 50) for s in (400, 600, 800)]
    series = {p: alt_rets(btc, frames, p[0], p[1], longonly) for p in grid}
    n = len(btc); out = np.zeros(n); mask = np.zeros(n, bool); start = train
    while start + test <= n:
        best, bp = -9, None
        for p in grid:
            _, _, r = met(series[p][start - train:start])
            if r > best:
                best, bp = r, p
        out[start:start + test] = series[bp][start:start + test]; mask[start:start + test] = True
        start += test
    return met(out[mask])


def main():
    raw = {s: load(s) for s in (["BTCUSDT"] + ALTS)}
    common = max(df["timestamp"].iloc[0] for df in raw.values())
    raw = {s: df[df["timestamp"] >= common].reset_index(drop=True) for s, df in raw.items()}
    n = min(len(df) for df in raw.values())
    raw = {s: df.iloc[:n].reset_index(drop=True) for s, df in raw.items()}
    btc = raw["BTCUSDT"]; yr = btc["timestamp"].dt.year.values
    span = f"{btc.timestamp.iloc[0].date()}->{btc.timestamp.iloc[-1].date()}"

    lo = alt_rets(btc, raw, 32, 800, True)
    ls = alt_rets(btc, raw, 32, 800, False)
    print("=" * 78)
    print(f"BTC-LED ALTS — LONG-ONLY vs LONG+SHORT (vol-scaled, BTC 32/800)  {span}")
    print("=" * 78)
    print("\n  Full period (real drawdowns, no yearly reset):")
    for name, r in (("LONG-only ", lo), ("LONG+SHORT", ls)):
        c, d, rd = met(r)
        print(f"    {name}:  CAGR {c*100:6.0f}%   maxDD {d*100:6.0f}%   ret/DD {rd:5.2f}")

    print("\n  Year by year (net%):")
    print(f"    {'year':<6}{'LONG-only':>11}{'LONG+SHORT':>12}")
    for y in sorted(set(yr)):
        m = yr == y
        if m.sum() < 200:
            continue
        nlo = (np.prod(1 + np.nan_to_num(lo[m])) - 1) * 100
        nls = (np.prod(1 + np.nan_to_num(ls[m])) - 1) * 100
        print(f"    {y:<6}{nlo:>11.1f}{nls:>12.1f}")

    print("\n  WALK-FORWARD (BTC EMA re-optimised each fold — honest OOS):")
    for name, lon in (("LONG-only ", True), ("LONG+SHORT", False)):
        c, d, rd = walk_forward(btc, raw, lon)
        print(f"    {name}:  WF CAGR {c*100:6.0f}%   WF maxDD {d*100:6.0f}%   WF ret/DD {rd:5.2f}")


if __name__ == "__main__":
    main()
