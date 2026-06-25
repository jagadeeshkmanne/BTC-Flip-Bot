#!/usr/bin/env python3
"""backtest_trend_binance.py — BTC 4h trend from 2019 to now (Binance history).

Long/flat vs long/short reverse on EMA50>EMA200, plus a per-year breakdown so you can
see robustness across every year/cycle. Next-open fills, fee+slippage, all-in sizing.
"""
from __future__ import annotations
import argparse, time
import pandas as pd
import requests

FEE_PCT = 0.00055; SLIP_PCT = 0.0005
HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]


def fetch_binance(symbol, interval, start_ms):
    rows = []
    url_ok = None
    cur = start_ms
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
        cur = data[-1][0] + 1
        time.sleep(0.25)
    seen = {}
    for x in rows:
        seen[int(x[0])] = x
    rows = [seen[k] for k in sorted(seen)]
    return pd.DataFrame({"timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
                         "open": [float(x[1]) for x in rows], "high": [float(x[2]) for x in rows],
                         "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows]}).reset_index(drop=True)


def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def metrics(eq):
    e = pd.Series(eq)
    if len(e) < 2:
        return 0.0, 0.0
    dd = e/e.cummax()-1
    return (e.iloc[-1]-1)*100, float(dd.min()*100)


def run(df, *, reverse, ema_f=50, ema_s=200):
    c = df["close"]; bull = ema(c, ema_f) > ema(c, ema_s)
    bal = 1.0; side = 0; entry = 0.0; eq = []; lp = sp = 0.0
    for i in range(200, len(df)-1):
        nxt = float(df.iloc[i+1]["open"])
        want = 1 if bool(bull.iloc[i]) else (-1 if reverse else 0)
        if side != want:
            if side == 1:
                f = nxt*(1-SLIP_PCT); new = bal*(f/entry)*(1-2*FEE_PCT); lp += new-bal; bal = new
            elif side == -1:
                f = nxt*(1+SLIP_PCT); new = bal*((2*entry-f)/entry)*(1-2*FEE_PCT); sp += new-bal; bal = new
            if want == 1:
                entry = nxt*(1+SLIP_PCT)
            elif want == -1:
                entry = nxt*(1-SLIP_PCT)
            side = want
        nc = float(df.iloc[i+1]["close"])
        eq.append(bal if side == 0 else (bal*nc/entry if side == 1 else bal*(2*entry-nc)/entry))
    ts = list(df["timestamp"].iloc[200:len(df)-1])
    net, dd = metrics(eq)
    return {"net": net, "dd": dd, "lp": lp*100, "sp": sp*100, "eq": eq, "ts": ts}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--interval", default="4h")
    ap.add_argument("--symbol", default="BTCUSDT"); ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--ema-fast", type=int, default=50); ap.add_argument("--ema-slow", type=int, default=200); a = ap.parse_args()
    start_ms = int(pd.Timestamp(a.start).timestamp()*1000)
    df = fetch_binance(a.symbol, a.interval, start_ms)
    if len(df) < 300:
        print(f"insufficient data ({len(df)} bars) — Binance may be unreachable here"); return
    days = (df.timestamp.iloc[-1]-df.timestamp.iloc[0]).days
    print(f"{a.symbol} {a.interval} bars={len(df)} (~{days/365:.1f}y) {df.timestamp.iloc[0].date()} -> {df.timestamp.iloc[-1].date()}")
    p0 = float(df.iloc[0]['close']); bh = (float(df.iloc[-1]['close'])/p0-1)*100
    print(f"buy & hold: {bh:.0f}%\n")

    print(f"(EMA {a.ema_fast}/{a.ema_slow})")
    rf = run(df, reverse=False, ema_f=a.ema_fast, ema_s=a.ema_slow)
    rr = run(df, reverse=True, ema_f=a.ema_fast, ema_s=a.ema_slow)
    print(f"{'mode':18s} {'net%':>9} {'DD%':>7} {'ret/DD':>7} {'Lpnl%':>8} {'Spnl%':>8}")
    for name, r in (("long/flat", rf), ("long/short reverse", rr)):
        rd = r['net']/abs(r['dd']) if r['dd'] else 0
        print(f"{name:18s} {r['net']:9.0f} {r['dd']:7.1f} {rd:7.2f} {r['lp']:8.0f} {r['sp']:8.0f}")

    # per-year breakdown: long/flat vs reverse vs buy&hold
    def yearly(eq, ts):
        s = pd.Series(eq, index=pd.to_datetime(ts))
        yr = s.resample("YE").last(); base0 = s.iloc[0]
        out = {}
        for idx in yr.index:
            out[idx.year] = (yr.loc[idx]/yr.shift(1).loc[idx]-1)*100 if idx != yr.index[0] else (yr.loc[idx]/base0-1)*100
        return out
    yf = yearly(rf["eq"], rf["ts"]); yv = yearly(rr["eq"], rr["ts"])
    cprice = df.set_index("timestamp")["close"]; py = cprice.resample("YE").last(); pychg = py.pct_change()
    yb = {idx.year: (pychg.loc[idx]*100 if pd.notna(pychg.loc[idx]) else float('nan')) for idx in py.index}
    print("\nper-year:  long/flat   reverse   buy&hold")
    print(f"{'year':6s} {'L/flat%':>10} {'reverse%':>10} {'b&h%':>10}")
    for y in sorted(yf):
        print(f"{y:6d} {yf.get(y, float('nan')):10.1f} {yv.get(y, float('nan')):10.1f} {yb.get(y, float('nan')):10.1f}")


if __name__ == "__main__":
    main()
