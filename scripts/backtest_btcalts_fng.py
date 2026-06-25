#!/usr/bin/env python3
"""backtest_btcalts_fng.py — does the Fear & Greed Index improve btcalts? (a)

btcalts = BTC EMA32/800 -> long/short eqw ETH/BNB/SOL, vol-scaled, thr20%. Tests F&G as a
contrarian filter: skip shorts in extreme FEAR (capitulation bounce risk), skip longs in extreme
GREED (overheated), and F&G-scaled exposure. Causal (uses prior-day F&G). 2021-2026.
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


def run(mode="base", thresh=0.20):
    btc = bt.load("BTCUSDT", "1h"); ts = pd.to_datetime(btc["timestamp"])
    sig = np.where((bt.ema(btc["close"], 32) > bt.ema(btc["close"], 800)).shift(1), 1.0, -1.0)
    rv = btc["close"].pct_change().rolling(24).std()
    vf = (rv.rolling(720, min_periods=72).median() / rv).clip(0.2, 1.0).fillna(1.0).values
    # F&G (daily) -> hourly, prior-day (causal)
    fng = pd.read_csv(os.path.join(bt.CACHE, "fng.csv"), parse_dates=["ts"])
    fng["ts"] = fng["ts"] + pd.Timedelta(days=1)   # use yesterday's reading
    f = pd.merge_asof(pd.DataFrame({"ts": ts}), fng.sort_values("ts"), on="ts", direction="backward")["fng"].ffill().fillna(50).values
    rets = []
    for a in ALTS:
        d = bt.load(a, "1h"); j = pd.merge_asof(pd.DataFrame({"ts": ts}),
            d.assign(t=pd.to_datetime(d["timestamp"]))[["t", "open"]].rename(columns={"t": "ts"}),
            on="ts", direction="backward")
        rets.append((j["open"].shift(-1) / j["open"] - 1).fillna(0).values)
    basket = np.nanmean(rets, axis=0)
    eq = 1.0; held = 0.0; out = np.ones(len(btc))
    for i in range(805, len(btc) - 1):
        s = sig[i]; fv = f[i]; tgt = s * vf[i]
        if mode == "no_short_fear" and s < 0 and fv < 25: tgt = 0.0
        elif mode == "no_long_greed" and s > 0 and fv > 75: tgt = 0.0
        elif mode == "both":
            if (s < 0 and fv < 25) or (s > 0 and fv > 75): tgt = 0.0
        elif mode == "fng_scale":
            # reduce exposure at extremes (contrarian): scale by how far from the extreme
            ex = 1.0 - max(0, fv - 75) / 25 * 0.7 - max(0, 25 - fv) / 25 * 0.7
            tgt = s * vf[i] * max(0.3, ex)
        elif mode == "contrarian_fear_long":
            # extreme fear -> allow long even if trend bearish (bounce bet), small
            if fv < 20 and s < 0: tgt = 0.4
        if abs(tgt - held) >= thresh:
            eq *= (1 - abs(tgt - held) * COST); held = tgt
        eq *= (1 + held * basket[i]); out[i + 1] = eq
    s2 = pd.Series(out, index=ts); s2 = s2[s2.index >= pd.Timestamp("2021-01-01")]
    yrs = (s2.index[-1] - s2.index[0]).days / 365.25
    cg = (s2.iloc[-1] / s2.iloc[0]) ** (1 / yrs) - 1; dd = (s2 / s2.cummax() - 1).min()
    return cg, dd, (cg / abs(dd) if dd < -1e-9 else 0), s2


def yr(s, y):
    seg = s[s.index.year == y]; return (seg.iloc[-1] / seg.iloc[0] - 1) * 100 if len(seg) > 20 else 0


def main():
    print("=" * 80)
    print("(a) FEAR & GREED filter on btcalts (2021-2026)")
    print("=" * 80)
    print(f"  {'config':<34}{'CAGR':>6}{'DD':>6}{'ret/DD':>8}  2022/2024/2026")
    for name, mode in [("base (no F&G) — LIVE", "base"),
                       ("skip SHORT in extreme fear(<25)", "no_short_fear"),
                       ("skip LONG in extreme greed(>75)", "no_long_greed"),
                       ("both skips", "both"),
                       ("F&G-scaled exposure (contrarian)", "fng_scale"),
                       ("contrarian: long in extreme fear", "contrarian_fear_long")]:
        cg, dd, rr, s = run(mode)
        print(f"  {name:<34}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>8.2f}  {yr(s,2022):>+5.0f}/{yr(s,2024):>+4.0f}/{yr(s,2026):>+4.0f}")


if __name__ == "__main__":
    main()
