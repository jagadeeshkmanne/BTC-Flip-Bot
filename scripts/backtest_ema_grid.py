#!/usr/bin/env python3
"""backtest_ema_grid.py — comprehensive EMA sweep on BTC: break above/below + crosses.

Tests, with IS/OOS:
  break : close > EMA(n) -> long (break above), close < EMA(n) -> short/flat (break below)
          for many n, both long/flat (LF) and long/short (LS)
  cross : EMA(fast) vs EMA(slow) for a grid of pairs, LF and LS
Reports net, DD, ret/DD, OOS for every combo, sorted by ret/DD. Data: Binance 4h.
fee+slippage, next-open fills.
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
                         "open": [float(x[1]) for x in rows], "close": [float(x[4]) for x in rows]}).reset_index(drop=True)


def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def metrics(eq):
    e = pd.Series(eq)
    if len(e) < 2:
        return 0.0, 0.0
    dd = e/e.cummax()-1
    return (e.iloc[-1]-1)*100, float(dd.min()*100)


def run(df, sig, ls):
    """sig: bool Series (True=bullish). ls: True=long/short, False=long/flat."""
    bal = 1.0; side = 0; entry = 0.0; eq = []
    warm = 210
    for i in range(warm, len(df)-1):
        nxt = float(df.iloc[i+1]["open"])
        want = 1 if bool(sig.iloc[i]) else (-1 if ls else 0)
        if side != want:
            if side == 1:
                f = nxt*(1-SLIP_PCT); bal = bal*(f/entry)*(1-2*FEE_PCT)
            elif side == -1:
                f = nxt*(1+SLIP_PCT); bal = bal*((2*entry-f)/entry)*(1-2*FEE_PCT)
            if want == 1:
                entry = nxt*(1+SLIP_PCT)
            elif want == -1:
                entry = nxt*(1-SLIP_PCT)
            side = want
        nc = float(df.iloc[i+1]["close"])
        eq.append(bal if side == 0 else (bal*nc/entry if side == 1 else bal*(2*entry-nc)/entry))
    return metrics(eq)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="4h"); ap.add_argument("--start", default="2019-01-01"); a = ap.parse_args()
    df = fetch_binance(a.symbol, a.interval, int(pd.Timestamp(a.start).timestamp()*1000))
    if len(df) < 500:
        print("insufficient data"); return
    split = int(len(df)*0.6); oos = df.iloc[split:].reset_index(drop=True)
    bh = (float(df.iloc[-1]['close'])/float(df.iloc[0]['close'])-1)*100
    print(f"{a.symbol} {a.interval} bars={len(df)} ({df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()}) buy&hold {bh:.0f}%\n")

    results = []
    c = df["close"]; co = oos["close"]
    # single-EMA break above/below
    for n in (10, 20, 50, 100, 150, 200):
        sig = c > ema(c, n); sigo = co > ema(co, n)
        for ls in (False, True):
            fn, fd = run(df, sig, ls); on, od = run(oos, sigo, ls)
            results.append((f"px>EMA{n} {'LS' if ls else 'LF'}", fn, fd, on, od))
    # EMA cross pairs
    for f in (9, 13, 20, 50):
        for s in (50, 100, 200):
            if f >= s:
                continue
            sig = ema(c, f) > ema(c, s); sigo = ema(co, f) > ema(co, s)
            for ls in (False, True):
                fn, fd = run(df, sig, ls); on, od = run(oos, sigo, ls)
                results.append((f"EMA{f}/{s} {'LS' if ls else 'LF'}", fn, fd, on, od))

    results.sort(key=lambda r: (r[1]/abs(r[2]) if r[2] else 0), reverse=True)
    print(f"{'strategy':16s} {'FULL net%':>10} {'DD%':>7} {'ret/DD':>7} | {'OOS net%':>9} {'OOS DD%':>8}")
    for name, fn, fd, on, od in results:
        rd = fn/abs(fd) if fd else 0
        print(f"{name:16s} {fn:10.0f} {fd:7.1f} {rd:7.2f} | {on:9.1f} {od:8.1f}")


if __name__ == "__main__":
    main()
