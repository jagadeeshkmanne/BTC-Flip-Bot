#!/usr/bin/env python3
"""backtest_bb_squeeze.py — Bollinger Band / TTM Squeeze breakout (breakup/breakdown), honest.

Squeeze = BB(20,2) contracts INSIDE Keltner(20, mult*ATR) -> low-vol coil. When it FIRES
(BB expands back outside KC) a directional move tends to follow. Trade the breakout direction:
  breakup   -> LONG ;  breakdown -> SHORT  (direction from momentum at the fire)

Only trades AFTER a coil (no trading during the squeeze) — the breakout's best setup.

Modes:
  mom_ls : on fire, enter sign(momentum); flip when momentum flips; flat during squeeze. (L/S)
  mom_lf : same, long/flat (short -> flat).
  trend  : on fire, only LONG if close>EMA200 / only SHORT if close<EMA200; flat otherwise.
KC mult {1.5, 2.0}. Coins BTC/ETH/BNB on 15m/1h/4h. Select best by IN-SAMPLE ret/DD, report OOS.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt


def squeeze_pos(df, mode, kc_mult):
    c = df["close"]
    mid = c.rolling(20).mean(); sd = c.rolling(20).std(ddof=0)
    bb_up, bb_lo = mid + 2 * sd, mid - 2 * sd
    a = bt.atr(df, 20)
    kc_up, kc_lo = mid + kc_mult * a, mid - kc_mult * a
    on = ((bb_up < kc_up) & (bb_lo > kc_lo)).values            # squeeze ON (coiled)
    # TTM-style momentum: close vs midpoint of donchian-mid & sma
    dmid = (df["high"].rolling(20).max() + df["low"].rolling(20).min()) / 2
    mom = (c - (dmid + mid) / 2).values
    e200 = (c > bt.ema(c, 200)).values
    n = len(df); pos = np.zeros(n); side = 0; was = False
    for i in range(n):
        if on[i]:
            was = True; side = 0                                # flat while coiling
        else:
            m = mom[i]
            if was:                                             # just fired -> enter breakout dir
                if mode == "trend":
                    side = 1 if (m > 0 and e200[i]) else (-1 if (m < 0 and not e200[i]) else 0)
                else:
                    side = 1 if m > 0 else -1
                was = False
            else:                                               # riding the move
                if side == 1 and m < 0:
                    side = -1 if mode == "mom_ls" else 0
                elif side == -1 and m > 0:
                    side = 1 if mode == "mom_ls" else 0
                elif mode == "trend":
                    if side == 1 and not e200[i]: side = 0
                    elif side == -1 and e200[i]: side = 0
        pos[i] = side
    return pd.Series(pos, index=df.index)


def main():
    modes = ["mom_ls", "mom_lf", "trend"]
    kcs = [1.5, 2.0]
    for tf in ("15m", "1h", "4h"):
        coins = ["BTCUSDT"] if tf == "15m" else ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
        print("\n" + "=" * 90)
        print(f"BB / TTM SQUEEZE breakout — {tf} (best-IS param, honest OOS)")
        print("=" * 90)
        print(f"  {'coin':<7}{'best mode/kc':<16}{'IS rDD':>8}{'OOS CAGR':>9}{'OOS DD':>8}{'OOS rDD':>8}{'trades':>8}{'B&H rDD':>9}")
        for coin in coins:
            df = bt.load(coin, tf)
            bh = bt.metrics(bt.oos_split(bt.buyhold(df))[1])[2]
            best = None
            for mode in modes:
                for kc in kcs:
                    pos = squeeze_pos(df, mode, kc)
                    eq, nt = bt.backtest_signal(df, pos)
                    isr = bt.metrics(bt.oos_split(eq)[0])[2]
                    if best is None or isr > best[0]:
                        c, d, r = bt.metrics(bt.oos_split(eq)[1])
                        best = (isr, f"{mode}/{kc}", c, d, r, nt)
            isr, lbl, c, d, r, nt = best
            print(f"  {coin[:-4]:<7}{lbl:<16}{isr:>8.2f}{c*100:>8.0f}%{d*100:>7.0f}%{r:>8.2f}{nt:>8}{bh:>9.2f}")


if __name__ == "__main__":
    main()
