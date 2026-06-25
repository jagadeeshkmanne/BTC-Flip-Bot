#!/usr/bin/env python3
"""backtest_btcalts_rebal.py — tune btcalts rebalancing: timeframe (1h/4h) x threshold x vol-scale.

The live bot: BTC EMA32/800 (1h) -> long/short eqw ETH/BNB/SOL, vol-scaled exposure, rebalance when
target moves >10% of equity. Tests whether a different TF or threshold cuts the rebalance churn
(fee drag) without hurting returns. Reports CAGR/DD/ret/DD + #rebalances + total fee drag.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

COST = bt.FEE_PCT + bt.SLIP_PCT
ALTS = ["ETHUSDT", "BNBUSDT", "SOLUSDT"]


def run(tf, ema_f, ema_s, vol_len, med_len, thresh, vol_scale=True):
    btc = bt.load("BTCUSDT", tf); ts = pd.to_datetime(btc["timestamp"])
    bull = (bt.ema(btc["close"], ema_f) > bt.ema(btc["close"], ema_s)).shift(1)
    sig = np.where(bull, 1.0, -1.0)
    # vol-scale from BTC realized vol (causal expanding median reference)
    ret = btc["close"].pct_change(); rv = ret.rolling(vol_len).std()
    med = rv.rolling(med_len, min_periods=vol_len * 3).median()
    vf = (med / rv).clip(0.2, 1.0).fillna(1.0).values if vol_scale else np.ones(len(btc))
    # equal-weight alt basket open->open returns, aligned to BTC ts
    rets = []
    for a in ALTS:
        d = bt.load(a, tf); j = pd.merge_asof(pd.DataFrame({"ts": ts}),
            d.assign(t=pd.to_datetime(d["timestamp"]))[["t", "open"]].rename(columns={"t": "ts"}),
            on="ts", direction="backward")
        oo = (j["open"].shift(-1) / j["open"] - 1).fillna(0).values
        rets.append(oo)
    basket = np.nanmean(rets, axis=0)
    n = len(btc); held = 0.0; eq = 1.0; eqs = np.ones(n); nreb = 0; feedrag = 0.0
    warm = ema_s + 5
    for i in range(warm, n - 1):
        target = sig[i] * vf[i]
        if abs(target - held) >= thresh:
            f = abs(target - held) * COST
            eq *= (1 - f); feedrag += f; held = target; nreb += 1
        eq *= (1 + held * basket[i])
        eqs[i + 1] = eq
    s = pd.Series(eqs, index=ts).iloc[warm:]
    yrs = max((s.index[-1] - s.index[0]).days / 365.25, 1e-9)
    cg = (s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1 if s.iloc[-1] > 0 else -1
    dd = (s / s.cummax() - 1).min()
    return cg, dd, (cg / abs(dd) if dd < -1e-9 else 0), nreb, feedrag * 100, yrs


def main():
    print("=" * 90)
    print("BTC-ALTS REBALANCE TUNE — timeframe x threshold x vol-scale (alt data 2021-2026)")
    print("=" * 90)
    print(f"  {'config':<40}{'CAGR':>6}{'DD':>6}{'r/DD':>6}{'#rebal':>8}{'feeDrag%':>9}")
    cfgs = [
        ("1h EMA32/800, thr10% (LIVE)", "1h", 32, 800, 24, 720, 0.10, True),
        ("1h EMA32/800, thr5%", "1h", 32, 800, 24, 720, 0.05, True),
        ("1h EMA32/800, thr20%", "1h", 32, 800, 24, 720, 0.20, True),
        ("1h EMA32/800, thr30%", "1h", 32, 800, 24, 720, 0.30, True),
        ("1h EMA32/800, NO vol-scale", "1h", 32, 800, 24, 720, 0.10, False),
        ("4h EMA8/200, thr10%", "4h", 8, 200, 6, 180, 0.10, True),
        ("4h EMA8/200, thr20%", "4h", 8, 200, 6, 180, 0.20, True),
        ("4h EMA8/200, NO vol-scale", "4h", 8, 200, 6, 180, 0.10, False),
    ]
    for name, *args in cfgs:
        try:
            cg, dd, rr, nreb, fd, yrs = run(*args)
            print(f"  {name:<40}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{nreb:>8}{fd:>8.1f}%")
        except Exception as e:
            print(f"  {name:<40} ERR {str(e)[:30]}")
    print("\n  (#rebal = total rebalances; feeDrag% = cumulative fees+slippage paid to rebalancing)")


if __name__ == "__main__":
    main()
