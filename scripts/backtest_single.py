#!/usr/bin/env python3
"""backtest_single.py — 100% single-pair (BTC, ETH) fine-tune, honest CAGR metrics.

Sweeps EMA pairs x {long/flat, reverse}, 1x, reports CAGR / maxDD / ret-DD (CAGR/|DD|) /
recent-3yr avg / worst-year (all-green?). Single-pair = concentrated; watch overfitting.
Binance 4h from 2019.
"""
from __future__ import annotations
import time
import pandas as pd
import requests

FEE_PCT = 0.00055; SLIP_PCT = 0.0005
HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]


def fetch(symbol, start_ms):
    rows = []; url_ok = None; cur = start_ms
    while True:
        params = {"symbol": symbol, "interval": "4h", "startTime": cur, "limit": 1000}
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
        cur = data[-1][0]+1; time.sleep(0.15)
    seen = {int(x[0]): x for x in rows}; rows = [seen[k] for k in sorted(seen)]
    return pd.DataFrame({"timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
                         "open": [float(x[1]) for x in rows], "close": [float(x[4]) for x in rows]}).reset_index(drop=True)


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def strat(df, f, s, reverse):
    c = df["close"]; bull = ema(c, f) > ema(c, s)
    bal = 1.0; side = 0; entry = 0.0; eq = []; ts = []
    for i in range(s+5, len(df)-1):
        nxt = float(df.iloc[i+1]["open"])
        want = 1 if bool(bull.iloc[i]) else (-1 if reverse else 0)
        if side != want:
            if side == 1:
                fpx = nxt*(1-SLIP_PCT); bal = bal*(fpx/entry)*(1-2*FEE_PCT)
            elif side == -1:
                fpx = nxt*(1+SLIP_PCT); bal = bal*((2*entry-fpx)/entry)*(1-2*FEE_PCT)
            if want == 1:
                entry = nxt*(1+SLIP_PCT)
            elif want == -1:
                entry = nxt*(1-SLIP_PCT)
            side = want
        nc = float(df.iloc[i+1]["close"])
        eq.append(bal if side == 0 else (bal*nc/entry if side == 1 else bal*(2*entry-nc)/entry))
        ts.append(df.iloc[i+1]["timestamp"])
    return pd.Series(eq, index=pd.to_datetime(ts))


def metrics(e):
    yrs = (e.index[-1]-e.index[0]).days/365.25
    cagr = ((e.iloc[-1]/e.iloc[0])**(1/yrs)-1)*100
    dd = (e/e.cummax()-1).min()*100
    ych = e.resample("YE").last().pct_change().dropna()*100
    by = {idx.year: float(ych.loc[idx]) for idx in ych.index}
    recent = [by[y] for y in by if y >= 2023]
    rec = (pd.Series([1+r/100 for r in recent]).prod()**(1/len(recent))-1)*100 if recent else 0
    worst = min(by.values()) if by else 0
    return cagr, dd, rec, worst


def main():
    start = int(pd.Timestamp("2019-01-01").timestamp()*1000)
    for sym in ("BTCUSDT", "ETHUSDT"):
        df = fetch(sym, start)
        print(f"\n===== {sym} 100% single-pair fine-tune (1x) =====")
        print(f"{'config':22s} {'CAGR%':>7} {'maxDD%':>8} {'ret/DD':>7} {'recent%':>8} {'worstYr%':>9} {'green':>6}")
        rows = []
        for f, s in ((5, 200), (8, 200), (13, 200), (21, 200), (50, 200), (8, 100), (13, 100)):
            for rev in (False, True):
                e = strat(df, f, s, rev)
                cagr, dd, rec, worst = metrics(e)
                rows.append((cagr/abs(dd), f"EMA{f}/{s} {'rev' if rev else 'L/F'}", cagr, dd, rec, worst))
        rows.sort(reverse=True, key=lambda x: x[0])
        for rd, name, cagr, dd, rec, worst in rows:
            print(f"{name:22s} {cagr:7.0f} {dd:8.0f} {rd:7.2f} {rec:8.0f} {worst:9.0f} {'YES' if worst > 0 else 'no':>6}")


if __name__ == "__main__":
    main()
