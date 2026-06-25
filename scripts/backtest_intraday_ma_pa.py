#!/usr/bin/env python3
"""backtest_intraday_ma_pa.py — intraday BTC: Moving-Average vs Price-Action, head to head.

Two strategy families, both long/short reverse, evaluated on 5m and 15m bars:

  MOVING AVERAGE  — EMA fast/slow cross. LONG when ema_f>ema_s, SHORT when ema_f<ema_s.
                    Optional trend filter (only long above ema_trend / short below).
  PRICE ACTION    — Donchian breakout (pure price, no indicator). LONG on close above the
                    prior N-bar high, SHORT on close below the prior N-bar low, hold between.

HONESTY: signal decided on a CLOSED bar; position is established at the NEXT open and the
P&L is measured open->open (no peeking, no close-of-signal-bar fill). Fee 0.055%/side +
0.05% slippage/side charged on every position change (a reverse pays 2x). IS/OOS 60/40.
Benchmark = buy & hold on the same open basis.

Data: local 5m cache (2023->now). 15m is resampled from the 5m so both series cover the
same span (the bybit 15m cache is only ~160 days).
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
FEE_PCT = 0.00055    # taker fee per side
SLIP_PCT = 0.0005    # slippage per side
COST = FEE_PCT + SLIP_PCT


# ── data ───────────────────────────────────────────────────────────────────
def load_5m() -> pd.DataFrame:
    df = pd.read_csv(os.path.join(CACHE, "BTCUSDT_5m_binance.csv"), parse_dates=["timestamp"])
    return df[["timestamp", "open", "high", "low", "close"]].reset_index(drop=True)


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    g = df.set_index("timestamp").resample(rule)
    out = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(),
        "low": g["low"].min(), "close": g["close"].last(),
    }).dropna().reset_index()
    return out


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


# ── signal generators (return target position array in {-1,0,1}, one per bar,
#    where signal[i] is the decision made at the CLOSE of bar i) ─────────────
def sig_ma(df, fast, slow, trend=None):
    ef, es = ema(df["close"], fast), ema(df["close"], slow)
    sig = np.where(ef > es, 1, -1).astype(float)
    if trend:
        et = ema(df["close"], trend)
        c = df["close"].values
        sig = np.where((sig > 0) & (c > et.values), 1.0,
              np.where((sig < 0) & (c < et.values), -1.0, 0.0))
    return sig


def sig_donchian(df, n):
    hh = df["high"].rolling(n).max().shift(1).values   # prior n-bar high, excl current
    ll = df["low"].rolling(n).min().shift(1).values
    c = df["close"].values
    sig = np.zeros(len(df))
    cur = 0.0
    for i in range(len(df)):
        if not np.isnan(hh[i]) and c[i] > hh[i]:
            cur = 1.0
        elif not np.isnan(ll[i]) and c[i] < ll[i]:
            cur = -1.0
        sig[i] = cur
    return sig


# ── backtest core: honest open->open fills ──────────────────────────────────
def simulate(df, sig):
    """sig[i] decided at close[i] -> held over [open[i+1], open[i+2]). Fees on turnover."""
    o = df["open"].values
    n = len(df)
    eq = 1.0
    curve = np.empty(n)
    curve[:] = 1.0
    prev_pos = 0.0
    trades = 0
    for i in range(n - 2):
        pos = sig[i]
        if pos != prev_pos:
            eq *= (1 - COST * abs(pos - prev_pos))
            if pos != 0:
                trades += 1
            prev_pos = pos
        r = o[i + 2] / o[i + 1] - 1.0
        eq *= (1 + pos * r)
        curve[i + 2] = eq
    return curve, trades


def stats(curve, bars_per_year):
    e = pd.Series(curve)
    total = (e.iloc[-1] - 1) * 100
    dd = float((e / e.cummax() - 1).min() * 100)
    yrs = len(e) / bars_per_year
    cagr = ((e.iloc[-1]) ** (1 / yrs) - 1) * 100 if yrs > 0 and e.iloc[-1] > 0 else -100.0
    rdd = (total / abs(dd)) if dd < 0 else float("inf")
    return total, cagr, dd, rdd


def buy_hold(df, bars_per_year):
    o = df["open"].values
    curve = o[1:] / o[1]
    return stats(np.concatenate([[1.0], curve]), bars_per_year)


# ── runner ───────────────────────────────────────────────────────────────────
TF = {"5m": 105_120, "15m": 35_040}  # bars/year (525600 min / tf)


def evaluate(label, df, sig, bpy):
    n = len(df)
    cut = int(n * 0.6)
    full = simulate(df, sig)
    is_ = simulate(df.iloc[:cut].reset_index(drop=True), sig[:cut])
    oos = simulate(df.iloc[cut:].reset_index(drop=True), sig[cut:])
    f = stats(full[0], bpy)
    i = stats(is_[0], bpy)
    o = stats(oos[0], bpy)
    return {"label": label, "trades": full[1],
            "full": f, "is": i, "oos": o,
            "oos_trades": oos[1]}


def fmt_row(r):
    f, i, o = r["full"], r["is"], r["oos"]
    return (f"{r['label']:<26} {r['trades']:>6} | "
            f"IS {i[1]:>7.1f}%cagr {i[2]:>7.1f}%dd | "
            f"OOS {o[1]:>7.1f}%cagr {o[2]:>7.1f}%dd r/dd {o[3]:>5.2f} | "
            f"FULL {f[0]:>9.0f}% {f[2]:>7.1f}%dd")


def main():
    base = load_5m()
    print(f"data: {base['timestamp'].iloc[0]} -> {base['timestamp'].iloc[-1]}  ({len(base):,} 5m bars)\n")

    series = {"5m": base, "15m": resample(base, "15min")}

    for tf, df in series.items():
        bpy = TF[tf]
        bh = buy_hold(df, bpy)
        print(f"════════ {tf}  ({len(df):,} bars) ════════")
        print(f"{'BUY & HOLD':<26} {'':>6} | "
              f"{'':>20} | {'':>34} | FULL {bh[0]:>9.0f}% {bh[2]:>7.1f}%dd")
        print("─" * 118)

        results = []
        # Moving average variants
        for fast, slow in [(9, 21), (20, 50), (50, 200)]:
            results.append(evaluate(f"MA {fast}/{slow}", df, sig_ma(df, fast, slow), bpy))
        results.append(evaluate("MA 9/21 +200filt", df, sig_ma(df, 9, 21, 200), bpy))
        results.append(evaluate("MA 20/50 +200filt", df, sig_ma(df, 20, 50, 200), bpy))
        # Price action (Donchian breakout) variants
        for n in [20, 55, 100]:
            results.append(evaluate(f"PA Donchian-{n}", df, sig_donchian(df, n), bpy))

        for r in results:
            print(fmt_row(r))
        print()


if __name__ == "__main__":
    main()
