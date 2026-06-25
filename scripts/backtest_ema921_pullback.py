#!/usr/bin/env python3
"""backtest_ema921_pullback.py — 9/21 EMA PULLBACK-continuation (+chop filter +ORB), honest.

NOT the naive cross (already proven to lose). This is the real method from the video:
  - 9/21 EMA cross sets DIRECTION only (bias), never the entry.
  - ENTRY = pullback to the 9 EMA, then a continuation trigger (next bar makes a new
    high in an uptrend / new low in a downtrend) — i.e. trade the flag, not the cross.
  - CHOP FILTER = require EMA separation (|9-21|/price > thr); skip the "poo poo" tangle.
  - ORB (optional) = only trade in the direction of the day's opening-range break.
  - STOP = the pullback swing (low for longs / high for shorts). This is the "proper SL"
    the user kept asking for — a STRUCTURAL stop, not a tight ATR stop that gets churned.

This is the ONE untested angle my notes flagged: trend-CONTINUATION off levels (vs reversion).

Exits tested: fixed R-multiple TP | ATR trailing | opposite EMA cross.
ORB session = UTC day; opening range = first 60 min.

Honesty: signal on CLOSED bar, fill NEXT bar open, fee 0.055%/side + 0.05% slip, SL/TP on
real intrabar high/low (STOP-FIRST), 60/40 OOS. Data: Binance 5m (cached) + 15m resampled.
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


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return ema(tr, n)


def load(tf):
    df = pd.read_csv(CACHE5, parse_dates=["timestamp"])
    if tf in ("15m", "1h", "4h"):
        rule = {"15m": "15min", "1h": "1h", "4h": "4h"}[tf]
        df = df.set_index("timestamp").resample(rule).agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    df["e9"] = ema(df["close"], 9)
    df["e21"] = ema(df["close"], 21)
    df["atr"] = atr(df, 14)
    # opening range (first 60 min of each UTC day)
    day = df["timestamp"].dt.floor("D")
    minute_of_day = (df["timestamp"] - day).dt.total_seconds() / 60
    in_or = minute_of_day < 60
    orh = df["high"].where(in_or).groupby(day).cummax()
    orl = df["low"].where(in_or).groupby(day).cummin()
    df["or_high"] = orh.groupby(day).ffill()
    df["or_low"] = orl.groupby(day).ffill()
    df["after_or"] = ~in_or
    return df


def backtest(df, direction, sep_thr, exit_mode, r_mult=2.0, atr_mult=2.5, use_orb=False):
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    e9 = df["e9"].values; e21 = df["e21"].values; a = df["atr"].values
    orh = df["or_high"].values; orl = df["or_low"].values; aor = df["after_or"].values
    n = len(df)
    bal = 1.0; side = 0; entry = 0.0; stop = 0.0; tp = 0.0; trail = 0.0
    armed_long = armed_short = False; piv_lo = piv_hi = 0.0
    eq = np.ones(n); trades = []
    start = 60

    def exit_pos(px, dirn):
        nonlocal bal, side
        fpx = px * (1 - SLIP_PCT * dirn)
        bal *= ((fpx / entry) if dirn == 1 else ((2 * entry - fpx) / entry)) * (1 - 2 * FEE_PCT)
        trades.append((fpx / entry - 1) if dirn == 1 else (entry - fpx) / entry)
        side = 0

    for i in range(start, n - 1):
        oN, hN, lN, cN, aN = o[i + 1], h[i + 1], l[i + 1], c[i + 1], a[i]
        up = e9[i] > e21[i]
        sep_ok = abs(e9[i] - e21[i]) / c[i] > sep_thr
        orb_long = (not use_orb) or (aor[i] and c[i] > orh[i])
        orb_short = (not use_orb) or (aor[i] and c[i] < orl[i])

        # ── manage open position (STOP-FIRST, then TP / trail / cross) ──
        if side == 1:
            if lN <= stop:
                exit_pos(stop, 1)
            elif exit_mode == "rmult" and hN >= tp:
                exit_pos(tp, 1)
            elif exit_mode == "atr":
                trail = max(trail, cN - atr_mult * aN)
                if lN <= trail:
                    exit_pos(trail, 1)
            elif exit_mode == "cross" and not up:
                exit_pos(oN, 1)
        elif side == -1:
            if hN >= stop:
                exit_pos(stop, -1)
            elif exit_mode == "rmult" and lN <= tp:
                exit_pos(tp, -1)
            elif exit_mode == "atr":
                trail = min(trail, cN + atr_mult * aN) if trail else cN + atr_mult * aN
                if hN >= trail:
                    exit_pos(trail, -1)
            elif exit_mode == "cross" and up:
                exit_pos(oN, -1)

        # ── pullback-continuation entry logic ──
        if side == 0:
            if up and sep_ok:
                armed_short = False
                if l[i] <= e9[i]:                      # tagged the 9 EMA -> arm a long
                    armed_long = True
                    piv_lo = l[i] if not armed_long else min(piv_lo, l[i]) if piv_lo else l[i]
                if armed_long:
                    piv_lo = min(piv_lo, l[i]) if piv_lo else l[i]
                    if h[i] > h[i - 1] and orb_long:   # continuation: new high
                        side = 1; entry = oN * (1 + SLIP_PCT); stop = piv_lo
                        risk = max(entry - stop, 1e-9); tp = entry + r_mult * risk
                        trail = oN - atr_mult * aN; armed_long = False; piv_lo = 0.0
            elif (not up) and sep_ok and direction == "ls":
                armed_long = False
                if h[i] >= e9[i]:                       # tagged the 9 EMA -> arm a short
                    armed_short = True
                    piv_hi = h[i] if not piv_hi else max(piv_hi, h[i])
                if armed_short:
                    piv_hi = max(piv_hi, h[i]) if piv_hi else h[i]
                    if l[i] < l[i - 1] and orb_short:   # continuation: new low
                        side = -1; entry = oN * (1 - SLIP_PCT); stop = piv_hi
                        risk = max(stop - entry, 1e-9); tp = entry - r_mult * risk
                        trail = oN + atr_mult * aN; armed_short = False; piv_hi = 0.0
            else:
                armed_long = armed_short = False

        eq[i + 1] = bal if side == 0 else (bal * cN / entry if side == 1 else bal * (2 * entry - cN) / entry)

    s = pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[start:]
    return s, len(trades), trades


def metrics(eq):
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    return cagr, dd, (cagr / abs(dd) if dd < -1e-9 else 0.0)


def main():
    for tf in ("4h", "1h"):
        df = load(tf)
        idx = pd.to_datetime(df["timestamp"])
        cut_ts = idx.iloc[int(len(df) * 0.6)]
        span = f"{idx.iloc[0]:%Y-%m-%d}->{idx.iloc[-1]:%Y-%m-%d}"
        print("\n" + "=" * 104)
        print(f"BTC {tf} — 9/21 EMA PULLBACK-continuation ({len(df)} bars, {span}, OOS {cut_ts:%Y-%m-%d})")
        print("=" * 104)
        print(f"  {'config':<40}{'CAGR':>8}{'DD':>6}{'r/DD':>6}{'trades':>8}{'win':>6}   {'oCAGR':>7}{'oDD':>6}{'or/DD':>6}")

        configs = [
            ("ls, no-chop-filt, cross exit", "ls", 0.0, "cross", {}),
            ("ls, chop>0.05%, cross exit", "ls", 0.0005, "cross", {}),
            ("ls, chop>0.1%, 2R TP", "ls", 0.001, "rmult", dict(r_mult=2.0)),
            ("ls, chop>0.1%, 3R TP", "ls", 0.001, "rmult", dict(r_mult=3.0)),
            ("ls, chop>0.1%, ATR2.5 trail", "ls", 0.001, "atr", dict(atr_mult=2.5)),
            ("ls, chop>0.1%, 2R TP, +ORB", "ls", 0.001, "rmult", dict(r_mult=2.0, use_orb=True)),
            ("lf, chop>0.1%, 2R TP", "lf", 0.001, "rmult", dict(r_mult=2.0)),
            ("lf, chop>0.1%, ATR2.5 trail", "lf", 0.001, "atr", dict(atr_mult=2.5)),
            ("lf, chop>0.1%, 2R TP, +ORB", "lf", 0.001, "rmult", dict(r_mult=2.0, use_orb=True)),
            ("lf, chop>0.15%, ATR3 trail, +ORB", "lf", 0.0015, "atr", dict(atr_mult=3.0, use_orb=True)),
        ]
        rows = []
        for name, d, sep, ex, kw in configs:
            eq, nt, tr = backtest(df, d, sep, ex, **kw)
            wr = (sum(1 for t in tr if t > 0) / nt * 100) if nt else 0
            eo = eq[eq.index >= cut_ts]
            rows.append((name, metrics(eq), metrics(eo), nt, wr))
        rows.sort(key=lambda x: x[2][2], reverse=True)
        for name, m, mo, nt, wr in rows:
            print(f"  {name:<40}{m[0]*100:>7.0f}%{m[1]*100:>5.0f}%{m[2]:>6.2f}{nt:>8}{wr:>5.0f}%   "
                  f"{mo[0]*100:>6.0f}%{mo[1]*100:>5.0f}%{mo[2]:>6.2f}")
        bh = df["close"] / df["close"].iloc[0]; bh.index = idx
        bo = metrics(bh[bh.index >= cut_ts])
        print(f"  {'buy & hold':<40}{'':>27}   OOS CAGR {bo[0]*100:.0f}%  OOS r/DD {bo[2]:.2f}")


if __name__ == "__main__":
    main()
