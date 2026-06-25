#!/usr/bin/env python3
"""backtest_trend_reverse.py — long/flat vs long/short 'stop-and-reverse' on 4h trend.

Base signal: EMA50>EMA200 = bull, else bear.
  flat    : long in bull, FLAT in bear (baseline)
  reverse : long in bull, SHORT in bear (always in the market, flip on every cross)
Reports net, DD, ret/DD, and long-vs-short P&L split, IS/OOS 60/40.
Next-open fills, fee+slippage, all-in sizing.
"""
from __future__ import annotations
import argparse, time
import pandas as pd
import requests

BYBIT_BASE = "https://api.bybit.com"; FEE_PCT = 0.00055; SLIP_PCT = 0.0005


def fetch(symbol, interval, bars):
    rows, end_ms = [], None
    while len(rows) < bars:
        p = {"category": "linear", "symbol": symbol, "interval": interval, "limit": min(1000, bars-len(rows))}
        if end_ms is not None:
            p["end"] = end_ms
        b = requests.get(f"{BYBIT_BASE}/v5/market/kline", params=p, timeout=20).json()
        batch = b.get("result", {}).get("list", [])
        if not batch:
            break
        rows.extend(batch); end_ms = min(int(x[0]) for x in batch)-1; time.sleep(0.05)
    rows = sorted({int(x[0]): x for x in rows}.values(), key=lambda x: int(x[0]))
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


def run(df, *, reverse):
    c = df["close"]; bull = ema(c, 50) > ema(c, 200)
    bal = 1.0; side = 0; entry = 0.0; eq = []
    long_pnl = short_pnl = trades = 0.0

    def close_pos(side, entry, fillpx, bal):
        nonlocal long_pnl, short_pnl, trades
        if side == 1:
            f = fillpx*(1-SLIP_PCT); new = bal*(f/entry)*(1-2*FEE_PCT); long_pnl += new-bal
        else:
            f = fillpx*(1+SLIP_PCT); new = bal*((2*entry-f)/entry)*(1-2*FEE_PCT); short_pnl += new-bal
        trades += 1
        return new

    for i in range(200, len(df)-1):
        nxt = float(df.iloc[i+1]["open"])
        want = 1 if bool(bull.iloc[i]) else (-1 if reverse else 0)
        if side != want:
            if side != 0:
                bal = close_pos(side, entry, nxt, bal)
            if want != 0:
                entry = nxt*(1+SLIP_PCT) if want == 1 else nxt*(1-SLIP_PCT)
            side = want
        nc = float(df.iloc[i+1]["close"])
        if side == 1:
            eq.append(bal*nc/entry)
        elif side == -1:
            eq.append(bal*(2*entry-nc)/entry)
        else:
            eq.append(bal)
    net, dd = metrics(eq)
    return {"net": net, "dd": dd, "trades": int(trades), "long_pnl": long_pnl*100, "short_pnl": short_pnl*100}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--bars", type=int, default=6000)
    ap.add_argument("--interval", default="240"); ap.add_argument("--symbol", default="BTCUSDT"); a = ap.parse_args()
    df = fetch(a.symbol, a.interval, a.bars)
    days = (df.timestamp.iloc[-1]-df.timestamp.iloc[0]).days
    split = int(len(df)*0.6); oos = df.iloc[split:].reset_index(drop=True)
    print(f"{a.symbol} {a.interval} bars={len(df)} (~{days}d) {df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()}")
    p0 = float(df.iloc[0]['close']); bh = (float(df.iloc[-1]['close'])/p0-1)*100
    op0 = float(oos.iloc[0]['close']); bho = (float(oos.iloc[-1]['close'])/op0-1)*100
    print(f"buy&hold FULL {bh:.1f}% | OOS {bho:.1f}%\n")
    print(f"{'mode':18s} {'FULL net%':>9} {'DD%':>7} {'ret/DD':>7} {'Lpnl%':>7} {'Spnl%':>7} {'tr':>4} | {'OOS net%':>8} {'OOS DD%':>8}")
    for name, rev in (("long/flat", False), ("long/short reverse", True)):
        rf = run(df, reverse=rev); ro = run(oos, reverse=rev)
        rdf = rf['net']/abs(rf['dd']) if rf['dd'] else 0
        print(f"{name:18s} {rf['net']:9.1f} {rf['dd']:7.1f} {rdf:7.2f} {rf['long_pnl']:7.1f} {rf['short_pnl']:7.1f} {rf['trades']:4d} | "
              f"{ro['net']:8.1f} {ro['dd']:8.1f}")


if __name__ == "__main__":
    main()
