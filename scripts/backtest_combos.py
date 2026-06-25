#!/usr/bin/env python3
"""backtest_combos.py — explore every coin-basket combination for EMA8/200 reverse.

Tests all 15 non-empty subsets of {BTC,ETH,BNB,SOL}, equal-weight (rebalanced each bar),
EMA8/200 long/short reverse. Reports net/DD/ret-DD/%pos-months/worst-year, flags baskets
that are GREEN EVERY YEAR. Also sweeps the EMA pair on the best basket. Binance 4h.
"""
from __future__ import annotations
import time
from itertools import combinations
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


def reverse_returns(df, f, s):
    c = df["close"]; bull = ema(c, f) > ema(c, s)
    bal = 1.0; side = 0; entry = 0.0; eq = []; ts = []
    for i in range(s+5, len(df)-1):
        nxt = float(df.iloc[i+1]["open"])
        want = 1 if bool(bull.iloc[i]) else -1
        if side != want:
            if side == 1:
                fpx = nxt*(1-SLIP_PCT); bal = bal*(fpx/entry)*(1-2*FEE_PCT)
            elif side == -1:
                fpx = nxt*(1+SLIP_PCT); bal = bal*((2*entry-fpx)/entry)*(1-2*FEE_PCT)
            entry = nxt*(1+SLIP_PCT) if want == 1 else nxt*(1-SLIP_PCT); side = want
        nc = float(df.iloc[i+1]["close"])
        eq.append(bal*nc/entry if side == 1 else bal*(2*entry-nc)/entry); ts.append(df.iloc[i+1]["timestamp"])
    return pd.Series(eq, index=pd.to_datetime(ts)).pct_change()


def port_stats(retdf, names):
    r = retdf[list(names)].mean(axis=1, skipna=True).fillna(0)
    e = (1+r).cumprod()
    net = (e.iloc[-1]-1)*100; dd = (e/e.cummax()-1).min()*100
    mo = e.resample("ME").last().pct_change().dropna()*100
    pos = (mo > 0.2).mean()*100
    ys = e.resample("YE").last(); ych = ys.pct_change()*100
    yrs = {idx.year: float(ych.loc[idx]) for idx in ys.index if pd.notna(ych.loc[idx])}
    worst = min(yrs.values()) if yrs else 0
    return net, float(dd), pos, worst, yrs


def main():
    coins = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "BNB": "BNBUSDT", "SOL": "SOLUSDT"}
    start = int(pd.Timestamp("2020-08-01").timestamp()*1000)   # common window (SOL starts Aug 2020)
    rets = {}
    for k, sym in coins.items():
        df = fetch_binance(sym, "4h", start)
        rets[k] = reverse_returns(df, 8, 200)
    R = pd.DataFrame(rets).sort_index()

    keys = list(coins)
    rows = []
    for n in range(1, 5):
        for combo in combinations(keys, n):
            net, dd, pos, worst, yrs = port_stats(R, combo)
            rows.append((net/abs(dd), "+".join(combo), net, dd, pos, worst))
    rows.sort(reverse=True, key=lambda x: x[0])
    print("EMA8/200 reverse — all baskets (equal weight, 2020-08 -> now)\n")
    print(f"{'basket':16s} {'ret/DD':>7} {'net%':>9} {'DD%':>7} {'+mo%':>5} {'worstYr%':>9} {'allGreen':>9}")
    for rd, name, net, dd, pos, worst in rows:
        print(f"{name:16s} {rd:7.1f} {net:9.0f} {dd:7.1f} {pos:5.0f} {worst:9.1f} {'YES' if worst > 0 else 'no':>9}")

    # param sweep on a strong diversified basket
    print("\nEMA-pair sweep on BTC+BNB+SOL (ret/DD, net%, DD%, worstYr):")
    for f, s in ((8, 200), (13, 200), (21, 200), (8, 100), (13, 100)):
        rr = pd.DataFrame({k: reverse_returns(fetch_binance(coins[k], "4h", start), f, s) for k in ("BTC", "BNB", "SOL")}).sort_index()
        net, dd, pos, worst, yrs = port_stats(rr, ("BTC", "BNB", "SOL"))
        print(f"  EMA{f}/{s}: ret/DD {net/abs(dd):6.1f}  net {net:8.0f}%  DD {dd:6.1f}%  +mo {pos:.0f}%  worstYr {worst:+.1f}%")


if __name__ == "__main__":
    main()
