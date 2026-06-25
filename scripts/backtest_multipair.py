#!/usr/bin/env python3
"""backtest_multipair.py — EMA8/200 reverse (and long/flat) across BTC/ETH/BNB/SOL + portfolio.

Runs the strategy per coin, then builds an equal-weight portfolio (rebalanced each bar among
available coins). Diversification aims to smooth drawdown / lift positive months WITHOUT the
return cost of a filter. Reports per-coin + portfolio net/DD/ret-DD/%pos-months/by-year.
4h, Binance, fee+slippage, next-open fills.
"""
from __future__ import annotations
import time
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


def run(df, *, f, s, reverse):
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


def summ(eqser):
    net = (eqser.iloc[-1]/eqser.iloc[0]-1)*100
    dd = (eqser/eqser.cummax()-1).min()*100
    mo = eqser.resample("ME").last().pct_change().dropna()*100
    pos = (mo > 0.2).mean()*100
    ys = eqser.resample("YE").last(); ychg = ys.pct_change()*100
    by = {idx.year: float(ychg.loc[idx]) for idx in ys.index if pd.notna(ychg.loc[idx])}
    return net, float(dd), pos, by


def main():
    coins = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
    start = int(pd.Timestamp("2019-01-01").timestamp()*1000)
    for reverse in (True, False):
        mode = "REVERSE (long/short)" if reverse else "LONG/FLAT"
        print(f"\n================ EMA8/200 {mode} ================")
        print(f"{'coin/portf':16s} {'net%':>10} {'DD%':>7} {'ret/DD':>7} {'+mo%':>5} | {'2022':>7} {'2025':>7} {'2026':>7}")
        rets = {}
        for sym in coins:
            df = fetch_binance(sym, "4h", start)
            if len(df) < 500:
                continue
            eq = run(df, f=8, s=200, reverse=reverse)
            rets[sym] = eq.pct_change()
            net, dd, pos, by = summ(eq)
            print(f"{sym:16s} {net:10.0f} {dd:7.1f} {net/abs(dd):7.1f} {pos:5.0f} | "
                  f"{by.get(2022,0):7.1f} {by.get(2025,0):7.1f} {by.get(2026,0):7.1f}")
        # equal-weight portfolio: mean of available coins' bar returns
        R = pd.DataFrame(rets).sort_index()
        port_ret = R.mean(axis=1, skipna=True).fillna(0)
        port_eq = (1+port_ret).cumprod()
        net, dd, pos, by = summ(port_eq)
        print(f"{'PORTFOLIO(eqw)':16s} {net:10.0f} {dd:7.1f} {net/abs(dd):7.1f} {pos:5.0f} | "
              f"{by.get(2022,0):7.1f} {by.get(2025,0):7.1f} {by.get(2026,0):7.1f}")
        ys = port_eq.resample("YE").last(); ychg = ys.pct_change()*100
        yr_line = " ".join(f"{idx.year}:{ychg.loc[idx]:+.0f}%" for idx in ys.index if pd.notna(ychg.loc[idx]))
        print(f"   portfolio by year: {yr_line}")


if __name__ == "__main__":
    main()
