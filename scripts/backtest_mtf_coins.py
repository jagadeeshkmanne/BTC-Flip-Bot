#!/usr/bin/env python3
"""backtest_mtf_coins.py — MTF Regime (+10% break-even) on BTC / ETH / BNB / SOL.

Per coin: LONG when (own 4h EMA50>200) AND (own prior-day EMA50>200), else flat; +10% break-even.
Reports full + OOS + year-by-year so we see whether the edge generalizes beyond BTC.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT


def regime(coin):
    df4 = bt.load(coin, "4h"); c4 = df4["close"]
    dfd = bt.load(coin, "1d"); cd = dfd["close"]
    ts4 = pd.to_datetime(df4["timestamp"]); tsd = pd.to_datetime(dfd["timestamp"])
    didx = pd.merge_asof(pd.DataFrame({"ts": ts4}).sort_values("ts"),
                         pd.DataFrame({"ts": tsd, "j": np.arange(len(tsd))}).sort_values("ts"),
                         on="ts", direction="backward")["j"].fillna(0).astype(int).values
    d_up = (bt.ema(cd, 50) > bt.ema(cd, 200)).shift(1).fillna(False).astype(bool).values[didx]
    f_up = (bt.ema(c4, 50) > bt.ema(c4, 200)).values
    return df4, (f_up & d_up)


def run(df4, bull, be=0.10):
    o = df4["open"].values; h = df4["high"].values; l = df4["low"].values; c = df4["close"].values
    n = len(df4); bal = 1.0; side = 0; entry = peak = stop = 0.0; armed = True
    eq = np.ones(n)
    for i in range(16, n - 1):
        oN, hN, lN, cN = o[i+1], h[i+1], l[i+1], c[i+1]
        if not bull[i]:
            armed = True
        if side == 1:
            peak = max(peak, hN)
            if be is not None and peak/entry - 1 >= be: stop = max(stop, entry)
            if stop > 0 and lN <= stop:
                fpx = stop*(1-SLIP); bal *= (fpx/entry)*(1-2*FEE); side = 0
            elif not bull[i]:
                fpx = oN*(1-SLIP); bal *= (fpx/entry)*(1-2*FEE); side = 0
        if side == 0 and bull[i] and armed:
            side = 1; entry = oN*(1+SLIP); peak = entry; stop = 0.0; armed = False; bal *= (1-FEE)
        eq[i+1] = bal if side == 0 else bal*cN/entry
    return pd.Series(eq, index=pd.to_datetime(df4["timestamp"])).iloc[16:]


def m(s):
    yrs = max((s.index[-1]-s.index[0]).days/365.25, 1e-9)
    cg = (s.iloc[-1]/s.iloc[0])**(1/yrs)-1 if s.iloc[-1] > 0 else -1
    dd = (s/s.cummax()-1).min()
    return cg, dd, (cg/abs(dd) if dd < -1e-9 else 0.0)


def main():
    print("=" * 84)
    print("MTF REGIME (+10% break-even) on BTC / ETH / BNB / SOL")
    print("=" * 84)
    print(f"  {'coin':<7}{'FULL CAGR':>10}{'DD':>7}{'r/DD':>6}   {'OOS CAGR':>9}{'OOS rDD':>8}   buy&hold rDD")
    eqs = {}
    for coin in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"):
        df4, bull = regime(coin); s = run(df4, bull); eqs[coin] = (s, df4)
        fc, fd, fr = m(s); cut = s.index[int(len(s)*0.6)]; oc, od, orr = m(s[s.index >= cut])
        bh = bt.buyhold(df4); bhr = m(bt.oos_split(bh)[1])[2]
        print(f"  {coin[:-4]:<7}{fc*100:>9.0f}%{fd*100:>6.0f}%{fr:>6.2f}   {oc*100:>8.0f}%{orr:>8.2f}   {bhr:>10.2f}")
    print("\n  YEAR-BY-YEAR net% per coin:")
    years = list(range(2020, 2027))
    print(f"    {'coin':<7}" + "".join(f"{y:>9}" for y in years))
    for coin in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"):
        s, df4 = eqs[coin]; yr = pd.Series([t.year for t in s.index]); row = f"    {coin[:-4]:<7}"
        for y in years:
            ss = s[[t.year == y for t in s.index]]
            if len(ss) < 50: row += f"{'-':>9}"; continue
            row += f"{(ss.iloc[-1]/ss.iloc[0]-1)*100:>+8.0f}%"
        print(row)


if __name__ == "__main__":
    main()
