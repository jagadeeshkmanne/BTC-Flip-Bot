#!/usr/bin/env python3
"""backtest_daily_entry.py — "how do we always enter a trade every day?" answered from scratch.

Compares, on BTC daily bars:
  HOLD (always-in-market)  : trend reverse (EMA fast>slow long / else short); flip only on a
                             signal change. You hold a position EVERY day -> already "in a
                             trade every day" -- but you don't churn.
  DAILY-CHURN              : same signal, but EXIT at each day's close and RE-ENTER next open.
                             Forces a fresh entry every single day (~365 trades/yr).
  DAILY LONG-ONLY          : long every up-trend day, flat on down days (in a trade on up days).
  PRIOR-DAY BREAKOUT       : enter long if today closes above yesterday's high (in uptrend),
                             short if below yesterday's low; exit next day. Naturally triggers
                             most days.

Point: on a 24/7 asset, HOLD ~= DAILY-CHURN minus fees -> forcing a daily re-entry is a tax.
Honest: signal on CLOSED daily bar, fill NEXT open (open-to-open), fee 0.055%/side + 0.05%
slip on turnover, 60/40 OOS. Data: Binance 1h (cached) -> daily.
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_daily():
    df = pd.read_csv(os.path.join(HERE, "data/cache/BTCUSDT_1h_binance_full.csv"), parse_dates=["timestamp"])
    d = df.set_index("timestamp").resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    return d


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def pnl_from_positions(df, pos):
    """pos = target position decided at close[t], entered at open[t+1]. Open-to-open returns."""
    held = pos.shift(1).fillna(0)
    oo = (df["open"].shift(-1) / df["open"] - 1).fillna(0)
    turn = held.diff().abs().fillna(held.abs())
    r = held * oo - turn * (FEE_PCT + SLIP_PCT)
    eq = (1 + r).cumprod(); eq.index = pd.to_datetime(df["timestamp"])
    in_mkt = (held != 0).mean() * 100
    trades = int((held.diff().abs().fillna(held.abs()) > 0).sum())
    return eq, in_mkt, trades


def metrics(eq):
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    return cagr, dd, (cagr / abs(dd) if dd < -1e-9 else 0.0)


def main():
    d = load_daily()
    f, s = 10, 30
    up = (ema(d["close"], f) > ema(d["close"], s))
    idx = pd.to_datetime(d["timestamp"]); cut = idx.iloc[int(len(d) * 0.6)]
    span = f"{idx.iloc[0].date()}->{idx.iloc[-1].date()}"
    print("=" * 96)
    print(f"BTC DAILY — 'always in a trade every day?'  ({len(d)} days, {span}, OOS {cut.date()})")
    print("=" * 96)
    print(f"  {'strategy':<26}{'CAGR':>8}{'DD':>6}{'r/DD':>6}{'trades':>8}{'%days in mkt':>13}   {'oCAGR':>7}{'or/DD':>7}")

    strategies = {}
    # HOLD always-in-market reverse
    strategies["HOLD (always in mkt)"] = (up.astype(float) * 2 - 1)
    # DAILY CHURN: same direction but flat each close then re-enter -> emulate by inserting a
    # forced 1-bar flat is not how it works on continuous data; the honest churn cost is the
    # round-trip fee charged EVERY day. Model: position same as hold, but turnover forced =1/day.
    # We implement by toggling sign->0->sign each day is wrong; instead charge daily round-trip:
    churn_pos = (up.astype(float) * 2 - 1)
    # LONG-ONLY (in a trade only on up days)
    strategies["DAILY long-only"] = up.astype(float)
    # PRIOR-DAY BREAKOUT
    pdh = d["high"].shift(1); pdl = d["low"].shift(1)
    bo = np.where((d["close"] > pdh) & up, 1.0, np.where((d["close"] < pdl) & ~up, -1.0, np.nan))
    strategies["prior-day breakout"] = pd.Series(bo).ffill().fillna(0)

    rows = []
    for name, pos in strategies.items():
        eq, inm, tr = pnl_from_positions(d, pos)
        m = metrics(eq); mo = metrics(eq[eq.index >= cut])
        rows.append((name, m, mo, tr, inm))

    # DAILY-CHURN handled specially: HOLD returns minus a forced daily round-trip fee
    held = churn_pos.shift(1).fillna(0)
    oo = (d["open"].shift(-1) / d["open"] - 1).fillna(0)
    churn_r = held * oo - (np.abs(held) > 0).astype(float) * (2 * (FEE_PCT + SLIP_PCT))  # full round-trip EVERY day
    eqc = (1 + churn_r).cumprod(); eqc.index = idx
    mc = metrics(eqc); moc = metrics(eqc[eqc.index >= cut])
    rows.insert(1, ("DAILY-CHURN (re-enter)", mc, moc, int((held != 0).sum()), (held != 0).mean() * 100))

    for name, m, mo, tr, inm in rows:
        print(f"  {name:<26}{m[0]*100:>7.0f}%{m[1]*100:>5.0f}%{m[2]:>6.2f}{tr:>8d}{inm:>12.0f}%   "
              f"{mo[0]*100:>6.0f}%{mo[2]:>7.2f}")

    bh = (d["close"] / d["close"].iloc[0]); bh.index = idx
    bo_ = metrics(bh[bh.index >= cut])
    print(f"  {'buy & hold':<26}{'':>35}   OOS CAGR {bo_[0]*100:.0f}%  OOS r/DD {bo_[2]:.2f}")
    print("\n  HOLD vs DAILY-CHURN = identical signal; the only difference is the forced daily")
    print("  round-trip fee. The gap between them IS the cost of 'always enter a trade every day'.")


if __name__ == "__main__":
    main()
