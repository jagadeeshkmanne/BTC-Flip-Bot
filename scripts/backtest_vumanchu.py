#!/usr/bin/env python3
"""backtest_vumanchu.py — VuManChu Cipher B / WaveTrend, honest backtest.

WaveTrend (LazyBear/VuManChu):
  ap = hlc3 ; esa = ema(ap,n1) ; d = ema(|ap-esa|,n1) ; ci = (ap-esa)/(0.015*d)
  wt1 = ema(ci,n2) ; wt2 = sma(wt1,4) ; OB=+53/+60, OS=-53/-60
  GREEN dot (buy)  = wt1 crosses ABOVE wt2 while wt2 <= OS  (oversold)
  RED dot (sell)   = wt1 crosses BELOW wt2 while wt2 >= OB  (overbought)

Strategies tested (it's a mean-reversion oscillator, so test both raw and trend-filtered):
  dots_ls   : buy dot -> LONG ; sell dot -> SHORT (flip)        [pure VuManChu reversal]
  dots_lf   : buy dot -> LONG ; sell dot -> FLAT               [long-only]
  cross_ls  : wt1>wt2 -> LONG else SHORT (always-in, momentum read)
  dots_trend: buy dot only when close>EMA200, exit sell/below EMA200  [trend-filtered]

Coins BTC/ETH/BNB, 1h + 4h, param sets (9,12) & (10,21). Select best by IN-SAMPLE ret/DD,
report OOS. Honest engine (bt_helpers): next-open fills, fee+slip, 60/40 OOS.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt


def wavetrend(df, n1, n2):
    ap = (df["high"] + df["low"] + df["close"]) / 3
    esa = bt.ema(ap, n1)
    d = bt.ema((ap - esa).abs(), n1)
    ci = (ap - esa) / (0.015 * d.replace(0, 1e-9))
    wt1 = bt.ema(ci, n2)
    wt2 = wt1.rolling(4).mean()
    return wt1, wt2


def signals(df, n1, n2, OS=-53, OB=53):
    wt1, wt2 = wavetrend(df, n1, n2)
    cross_up = (wt1 > wt2) & (wt1.shift(1) <= wt2.shift(1))
    cross_dn = (wt1 < wt2) & (wt1.shift(1) >= wt2.shift(1))
    buy = cross_up & (wt2 <= OS)
    sell = cross_dn & (wt2 >= OB)
    return buy.values, sell.values, (wt1 > wt2).values


def build_pos(df, mode, n1, n2):
    buy, sell, wtup = signals(df, n1, n2)
    e200 = (df["close"] > bt.ema(df["close"], 200)).values
    n = len(df); pos = np.zeros(n); side = 0
    for i in range(n):
        if mode == "cross_ls":
            side = 1 if wtup[i] else -1
        elif mode == "dots_ls":
            if buy[i]: side = 1
            elif sell[i]: side = -1
        elif mode == "dots_lf":
            if buy[i]: side = 1
            elif sell[i]: side = 0
        elif mode == "dots_trend":
            if buy[i] and e200[i]: side = 1
            elif sell[i] or not e200[i]: side = 0
        pos[i] = side
    return pd.Series(pos, index=df.index)


def main():
    modes = ["dots_ls", "dots_lf", "cross_ls", "dots_trend"]
    params = [(9, 12), (10, 21)]
    for tf in ("15m", "1h", "4h"):
        coins = ["BTCUSDT"] if tf == "15m" else ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
        print("\n" + "=" * 92)
        print(f"VuManChu Cipher B / WaveTrend — {tf} (best-IS param, honest OOS) — '15m/1h said to be good'")
        print("=" * 92)
        print(f"  {'coin':<7}{'best mode/params':<22}{'IS rDD':>8}{'OOS CAGR':>9}{'OOS DD':>8}{'OOS rDD':>8}{'trades':>8}{'B&H rDD':>9}")
        for coin in coins:
            df = bt.load(coin, tf)
            bh_oos = bt.metrics(bt.oos_split(bt.buyhold(df))[1])[2]
            best = None
            for mode in modes:
                for (n1, n2) in params:
                    pos = build_pos(df, mode, n1, n2)
                    eq, nt = bt.backtest_signal(df, pos)
                    is_r = bt.metrics(bt.oos_split(eq)[0])[2]
                    if best is None or is_r > best[0]:
                        c, d, r = bt.metrics(bt.oos_split(eq)[1])
                        best = (is_r, f"{mode} {n1}/{n2}", c, d, r, nt)
            isr, lbl, c, d, r, nt = best
            print(f"  {coin[:-4]:<7}{lbl:<22}{isr:>8.2f}{c*100:>8.0f}%{d*100:>7.0f}%{r:>8.2f}{nt:>8}{bh_oos:>9.2f}")


if __name__ == "__main__":
    main()
