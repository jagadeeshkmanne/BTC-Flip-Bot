#!/usr/bin/env python3
"""backtest_mtf_tpsl.py — does adding TP / SL / trailing-stop to the MTF Regime help or hurt?

Base: LONG when (4h EMA50>200) AND (prior-day EMA50>200); exit when that regime flips off.
Overlays tested on top of the regime entry (BTC 4h, long-only):
  - fixed take-profit (exit at +X%, then wait for a FRESH regime re-arm before re-entering)
  - fixed stop-loss (exit at -Y%)
  - ATR trailing stop
  - combinations
Honest: signal on closed bar, fill next open, intrabar TP/SL on real high/low (stop-first),
taker fees. Reports full + OOS(40%) ret/DD vs the no-overlay baseline.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt


def regime(coin="BTCUSDT"):
    df4 = bt.load(coin, "4h"); c4 = df4["close"]
    dfd = bt.load(coin, "1d"); cd = dfd["close"]
    ts4 = pd.to_datetime(df4["timestamp"]); tsd = pd.to_datetime(dfd["timestamp"])
    didx = pd.merge_asof(pd.DataFrame({"ts": ts4}).sort_values("ts"),
                         pd.DataFrame({"ts": tsd, "j": np.arange(len(tsd))}).sort_values("ts"),
                         on="ts", direction="backward")["j"].fillna(0).astype(int).values
    dtrend = (bt.ema(cd, 50) > bt.ema(cd, 200)).shift(1).fillna(False).astype(bool).values[didx]
    trend4 = (bt.ema(c4, 50) > bt.ema(c4, 200)).values
    return df4, (trend4 & dtrend)


def run(df4, bull, tp=None, sl=None, trail_atr=None):
    o = df4["open"].values; h = df4["high"].values; l = df4["low"].values; c = df4["close"].values
    a = bt.atr(df4, 14).values
    n = len(df4); bal = 1.0; side = 0; entry = trail = 0.0; armed = True
    eq = np.ones(n); trades = []

    def close(px):
        nonlocal bal, side
        fpx = px * (1 - bt.SLIP_PCT)
        bal *= (fpx / entry) * (1 - 2 * bt.FEE_PCT); trades.append(fpx / entry - 1); side = 0

    for i in range(16, n - 1):
        oN, hN, lN, cN, aN = o[i+1], h[i+1], l[i+1], c[i+1], a[i]
        if not bull[i]:
            armed = True
        if side == 1:
            if sl is not None and lN <= entry*(1-sl): close(entry*(1-sl))
            elif trail_atr and lN <= trail: close(trail)
            elif tp is not None and hN >= entry*(1+tp): close(entry*(1+tp))
            elif not bull[i]: close(oN)                      # regime flip exit
            elif trail_atr: trail = max(trail, cN - trail_atr*aN)
        if side == 0 and bull[i] and armed:
            side = 1; entry = oN*(1+bt.SLIP_PCT); armed = False
            trail = oN - trail_atr*aN if trail_atr else 0.0
        eq[i+1] = bal if side == 0 else bal*cN/entry
    s = pd.Series(eq, index=pd.to_datetime(df4["timestamp"])).iloc[16:]
    return s, len(trades)


def met(s):
    yrs = max((s.index[-1]-s.index[0]).days/365.25, 1e-9)
    cagr = (s.iloc[-1]/s.iloc[0])**(1/yrs)-1
    dd = (s/s.cummax()-1).min()
    return cagr, dd, (cagr/abs(dd) if dd < -1e-9 else 0.0)


def main():
    df4, bull = regime("BTCUSDT")
    print("=" * 84)
    print("MTF REGIME (BTC 4h) — TP / SL / trailing overlays vs the no-overlay baseline")
    print("=" * 84)
    print(f"  {'overlay':<26}{'FULL CAGR':>10}{'DD':>6}{'r/DD':>6}{'trades':>8}   {'OOS CAGR':>9}{'OOS rDD':>8}")
    configs = [
        ("baseline (regime exit only)", {}),
        ("+TP 15%", dict(tp=0.15)),
        ("+TP 25%", dict(tp=0.25)),
        ("+TP 50%", dict(tp=0.50)),
        ("+SL 10%", dict(sl=0.10)),
        ("+SL 20%", dict(sl=0.20)),
        ("+ATR3 trail", dict(trail_atr=3.0)),
        ("+ATR4 trail", dict(trail_atr=4.0)),
        ("+TP25 +SL10", dict(tp=0.25, sl=0.10)),
        ("+SL15 +ATR4 trail", dict(sl=0.15, trail_atr=4.0)),
    ]
    base = None
    for name, kw in configs:
        s, nt = run(df4, bull, **kw)
        fc, fd, fr = met(s)
        cut = s.index[int(len(s)*0.6)]; oc, od, orr = met(s[s.index >= cut])
        if base is None: base = fr
        flag = "" if name.startswith("baseline") else ("  better" if fr > base else "  worse")
        print(f"  {name:<26}{fc*100:>9.0f}%{fd*100:>5.0f}%{fr:>6.2f}{nt:>8}   {oc*100:>8.0f}%{orr:>8.2f}{flag}")


if __name__ == "__main__":
    main()
