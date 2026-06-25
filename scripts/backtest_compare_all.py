#!/usr/bin/env python3
"""backtest_compare_all.py — compare ALL live paper bots on the same window (2021-2026).

Bots: btcv2 (conviction long BTC + ETH short), btcalts (BTC EMA32/800 -> alt basket, vol-scaled),
allweather (4-coin EMA8/200 reverse). Same 2021-01 .. 2026-06 window for a fair comparison
(alt data starts 2021). Reports CAGR/DD/ret/DD + year-by-year for each.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_v2_eth_conviction import run_ec
from backtest_btclong_ethshort import load4h

COST = bt.FEE_PCT + bt.SLIP_PCT
ALTS = ["ETHUSDT", "BNBUSDT", "SOLUSDT"]
W0 = pd.Timestamp("2021-01-01")


def metrics(s):
    s = s[s.index >= W0]
    if len(s) < 50: return None
    s = s / s.iloc[0]
    yrs = max((s.index[-1] - s.index[0]).days / 365.25, 1e-9)
    cg = (s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1 if s.iloc[-1] > 0 else -1
    dd = (s / s.cummax() - 1).min()
    return cg, dd, (cg / abs(dd) if dd < -1e-9 else 0), s


def btcv2_eq():
    btc, _ = load4h("BTCUSDT"); eth, _ = load4h("ETHUSDT")
    common = pd.Index(btc["timestamp"]).intersection(pd.Index(eth["timestamp"]))
    b = btc[btc["timestamp"].isin(common)].reset_index(drop=True); c = b["close"]
    adx = bt.adx(b, 14).shift(1).fillna(0).values
    egap = ((bt.ema(c, 50) - bt.ema(c, 200)) / bt.ema(c, 200)).shift(1).fillna(0).values
    conv = np.clip(adx / 35.0, 0, 1) * 0.5 + np.clip(egap / 0.12, 0, 1) * 0.5
    return run_ec("eth", long_lev=1.0 + 1.5 * conv)


def btcalts_eq(thresh=0.20):
    btc = bt.load("BTCUSDT", "1h"); ts = pd.to_datetime(btc["timestamp"])
    sig = np.where((bt.ema(btc["close"], 32) > bt.ema(btc["close"], 800)).shift(1), 1.0, -1.0)
    rv = btc["close"].pct_change().rolling(24).std()
    vf = (rv.rolling(720, min_periods=72).median() / rv).clip(0.2, 1.0).fillna(1.0).values
    rets = []
    for a in ALTS:
        d = bt.load(a, "1h"); j = pd.merge_asof(pd.DataFrame({"ts": ts}),
            d.assign(t=pd.to_datetime(d["timestamp"]))[["t", "open"]].rename(columns={"t": "ts"}),
            on="ts", direction="backward")
        rets.append((j["open"].shift(-1) / j["open"] - 1).fillna(0).values)
    basket = np.nanmean(rets, axis=0)
    eq = 1.0; held = 0.0; out = np.ones(len(btc))
    for i in range(805, len(btc) - 1):
        tgt = sig[i] * vf[i]
        if abs(tgt - held) >= thresh:
            eq *= (1 - abs(tgt - held) * COST); held = tgt
        eq *= (1 + held * basket[i]); out[i + 1] = eq
    return pd.Series(out, index=ts)


def allweather_eq():
    eqs = []; idx = None
    for sym in ["BTCUSDT"] + ALTS:
        d = bt.load(sym, "4h"); cc = d["close"]
        pos = pd.Series(np.where(bt.ema(cc, 8) > bt.ema(cc, 200), 1, -1), index=d.index).shift(1).fillna(0)
        oo = (d["open"].shift(-1) / d["open"] - 1).fillna(0)
        turn = pos.diff().abs().fillna(pos.abs())
        eq = (1 + pos * oo - turn * COST).cumprod(); eq.index = pd.to_datetime(d["timestamp"])
        eqs.append(eq)
    ix = eqs[0].index
    for e in eqs[1:]: ix = ix.intersection(e.index)
    return sum(e.reindex(ix).ffill() for e in eqs) / len(eqs)


def main():
    print("=" * 78)
    print("ALL LIVE BOTS — same window 2021-01 .. 2026-06 (fair comparison)")
    print("=" * 78)
    bots = {"btcv2 (V2: conv long BTC + ETH short)": btcv2_eq(),
            "btcalts (BTC->alt basket, thr20%)": btcalts_eq(),
            "allweather (4-coin EMA8/200)": allweather_eq()}
    print(f"  {'bot':<40}{'CAGR':>6}{'DD':>6}{'ret/DD':>8}")
    series = {}
    for name, eq in bots.items():
        r = metrics(eq)
        if r is None: print(f"  {name:<40} (insufficient)"); continue
        cg, dd, rr, s = r; series[name] = s
        print(f"  {name:<40}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>8.2f}")
    print("\n  YEAR-BY-YEAR return%:")
    hdr = "  ".join(str(y) for y in range(2021, 2027))
    print(f"    {'bot':<40}{hdr}")
    for name, s in series.items():
        row = []
        for y in range(2021, 2027):
            seg = s[s.index.year == y]
            row.append(f"{(seg.iloc[-1]/seg.iloc[0]-1)*100:+5.0f}%" if len(seg) > 20 else "   --")
        print(f"    {name:<40}{'  '.join(row)}")
    print("\n  (btcv2 also backtests 2017-2026 incl. 3 bears: CAGR ~150%/DD -36%/ret-DD 4.18.")
    print("   trend_btc (long-only 4h BTC, dynamic-lev) is BTC-only ~50-65% CAGR / -28% DD, 2019-2026.)")


if __name__ == "__main__":
    main()
