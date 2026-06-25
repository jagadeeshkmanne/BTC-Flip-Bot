#!/usr/bin/env python3
"""backtest_basket_tf_tune.py — can the all-weather basket be tuned for SHORTER timeframes?

The live bot is the 4-coin (BTC/ETH/BNB/SOL) equal-weight basket, EMA8/200 long/short
REVERSE, 1x, on 4h. The user wants to fine-tune the EMA params for 1h (and check 15m via
BTC). This sweeps (EMA_fast, EMA_slow) × direction(reverse / long-flat) on 1h vs the SAME
data resampled to 4h (identical window → apples-to-apples), and ranks by OOS ret/DD.

Basket P&L = equal-weight mean of per-coin strategy returns each bar (continuous rebalance,
matching backtest_final.py / the deployed bot's reported 2.21 ret/DD). Honest: next-open
fills, 0.055%/side + 0.05% slip on turnover, 60/40 OOS.

Data: Binance 1h for the 4 coins (cached), 4h resampled from it.
"""
from __future__ import annotations
import os, time
import pandas as pd
import numpy as np
import requests

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]
COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]


def fetch_1h(symbol, start="2021-01-01"):
    cache = os.path.join(HERE, f"data/cache/{symbol}_1h_binance.csv")
    if os.path.exists(cache):
        df = pd.read_csv(cache, parse_dates=["timestamp"])
        if (pd.Timestamp.utcnow().tz_localize(None) - df["timestamp"].iloc[-1]) < pd.Timedelta("2D"):
            return df
    cur = int(pd.Timestamp(start).timestamp() * 1000); rows, url_ok = [], None
    while True:
        params = {"symbol": symbol, "interval": "1h", "startTime": cur, "limit": 1000}
        data = None
        for h in ([url_ok] if url_ok else HOSTS):
            try:
                r = requests.get(f"{h}/api/v3/klines", params=params, timeout=20)
                if r.status_code == 200:
                    data = r.json(); url_ok = h; break
            except Exception:
                continue
        if not data:
            break
        rows.extend(data)
        if len(data) < 1000:
            break
        cur = data[-1][0] + 1; time.sleep(0.08)
    seen = {int(x[0]): x for x in rows}; rows = [seen[k] for k in sorted(seen)]
    df = pd.DataFrame({"timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
                       "open": [float(x[1]) for x in rows], "high": [float(x[2]) for x in rows],
                       "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows]})
    df.to_csv(cache, index=False)
    return df


def to_4h(df):
    return df.set_index("timestamp").resample("4h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def coin_returns(df, f, s, direction):
    """Per-coin per-bar strategy return (open-to-open, fees on turnover)."""
    c = df["close"]
    up = ema(c, f) > ema(c, s)
    pos = up.astype(float) if direction == "lf" else (2 * up.astype(float) - 1)
    held = pos.shift(1).fillna(0)
    oo = (df["open"].shift(-1) / df["open"] - 1).fillna(0)
    turn = held.diff().abs().fillna(held.abs())
    r = held * oo - turn * (FEE_PCT + SLIP_PCT)
    r.index = pd.to_datetime(df["timestamp"])
    return r


def basket_equity(frames, f, s, direction):
    rets = pd.DataFrame({sym: coin_returns(frames[sym], f, s, direction) for sym in COINS}).sort_index()
    port = rets.mean(axis=1, skipna=True).fillna(0)   # equal weight, continuous rebalance
    return (1 + port).cumprod()


def metrics(eq):
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    return cagr, dd, (cagr / abs(dd) if dd < -1e-9 else 0.0)


def main():
    raw = {sym: fetch_1h(sym) for sym in COINS}
    common_start = max(df["timestamp"].iloc[0] for df in raw.values())
    raw = {sym: df[df["timestamp"] >= common_start].reset_index(drop=True) for sym, df in raw.items()}
    span = f"{common_start:%Y-%m-%d}->{raw['BTCUSDT']['timestamp'].iloc[-1]:%Y-%m-%d}"

    tfs = {"1h": raw, "4h": {s: to_4h(df) for s, df in raw.items()}}
    # scaled grids: on 1h the 4h-EMA200 (33-day) trend lives near slow≈800, so reach for it
    fasts = [5, 8, 9, 13, 20, 21, 32, 50, 80]
    slows = [50, 100, 150, 200, 300, 400, 600, 800]

    for tf, frames in tfs.items():
        idx = pd.to_datetime(frames["BTCUSDT"]["timestamp"])
        cut_ts = idx.iloc[int(len(idx) * 0.6)]
        print("\n" + "=" * 96)
        print(f"BASKET (BTC+ETH+BNB+SOL eqw) on {tf}  ({span}, OOS {cut_ts:%Y-%m-%d})")
        print("=" * 96)
        print(f"  {'EMA f/s':<10}{'dir':<5}{'CAGR':>8}{'DD':>6}{'r/DD':>6}   {'oCAGR':>7}{'oDD':>6}{'or/DD':>7}")
        rows = []
        for direction in ("ls", "lf"):
            for f in fasts:
                for s in slows:
                    if f >= s:
                        continue
                    eq = basket_equity(frames, f, s, direction)
                    eo = eq[eq.index >= cut_ts]
                    rows.append((f"{f}/{s}", direction, metrics(eq), metrics(eo)))
        rows.sort(key=lambda x: x[3][2], reverse=True)
        for p, d, m, mo in rows[:10]:
            mark = "  <- LIVE" if (p == "8/200" and d == "ls") else ""
            print(f"  {p:<10}{d:<5}{m[0]*100:>7.0f}%{m[1]*100:>5.0f}%{m[2]:>6.2f}   "
                  f"{mo[0]*100:>6.0f}%{mo[1]*100:>5.0f}%{mo[2]:>7.2f}{mark}")
        # always show the live config for reference
        eq = basket_equity(frames, 8, 200, "ls"); eo = eq[eq.index >= cut_ts]
        m, mo = metrics(eq), metrics(eo)
        print(f"  {'8/200':<10}{'ls':<5}{m[0]*100:>7.0f}%{m[1]*100:>5.0f}%{m[2]:>6.2f}   "
              f"{mo[0]*100:>6.0f}%{mo[1]*100:>5.0f}%{mo[2]:>7.2f}  <- LIVE config")


if __name__ == "__main__":
    main()
