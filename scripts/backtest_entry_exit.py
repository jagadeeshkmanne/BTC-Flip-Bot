#!/usr/bin/env python3
"""backtest_entry_exit.py — fine-tune ENTRY vs EXIT conditions (decoupled) on 4-coin reverse.

Regime: EMA(entry) vs EMA200 sets bull/bear.
  exit='flip'  : long all of bull, short all of bear (symmetric reverse, baseline)
  exit=EMA_x   : long only when (bull AND close>EMA_x); short only when (bear AND close<EMA_x);
                 else FLAT. -> faster exit when price loses the exit-EMA, decoupled from entry.
4-coin equal-weight, 1x. Reports CAGR/maxDD/ret-DD/recent/worst-yr. Binance 4h.
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


def run(df, entry_ema, exit_ema):
    c = df["close"]; bull = ema(c, entry_ema) > ema(c, 200)
    ex = ema(c, exit_ema) if exit_ema else None
    bal = 1.0; side = 0; entry = 0.0; eq = []; ts = []
    for i in range(205, len(df)-1):
        b = bool(bull.iloc[i]); cl = float(df.iloc[i]["close"])
        if exit_ema:
            exv = float(ex.iloc[i])
            want = 1 if (b and cl > exv) else (-1 if ((not b) and cl < exv) else 0)
        else:
            want = 1 if b else -1
        nxt = float(df.iloc[i+1]["open"])
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
    return pd.Series(eq, index=pd.to_datetime(ts)).pct_change()


def stats(R):
    e = (1+R.mean(axis=1, skipna=True).fillna(0)).cumprod()
    yrs = (e.index[-1]-e.index[0]).days/365.25
    cagr = ((e.iloc[-1]/e.iloc[0])**(1/yrs)-1)*100
    dd = (e/e.cummax()-1).min()*100
    ych = e.resample("YE").last().pct_change().dropna()*100
    by = {idx.year: float(ych.loc[idx]) for idx in ych.index}
    recent = [by[y] for y in by if y >= 2023]
    rec = (pd.Series([1+r/100 for r in recent]).prod()**(1/len(recent))-1)*100 if recent else 0
    return cagr, float(dd), rec, (min(by.values()) if by else 0)


def main():
    coins = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
    start = int(pd.Timestamp("2020-08-01").timestamp()*1000)
    data = {s: fetch(s, start) for s in coins}
    print("4-coin reverse, 1x — ENTRY x EXIT fine-tune\n")
    print(f"{'entry':>7} {'exit':>7} | {'CAGR%':>7} {'maxDD%':>8} {'ret/DD':>7} {'recent%':>8} {'worstYr%':>9} {'green':>6}")
    rows = []
    for ee in (8, 13, 21):
        for xe in (None, 50, 20, 13):
            R = pd.DataFrame({s: run(data[s], ee, xe) for s in coins}).sort_index()
            cagr, dd, rec, worst = stats(R)
            rows.append((cagr/abs(dd), ee, xe, cagr, dd, rec, worst))
    rows.sort(reverse=True, key=lambda x: x[0])
    for rd, ee, xe, cagr, dd, rec, worst in rows:
        print(f"EMA{ee:>4} {('flip' if xe is None else 'EMA'+str(xe)):>7} | {cagr:7.0f} {dd:8.0f} {rd:7.2f} {rec:8.0f} {worst:9.0f} {'YES' if worst > 0 else 'no':>6}")


if __name__ == "__main__":
    main()
