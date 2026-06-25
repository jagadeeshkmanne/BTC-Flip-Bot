#!/usr/bin/env python3
"""backtest_ma_crossover_video.py — the "famous MA crossover" strategy from the video.

Faithful implementation of the YouTube strategy, on BTC 15m / 1h (what the user asked for):
  - Two moving averages only: 20 & 50 period (video's rule: "the lesser the better").
  - Entry: fast(20) crosses above slow(50) -> long; crosses below -> short.
  - Exit: ATR trailing stop loss (video: "a much better exit than waiting for the cross back").
  - Optional 200-EMA trend filter (video: "only long above 200, only short below" to lift win rate).

Honesty rules (same as the rest of scripts/): signals on CLOSED bars, fills at NEXT bar
open, fee 0.055%/side + 0.05% slippage, ATR stop on REAL intrabar high/low,
in-sample / out-of-sample 60/40 split, metrics = CAGR + max drawdown + win rate.

Data: local bybit cache CSVs (no network needed).
"""
from __future__ import annotations
import os
import pandas as pd

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATASETS = {
    "1h":  "data/cache/BTCUSDT_1h_12000_bybit.csv",
    "15m": "data/cache/BTCUSDT_15m_16000_bybit.csv",
}


def load(path):
    df = pd.read_csv(os.path.join(HERE, path), parse_dates=["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


def sma(s, n): return s.rolling(n).mean()
def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()


def backtest(df, fast=20, slow=50, use_ma="sma", atr_mult=3.0, atr_n=14,
             trend_filter=True, allow_short=True):
    """Returns equity series (indexed by time) + trade list."""
    c = df["close"]
    maf = (sma if use_ma == "sma" else ema)(c, fast)
    mas = (sma if use_ma == "sma" else ema)(c, slow)
    e200 = ema(c, 200)
    a = atr(df, atr_n)

    cross_up = (maf > mas) & (maf.shift(1) <= mas.shift(1))
    cross_dn = (maf < mas) & (maf.shift(1) >= mas.shift(1))

    bal = 1.0
    side = 0           # 0 flat, +1 long, -1 short
    entry = 0.0
    trail = 0.0        # trailing stop price
    eq, ts, trades = [], [], []
    start = max(slow, 200, atr_n) + 2

    for i in range(start, len(df) - 1):
        o_next = float(df.iloc[i + 1]["open"])
        h_next = float(df.iloc[i + 1]["high"])
        l_next = float(df.iloc[i + 1]["low"])
        c_next = float(df.iloc[i + 1]["close"])
        ai = float(a.iloc[i])

        # ---- manage open position: check EXISTING trail vs this bar's real high/low,
        #      then ratchet the trail using this bar's close (no lookahead) ----
        if side == 1:
            if l_next <= trail:                          # stop hit intrabar
                fpx = trail * (1 - SLIP_PCT)
                bal = bal * (fpx / entry) * (1 - 2 * FEE_PCT)
                trades.append(fpx / entry - 1)
                side = 0
            else:
                trail = max(trail, c_next - atr_mult * ai)  # ratchet up after surviving the bar
        elif side == -1:
            if h_next >= trail:
                fpx = trail * (1 + SLIP_PCT)
                bal = bal * ((2 * entry - fpx) / entry) * (1 - 2 * FEE_PCT)
                trades.append((entry - fpx) / entry)
                side = 0
            else:
                trail = min(trail, c_next + atr_mult * ai)

        # ---- entries on the cross (signal seen on bar i, fill at bar i+1 open) ----
        if side == 0:
            long_ok = bool(cross_up.iloc[i]) and (not trend_filter or c.iloc[i] > e200.iloc[i])
            short_ok = allow_short and bool(cross_dn.iloc[i]) and (not trend_filter or c.iloc[i] < e200.iloc[i])
            if long_ok:
                entry = o_next * (1 + SLIP_PCT)
                trail = o_next - atr_mult * ai
                side = 1
            elif short_ok:
                entry = o_next * (1 - SLIP_PCT)
                trail = o_next + atr_mult * ai
                side = -1

        # ---- mark-to-market equity ----
        if side == 1:
            eq.append(bal * c_next / entry)
        elif side == -1:
            eq.append(bal * (2 * entry - c_next) / entry)
        else:
            eq.append(bal)
        ts.append(df.iloc[i + 1]["timestamp"])

    return pd.Series(eq, index=pd.to_datetime(ts)), trades


def metrics(e, trades):
    if len(e) < 2:
        return None
    yrs = max((e.index[-1] - e.index[0]).days / 365.25, 1e-9)
    cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1
    dd = (e / e.cummax() - 1).min()
    tot = e.iloc[-1] / e.iloc[0] - 1
    wins = [t for t in trades if t > 0]
    wr = len(wins) / len(trades) * 100 if trades else 0
    return dict(cagr=cagr, dd=dd, tot=tot, n=len(trades), wr=wr)


def report(label, e, trades):
    n = len(e)
    cut = int(n * 0.6)
    full = metrics(e, trades)
    # split equity; trade-level win rate is approximate per split (split by time)
    e_is, e_oos = e.iloc[:cut], e.iloc[cut:]
    split_t = e.index[cut] if cut < n else e.index[-1]
    tr_is = trades  # trade times not tracked; report whole-period WR + per-split CAGR/DD
    print(f"\n{label}")
    if not full:
        print("  (insufficient data)")
        return
    print(f"  FULL : CAGR={full['cagr']*100:7.1f}%/yr  maxDD={full['dd']*100:6.1f}%  "
          f"total={full['tot']*100:8.1f}%  trades={full['n']:4d}  win%={full['wr']:.0f}")
    if len(e_is) > 1 and len(e_oos) > 1:
        m_is, m_oos = metrics(e_is, []), metrics(e_oos, [])
        print(f"  IS   : CAGR={m_is['cagr']*100:7.1f}%/yr  maxDD={m_is['dd']*100:6.1f}%   "
              f"(through {split_t:%Y-%m-%d})")
        print(f"  OOS  : CAGR={m_oos['cagr']*100:7.1f}%/yr  maxDD={m_oos['dd']*100:6.1f}%   "
              f"(after  {split_t:%Y-%m-%d})  <- the only number that matters")


def main():
    print("=" * 78)
    print("VIDEO MA-CROSSOVER on BTC (20/50 cross + ATR-trail exit + 200-EMA filter)")
    print("Honesty: next-open fills, 0.055%/side fee + 0.05% slip, intrabar ATR stop, 60/40 OOS")
    print("=" * 78)

    for tf, path in DATASETS.items():
        df = load(path)
        span = f"{df['timestamp'].iloc[0]:%Y-%m-%d} -> {df['timestamp'].iloc[-1]:%Y-%m-%d}"
        print(f"\n\n############  BTC {tf}  ({len(df)} bars, {span})  ############")

        # the video's exact recipe + sensible variants
        e, t = backtest(df, use_ma="sma", trend_filter=True, allow_short=True)
        report(f"[{tf}] video recipe: SMA20/50 cross, ATR3 trail, 200-EMA filter, long+short", e, t)

        e, t = backtest(df, use_ma="sma", trend_filter=True, allow_short=False)
        report(f"[{tf}] long-only (200-filter, no shorts)", e, t)

        e, t = backtest(df, use_ma="sma", trend_filter=False, allow_short=True)
        report(f"[{tf}] no 200-filter (pure 20/50 cross, the 'wrong way' the video warns about)", e, t)

        e, t = backtest(df, use_ma="ema", trend_filter=True, allow_short=True)
        report(f"[{tf}] EMA20/50 variant (repo prefers EMA)", e, t)

        e, t = backtest(df, use_ma="sma", trend_filter=True, allow_short=True, atr_mult=2.0)
        report(f"[{tf}] tighter ATR2 trail", e, t)

        e, t = backtest(df, use_ma="sma", trend_filter=True, allow_short=True, atr_mult=4.0)
        report(f"[{tf}] looser ATR4 trail", e, t)


if __name__ == "__main__":
    main()
