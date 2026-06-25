#!/usr/bin/env python3
"""backtest_basket_tp.py — does an overall take-profit (+5%/+10%, then wait for signal flip) help?

The basket/btcalts bots ride until the trend signal flips (no TP). This tests adding: bank the
position when it's up +X%, go flat, and re-enter only when the signal FLIPS to the other side.
Tested on btcalts (BTC EMA32/800 -> alt basket, unified signal) and all-weather (per-coin EMA8/200).
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


def run_tp(ret, sig, tp=None, warm=0):
    """Ride sig direction; if tp set, bank at +tp% then stay flat until sig flips away."""
    n = len(ret); eq = 1.0; p = 0; entry_eq = 1.0; wait = 0; eqs = np.ones(n)
    for i in range(warm, n - 1):
        s = sig[i]
        if np.isnan(s): s = 0
        if p != 0:
            gain = eq / entry_eq - 1
            if tp is not None and gain >= tp:                 # take profit -> flat, wait for flip
                eq *= (1 - COST); p = 0; wait = int(np.sign(s)) if s != 0 else wait
            elif s != 0 and np.sign(s) != p:                  # signal flipped -> reverse
                eq *= (1 - 2 * COST); p = int(np.sign(s)); entry_eq = eq; wait = 0
        if p == 0 and s != 0:                                 # (re)enter
            if wait == 0 or np.sign(s) != wait:
                eq *= (1 - COST); p = int(np.sign(s)); entry_eq = eq; wait = 0
        eq *= (1 + p * ret[i])
        eqs[i + 1] = eq
    return pd.Series(eqs).iloc[warm:]


def m(s, idx):
    s = pd.Series(s.values, index=idx[:len(s)] if len(idx) >= len(s) else idx)
    yrs = max((s.index[-1] - s.index[0]).days / 365.25, 1e-9)
    cg = (s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1 if s.iloc[-1] > 0 else -1
    dd = (s / s.cummax() - 1).min()
    return cg, dd, (cg / abs(dd) if dd < -1e-9 else 0)


def main():
    # ---- btcalts: BTC EMA32/800 (1h) -> eqw alt basket ----
    btc = bt.load("BTCUSDT", "1h"); ts = pd.to_datetime(btc["timestamp"])
    sig = np.where((bt.ema(btc["close"], 32) > bt.ema(btc["close"], 800)).shift(1), 1.0, -1.0)
    rets = []
    for a in ALTS:
        d = bt.load(a, "1h"); j = pd.merge_asof(pd.DataFrame({"ts": ts}),
            d.assign(t=pd.to_datetime(d["timestamp"]))[["t", "open"]].rename(columns={"t": "ts"}),
            on="ts", direction="backward")
        rets.append((j["open"].shift(-1) / j["open"] - 1).fillna(0).values)
    basket = np.nanmean(rets, axis=0)
    print("=" * 66)
    print("OVERALL TAKE-PROFIT then wait-for-flip — does it help the basket bots?")
    print("=" * 66)
    print("  BTC-ALTS (BTC EMA32/800 -> eqw ETH/BNB/SOL, 1h):")
    print(f"    {'config':<22}{'CAGR':>7}{'DD':>7}{'ret/DD':>8}")
    for name, tp in [("no TP (LIVE)", None), ("TP +5%", 0.05), ("TP +10%", 0.10), ("TP +20%", 0.20)]:
        s = run_tp(basket, sig, tp, warm=805); cg, dd, rr = m(s, ts.iloc[805:])
        print(f"    {name:<22}{cg*100:>6.0f}%{dd*100:>6.0f}%{rr:>8.2f}")

    # ---- all-weather: per-coin EMA8/200 (4h), TP each coin, average ----
    print("\n  ALL-WEATHER (4-coin EMA8/200, 4h, per-coin TP):")
    print(f"    {'config':<22}{'CAGR':>7}{'DD':>7}{'ret/DD':>8}")
    coins4 = ["BTCUSDT"] + ALTS
    for name, tp in [("no TP (LIVE)", None), ("TP +5%", 0.05), ("TP +10%", 0.10), ("TP +20%", 0.20)]:
        eqs = []
        for sym in coins4:
            d = bt.load(sym, "4h"); cc = d["close"]
            sg = np.where((bt.ema(cc, 8) > bt.ema(cc, 200)).shift(1), 1.0, -1.0)
            rr_ = (d["open"].shift(-1) / d["open"] - 1).fillna(0).values
            eqs.append(run_tp(rr_, sg, tp, warm=205).reset_index(drop=True))
        L = min(len(e) for e in eqs); basket_eq = sum(e.iloc[:L] for e in eqs) / len(eqs)
        idx = pd.to_datetime(bt.load("BTCUSDT", "4h")["timestamp"]).iloc[205:205 + L]
        cg, dd, rr = m(basket_eq, idx)
        print(f"    {name:<22}{cg*100:>6.0f}%{dd*100:>6.0f}%{rr:>8.2f}")
    print("\n  (TP = bank at +X% then sit flat until the trend signal flips, then re-enter)")


if __name__ == "__main__":
    main()
