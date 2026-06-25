#!/usr/bin/env python3
"""backtest_mtf_trademgmt.py — can trade management (break-even, trailing stop, trailing TP)
reduce DD AND lift CAGR on the MTF Regime? Tested at 1x and 2x leverage.

Base: BTC 4h long/flat, LONG when (4h EMA50>200)&(prior-day EMA50>200); regime-flip exit.
Overlays on the open long (leverage L, honest liquidation):
  break_even : once peak profit >= be%, move stop to ENTRY (protect the trade)
  trail_pct  : stop trails X% below the highest price since entry
  trail_atr  : ATR(14) chandelier trailing stop
After any stop exit, stay flat until the regime re-arms (regime false -> true).
Goal: find a config with CAGR >= 1x-base AND DD <= 1x-base. Honest taker fees, full + OOS.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

MAINT = 0.005; FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT


def regime():
    df4 = bt.load("BTCUSDT", "4h"); c4 = df4["close"]
    dfd = bt.load("BTCUSDT", "1d"); cd = dfd["close"]
    ts4 = pd.to_datetime(df4["timestamp"]); tsd = pd.to_datetime(dfd["timestamp"])
    didx = pd.merge_asof(pd.DataFrame({"ts": ts4}).sort_values("ts"),
                         pd.DataFrame({"ts": tsd, "j": np.arange(len(tsd))}).sort_values("ts"),
                         on="ts", direction="backward")["j"].fillna(0).astype(int).values
    d_up = (bt.ema(cd, 50) > bt.ema(cd, 200)).shift(1).fillna(False).astype(bool).values[didx]
    f_up = (bt.ema(c4, 50) > bt.ema(c4, 200)).values
    return df4, (f_up & d_up)


def run(df4, bull, lev=1.0, be=None, trail_pct=None, trail_atr=None):
    o = df4["open"].values; h = df4["high"].values; l = df4["low"].values; c = df4["close"].values
    a = bt.atr(df4, 14).values
    n = len(df4); bal = 1.0; side = 0; entry = peak = stop = 0.0; armed = True
    eq = np.ones(n)

    def close(px):
        nonlocal bal, side
        fpx = px * (1 - SLIP); bal *= (1 + lev*(fpx/entry - 1))*(1 - FEE*lev); side = 0

    for i in range(16, n - 1):
        oN, hN, lN, cN, aN = o[i+1], h[i+1], l[i+1], c[i+1], a[i]
        if not bull[i]:
            armed = True
        if side == 1 and bal > 0:
            # liquidation first
            if lN <= entry*(1 - (1/lev - MAINT)):
                bal = 0.0; side = 0
            else:
                peak = max(peak, hN)
                if be is not None and peak/entry - 1 >= be: stop = max(stop, entry)
                if trail_pct is not None: stop = max(stop, peak*(1 - trail_pct))
                if trail_atr is not None: stop = max(stop, cN - trail_atr*aN)
                if stop > 0 and lN <= stop: close(stop)
                elif not bull[i]: close(oN)               # regime-flip exit
        if side == 0 and bull[i] and armed and bal > 0:
            side = 1; entry = oN*(1+SLIP); peak = entry; stop = 0.0; armed = False; bal *= (1 - FEE*lev)
        eq[i+1] = max(bal*(1 + lev*(cN/entry - 1)), 0.0) if side == 1 and bal > 0 else bal
        if eq[i+1] <= 0: eq[i+1:] = 0.0; break
    s = pd.Series(eq, index=pd.to_datetime(df4["timestamp"])).iloc[16:]
    return s


def m(s):
    yrs = max((s.index[-1]-s.index[0]).days/365.25, 1e-9)
    cg = (s.iloc[-1]/s.iloc[0])**(1/yrs)-1 if s.iloc[-1] > 0 else -1
    dd = (s/s.cummax()-1).min()
    return cg, dd, (cg/abs(dd) if dd < -1e-9 else 0.0)


def main():
    df4, bull = regime()
    base = run(df4, bull, 1.0); bc, bd, br = m(base)
    print("=" * 90)
    print("MTF REGIME (BTC 4h) — trade management (break-even / trailing) at 1x and 2x")
    print(f"  Target to beat: 1x base CAGR {bc*100:.0f}%, DD {bd*100:.0f}%, ret/DD {br:.2f}")
    print("=" * 90)
    print(f"  {'config':<34}{'FULL CAGR':>10}{'DD':>7}{'r/DD':>6}   {'OOS CAGR':>9}{'OOS DD':>7}{'OOS rDD':>8}{'  vs base'}")
    cfgs = [
        ("1x baseline", dict(lev=1.0)),
        ("1x +breakeven10%", dict(lev=1.0, be=0.10)),
        ("1x +trail 20%", dict(lev=1.0, trail_pct=0.20)),
        ("1x +trail 30%", dict(lev=1.0, trail_pct=0.30)),
        ("1x +ATR4 trail", dict(lev=1.0, trail_atr=4.0)),
        ("1x +BE10 +trail30%", dict(lev=1.0, be=0.10, trail_pct=0.30)),
        ("2x baseline", dict(lev=2.0)),
        ("2x +breakeven10%", dict(lev=2.0, be=0.10)),
        ("2x +trail 20%", dict(lev=2.0, trail_pct=0.20)),
        ("2x +trail 30%", dict(lev=2.0, trail_pct=0.30)),
        ("2x +ATR4 trail", dict(lev=2.0, trail_atr=4.0)),
        ("2x +BE10 +trail25%", dict(lev=2.0, be=0.10, trail_pct=0.25)),
    ]
    for name, kw in cfgs:
        s = run(df4, bull, **kw); fc, fd, fr = m(s)
        cut = s.index[int(len(s)*0.6)]; oc, od, orr = m(s[s.index >= cut])
        win = "  <<< CAGR>base & DD<=base" if (fc > bc and fd >= bd) else ""
        print(f"  {name:<34}{fc*100:>9.0f}%{fd*100:>6.0f}%{fr:>6.2f}   {oc*100:>8.0f}%{od*100:>6.0f}%{orr:>8.2f}{win}")


if __name__ == "__main__":
    main()
