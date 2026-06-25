#!/usr/bin/env python3
"""backtest_final.py — the two final candidates, reported with HONEST robust metrics.

CORE (safe)        : BTC 4h EMA50/200 long/flat, 1x.
ALL-WEATHER (aggr) : BTC+ETH+BNB+SOL equal-weight, EMA8/200 long/short reverse, 1x.
Reports CAGR (the honest forward metric, not 2021-inflated totals), max DD, ret/DD,
%positive months, and year-by-year. Common window so numbers are comparable. Binance 4h.
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


def report(name, e):
    yrs = (e.index[-1]-e.index[0]).days/365.25
    cagr = (e.iloc[-1]/e.iloc[0])**(1/yrs)-1
    dd = (e/e.cummax()-1).min()
    mo = e.resample("ME").last().pct_change().dropna()*100
    pos = (mo > 0.2).mean()*100
    ys = e.resample("YE").last(); ych = ys.pct_change().dropna()*100
    by = {idx.year: float(ych.loc[idx]) for idx in ych.index}
    recent = [by[y] for y in by if y >= 2023]
    rec_cagr = (pd.Series([1+r/100 for r in recent]).prod())**(1/len(recent))-1 if recent else 0
    print(f"\n{name}")
    print(f"  CAGR={cagr*100:.0f}%/yr  maxDD={dd*100:.0f}%  ret/DD={(cagr*100)/abs(dd*100):.2f}  +months={pos:.0f}%")
    print(f"  recent (2023-26) avg={rec_cagr*100:.0f}%/yr  <- realistic forward expectation")
    print("  by year: " + " ".join(f"{y}:{v:+.0f}%" for y, v in by.items()))
    return cagr, dd


def main():
    start = int(pd.Timestamp("2020-08-01").timestamp()*1000)
    print("FINAL CANDIDATES (common window 2020-08 -> now, 1x, honest metrics)")
    btc = fetch("BTCUSDT", start)
    report("CORE: BTC 4h EMA50/200 long/flat", strat(btc, 50, 200, False))

    rets = {}
    for sym in ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"):
        e = strat(fetch(sym, start), 8, 200, True)
        rets[sym] = e.pct_change()
    R = pd.DataFrame(rets).sort_index()
    pe = (1+R.mean(axis=1, skipna=True).fillna(0)).cumprod()
    report("ALL-WEATHER: BTC+ETH+BNB+SOL EMA8/200 reverse (eqw)", pe)


if __name__ == "__main__":
    main()
