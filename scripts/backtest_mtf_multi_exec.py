#!/usr/bin/env python3
"""backtest_mtf_multi_exec.py — MTF Regime with FASTER execution TFs + more gates + SL/TP.

Tests the user's configs on BTC:
  A) 1h exec gated by 4h trend
  B) 1h exec gated by 4h AND 1d (triple-TF)
  C) 15m exec gated by 1h AND 4h
Execution-TF trend EMAs are scaled (slow) so they don't over-trade. Risk overlays: +5% break-even,
optional stop-loss, optional ATR trail. Reports CAGR/DD/ret/DD/TRADES + OOS so the
more-trades-vs-fees tradeoff is explicit.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT


def map_htf(ts_lo, ts_hi, vals):
    j = pd.merge_asof(pd.DataFrame({"ts": ts_lo}).sort_values("ts"),
                      pd.DataFrame({"ts": ts_hi, "j": np.arange(len(ts_hi))}).sort_values("ts"),
                      on="ts", direction="backward")["j"].fillna(0).astype(int).values
    return vals[j]


def trend_up(df, f, s):
    return (bt.ema(df["close"], f) > bt.ema(df["close"], s)).shift(1).fillna(False).astype(bool).values


def build(exec_tf, exec_ema, gates):
    """gates: list of (tf, f, s). exec_ema=(f,s) for the execution-TF trend."""
    de = bt.load("BTCUSDT", exec_tf); ts = pd.to_datetime(de["timestamp"])
    bull = (bt.ema(de["close"], exec_ema[0]) > bt.ema(de["close"], exec_ema[1])).values
    for tf, f, s in gates:
        dg = bt.load("BTCUSDT", tf)
        bull = bull & map_htf(ts, pd.to_datetime(dg["timestamp"]), trend_up(dg, f, s))
    return de, bull


def run(df, bull, be=0.05, sl=None, trail_atr=None):
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    a = bt.atr(df, 14).values
    n = len(df); bal = 1.0; side = 0; entry = peak = stop = 0.0; armed = True
    eq = np.ones(n); trades = 0; warm = 820
    for i in range(warm, n - 1):
        oN, hN, lN, cN, aN = o[i+1], h[i+1], l[i+1], c[i+1], a[i]
        if not bull[i]:
            armed = True
        if side == 1:
            peak = max(peak, hN)
            if be is not None and peak/entry - 1 >= be: stop = max(stop, entry)
            if sl is not None: stop = max(stop, entry*(1-sl))
            if trail_atr is not None: stop = max(stop, cN - trail_atr*aN)
            if stop > 0 and lN <= stop:
                fpx = stop*(1-SLIP); bal *= (fpx/entry)*(1-2*FEE); side = 0; trades += 1
            elif not bull[i]:
                fpx = oN*(1-SLIP); bal *= (fpx/entry)*(1-2*FEE); side = 0; trades += 1
        if side == 0 and bull[i] and armed:
            side = 1; entry = oN*(1+SLIP); peak = entry; stop = 0.0; armed = False; bal *= (1-FEE)
        eq[i+1] = bal if side == 0 else bal*cN/entry
    s = pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[warm:]
    return s, trades


def m(s):
    yrs = max((s.index[-1]-s.index[0]).days/365.25, 1e-9)
    cg = (s.iloc[-1]/s.iloc[0])**(1/yrs)-1 if s.iloc[-1] > 0 else -1
    dd = (s/s.cummax()-1).min()
    return cg, dd, (cg/abs(dd) if dd < -1e-9 else 0.0)


def main():
    print("=" * 92)
    print("MTF REGIME — faster execution TFs + more gates + SL/TP (BTC)")
    print("=" * 92)
    print(f"  {'config':<46}{'CAGR':>7}{'DD':>6}{'r/DD':>6}{'trades':>8}   {'OOS rDD':>8}")
    cfgs = [
        ("[ref] 4h exec, 1d gate, +5%BE", "4h", (50,200), [("1d",50,200)], dict(be=0.05)),
        ("A) 1h exec(200/800), 4h gate", "1h", (200,800), [("4h",50,200)], dict(be=0.05)),
        ("A) 1h exec(200/800), 4h gate +SL10%", "1h", (200,800), [("4h",50,200)], dict(be=0.05, sl=0.10)),
        ("B) 1h exec(200/800), 4h+1d gate", "1h", (200,800), [("4h",50,200),("1d",50,200)], dict(be=0.05)),
        ("B) 1h exec(100/400), 4h+1d gate", "1h", (100,400), [("4h",50,200),("1d",50,200)], dict(be=0.05)),
        ("B) 1h exec(200/800), 4h+1d +ATR4 trail", "1h", (200,800), [("4h",50,200),("1d",50,200)], dict(be=0.05, trail_atr=4.0)),
        ("C) 15m exec(800/3200), 1h+4h gate", "15m", (800,3200), [("1h",200,800),("4h",50,200)], dict(be=0.05)),
        ("C) 15m exec(200/800), 1h+4h gate", "15m", (200,800), [("1h",200,800),("4h",50,200)], dict(be=0.05)),
    ]
    for name, etf, eema, gates, kw in cfgs:
        try:
            de, bull = build(etf, eema, gates); s, nt = run(de, bull, **kw)
            cg, dd, rr = m(s); oo = m(s[s.index >= s.index[int(len(s)*0.6)]])
            print(f"  {name:<46}{cg*100:>6.0f}%{dd*100:>5.0f}%{rr:>6.2f}{nt:>8}   {oo[2]:>8.2f}")
        except Exception as e:
            print(f"  {name:<46} ERROR: {str(e)[:30]}")


if __name__ == "__main__":
    main()
