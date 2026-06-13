#!/usr/bin/env python3
"""momo_multiasset.py — MOMO v1 rules on ETH/SOL/BNB + 4-asset portfolio.

Pre-registered test (2026-06-11): apply the EXACT momo_v1 rule set — daily
close > SMA200 AND RSI14 > 70 within last 7 closed bars, long/flat spot,
no leverage — to other majors, unchanged parameters (no tuning). Then an
equal-weight portfolio: capital split 4 ways, each sleeve runs momo on its
asset independently (sleeve rebalance only via compounding, no transfers).

Honest engine: signals on closed bars, fill next bar open, spot fees 0.10%
+ 0.02% slip per side, MTM equity/DD, halves + yearly reported.
ETH daily bars resampled from 1h (UTC days, last close = day close).
"""
import pandas as pd
import numpy as np

FEE, SLIP = 0.0010, 0.0002
SMA, RSIL, TRIG, HOLD = 200, 14, 70.0, 7
CACHE = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache"

def wilder_rsi(c, n=14):
    d = c.diff()
    ag = d.clip(lower=0).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    return 100 - 100/(1 + ag/al)

def load_daily(sym):
    try:
        df = pd.read_csv(f"{CACHE}/{sym}_1d.csv")
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    except FileNotFoundError:
        h = pd.read_csv(f"{CACHE}/{sym}_1h.csv")
        h["timestamp"] = pd.to_datetime(h["timestamp"])
        h["day"] = h["timestamp"].dt.floor("D")
        df = h.groupby("day").agg(open=("open", "first"), high=("high", "max"),
                                  low=("low", "min"), close=("close", "last")).reset_index()
        df = df.rename(columns={"day": "timestamp"})
    df["sma"] = df["close"].rolling(SMA).mean()
    df["rsi"] = wilder_rsi(df["close"])
    df["sig"] = (df["close"] > df["sma"]) & (df["rsi"].rolling(HOLD).max() > TRIG)
    return df

def run(df, start_capital=5000.0):
    """returns daily equity series (indexed by date) + trade count."""
    eq, pos, trades = start_capital, None, 0
    out = []
    for i in range(SMA + 1, len(df)):
        bar, prev = df.iloc[i], df.iloc[i - 1]
        tgt = bool(prev["sig"])
        if pos is None and tgt:
            fill = bar["open"] * (1 + SLIP)
            qty = eq * (1 - FEE) / fill
            pos = {"qty": qty}
            trades += 1
        elif pos is not None and not tgt:
            fill = bar["open"] * (1 - SLIP)
            eq = pos["qty"] * fill * (1 - FEE)
            pos = None
        cur = pos["qty"] * bar["close"] * (1 - FEE - SLIP) if pos else eq
        out.append((bar["timestamp"], cur))
    s = pd.Series(dict(out))
    return s, trades

def stats(s, label, trades=None, bh=None):
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    total = s.iloc[-1] / s.iloc[0] - 1
    cagr = (1 + total) ** (1 / yrs) - 1
    dd = (1 - s / s.cummax()).max()
    half = len(s) // 2
    h1 = s.iloc[half - 1] / s.iloc[0] - 1
    h2 = s.iloc[-1] / s.iloc[half - 1] - 1
    yearly = s.groupby(s.index.year).agg(["first", "last"])
    yr_str = " ".join(f"{y}:{(r['last']/r['first']-1)*100:+.0f}%" for y, r in yearly.iterrows())
    print(f"{label:>10}: {total*100:+8.1f}% ({cagr*100:+5.1f}%/yr, {cagr/3.65/100*100:+.3f}%/day eq) | "
          f"DD {dd*100:4.1f}% | H1 {h1*100:+7.1f}% H2 {h2*100:+7.1f}%"
          + (f" | {trades} trades" if trades else "")
          + (f" | B&H {bh*100:+.0f}%" if bh is not None else ""))
    print(f"{'':>12}{yr_str}")

series = {}
for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]:
    df = load_daily(sym)
    s, n = run(df)
    series[sym] = s
    bh = df["close"].iloc[-1] / df["open"].iloc[SMA + 1] - 1
    stats(s, sym.replace("USDT", ""), n, bh)

# 4-asset equal-weight portfolio on the COMMON window
common = None
for s in series.values():
    common = s.index if common is None else common.intersection(s.index)
port = sum(series[k][common] / series[k][common].iloc[0] for k in series) / 4 * 5000
print()
stats(port, "PORTFOLIO")
g = (port.iloc[-1] / port.iloc[0]) ** (1 / len(port)) - 1
print(f"{'':>12}portfolio geometric daily rate: {g*100:.4f}%/day "
      f"(+ ~0.02%/day funding harvest on flat sleeves)")
