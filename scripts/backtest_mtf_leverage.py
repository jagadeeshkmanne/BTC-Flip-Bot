#!/usr/bin/env python3
"""backtest_mtf_leverage.py — can we lift the MTF Regime's CAGR while keeping DD controlled?

Base: BTC 4h long/flat, LONG when (4h EMA50>200) AND (prior-day EMA50>200). It sits in CASH in
bears (no drawdown when flat), so leverage applied ONLY when long has 'room'. Tests:
  - fixed leverage 1x..3x (with HONEST intrabar liquidation, ~1/L adverse move = wipe)
  - vol-targeted leverage: lev = clip(target_vol / realised_vol, lo, hi)  (size up calm / down wild)
  - vol-target + daily-bear half (extra de-lever if BTC < daily EMA200)
Reports CAGR / maxDD / ret/DD + year-by-year so the return-vs-risk tradeoff is explicit.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

MAINT = 0.005


def regime():
    df4 = bt.load("BTCUSDT", "4h"); c4 = df4["close"]
    dfd = bt.load("BTCUSDT", "1d"); cd = dfd["close"]
    ts4 = pd.to_datetime(df4["timestamp"]); tsd = pd.to_datetime(dfd["timestamp"])
    didx = pd.merge_asof(pd.DataFrame({"ts": ts4}).sort_values("ts"),
                         pd.DataFrame({"ts": tsd, "j": np.arange(len(tsd))}).sort_values("ts"),
                         on="ts", direction="backward")["j"].fillna(0).astype(int).values
    d_up = (bt.ema(cd, 50) > bt.ema(cd, 200)).shift(1).fillna(False).astype(bool).values[didx]
    d_above200 = (cd > bt.ema(cd, 200)).shift(1).fillna(False).astype(bool).values[didx]
    f_up = (bt.ema(c4, 50) > bt.ema(c4, 200)).values
    bull = f_up & d_up
    rv = df4["close"].pct_change().rolling(30).std().bfill().values
    return df4, bull, d_above200, rv


def run(df4, bull, lev_arr):
    """Long/flat with a per-bar target leverage (lev_arr); intrabar liquidation; fees scale w/ lev."""
    o = df4["open"].values; h = df4["high"].values; l = df4["low"].values; c = df4["close"].values
    n = len(df4); bal = 1.0; side = 0; entry = 0.0; lv = 1.0
    eq = np.ones(n); liqs = 0
    for i in range(2, n - 1):
        oN, lN, cN = o[i+1], l[i+1], c[i+1]
        want = 1 if bull[i] else 0
        if side == 1 and bal > 0:
            if lN <= entry * (1 - (1/lv - MAINT)):            # liquidation
                bal = 0.0; liqs += 1; side = 0
        if side != want and bal > 0:
            if side == 1:                                      # exit
                fpx = oN*(1 - bt.SLIP_PCT); bal *= (1 + lv*(fpx/entry - 1))*(1 - bt.FEE_PCT*lv)
                side = 0
            if want == 1 and bal > 0:                          # enter
                side = 1; entry = oN*(1 + bt.SLIP_PCT); lv = lev_arr[i]; bal *= (1 - bt.FEE_PCT*lv)
        eq[i+1] = max(bal*(1 + lv*(cN/entry - 1)), 0.0) if side == 1 and bal > 0 else bal
        if eq[i+1] <= 0: eq[i+1:] = 0.0; break
    s = pd.Series(eq, index=pd.to_datetime(df4["timestamp"])).iloc[2:]
    yrs = max((s.index[-1]-s.index[0]).days/365.25, 1e-9)
    cagr = (s.iloc[-1]/s.iloc[0])**(1/yrs)-1 if s.iloc[-1] > 0 else -1
    dd = (s/s.cummax()-1).min()
    return cagr, dd, (cagr/abs(dd) if dd < -1e-9 else 0.0), liqs, s


def main():
    df4, bull, d_above200, rv = regime()
    # CAUSAL vol target: expanding median of realised vol using ONLY past data (no full-sample leak)
    medv = pd.Series(rv).expanding(min_periods=300).median().bfill().values
    n = len(df4)
    print("=" * 80)
    print("MTF REGIME (BTC 4h long/flat) — leverage & vol-targeting frontier (honest liquidation)")
    print("=" * 80)
    print(f"  {'config':<30}{'CAGR':>8}{'maxDD':>8}{'ret/DD':>8}{'liq':>5}")
    configs = []
    for L in (1.0, 1.5, 2.0, 2.5, 3.0):
        configs.append((f"fixed {L:.1f}x", np.full(n, L)))
    # vol-targeted, capped
    for cap in (2.0, 3.0):
        lv = np.clip(medv / (rv + 1e-9), 0.5, cap)
        configs.append((f"vol-target cap {cap:.0f}x", lv))
    # vol-target cap3 + daily-bear half
    lv = np.clip(medv / (rv + 1e-9), 0.5, 3.0) * np.where(d_above200, 1.0, 0.5)
    configs.append(("vol-target3 + dly-bear half", lv))
    results = {}
    print(f"  {'config':<30}{'CAGR':>8}{'maxDD':>8}{'ret/DD':>8}{'liq':>5}  |  OOS(40%) CAGR/DD/rDD")
    for name, lv in configs:
        cagr, dd, rdd, liq, s = run(df4, bull, lv)
        results[name] = s
        cut = s.index[int(len(s) * 0.6)]; so = s[s.index >= cut]
        oy = max((so.index[-1]-so.index[0]).days/365.25, 1e-9)
        oc = (so.iloc[-1]/so.iloc[0])**(1/oy)-1; od = (so/so.cummax()-1).min()
        orr = oc/abs(od) if od < -1e-9 else 0
        print(f"  {name:<30}{cagr*100:>7.0f}%{dd*100:>7.0f}%{rdd:>8.2f}{liq:>5}  |  {oc*100:>5.0f}% {od*100:>5.0f}% {orr:>5.2f}")
    print("\n  year-by-year net% (1x base vs vol-target cap2x vs vol-target3+bear-half):")
    yr = pd.to_datetime(df4['timestamp']).dt.year.values[2:]
    for key in ('fixed 1.0x','vol-target cap 2x','vol-target3 + dly-bear half'):
        s=results[key]; print(f"    {key:<30}", end='')
        r=s.pct_change().fillna(0).values
        for y in sorted(set(yr)):
            m=yr==y
            if m.sum()<100: continue
            print(f"{y}:{(np.prod(1+r[m])-1)*100:+.0f}% ", end='')
        print()


if __name__ == "__main__":
    main()
