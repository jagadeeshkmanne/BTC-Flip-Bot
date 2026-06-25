#!/usr/bin/env python3
"""backtest_5m_trend_rsi.py — SINGLE-timeframe 5m/15m trend + RSI (the user's exact idea).

Hypothesis to test: v2.2 lost because it mixed timeframes; a SAME-timeframe system
(5m trend filter + 5m RSI execution, no cross-TF mismatch) should do better.

Spec (same TF for everything):
  trend  : close > EMA200  -> uptrend (long-only zone) ; close < EMA200 -> downtrend
  entry  : LONG when RSI(14) crosses UP through `rsi_lo` while in uptrend (buy the dip);
           SHORT (if enabled) when RSI crosses DOWN through `rsi_hi` while in downtrend.
  exit   : RSI crosses to the opposite band (TP) OR trend flips OR ATR stop.
  gap    : optional filter — only act when |open-prevclose|/prevclose > gap_thr (gap play).
           (NOTE: BTC is 24/7 — real gaps are rare; included only to honour the request.)

Honesty: signal on CLOSED bar, fill NEXT open, fee 0.055%/side + 0.05% slip, intrabar ATR
stop, 60/40 OOS. Data: Binance 5m (cached) + 15m resampled.
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


def load(tf):
    df = pd.read_csv(CACHE5, parse_dates=["timestamp"])
    if tf != "5m":
        rule = {"15m": "15min", "1h": "1h"}[tf]
        df = df.set_index("timestamp").resample(rule).agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    df["e200"] = ema(df["close"], 200)
    df["rsi"] = rsi(df["close"], 14)
    df["atr"] = atr(df, 14)
    df["gap"] = (df["open"] - df["close"].shift(1)).abs() / df["close"].shift(1)
    return df


def backtest(df, direction, rsi_lo, rsi_hi, exit_mode, atr_mult=3.0, gap_thr=0.0):
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    e2 = df["e200"].values; r = df["rsi"].values; a = df["atr"].values; gp = df["gap"].values
    n = len(df)
    bal = 1.0; side = 0; entry = 0.0; trail = 0.0
    eq = np.ones(n); trades = []

    def exit_pos(px, dirn):
        nonlocal bal, side
        fpx = px * (1 - SLIP_PCT * dirn)
        bal *= ((fpx / entry) if dirn == 1 else ((2 * entry - fpx) / entry)) * (1 - 2 * FEE_PCT)
        trades.append((fpx / entry - 1) if dirn == 1 else (entry - fpx) / entry); side = 0

    for i in range(210, n - 1):
        oN, hN, lN, cN, aN = o[i + 1], h[i + 1], l[i + 1], c[i + 1], a[i]
        up = c[i] > e2[i]
        gap_ok = (gap_thr == 0.0) or (gp[i] > gap_thr)

        if side == 1:
            if exit_mode == "atr":
                trail = max(trail, cN - atr_mult * aN)
                if lN <= trail: exit_pos(trail, 1)
            if side == 1 and (r[i] >= rsi_hi or not up):    # RSI TP or trend flip
                exit_pos(oN, 1)
        elif side == -1:
            if exit_mode == "atr":
                trail = min(trail, cN + atr_mult * aN) if trail else cN + atr_mult * aN
                if hN >= trail: exit_pos(trail, -1)
            if side == -1 and (r[i] <= rsi_lo or up):
                exit_pos(oN, -1)

        if side == 0 and gap_ok:
            if up and r[i - 1] < rsi_lo <= r[i]:
                side = 1; entry = oN * (1 + SLIP_PCT); trail = oN - atr_mult * aN
            elif (not up) and direction == "ls" and r[i - 1] > rsi_hi >= r[i]:
                side = -1; entry = oN * (1 - SLIP_PCT); trail = oN + atr_mult * aN

        eq[i + 1] = bal if side == 0 else (bal * cN / entry if side == 1 else bal * (2 * entry - cN) / entry)

    s = pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[210:]
    return s, len(trades), trades


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
        print("\n" + "=" * 100)
        print(f"BTC {tf} — SAME-TF trend(EMA200) + RSI ({len(df)} bars, {span}, OOS {cut_ts:%Y-%m-%d})")
        print("=" * 100)
        print(f"  {'config':<38}{'CAGR':>8}{'DD':>6}{'r/DD':>6}{'trades':>8}{'win':>6}   {'oCAGR':>7}{'oDD':>6}{'or/DD':>6}")
        configs = [
            ("lf RSI30/70, RSI-exit", "lf", 30, 70, "rsi", {}),
            ("lf RSI35/65, RSI-exit", "lf", 35, 65, "rsi", {}),
            ("lf RSI30/70, ATR3 stop", "lf", 30, 70, "atr", dict(atr_mult=3.0)),
            ("lf RSI25/75, ATR3 stop", "lf", 25, 75, "atr", dict(atr_mult=3.0)),
            ("lf RSI30/70 +gap>0.1%", "lf", 30, 70, "rsi", dict(gap_thr=0.001)),
            ("ls RSI30/70, RSI-exit", "ls", 30, 70, "rsi", {}),
            ("ls RSI35/65, RSI-exit", "ls", 35, 65, "rsi", {}),
            ("ls RSI30/70, ATR3 stop", "ls", 30, 70, "atr", dict(atr_mult=3.0)),
        ]
        rows = []
        for name, d, lo, hi, ex, kw in configs:
            eq, nt, tr = backtest(df, d, lo, hi, ex, **kw)
            wr = (sum(1 for t in tr if t > 0) / nt * 100) if nt else 0
            eo = eq[eq.index >= cut_ts]
            rows.append((name, metrics(eq), metrics(eo), nt, wr))
        rows.sort(key=lambda x: x[2][2], reverse=True)
        for name, m, mo, nt, wr in rows:
            print(f"  {name:<38}{m[0]*100:>7.0f}%{m[1]*100:>5.0f}%{m[2]:>6.2f}{nt:>8}{wr:>5.0f}%   "
                  f"{mo[0]*100:>6.0f}%{mo[1]*100:>5.0f}%{mo[2]:>6.2f}")
        bh = df["close"] / df["close"].iloc[0]; bh.index = idx
        bo = metrics(bh[bh.index >= cut_ts])
        print(f"  {'buy & hold':<38}{'':>27}   OOS CAGR {bo[0]*100:.0f}%  OOS r/DD {bo[2]:.2f}")


if __name__ == "__main__":
    main()
