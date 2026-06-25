#!/usr/bin/env python3
"""backtest_price_action.py — PURE price action (no indicators), honest.

Only swing structure is used — no EMA/RSI/MACD. Swing pivots (confirmed R bars late, no
lookahead) define the last swing high (SH) and swing low (SL). Strategies:

  bos_ls  : break of structure, always-in-market. close > last SH -> LONG ; close < last SL -> SHORT.
  bos_lf  : same but long/flat (cash when bearish).
  bos_trail: long on a bullish break (close>SH); trailing stop = the rising last SL (pure-PA
            "stop game"); exit when price closes below SL, re-enter on the next break. long-only.

Tests pivot lookback L/R in {2,3,5} on BTC/ETH/BNB, 4h + 1d + 1h, selects best by IN-SAMPLE
ret/DD, reports OOS. Honest engine (bt_helpers): next-open fills, fee+slip, 60/40 OOS.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt


def pivots(df, L, R):
    """Confirmed swing highs/lows. Returns list of (confirm_idx, price, kind)."""
    h, l = df["high"].values, df["low"].values
    n = len(df); ev = []
    for j in range(L, n - R):
        wh, wl = h[j - L:j + R + 1], l[j - L:j + R + 1]
        if h[j] == wh.max() and (wh == h[j]).sum() == 1:
            ev.append((j + R, float(h[j]), "H"))
        if l[j] == wl.min() and (wl == l[j]).sum() == 1:
            ev.append((j + R, float(l[j]), "L"))
    ev.sort()
    return ev


def structure_pos(df, L, R, mode):
    """Build a position array from break-of-structure logic (no lookahead)."""
    ev = pivots(df, L, R)
    by_idx = {}
    for ci, px, k in ev:
        by_idx.setdefault(ci, []).append((k, px))
    o = df["open"].values; c = df["close"].values; h = df["high"].values; lo = df["low"].values
    n = len(df); pos = np.zeros(n)
    last_sh = last_sl = None; side = 0; trail = None
    for i in range(n):
        for k, px in by_idx.get(i, []):
            if k == "H": last_sh = px
            else: last_sl = px
        if mode in ("bos_ls", "bos_lf"):
            if last_sh is not None and c[i] > last_sh:
                side = 1
            elif last_sl is not None and c[i] < last_sl:
                side = -1 if mode == "bos_ls" else 0
            pos[i] = side
        elif mode == "bos_trail":
            if side == 0:
                if last_sh is not None and c[i] > last_sh:
                    side = 1; trail = last_sl
            else:
                trail = max(trail, last_sl) if (trail is not None and last_sl is not None) else (last_sl or trail)
                if last_sl is not None and c[i] < last_sl:   # close below rising swing-low -> exit
                    side = 0; trail = None
            pos[i] = side
    return pd.Series(pos, index=df.index)


def main():
    coins = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
    tfs = ["1d", "4h", "1h"]
    modes = ["bos_ls", "bos_lf", "bos_trail"]
    LRs = [(2, 2), (3, 3), (5, 5)]
    print("=" * 92)
    print("PURE PRICE ACTION (break-of-structure, swing stops — NO indicators), honest OOS")
    print("=" * 92)
    for tf in tfs:
        print(f"\n#### {tf} ####")
        print(f"  {'coin':<7}{'best mode/LR':<18}{'IS rDD':>8}{'OOS CAGR':>9}{'OOS DD':>8}{'OOS rDD':>8}{'trades':>8}{'B&H rDD':>9}")
        for coin in coins:
            df = bt.load(coin, tf)
            bh = bt.buyhold(df); _, bo = bt.oos_split(bh); bh_oos_rdd = bt.metrics(bo)[2]
            best = None
            for mode in modes:
                for (L, R) in LRs:
                    pos = structure_pos(df, L, R, mode)
                    eq, nt = bt.backtest_signal(df, pos)
                    iseq, ooseq = bt.oos_split(eq)
                    is_rdd = bt.metrics(iseq)[2]
                    if best is None or is_rdd > best[0]:
                        c, d, r = bt.metrics(ooseq)
                        best = (is_rdd, f"{mode}/{L}", c, d, r, nt)
            isr, lbl, c, d, r, nt = best
            print(f"  {coin[:-4]:<7}{lbl:<18}{isr:>8.2f}{c*100:>8.0f}%{d*100:>7.0f}%{r:>8.2f}{nt:>8}{bh_oos_rdd:>9.2f}")


if __name__ == "__main__":
    main()
