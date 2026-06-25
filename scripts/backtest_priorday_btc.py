#!/usr/bin/env python3
"""backtest_priorday_btc.py — prior-day high/low breakout on BTC 1h, honest, many exit styles.

Idea: BTC breaks PRIOR DAY's HIGH on the 1h chart -> go LONG (breakup). Also test breakdown
(prior day low -> short). PDH/PDL from COMPLETED UTC days, shifted (no lookahead).

Variants:
  hold_lf  : long while close>PDH, flat when close<PDL                       (breakout-and-hold)
  hold_ls  : long close>PDH, short close<PDL                                 (both directions)
  trend_lf : long close>PDH AND close>EMA200; exit close<EMA200 or close<PDL (trend-filtered)
  eod_lf   : long on first close>PDH of the day, EXIT at end of UTC day      (pure intraday)
  tpsl     : long on close>PDH cross, fixed TP/SL (stateful)                 (defined risk)

Honest engine (bt_helpers): next-open fills, fee+slip, 60/40 OOS. + walk-forward on the best.
Data: BTC 1h (2020-2026).
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt


def prep(df):
    day = df["timestamp"].dt.floor("D")
    pdh = df.groupby(day)["high"].max().shift(1)      # PRIOR completed day's high
    pdl = df.groupby(day)["low"].min().shift(1)
    df = df.copy()
    df["pdh"] = day.map(pdh); df["pdl"] = day.map(pdl)
    df["day"] = day
    df["e200"] = bt.ema(df["close"], 200)
    return df


def pos_series(df, mode):
    c = df["close"].values; pdh = df["pdh"].values; pdl = df["pdl"].values
    e2 = (df["close"] > df["e200"]).values; day = df["day"].values
    n = len(df); pos = np.zeros(n); side = 0; traded_day = None
    for i in range(n):
        if np.isnan(pdh[i]):
            pos[i] = 0; continue
        if mode == "hold_lf":
            if c[i] > pdh[i]: side = 1
            elif c[i] < pdl[i]: side = 0
        elif mode == "hold_ls":
            if c[i] > pdh[i]: side = 1
            elif c[i] < pdl[i]: side = -1
        elif mode == "trend_lf":
            if c[i] > pdh[i] and e2[i]: side = 1
            elif c[i] < pdl[i] or not e2[i]: side = 0
        elif mode == "eod_lf":
            if day[i] != day[i-1]:
                side = 0; traded_day = None             # flat at each new day start
            if side == 0 and traded_day != day[i] and c[i] > pdh[i]:
                side = 1; traded_day = day[i]
        pos[i] = side
    return pd.Series(pos, index=df.index)


def main():
    df = prep(bt.load("BTCUSDT", "1h"))
    span = f"{df['timestamp'].iloc[0].date()}->{df['timestamp'].iloc[-1].date()}"
    bh = bt.buyhold(df); bh_f, bh_o = bt.metrics(bh)[2], bt.metrics(bt.oos_split(bh)[1])[2]
    print("=" * 88)
    print(f"BTC 1h — PRIOR-DAY HIGH/LOW breakout ({len(df)} bars, {span})")
    print(f"buy&hold: full ret/DD {bh_f:.2f} | OOS {bh_o:.2f}")
    print("=" * 88)
    print(f"  {'mode':<12}{'FULL CAGR':>10}{'DD':>6}{'r/DD':>6}{'trades':>8}{'win%':>6}   {'OOS CAGR':>9}{'OOS rDD':>8}")
    for mode in ("hold_lf", "hold_ls", "trend_lf", "eod_lf"):
        pos = pos_series(df, mode)
        eq, nt = bt.backtest_signal(df, pos)
        held = pos.shift(1).fillna(0)
        # rough per-trade win rate
        seg = []; cur = 0.0; sgn = 0
        oo = (df["open"].shift(-1)/df["open"]-1).fillna(0).values
        h = held.values
        for i in range(len(h)):
            if h[i] != sgn:
                if sgn != 0: seg.append(cur)
                cur = 0.0; sgn = h[i]
            if sgn != 0: cur += sgn*oo[i]
        if sgn != 0: seg.append(cur)
        wr = (sum(1 for x in seg if x>0)/len(seg)*100) if seg else 0
        fc, fd, fr = bt.metrics(eq); oc, od, orr = bt.metrics(bt.oos_split(eq)[1])
        print(f"  {mode:<12}{fc*100:>9.0f}%{fd*100:>5.0f}%{fr:>6.2f}{nt:>8}{wr:>6.0f}   {oc*100:>8.0f}%{orr:>8.2f}")

    # TP/SL variant (defined risk)
    print("  -- defined-risk (long on close>PDH cross, fixed TP/SL) --")
    pdh = df["pdh"].values; c = df["close"].values
    cross = np.zeros(len(df), bool)
    for i in range(1, len(df)):
        if not np.isnan(pdh[i]) and c[i] > pdh[i] and c[i-1] <= pdh[i-1]:
            cross[i] = True
    for tp, sl in ((0.02, 0.015), (0.03, 0.02), (0.015, 0.01)):
        eq, nt, wr, pf = bt.backtest_tpsl(df, cross, tp=tp, sl=sl)
        fc, fd, fr = bt.metrics(eq); oc, od, orr = bt.metrics(bt.oos_split(eq)[1])
        print(f"  tp{tp*100:.1f}/sl{sl*100:.1f}{'':4}{fc*100:>9.0f}%{fd*100:>5.0f}%{fr:>6.2f}{nt:>8}{wr:>6.0f}   {oc*100:>8.0f}%{orr:>8.2f}")


if __name__ == "__main__":
    main()
