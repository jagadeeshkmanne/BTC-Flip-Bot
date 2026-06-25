#!/usr/bin/env python3
"""backtest_breakout_multi.py — Donchian BREAKOUT / BREAKDOWN on 5m / 15m / 1h, honest.

Breakout = momentum continuation (trend-following direction), so unlike reversal it has a
real shot. The enemy on low TF is FAKEOUTS + fees. Tested faithfully:

  entry : close breaks above the prior N-bar HIGH -> long ; below prior N-bar LOW -> short
  exit  : opposite M-bar channel break (turtle-style)  OR  ATR trailing stop
  filter: optional EMA200 regime (only long-breaks above EMA200 / short-breaks below)

Variants: long+short vs long-only; channel N in {20,50,100}; raw vs trend-filtered vs ATR-stop.

Honesty: signal CLOSED bar, fill NEXT open, fee 0.055%/side + 0.05% slip, intrabar stop on
real high/low, 60/40 OOS. Data: Binance 1h (full) for 1h; 5m cache (-> 15m) for 5m/15m.
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return ema(tr, n)


def load(tf):
    if tf == "1h":
        df = pd.read_csv(os.path.join(HERE, "data/cache/BTCUSDT_1h_binance_full.csv"), parse_dates=["timestamp"])
    else:
        df = pd.read_csv(os.path.join(HERE, "data/cache/BTCUSDT_5m_binance.csv"), parse_dates=["timestamp"])
        if tf == "15m":
            df = df.set_index("timestamp").resample("15min").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    df["ema200"] = ema(df["close"], 200)
    df["atr"] = atr(df, 14)
    return df


def backtest(df, N, M, direction, trend_filt, use_stop, atr_mult=3.0):
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    e2 = df["ema200"].values; a = df["atr"].values
    up = df["high"].rolling(N).max().shift(1).values     # prior N-bar high (no lookahead)
    dn = df["low"].rolling(N).min().shift(1).values
    xup = df["high"].rolling(M).max().shift(1).values
    xdn = df["low"].rolling(M).min().shift(1).values
    n = len(df); bal = 1.0; side = 0; entry = 0.0; trail = 0.0
    eq = np.ones(n); trades = []; start = max(N, M, 200) + 2

    def ex(px, dirn):
        nonlocal bal, side
        fpx = px * (1 - SLIP_PCT * dirn)
        bal *= ((fpx / entry) if dirn == 1 else ((2 * entry - fpx) / entry)) * (1 - 2 * FEE_PCT)
        trades.append((fpx / entry - 1) if dirn == 1 else (entry - fpx) / entry); side = 0

    for i in range(start, n - 1):
        oN, hN, lN, cN, aN = o[i + 1], h[i + 1], l[i + 1], c[i + 1], a[i]
        # manage — exits are INTRABAR stops at levels known from bar i (no lookahead)
        if side == 1:
            if use_stop and lN <= trail: ex(trail, 1)
            elif lN <= xdn[i]: ex(min(oN, xdn[i]), 1)        # channel-low stop, hit intrabar
            elif use_stop: trail = max(trail, cN - atr_mult * aN)
        elif side == -1:
            if use_stop and hN >= trail: ex(trail, -1)
            elif hN >= xup[i]: ex(max(oN, xup[i]), -1)       # channel-high stop, hit intrabar
            elif use_stop: trail = min(trail, cN + atr_mult * aN) if trail else cN + atr_mult * aN
        # entries on breakout (signal at bar i: did bar i close beyond prior channel?)
        if side == 0:
            long_ok = c[i] > up[i] and (not trend_filt or c[i] > e2[i])
            short_ok = (direction == "ls") and c[i] < dn[i] and (not trend_filt or c[i] < e2[i])
            if long_ok:
                side = 1; entry = oN * (1 + SLIP_PCT); trail = oN - atr_mult * aN
            elif short_ok:
                side = -1; entry = oN * (1 - SLIP_PCT); trail = oN + atr_mult * aN
        eq[i + 1] = bal if side == 0 else (bal * cN / entry if side == 1 else bal * (2 * entry - cN) / entry)

    s = pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[start:]
    wr = (sum(1 for t in trades if t > 0) / len(trades) * 100) if trades else 0
    return s, len(trades), wr


def metrics(eq):
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    return cagr, dd, (cagr / abs(dd) if dd < -1e-9 else 0.0)


def main():
    for tf in ("1h", "15m", "5m"):
        df = load(tf)
        idx = pd.to_datetime(df["timestamp"]); cut_ts = idx.iloc[int(len(df) * 0.6)]
        span = f"{idx.iloc[0]:%Y-%m-%d}->{idx.iloc[-1]:%Y-%m-%d}"
        print("\n" + "=" * 100)
        print(f"BTC {tf} — Donchian BREAKOUT/BREAKDOWN ({len(df)} bars, {span}, OOS {cut_ts:%Y-%m-%d})")
        print("=" * 100)
        print(f"  {'config':<40}{'CAGR':>8}{'DD':>6}{'r/DD':>6}{'trades':>8}{'win':>6}   {'oCAGR':>7}{'oDD':>6}{'or/DD':>7}")
        configs = [
            ("N20/M10 ls, raw", 20, 10, "ls", False, False),
            ("N20/M10 ls, +EMA200 filter", 20, 10, "ls", True, False),
            ("N20/M10 lf, +EMA200 filter", 20, 10, "lf", True, False),
            ("N50/M20 ls, +EMA200 filter", 50, 20, "ls", True, False),
            ("N50/M20 lf, +EMA200 filter", 50, 20, "lf", True, False),
            ("N100/M50 lf, +EMA200 filter", 100, 50, "lf", True, False),
            ("N50/M20 lf, +filter +ATR3 stop", 50, 20, "lf", True, True),
            ("N20/M10 ls, +ATR3 stop", 20, 10, "ls", False, True),
        ]
        rows = []
        for name, N, M, d, tflt, stop in configs:
            eq, nt, wr = backtest(df, N, M, d, tflt, stop)
            eo = eq[eq.index >= cut_ts]
            rows.append((name, metrics(eq), metrics(eo), nt, wr))
        rows.sort(key=lambda x: x[2][2], reverse=True)
        for name, m, mo, nt, wr in rows:
            print(f"  {name:<40}{m[0]*100:>7.0f}%{m[1]*100:>5.0f}%{m[2]:>6.2f}{nt:>8}{wr:>5.0f}%   "
                  f"{mo[0]*100:>6.0f}%{mo[1]*100:>5.0f}%{mo[2]:>7.2f}")
        bh = df["close"] / df["close"].iloc[0]; bh.index = idx
        bo = metrics(bh[bh.index >= cut_ts])
        print(f"  buy & hold OOS CAGR {bo[0]*100:.0f}%  OOS ret/DD {bo[2]:.2f}")


if __name__ == "__main__":
    main()
