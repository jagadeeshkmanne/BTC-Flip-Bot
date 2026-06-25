#!/usr/bin/env python3
"""backtest_mtf_lev2.py — push MTF-Regime CAGR high while reducing DD: higher vol-target caps
+ a drawdown circuit-breaker, with full YEAR-BY-YEAR (return AND max-DD per year).

Per-bar vol-targeting (proper, with rebalancing fees + intrabar liquidation):
  exposure[i] (decided at close i) = (causal vol-target leverage, capped) if regime-bull else 0,
  times a DD-BRAKE factor (de-lever 50% while the bot's own equity sits below brake*peak).
All causal: vol target = expanding median of past realised vol; brake uses own past equity.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

MAINT = 0.005
FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT


def regime():
    df4 = bt.load("BTCUSDT", "4h"); c4 = df4["close"]
    dfd = bt.load("BTCUSDT", "1d"); cd = dfd["close"]
    ts4 = pd.to_datetime(df4["timestamp"]); tsd = pd.to_datetime(dfd["timestamp"])
    didx = pd.merge_asof(pd.DataFrame({"ts": ts4}).sort_values("ts"),
                         pd.DataFrame({"ts": tsd, "j": np.arange(len(tsd))}).sort_values("ts"),
                         on="ts", direction="backward")["j"].fillna(0).astype(int).values
    d_up = (bt.ema(cd, 50) > bt.ema(cd, 200)).shift(1).fillna(False).astype(bool).values[didx]
    f_up = (bt.ema(c4, 50) > bt.ema(c4, 200)).values
    bull = f_up & d_up
    rv = df4["close"].pct_change().rolling(30).std().bfill().values
    medv = pd.Series(rv).expanding(min_periods=300).median().bfill().values
    return df4, bull, rv, medv


def run(df4, bull, lev_t, brake_thr=None):
    o = df4["open"].values; l = df4["low"].values; c = df4["close"].values
    n = len(df4); eq = 1.0; peak = 1.0; prev = 0.0
    out = np.ones(n)
    for i in range(2, n - 1):
        brake = 0.5 if (brake_thr is not None and eq < brake_thr * peak) else 1.0
        exp = (lev_t[i] if bull[i] else 0.0) * brake          # exposure decided at close i
        turn = abs(exp - prev)
        eq *= (1 - turn * (FEE + SLIP))                       # rebalance cost
        if exp > 0:                                           # intrabar liquidation over bar i+1
            worst = l[i + 1] / c[i] - 1
            if exp * worst <= -(1 - MAINT):
                eq = 0.0
        ret = c[i + 1] / c[i] - 1
        eq *= (1 + exp * ret); prev = exp
        eq = max(eq, 0.0); peak = max(peak, eq); out[i + 1] = eq
        if eq <= 0: out[i + 1:] = 0.0; break
    return pd.Series(out, index=pd.to_datetime(df4["timestamp"])).iloc[2:]


def m(s):
    yrs = max((s.index[-1] - s.index[0]).days / 365.25, 1e-9)
    cagr = (s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1 if s.iloc[-1] > 0 else -1
    dd = (s / s.cummax() - 1).min()
    return cagr, dd, (cagr / abs(dd) if dd < -1e-9 else 0.0)


def main():
    df4, bull, rv, medv = regime()
    vt = lambda cap: np.clip(medv / (rv + 1e-9), 0.5, cap)
    configs = [
        ("fixed 3x", np.full(len(df4), 3.0), None),
        ("vol-target cap 3x", vt(3.0), None),
        ("vol-target cap 4x", vt(4.0), None),
        ("vol-target cap 5x", vt(5.0), None),
        ("vol-target 5x + DDbrake .85", vt(5.0), 0.85),
        ("vol-target 4x + DDbrake .80", vt(4.0), 0.80),
    ]
    print("=" * 92)
    print("MTF REGIME (BTC 4h) — high-CAGR with DD control: vol-target caps + drawdown brake")
    print("=" * 92)
    print(f"  {'config':<30}{'FULL CAGR':>10}{'DD':>7}{'r/DD':>6}   {'OOS CAGR':>9}{'OOS DD':>7}{'OOS r/DD':>9}")
    series = {}
    for name, lv, br in configs:
        s = run(df4, bull, lv, br); series[name] = s
        fc, fd, fr = m(s); cut = s.index[int(len(s) * 0.6)]; oc, od, orr = m(s[s.index >= cut])
        print(f"  {name:<30}{fc*100:>9.0f}%{fd*100:>6.0f}%{fr:>6.2f}   {oc*100:>8.0f}%{od*100:>6.0f}%{orr:>9.2f}")

    print("\n  YEAR-BY-YEAR (net% / worst intra-year DD%):")
    yr = pd.Series([t.year for t in series["fixed 3x"].index])
    years = sorted(set(yr))
    hdr = "    " + f"{'config':<30}" + "".join(f"{y:>13}" for y in years)
    print(hdr)
    for name in ("fixed 3x", "vol-target cap 5x", "vol-target 5x + DDbrake .85", "vol-target 4x + DDbrake .80"):
        s = series[name]; row = f"    {name:<30}"
        for y in years:
            ss = s[[t.year == y for t in s.index]]
            if len(ss) < 50: row += f"{'-':>13}"; continue
            net = (ss.iloc[-1] / ss.iloc[0] - 1) * 100
            ddy = (ss / ss.cummax() - 1).min() * 100
            row += f"{net:>6.0f}/{ddy:>5.0f}"
        print(row)


if __name__ == "__main__":
    main()
