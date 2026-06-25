#!/usr/bin/env python3
"""backtest_dedicated_short.py — long side fixed (EMA50>200), SHORT side tuned separately.

Long  : EMA50>EMA200 -> long (the validated winner, untouched).
Short : ONLY in a confirmed downtrend (EMA50<EMA200) AND close < EMA(short_ma) -> short.
        Cover to flat when close climbs back above EMA(short_ma). So: long in bull, short
        only when clearly below the short MA in a bear, flat in the chop between.
        Optional buffer: require close < EMA(short_ma)*(1-buf) to avoid shorting noise.
Goal: find short params that ADD bear-market profit without dragging the bull, and that
work across BOTH the 2022 choppy crash and the 2025-26 decline (not curve-fit to one).
Per-year + full + IS/OOS. Next-open fills, fee+slippage, all-in.
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
        cur = data[-1][0] + 1; time.sleep(0.25)
    seen = {int(x[0]): x for x in rows}
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


def run(df, *, short_ma, buf, allow_short):
    c = df["close"]; e50 = ema(c, 50); e200 = ema(c, 200); esh = ema(c, short_ma)
    bull = e50 > e200
    bal = 1.0; side = 0; entry = 0.0; eq = []; lp = sp = 0.0
    for i in range(max(short_ma, 200), len(df)-1):
        nxt = float(df.iloc[i+1]["open"]); cl = float(df.iloc[i]["close"])
        if bool(bull.iloc[i]):
            want = 1
        elif allow_short and cl < float(esh.iloc[i])*(1-buf):
            want = -1
        else:
            want = 0
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
    ts = list(df["timestamp"].iloc[max(short_ma, 200):len(df)-1])
    net, dd = metrics(eq)
    return {"net": net, "dd": dd, "lp": lp*100, "sp": sp*100, "eq": eq, "ts": ts}


def yearly(eq, ts):
    s = pd.Series(eq, index=pd.to_datetime(ts)); yr = s.resample("YE").last(); base0 = s.iloc[0]
    return {idx.year: ((yr.loc[idx]/yr.shift(1).loc[idx]-1)*100 if idx != yr.index[0] else (yr.loc[idx]/base0-1)*100) for idx in yr.index}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="4h"); ap.add_argument("--start", default="2019-01-01"); a = ap.parse_args()
    df = fetch_binance(a.symbol, a.interval, int(pd.Timestamp(a.start).timestamp()*1000))
    if len(df) < 300:
        print("insufficient data"); return
    print(f"{a.symbol} {a.interval} bars={len(df)} {df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()}\n")

    base = run(df, short_ma=50, buf=0, allow_short=False)  # long/flat reference
    print("dedicated-short sweep (long side = EMA50>200 fixed):")
    print(f"{'config':22s} {'net%':>8} {'DD%':>7} {'ret/DD':>7} {'Spnl%':>8} | {'2022':>7} {'2025':>7} {'2026':>7}")
    by = yearly(base["eq"], base["ts"])
    print(f"{'long/flat (no short)':22s} {base['net']:8.0f} {base['dd']:7.1f} {base['net']/abs(base['dd']):7.2f} {0.0:8.1f} | "
          f"{by.get(2022,0):7.1f} {by.get(2025,0):7.1f} {by.get(2026,0):7.1f}")
    for sm in (20, 50, 100):
        for buf in (0.0, 0.02):
            r = run(df, short_ma=sm, buf=buf, allow_short=True)
            yy = yearly(r["eq"], r["ts"])
            rd = r['net']/abs(r['dd']) if r['dd'] else 0
            name = f"short<EMA{sm}" + (f" buf{int(buf*100)}%" if buf else "")
            print(f"{name:22s} {r['net']:8.0f} {r['dd']:7.1f} {rd:7.2f} {r['sp']:8.1f} | "
                  f"{yy.get(2022,0):7.1f} {yy.get(2025,0):7.1f} {yy.get(2026,0):7.1f}")


if __name__ == "__main__":
    main()
