#!/usr/bin/env python3
"""backtest_cascade_trend.py — cascade/pyramiding on the 4h trend entry (the good idea
from the 'Reverse MA + Cascade' EA, applied to an entry that actually HAS edge).

Base entry: golden cross, long when EMA50>EMA200, flat otherwise (parameter-free, +204%
full cycle). Exit ALL on the flip (no TP), next-open fills, fee+slippage.

Cascade: while long and price keeps extending in our favor (+add_step since the last
unit), ADD another unit (each = u * equity-at-trade-open of notional => leverage grows
only as the trend confirms). Cap at max_units. max_units=1 == the single-unit baseline.

Reports net, max DD, and return/DD (risk-adjusted) so we see if pressing winners actually
helps or just adds leverage. IS/OOS 60/40.
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


def backtest(df, *, u, add_step, max_units):
    c = df["close"]; bull = (ema(c, 50) > ema(c, 200))
    bal = 1.0
    units = None          # list of entry fills; E0 captured at open
    E0 = 0.0
    last_add = 0.0
    eq = []
    trades = adds = 0

    def mark(P):
        if units is None:
            return bal
        return E0*(1 + u*sum(P/e - 1 for e in units))

    for i in range(200, len(df)-1):
        nxt_open = float(df.iloc[i+1]["open"])
        long_now = bool(bull.iloc[i])

        if units is None and long_now:                      # open trade (unit 1)
            E0 = bal; fill = nxt_open*(1+SLIP_PCT)
            units = [fill]; last_add = fill
            bal -= u*E0*FEE_PCT                              # entry fee
            trades += 1
        elif units is not None and not long_now:            # flip -> close ALL
            fill = nxt_open*(1-SLIP_PCT)
            gross = E0*(1 + u*sum(fill/e - 1 for e in units))
            fee = u*E0*(fill/units[0])*FEE_PCT*len(units)    # approx exit fees
            bal = gross - fee
            units = None
        elif units is not None and len(units) < max_units:  # cascade: add on extension
            if nxt_open >= last_add*(1+add_step):
                fill = nxt_open*(1+SLIP_PCT)
                units.append(fill); last_add = fill
                bal -= u*E0*FEE_PCT; adds += 1

        eq.append(mark(float(df.iloc[i+1]["close"])) if units is not None else bal)

    if units is not None:
        fill = float(df.iloc[-1]["close"])*(1-SLIP_PCT)
        bal = E0*(1 + u*sum(fill/e - 1 for e in units))
    net, dd = metrics(eq)
    return {"net": net, "dd": dd, "trades": trades, "adds": adds}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--bars", type=int, default=6000)
    ap.add_argument("--interval", default="240"); a = ap.parse_args()
    df = fetch("BTCUSDT", a.interval, a.bars)
    days = (df.timestamp.iloc[-1]-df.timestamp.iloc[0]).days
    split = int(len(df)*0.6); oos = df.iloc[split:].reset_index(drop=True)
    print(f"BTCUSDT {a.interval} bars={len(df)} (~{days}d) {df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()}")
    p0 = float(df.iloc[0]['close']); bh = (float(df.iloc[-1]['close'])/p0-1)*100
    op0 = float(oos.iloc[0]['close']); bho = (float(oos.iloc[-1]['close'])/op0-1)*100
    print(f"buy&hold FULL {bh:.1f}% | OOS {bho:.1f}%\n")
    print(f"{'config':30s} {'FULL net%':>9} {'dd%':>7} {'ret/dd':>6} {'adds':>5} | {'OOS net%':>8} {'dd%':>7} {'ret/dd':>6}")
    for mu in (1, 2, 3, 4):
        for step in (0.02, 0.04):
            if mu == 1 and step != 0.02:
                continue
            rf = backtest(df, u=1.0, add_step=step, max_units=mu)
            ro = backtest(oos, u=1.0, add_step=step, max_units=mu)
            rdf = rf['net']/abs(rf['dd']) if rf['dd'] else 0
            rdo = ro['net']/abs(ro['dd']) if ro['dd'] else 0
            name = f"max_units={mu}" + ("" if mu == 1 else f", step={step}")
            print(f"{name:30s} {rf['net']:9.1f} {rf['dd']:7.1f} {rdf:6.2f} {rf['adds']:5d} | "
                  f"{ro['net']:8.1f} {ro['dd']:7.1f} {rdo:6.2f}")


if __name__ == "__main__":
    main()
