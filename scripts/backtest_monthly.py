#!/usr/bin/env python3
"""backtest_monthly.py — month-by-month & year-by-year returns of the 4h trend strategy.

Shows whether trend-following gives steady MONTHLY INCOME (spoiler: it's lumpy).
Strategy: EMA50>EMA200 long/flat on 4h BTC (the validated core). Reports a year x month
return grid + stats (%% positive months, avg, best/worst, longest losing streak).
Data: Binance 4h. fee+slippage, next-open fills.
"""
from __future__ import annotations
import argparse, time
import pandas as pd
import requests

FEE_PCT = 0.00055; SLIP_PCT = 0.0005
HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]


def fetch_binance(symbol, interval, start_ms):
    rows = []; url_ok = None; cur = start_ms
    while True:
        params = {"symbol": symbol, "interval": interval, "startTime": cur, "limit": 1000}
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
        cur = data[-1][0] + 1; time.sleep(0.2)
    seen = {int(x[0]): x for x in rows}
    rows = [seen[k] for k in sorted(seen)]
    return pd.DataFrame({"timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
                         "open": [float(x[1]) for x in rows], "high": [float(x[2]) for x in rows],
                         "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows]}).reset_index(drop=True)


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def run(df, f=50, s=200):
    c = df["close"]; bull = ema(c, f) > ema(c, s)
    bal = 1.0; qty = 0.0; entry = 0.0; eq = []; ts = []
    for i in range(s+5, len(df)-1):
        nxt = float(df.iloc[i+1]["open"]); long_now = bool(bull.iloc[i])
        if qty == 0 and long_now:
            bal -= bal*FEE_PCT; qty = bal/(nxt*(1+SLIP_PCT)); entry = nxt; bal = 0.0
        elif qty > 0 and not long_now:
            f2 = nxt*(1-SLIP_PCT); bal = qty*f2*(1-FEE_PCT); qty = 0.0
        eq.append(bal + qty*float(df.iloc[i+1]["close"])); ts.append(df.iloc[i+1]["timestamp"])
    return pd.Series(eq, index=pd.to_datetime(ts))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--start", default="2019-01-01"); ap.add_argument("--fast", type=int, default=50)
    ap.add_argument("--slow", type=int, default=200); a = ap.parse_args()
    df = fetch_binance(a.symbol, "4h", int(pd.Timestamp(a.start).timestamp()*1000))
    if len(df) < 500:
        print("insufficient data"); return
    eq = run(df, a.fast, a.slow)
    m = eq.resample("ME").last()
    mret = m.pct_change().dropna()*100
    print(f"{a.symbol} 4h EMA{a.fast}/{a.slow} long/flat  monthly returns (%)\n")
    # year x month grid
    grid = {}
    for idx, v in mret.items():
        grid.setdefault(idx.year, {})[idx.month] = v
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    print("year  " + "".join(f"{mm:>7}" for mm in months) + f"{'YEAR':>9}")
    for y in sorted(grid):
        row = grid[y]
        cells = "".join(f"{row.get(mn, float('nan')):7.1f}" if mn in row else f"{'-':>7}" for mn in range(1, 13))
        yr = 1.0
        for mn in range(1, 13):
            if mn in row:
                yr *= (1+row[mn]/100)
        print(f"{y}  {cells}{(yr-1)*100:9.1f}")
    pos = (mret > 0).mean()*100
    streak = mx = 0
    for v in mret:
        streak = streak+1 if v < 0 else 0; mx = max(mx, streak)
    print(f"\nmonths={len(mret)}  positive={pos:.0f}%  avg={mret.mean():+.2f}%/mo  median={mret.median():+.2f}%")
    print(f"best month={mret.max():+.1f}%  worst={mret.min():+.1f}%  longest losing streak={mx} months")
    flat = (mret.abs() < 0.5).mean()*100
    print(f"near-flat months (|ret|<0.5%)={flat:.0f}%   <- months in cash / chop")


if __name__ == "__main__":
    main()
