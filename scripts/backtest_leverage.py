#!/usr/bin/env python3
"""backtest_leverage.py — BTC+ETH+BNB EMA8/200 reverse at 1x / 3x / 5x WITH liquidation.

Leverage multiplies returns AND drawdowns, and adds LIQUIDATION risk: a long is liquidated
if price falls ~1/L from entry (3x -> -33%, 5x -> -20%); a short if it rises ~1/L. We model
this on real intrabar high/low. Once a coin's all-in sleeve is liquidated, its capital is gone.
Reports per-coin + equal-weight portfolio net/DD/#liquidations/by-year. Binance 4h.
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
                         "open": [float(x[1]) for x in rows], "high": [float(x[2]) for x in rows],
                         "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows]}).reset_index(drop=True)


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def run(df, f, s, L):
    c = df["close"]; bull = ema(c, f) > ema(c, s)
    bal = 1.0; side = 0; entry = 0.0; liq = 0.0; eq = []; ts = []; nliq = 0
    for i in range(s+5, len(df)-1):
        hi = float(df.iloc[i]["high"]); lo = float(df.iloc[i]["low"])
        # liquidation check (intrabar) on current open position
        if side != 0 and bal > 1e-9:
            if (side == 1 and lo <= liq) or (side == -1 and hi >= liq):
                bal = 0.0; side = 0; nliq += 1
        nxt = float(df.iloc[i+1]["open"])
        want = 1 if bool(bull.iloc[i]) else -1
        if side != want and bal > 1e-9:
            # close existing
            if side == 1:
                fpx = nxt*(1-SLIP_PCT); r = fpx/entry-1; bal = bal*(1+L*r)*(1-2*FEE_PCT*L)
            elif side == -1:
                fpx = nxt*(1+SLIP_PCT); r = 1-fpx/entry; bal = bal*(1+L*r)*(1-2*FEE_PCT*L)
            bal = max(bal, 0.0)
            # open new
            if bal > 1e-9:
                if want == 1:
                    entry = nxt*(1+SLIP_PCT); liq = entry*(1-1.0/L)
                else:
                    entry = nxt*(1-SLIP_PCT); liq = entry*(1+1.0/L)
                side = want
        nc = float(df.iloc[i+1]["close"])
        if side == 1 and bal > 1e-9:
            eq.append(max(bal*(1+L*(nc/entry-1)), 0.0))
        elif side == -1 and bal > 1e-9:
            eq.append(max(bal*(1+L*(1-nc/entry)), 0.0))
        else:
            eq.append(bal)
        ts.append(df.iloc[i+1]["timestamp"])
    return pd.Series(eq, index=pd.to_datetime(ts)), nliq


def summ(e):
    net = (e.iloc[-1]/e.iloc[0]-1)*100; dd = (e/e.cummax()-1).min()*100
    ys = e.resample("YE").last(); ych = ys.pct_change()*100
    by = {idx.year: float(ych.loc[idx]) for idx in ys.index if pd.notna(ych.loc[idx])}
    return net, float(dd), by


def main():
    coins = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "BNB": "BNBUSDT"}
    start = int(pd.Timestamp("2019-01-01").timestamp()*1000)
    data = {k: fetch_binance(v, "4h", start) for k, v in coins.items()}
    for L in (1, 3, 5):
        print(f"\n================ EMA8/200 reverse @ {L}x (with liquidation) ================")
        print(f"{'coin':14s} {'net%':>12} {'DD%':>8} {'liquidations':>13}")
        eqs = {}
        for k, df in data.items():
            e, nliq = run(df, 8, 200, L)
            eqs[k] = e.pct_change()
            net, dd, by = summ(e)
            print(f"{k:14s} {net:12.0f} {dd:8.1f} {nliq:13d}")
        R = pd.DataFrame(eqs).sort_index()
        pe = (1+R.mean(axis=1, skipna=True).fillna(0)).cumprod()
        net, dd, by = summ(pe)
        print(f"{'PORTFOLIO':14s} {net:12.0f} {dd:8.1f}")
        print("  by year: " + " ".join(f"{y}:{v:+.0f}%" for y, v in by.items()))


if __name__ == "__main__":
    main()
