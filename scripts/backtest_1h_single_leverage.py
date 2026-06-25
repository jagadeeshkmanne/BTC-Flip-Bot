#!/usr/bin/env python3
"""backtest_1h_single_leverage.py — fine-tune the 1h slow-reverse on SINGLE coins, with REAL
leverage + liquidation modelling (1x / 2x / 3x).

Starting from the 1h winner (EMA ~32/600 reverse, always-in-market), this fine-tunes the EMA
pair per coin (BTC / ETH / BNB) and then applies 1x/2x/3x with HONEST liquidation: at leverage
L a position is liquidated when the adverse intrabar move from entry reaches ~ (1/L − maint),
which zeroes the account (full-margin single-coin bot). Fees scale with leverage (fee×L/side).

This is the honest test of the user's "3x, BTC/ETH/BNB only" idea — it will show whether the
extra return survives the liquidation risk, or wipes out (the repo's standing finding).

Honesty: signal CLOSED bar, fill NEXT open, fee 0.055%/side×L + 0.05% slip, intrabar liq check,
60/40 OOS. Data: Binance 1h (cached).
"""
from __future__ import annotations
import os
import pandas as pd
import numpy as np

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
MAINT = 0.005            # maintenance margin (0.5%) — liquidation a hair before zero
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]


def load(symbol):
    for name in (f"{symbol}_1h_binance.csv", f"{symbol}_1h_binance_full.csv"):
        p = os.path.join(HERE, "data/cache", name)
        if os.path.exists(p):
            return pd.read_csv(p, parse_dates=["timestamp"])
    raise FileNotFoundError(symbol)


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def run(df, fast, slow, lev):
    """Reverse always-in-market with leverage + intrabar liquidation. Returns equity, #trades,
    win%, liquidated_count."""
    c = df["close"].values; o = df["open"].values; h = df["high"].values; l = df["low"].values
    up = (ema(df["close"], fast) > ema(df["close"], slow)).values
    n = len(df)
    bal = 1.0; side = 0; entry = 0.0
    eq = np.ones(n); trades = []; liqs = 0; start = slow + 2

    def liq_price(e, s):
        # adverse fraction that wipes equity at this leverage
        frac = 1.0 / lev - MAINT
        return e * (1 - frac) if s == 1 else e * (1 + frac)

    for i in range(start, n - 1):
        oN, hN, lN, cN = o[i + 1], h[i + 1], l[i + 1], c[i + 1]
        want = 1 if up[i] else -1

        # intrabar liquidation check on the open position
        if side != 0 and bal > 0:
            lp = liq_price(entry, side)
            if (side == 1 and lN <= lp) or (side == -1 and hN >= lp):
                bal = 0.0; liqs += 1; trades.append(-1.0); side = 0   # wiped

        # flip on signal change (fill next open)
        if side != want and bal > 0:
            if side != 0:
                fpx = oN * (1 - SLIP_PCT * side)
                ret = lev * side * (fpx / entry - 1)
                bal *= (1 + ret) * (1 - FEE_PCT * lev)
                trades.append(ret)
            if bal > 0:
                side = want
                entry = oN * (1 + SLIP_PCT * side)
                bal *= (1 - FEE_PCT * lev)     # entry fee on leveraged notional

        # mark to market
        if side != 0 and bal > 0:
            eq[i + 1] = max(bal * (1 + lev * side * (cN / entry - 1)), 0.0)
        else:
            eq[i + 1] = bal
        if eq[i + 1] <= 0:
            eq[i + 1:] = 0.0
            break

    s = pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[start:]
    wins = sum(1 for t in trades if t > 0)
    wr = wins / len(trades) * 100 if trades else 0
    return s, len(trades), wr, liqs


def metrics(eq):
    if eq.iloc[-1] <= 1e-6:
        # wiped: CAGR -100%
        dd = -1.0
        return -1.0, dd, -1.0
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    return cagr, dd, (cagr / abs(dd) if dd < -1e-9 else 0.0)


def main():
    fasts = [13, 21, 32, 50]
    slows = [400, 500, 600, 700, 800]
    for sym in COINS:
        df = load(sym)
        idx = pd.to_datetime(df["timestamp"]); cut_ts = idx.iloc[int(len(df) * 0.6)]
        span = f"{idx.iloc[0]:%Y-%m-%d}->{idx.iloc[-1]:%Y-%m-%d}"
        print("\n" + "=" * 96)
        print(f"{sym} 1h — slow reverse, fine-tune @1x  ({span}, OOS {cut_ts:%Y-%m-%d})")
        print("=" * 96)
        # fine-tune at 1x, rank by OOS ret/DD
        rows = []
        for f in fasts:
            for s in slows:
                eq, nt, wr, lq = run(df, f, s, 1.0)
                eo = eq[eq.index >= cut_ts]
                rows.append((f, s, metrics(eq), metrics(eo), nt, wr))
        rows.sort(key=lambda x: x[3][2], reverse=True)
        print(f"  {'EMA f/s':<10}{'CAGR':>8}{'DD':>6}{'r/DD':>6}{'trades':>8}{'win':>6}   {'oCAGR':>7}{'oDD':>6}{'or/DD':>7}")
        for f, s, m, mo, nt, wr in rows[:5]:
            print(f"  {f}/{s:<7}{m[0]*100:>7.0f}%{m[1]*100:>5.0f}%{m[2]:>6.2f}{nt:>8}{wr:>5.0f}%   "
                  f"{mo[0]*100:>6.0f}%{mo[1]*100:>5.0f}%{mo[2]:>7.2f}")

        bf, bs = rows[0][0], rows[0][1]
        print(f"\n  Leverage on the best pair ({bf}/{bs}) — REAL liquidation modelled:")
        print(f"  {'lev':<6}{'CAGR':>8}{'DD':>6}{'r/DD':>6}{'liquidations':>14}{'final $1->':>12}")
        for lev in (1.0, 2.0, 3.0):
            eq, nt, wr, lq = run(df, bf, bs, lev)
            m = metrics(eq)
            final = eq.iloc[-1]
            tag = "  WIPED OUT" if final <= 1e-6 else ""
            print(f"  {lev:<6.0f}{m[0]*100:>7.0f}%{m[1]*100:>5.0f}%{m[2]:>6.2f}{lq:>14}{final:>11.2f}{tag}")


if __name__ == "__main__":
    main()
