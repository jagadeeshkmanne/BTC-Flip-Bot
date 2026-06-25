#!/usr/bin/env python3
"""backtest_15m_reversal.py — TREND-REVERSAL (counter-trend) on 15m, entry AND exit on the turn.

The user wants to fade extremes on 15m: enter on a reversal signal, exit on the opposite
reversal signal (buy low / sell high). Three standard reversal engines, honestly tested:

  rsi   : LONG when RSI(14) turns up from <30 ; exit (and/or SHORT) when RSI turns down from >70
  bb    : LONG when price tags the lower Bollinger band ; exit at mid/upper band ; short mirror
  zscore: LONG when (close-SMA20)/std < -2 ; exit at mean (z>=0) ; short mirror

Each tested long-only & long/short, with and without an ATR protective stop (a runaway move
against a fade is what kills reversal systems, so the stop matters here).

Honesty: signal CLOSED bar, fill NEXT open, fee 0.055%/side + 0.05% slip, intrabar stop on
real high/low, 60/40 OOS. Data: Binance 5m (cached) -> resampled to 15m.
"""
from __future__ import annotations
import os
import pandas as pd
import numpy as np

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE5 = os.path.join(HERE, "data/cache/BTCUSDT_5m_binance.csv")


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff(); up = d.clip(lower=0); dn = -d.clip(upper=0)
    rs = ema(up, n) / ema(dn, n).replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return ema(tr, n)


def load(tf="15m"):
    df = pd.read_csv(CACHE5, parse_dates=["timestamp"])
    rule = {"15m": "15min", "5m": None, "1h": "1h"}[tf]
    if rule:
        df = df.set_index("timestamp").resample(rule).agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    df["rsi"] = rsi(df["close"], 14)
    mid = df["close"].rolling(20).mean(); sd = df["close"].rolling(20).std(ddof=0)
    df["bb_mid"] = mid; df["bb_up"] = mid + 2 * sd; df["bb_lo"] = mid - 2 * sd
    df["z"] = (df["close"] - mid) / sd.replace(0, 1e-9)
    df["atr"] = atr(df, 14)
    return df


def backtest(df, engine, direction, use_stop, atr_mult=3.0):
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    r = df["rsi"].values; up = df["bb_up"].values; mid = df["bb_mid"].values; lo = df["bb_lo"].values
    z = df["z"].values; a = df["atr"].values
    n = len(df); bal = 1.0; side = 0; entry = 0.0; stop = 0.0
    eq = np.ones(n); trades = []; start = 30

    def ex(px, dirn):
        nonlocal bal, side
        fpx = px * (1 - SLIP_PCT * dirn)
        bal *= ((fpx / entry) if dirn == 1 else ((2 * entry - fpx) / entry)) * (1 - 2 * FEE_PCT)
        trades.append((fpx / entry - 1) if dirn == 1 else (entry - fpx) / entry); side = 0

    def long_entry(i):
        if engine == "rsi": return r[i - 1] < 30 <= r[i]
        if engine == "bb":  return l[i] <= lo[i]
        return z[i] < -2
    def long_exit(i):
        if engine == "rsi": return r[i] >= 70
        if engine == "bb":  return h[i] >= mid[i]
        return z[i] >= 0
    def short_entry(i):
        if engine == "rsi": return r[i - 1] > 70 >= r[i]
        if engine == "bb":  return h[i] >= up[i]
        return z[i] > 2
    def short_exit(i):
        if engine == "rsi": return r[i] <= 30
        if engine == "bb":  return l[i] <= mid[i]
        return z[i] <= 0

    for i in range(start, n - 1):
        oN, hN, lN, cN, aN = o[i + 1], h[i + 1], l[i + 1], c[i + 1], a[i]
        if side == 1:
            if use_stop and lN <= stop: ex(stop, 1)
            elif long_exit(i): ex(oN, 1)
        elif side == -1:
            if use_stop and hN >= stop: ex(stop, -1)
            elif short_exit(i): ex(oN, -1)
        if side == 0:
            if long_entry(i):
                side = 1; entry = oN * (1 + SLIP_PCT); stop = oN - atr_mult * aN
            elif direction == "ls" and short_entry(i):
                side = -1; entry = oN * (1 - SLIP_PCT); stop = oN + atr_mult * aN
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
    for tf in ("15m", "5m"):
        df = load(tf)
        idx = pd.to_datetime(df["timestamp"]); cut_ts = idx.iloc[int(len(df) * 0.6)]
        span = f"{idx.iloc[0]:%Y-%m-%d}->{idx.iloc[-1]:%Y-%m-%d}"
        print("\n" + "=" * 98)
        print(f"BTC {tf} — TREND-REVERSAL (counter-trend), entry+exit on the turn ({span}, OOS {cut_ts:%Y-%m-%d})")
        print("=" * 98)
        print(f"  {'engine':<8}{'dir':<5}{'stop':<6}{'CAGR':>8}{'DD':>6}{'r/DD':>6}{'trades':>8}{'win':>6}   {'oCAGR':>7}{'oDD':>6}{'or/DD':>7}")
        rows = []
        for engine in ("rsi", "bb", "zscore"):
            for direction in ("lf", "ls"):
                for use_stop in (False, True):
                    eq, nt, wr = backtest(df, engine, direction, use_stop)
                    eo = eq[eq.index >= cut_ts]
                    rows.append((engine, direction, "ATR3" if use_stop else "none", metrics(eq), metrics(eo), nt, wr))
        rows.sort(key=lambda x: x[4][2], reverse=True)
        for eng, d, st, m, mo, nt, wr in rows:
            print(f"  {eng:<8}{d:<5}{st:<6}{m[0]*100:>7.0f}%{m[1]*100:>5.0f}%{m[2]:>6.2f}{nt:>8}{wr:>5.0f}%   "
                  f"{mo[0]*100:>6.0f}%{mo[1]*100:>5.0f}%{mo[2]:>7.2f}")
        bh = df["close"] / df["close"].iloc[0]; bh.index = idx
        bo = metrics(bh[bh.index >= cut_ts])
        print(f"  buy & hold OOS CAGR {bo[0]*100:.0f}%  OOS ret/DD {bo[2]:.2f}")


if __name__ == "__main__":
    main()
