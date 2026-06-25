#!/usr/bin/env python3
"""backtest_one_trade_per_day.py — ONE sniper trade per day: best setup, TP, then done.

The "$200/day sniper" idea done honestly: each UTC day take AT MOST one trade — the first
trend-aligned setup — with a take-profit and a stop; once it closes (win or lose) STOP for the
day. Selectivity instead of churn.

Daily bias: close > daily EMA50 -> long-only day ; below -> short-only day (trend with you).
Setups (intraday 1h):
  pullback : enter when price reclaims the EMA20 after dipping to it (continuation)
  breakout : enter on the first 1h close beyond the PRIOR DAY's high (long) / low (short)
  openmom  : enter at the day's first bar in the trend direction (always takes the trade)
Exit: fixed take-profit / stop (R-defined), intrabar, stop-first. No second entry that day.

Honest: signal CLOSED bar, fill NEXT open, fee 0.055%/side + 0.05% slip, TP/SL on real
intrabar high/low. Reports CAGR, DD, win%, avg win/loss, profit factor, trades/yr. Data:
Binance 1h (cached).
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def load():
    df = pd.read_csv(os.path.join(HERE, "data/cache/BTCUSDT_1h_binance_full.csv"), parse_dates=["timestamp"])
    df["e20"] = ema(df["close"], 20)
    dly = df.set_index("timestamp")["close"].resample("1D").last()
    dly_bull = (dly > ema(dly, 50)).shift(1)               # prior completed day only (no lookahead)
    df["bull"] = dly_bull.reindex(df["timestamp"], method="ffill").fillna(False).values
    df["day"] = df["timestamp"].dt.floor("D")
    pdh = df.groupby("day")["high"].max().shift(1)         # prior day's high/low
    pdl = df.groupby("day")["low"].min().shift(1)
    df["pdh"] = df["day"].map(pdh); df["pdl"] = df["day"].map(pdl)
    return df


def backtest(df, setup, direction, tp, sl):
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    e20 = df["e20"].values; bull = df["bull"].values; pdh = df["pdh"].values; pdl = df["pdl"].values
    day = df["day"].values
    n = len(df); bal = 1.0; side = 0; entry = tp_px = sl_px = 0.0
    eq = np.ones(n); trades = []; traded_day = None

    def ex(px, dirn):
        nonlocal bal, side
        fpx = px * (1 - SLIP_PCT * dirn)
        r = (fpx / entry - 1) if dirn == 1 else (entry - fpx) / entry
        bal *= (1 + r) * (1 - 2 * FEE_PCT); trades.append(r); side = 0

    for i in range(60, n - 1):
        if day[i] != traded_day:
            new_day = day[i]
        oN, hN, lN = o[i + 1], h[i + 1], l[i + 1]
        # manage
        if side == 1:
            if lN <= sl_px: ex(sl_px, 1)
            elif hN >= tp_px: ex(tp_px, 1)
        elif side == -1:
            if hN >= sl_px: ex(sl_px, -1)
            elif lN <= tp_px: ex(tp_px, -1)
        # entry (one per day)
        if side == 0 and day[i] != traded_day:
            longbias = bull[i]; want = 0
            if setup == "pullback":
                if longbias and l[i] <= e20[i] < c[i]: want = 1
                elif (not longbias) and h[i] >= e20[i] > c[i] and direction == "ls": want = -1
            elif setup == "breakout":
                if longbias and c[i] > pdh[i]: want = 1
                elif (not longbias) and c[i] < pdl[i] and direction == "ls": want = -1
            elif setup == "openmom":
                if day[i] != day[i - 1]:
                    want = 1 if longbias else (-1 if direction == "ls" else 0)
            if want != 0:
                side = want; entry = oN * (1 + SLIP_PCT * want)
                tp_px = entry * (1 + tp * want); sl_px = entry * (1 - sl * want)
                traded_day = day[i]
        eq[i + 1] = bal
    eqs = pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[60:]
    tr = np.array(trades)
    wins = tr[tr > 0]; losses = tr[tr <= 0]
    wr = len(wins) / len(tr) * 100 if len(tr) else 0
    pf = (wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else float("inf")
    return eqs, len(tr), wr, (wins.mean() * 100 if len(wins) else 0), (losses.mean() * 100 if len(losses) else 0), pf


def metrics(eq):
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    return cagr, dd, (cagr / abs(dd) if dd < -1e-9 else 0.0), yrs


def main():
    df = load()
    idx = pd.to_datetime(df["timestamp"]); cut = idx.iloc[int(len(df) * 0.6)]
    span = f"{idx.iloc[0].date()}->{idx.iloc[-1].date()}"
    print("=" * 104)
    print(f"BTC — ONE sniper trade per day (best setup, TP, then done)  ({span}, OOS {cut.date()})")
    print("=" * 104)
    print(f"  {'setup / dir / TP-SL':<34}{'CAGR':>7}{'DD':>6}{'r/DD':>6}{'trades':>7}{'tr/yr':>6}{'win%':>6}{'avgW':>6}{'avgL':>6}{'PF':>5}   {'oCAGR':>6}{'or/DD':>6}")
    cfgs = []
    for setup in ("pullback", "breakout", "openmom"):
        for direction in ("lf", "ls"):
            for tp, sl in ((0.010, 0.007), (0.015, 0.010), (0.02, 0.012)):
                cfgs.append((setup, direction, tp, sl))
    rows = []
    for setup, d, tp, sl in cfgs:
        eq, nt, wr, aw, al, pf = backtest(df, setup, d, tp, sl)
        if nt < 20: continue
        m = metrics(eq); mo = metrics(eq[eq.index >= cut])
        rows.append((f"{setup} {d} {tp*100:.1f}/{sl*100:.1f}", m, mo, nt, nt/m[3], wr, aw, al, pf))
    rows.sort(key=lambda x: x[2][2], reverse=True)
    for name, m, mo, nt, tpy, wr, aw, al, pf in rows:
        print(f"  {name:<34}{m[0]*100:>6.0f}%{m[1]*100:>5.0f}%{m[2]:>6.2f}{nt:>7d}{tpy:>6.0f}{wr:>6.0f}{aw:>6.2f}{al:>6.2f}{pf:>5.2f}   "
              f"{mo[0]*100:>5.0f}%{mo[2]:>6.2f}")
    bh = (df["close"] / df["close"].iloc[0]); bh.index = idx
    bo = metrics(bh[bh.index >= cut])
    print(f"  {'buy & hold':<34}{'':>52}   {bo[0]*100:>5.0f}%{bo[2]:>6.2f}")


if __name__ == "__main__":
    main()
