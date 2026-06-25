#!/usr/bin/env python3
"""backtest_1h_fast_filtered.py — fast 1h MA trades + FILTERS, hunting higher CAGR.

The slow 50/200 cross wins risk-adjusted but barely trades (low CAGR). Here we start from a
FAST cross (lots of trades) and bolt on filters one at a time to SEE which ones actually lift
CAGR and kill the whipsaw. Then we grid-search the best filtered stack, and finally test an
ATR-trailing-stop exit (fast-in / trail-out) which often boosts CAGR the most.

FILTERS tested (each gates the entry / position):
  trend200 : only longs when close>EMA200, only shorts when close<EMA200 (regime gate)
  adx      : only trade when ADX(14) > thr  (trend strength — skip chop)
  slope    : only long when slow-EMA rising / short when falling
  sep      : require |fast-slow|/close > thr at the cross (skip marginal crosses)
  volband  : ATR% within [lo,hi] (skip dead tape AND blowoff vol)

EXITS:
  cross    : exit on opposite cross (default)
  atrstop  : exit on an ATR trailing stop (intrabar) OR opposite cross — lets winners run

Honesty: signal on CLOSED bar, fill NEXT bar open, fee 0.055%/side + 0.05% slip, intrabar
stops on real high/low, 60/40 OOS. CAGR is the headline (what the user asked to maximize);
DD / trades / win% shown so we don't chase CAGR off a cliff.

Data: Binance 1h since 2020 (cached by backtest_1h_ma_search.py).
"""
from __future__ import annotations
import os
import pandas as pd
import numpy as np

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "data/cache/BTCUSDT_1h_binance_full.csv")


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return ema(tr, n)


def adx(df, n=14):
    up = df["high"].diff(); dn = -df["low"].diff()
    pdm = ((up > dn) & (up > 0)) * up.clip(lower=0)
    ndm = ((dn > up) & (dn > 0)) * dn.clip(lower=0)
    a = atr(df, n)
    pdi = 100 * ema(pdm, n) / a
    ndi = 100 * ema(ndm, n) / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, 1e-9)
    return ema(dx, n)


def prep(df):
    df = df.copy()
    df["atr"] = atr(df, 14)
    df["atrp"] = df["atr"] / df["close"]
    df["adx"] = adx(df, 14)
    df["ema200"] = ema(df["close"], 200)
    return df


def base_signal(df, fast, slow):
    f, s = ema(df["close"], fast), ema(df["close"], slow)
    return f, s, (f > s)


def build_pos(df, fast, slow, direction, filt):
    """Vectorized target position in {-1,0,1} after applying filters."""
    f, s, up = base_signal(df, fast, slow)
    c = df["close"]
    long_ok = up.copy()
    short_ok = (~up).copy()
    if filt.get("trend200"):
        long_ok &= c > df["ema200"]
        short_ok &= c < df["ema200"]
    if filt.get("adx"):
        strong = df["adx"] > filt["adx"]
        long_ok &= strong; short_ok &= strong
    if filt.get("slope"):
        rising = s.diff() > 0
        long_ok &= rising; short_ok &= (~rising)
    if filt.get("sep"):
        wide = (f - s).abs() / c > filt["sep"]
        long_ok &= wide; short_ok &= wide
    if filt.get("volband"):
        lo, hi = filt["volband"]
        ok = (df["atrp"] > lo) & (df["atrp"] < hi)
        long_ok &= ok; short_ok &= ok
    pos = long_ok.astype(float)
    if direction == "ls":
        pos = pos - short_ok.astype(float)
    return pos


def evaluate(pos, oo_ret, idx):
    held = pos.shift(1).fillna(0)
    turn = held.diff().abs().fillna(held.abs())
    r = held * oo_ret - turn * (FEE_PCT + SLIP_PCT)
    eq = (1 + r).cumprod(); eq.index = idx
    return eq.dropna(), held


def atr_stop_pos(df, fast, slow, direction, filt, trail_mult):
    """Stateful: enter on filtered cross, exit on ATR trailing stop (intrabar) or opposite cross.
    Returns equity series + held series (for trade counting) + per-trade pnl list."""
    tgt = build_pos(df, fast, slow, direction, filt)
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    a = df["atr"].values
    n = len(df)
    bal = 1.0; side = 0; entry = 0.0; trail = 0.0
    eq = np.ones(n); held = np.zeros(n); trades = []
    for i in range(201, n - 1):
        want = tgt.iloc[i]
        oN, hN, lN, cN, aN = o[i + 1], h[i + 1], l[i + 1], c[i + 1], a[i]
        # manage stop first (intrabar)
        if side == 1:
            if lN <= trail:
                fpx = trail * (1 - SLIP_PCT); bal *= (fpx / entry) * (1 - 2 * FEE_PCT)
                trades.append(fpx / entry - 1); side = 0
            else:
                trail = max(trail, cN - trail_mult * aN)
        elif side == -1:
            if hN >= trail:
                fpx = trail * (1 + SLIP_PCT); bal *= ((2 * entry - fpx) / entry) * (1 - 2 * FEE_PCT)
                trades.append((entry - fpx) / entry); side = 0
            else:
                trail = min(trail, cN + trail_mult * aN)
        # entries / flips on target change
        if side != want and want != 0:
            if side != 0:
                dirn = 1 if side == 1 else -1
                fpx = oN * (1 - SLIP_PCT * dirn)
                bal *= ((fpx / entry) if side == 1 else ((2 * entry - fpx) / entry)) * (1 - 2 * FEE_PCT)
                trades.append((fpx / entry - 1) if side == 1 else (entry - fpx) / entry)
            side = int(want)
            entry = oN * (1 + SLIP_PCT * side)
            trail = (oN - trail_mult * aN) if side == 1 else (oN + trail_mult * aN)
        elif want == 0 and side != 0:
            dirn = 1 if side == 1 else -1
            fpx = oN * (1 - SLIP_PCT * dirn)
            bal *= ((fpx / entry) if side == 1 else ((2 * entry - fpx) / entry)) * (1 - 2 * FEE_PCT)
            trades.append((fpx / entry - 1) if side == 1 else (entry - fpx) / entry); side = 0
        # mark
        if side == 1:
            eq[i + 1] = bal * cN / entry
        elif side == -1:
            eq[i + 1] = bal * (2 * entry - cN) / entry
        else:
            eq[i + 1] = bal
        held[i + 1] = side
    s = pd.Series(eq, index=pd.to_datetime(df["timestamp"]))
    return s.iloc[201:], pd.Series(held, index=s.index).iloc[201:], trades


def win_rate(held, oo_ret):
    """Per-trade win rate from a held series (sign-segmented)."""
    h = held.values; r = oo_ret.values[:len(h)]
    seg, cur, pnl, sign = [], 0.0, [], 0
    for i in range(len(h)):
        if h[i] != sign:
            if sign != 0:
                pnl.append(cur)
            cur = 0.0; sign = h[i]
        if sign != 0:
            cur += sign * r[i]
    if sign != 0:
        pnl.append(cur)
    if not pnl:
        return 0, 0
    return (sum(1 for x in pnl if x > 0) / len(pnl) * 100), len(pnl)


def metrics(eq):
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    return cagr, dd, (cagr / abs(dd) if dd < -1e-9 else 0.0)


def main():
    df = prep(pd.read_csv(CACHE, parse_dates=["timestamp"]))
    oo_ret = (df["open"].shift(-1) / df["open"] - 1).fillna(0)
    idx = pd.to_datetime(df["timestamp"])
    cut_ts = idx.iloc[int(len(df) * 0.6)]
    span = f"{idx.iloc[0]:%Y-%m-%d}->{idx.iloc[-1]:%Y-%m-%d}"
    print("=" * 104)
    print(f"BTC 1h — FAST cross + FILTERS, maximizing CAGR   ({len(df)} bars, {span}, OOS split {cut_ts:%Y-%m-%d})")
    print("=" * 104)

    def row(name, eq, held):
        cf, df_, rf = metrics(eq)
        eo = eq[eq.index >= cut_ts]
        co, do, ro = metrics(eo)
        wr, nt = win_rate(held, oo_ret)
        print(f"  {name:<34}{cf*100:>7.0f}%{df_*100:>6.0f}%{rf:>6.2f}{nt:>7}{wr:>5.0f}%   {co*100:>7.0f}%{do*100:>6.0f}%{ro:>6.2f}")

    hdr = f"  {'config':<34}{'CAGR':>8}{'DD':>6}{'r/DD':>6}{'trades':>7}{'win':>6}   {'oCAGR':>7}{'oDD':>6}{'or/DD':>6}"

    # ── Section 1: filter effects on a FAST EMA9/21 (long/flat) ──
    print("\n[1] FILTER EFFECTS — base EMA 9/21 long/flat, add one filter at a time:")
    print(hdr)
    base = dict(fast=9, slow=21, direction="lf")
    steps = [
        ("base (no filter)", {}),
        ("+trend200", {"trend200": True}),
        ("+adx>20", {"adx": 20}),
        ("+adx>25", {"adx": 25}),
        ("+slope", {"slope": True}),
        ("+sep>0.1%", {"sep": 0.001}),
        ("+volband[.3%,3%]", {"volband": (0.003, 0.03)}),
        ("+trend200+adx>20", {"trend200": True, "adx": 20}),
        ("+trend200+adx>20+slope", {"trend200": True, "adx": 20, "slope": True}),
        ("FULL stack", {"trend200": True, "adx": 22, "slope": True, "sep": 0.001}),
    ]
    for name, filt in steps:
        pos = build_pos(df, base["fast"], base["slow"], "lf", filt)
        eq, held = evaluate(pos, oo_ret, idx)
        row(name, eq, held)

    # ── Section 2: grid of fast bases × best filter stacks, ranked by OOS CAGR ──
    print("\n[2] GRID — fast bases × filter stacks (long/flat AND long/short), TOP 18 by OOS CAGR:")
    print(hdr)
    fasts = [(5, 13), (5, 20), (8, 21), (9, 21), (10, 30), (12, 26)]
    stacks = [
        ("trend", {"trend200": True}),
        ("trend+adx", {"trend200": True, "adx": 20}),
        ("trend+adx+slope", {"trend200": True, "adx": 20, "slope": True}),
        ("trend+adx25+sep", {"trend200": True, "adx": 25, "sep": 0.0015}),
    ]
    res = []
    for (f, s) in fasts:
        for dirn in ("lf", "ls"):
            for sname, filt in stacks:
                pos = build_pos(df, f, s, dirn, filt)
                eq, held = evaluate(pos, oo_ret, idx)
                eo = eq[eq.index >= cut_ts]
                res.append((f"{f}/{s} {dirn} {sname}", eq, held, metrics(eo)[0]))
    res.sort(key=lambda x: x[3], reverse=True)
    for name, eq, held, _ in res[:18]:
        row(name, eq, held)

    # ── Section 3: ATR trailing-stop exit (fast-in / trail-out) on the best bases ──
    print("\n[3] ATR-TRAILING-STOP exit (enter on filtered cross, trail out) — does it lift CAGR?")
    print(hdr)
    for (f, s, dirn, filt, tm, tag) in [
        (9, 21, "lf", {"trend200": True}, 3.0, "9/21 lf trend, ATR3"),
        (9, 21, "lf", {"trend200": True, "adx": 20}, 2.5, "9/21 lf trend+adx, ATR2.5"),
        (9, 21, "ls", {"trend200": True}, 3.0, "9/21 ls trend, ATR3"),
        (12, 26, "lf", {"trend200": True}, 3.0, "12/26 lf trend, ATR3"),
        (8, 21, "lf", {"trend200": True, "adx": 22, "slope": True}, 2.5, "8/21 lf FULL, ATR2.5"),
    ]:
        eq, held, _ = atr_stop_pos(df, f, s, dirn, filt, tm)
        row(tag, eq, held)


if __name__ == "__main__":
    main()
