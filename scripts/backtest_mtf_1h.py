#!/usr/bin/env python3
"""backtest_mtf_1h.py — port the MTF Regime to 1h execution, with 5% break-even + entry/exit ideas.

Base (4h): LONG when 4h EMA50>200 AND prior-day EMA50>200; regime-flip exit; +10% break-even.
1h version: execute on 1h, keep the SLOW regime gates (that's the edge). LONG when:
  (1h trend bull) AND (4h EMA50>200) AND (prior-day EMA50>200).
1h trend EMA pair is SCALED so it doesn't over-trade (1h is 4x faster than 4h). Break-even 5%.
Entry/exit ideas tested: regime-flip exit (base), +5% break-even, and a pullback entry.
Honest: closed-bar signals, next-open fills, taker fees, intrabar break-even, full + OOS.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT


def map_htf(ts_lo, ts_hi, vals_hi):
    """merge_asof: each low-TF bar gets the last CLOSED high-TF value (vals_hi already shifted)."""
    didx = pd.merge_asof(pd.DataFrame({"ts": ts_lo}).sort_values("ts"),
                         pd.DataFrame({"ts": ts_hi, "j": np.arange(len(ts_hi))}).sort_values("ts"),
                         on="ts", direction="backward")["j"].fillna(0).astype(int).values
    return vals_hi[didx]


def build(tf1, ef, es, use_4h=True):
    df1 = bt.load("BTCUSDT", tf1); c1 = df1["close"]
    df4 = bt.load("BTCUSDT", "4h"); dfd = bt.load("BTCUSDT", "1d")
    ts1 = pd.to_datetime(df1["timestamp"])
    d_up = (bt.ema(dfd["close"], 50) > bt.ema(dfd["close"], 200)).shift(1).fillna(False).astype(bool).values
    d_up_1 = map_htf(ts1, pd.to_datetime(dfd["timestamp"]), d_up)
    bull = (bt.ema(c1, ef) > bt.ema(c1, es)).values & d_up_1
    if use_4h:
        f4_up = (bt.ema(df4["close"], 50) > bt.ema(df4["close"], 200)).shift(1).fillna(False).astype(bool).values
        bull = bull & map_htf(ts1, pd.to_datetime(df4["timestamp"]), f4_up)
    return df1, bull


def run(df1, bull, be=None, pullback_ema=None):
    o = df1["open"].values; h = df1["high"].values; l = df1["low"].values; c = df1["close"].values
    pe = bt.ema(df1["close"], pullback_ema).values if pullback_ema else None
    n = len(df1); bal = 1.0; side = 0; entry = peak = stop = 0.0; armed = True
    eq = np.ones(n); trades = []
    for i in range(810, n - 1):
        oN, hN, lN, cN = o[i+1], h[i+1], l[i+1], c[i+1]
        if not bull[i]:
            armed = True
        if side == 1:
            peak = max(peak, hN)
            if be is not None and peak/entry - 1 >= be:
                stop = max(stop, entry)
            if stop > 0 and lN <= stop:
                fpx = stop*(1-SLIP); bal *= (fpx/entry)*(1-2*FEE); trades.append(fpx/entry-1); side = 0
            elif not bull[i]:
                fpx = oN*(1-SLIP); bal *= (fpx/entry)*(1-2*FEE); trades.append(fpx/entry-1); side = 0
        if side == 0 and bull[i] and armed:
            # optional pullback entry: only enter when price is near/below the fast EMA (buy the dip in-regime)
            if pullback_ema is None or c[i] <= pe[i]:
                side = 1; entry = oN*(1+SLIP); peak = entry; stop = 0.0; armed = False; bal *= (1-FEE)
        eq[i+1] = bal if side == 0 else bal*cN/entry
    s = pd.Series(eq, index=pd.to_datetime(df1["timestamp"])).iloc[810:]
    wr = (sum(1 for t in trades if t > 0)/len(trades)*100) if trades else 0
    return s, len(trades), wr


def m(s):
    yrs = max((s.index[-1]-s.index[0]).days/365.25, 1e-9)
    cg = (s.iloc[-1]/s.iloc[0])**(1/yrs)-1 if s.iloc[-1] > 0 else -1
    dd = (s/s.cummax()-1).min()
    return cg, dd, (cg/abs(dd) if dd < -1e-9 else 0.0)


def main():
    # 4h reference
    print("=" * 92)
    print("MTF REGIME ported to 1h — scaled trend EMAs, 5% break-even, entry/exit ideas")
    print("=" * 92)
    print(f"  {'config':<40}{'CAGR':>8}{'DD':>6}{'r/DD':>6}{'trades':>8}{'win%':>6}   {'OOS rDD':>8}")
    # 4h base for comparison
    df4, b4 = build("4h", 50, 200, use_4h=False)
    from backtest_mtf_trademgmt import run as run4, regime as reg4
    df4b, bull4 = reg4(); s4,_,_ = run4(df4b, bull4, lev=1.0, be=0.10), None, None
    s4 = run4(df4b, bull4, lev=1.0, be=0.10); fc,fd,fr=m(s4); o4=m(s4[s4.index>=s4.index[int(len(s4)*0.6)]])
    print(f"  {'[ref] 4h base +10% BE':<40}{fc*100:>7.0f}%{fd*100:>5.0f}%{fr:>6.2f}{'-':>8}{'-':>6}   {o4[2]:>8.2f}")
    # 1h variants: trend EMA pairs scaled, with 5% BE
    for ef, es in [(50,200),(100,400),(200,800)]:
        for be, pb, tag in [(0.05,None,'+5% BE'),(None,None,'regime-exit only'),(0.05,es,'+5% BE +pullback entry')]:
            df1, bull = build("1h", ef, es, use_4h=True)
            s, nt, wr = run(df1, bull, be=be, pullback_ema=pb)
            cg,dd,rr = m(s); oo=m(s[s.index>=s.index[int(len(s)*0.6)]])
            print(f"  {f'1h EMA{ef}/{es} {tag}':<40}{cg*100:>7.0f}%{dd*100:>5.0f}%{rr:>6.2f}{nt:>8}{wr:>6.0f}   {oo[2]:>8.2f}")


if __name__ == "__main__":
    main()
