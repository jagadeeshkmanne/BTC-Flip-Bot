#!/usr/bin/env python3
"""backtest_top10.py — EMA8/200 reverse 1x across a 10-coin basket; plain vs trailing-TP.

Diversification is the one free improvement we found, so this scales it to ~10 liquid coins.
Also tests a trailing take-profit (chandelier: exit to flat when price retraces k*ATR from the
favorable extreme; re-enter on the next EMA cross). Equal-weight portfolio, 1x, Binance 4h.
Reports portfolio net/DD/ret-DD/%pos-months/worst-year (all-green?) and 4-coin vs 10-coin.
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
        cur = data[-1][0] + 1; time.sleep(0.15)
    seen = {int(x[0]): x for x in rows}
    rows = [seen[k] for k in sorted(seen)]
    if not rows:
        return None
    return pd.DataFrame({"timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
                         "open": [float(x[1]) for x in rows], "high": [float(x[2]) for x in rows],
                         "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows]}).reset_index(drop=True)


def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def atr(df, n=14):
    pc = df["close"].shift(1)
    return pd.concat([df["high"]-df["low"], (df["high"]-pc).abs(), (df["low"]-pc).abs()], axis=1).max(axis=1).ewm(alpha=1/n, adjust=False).mean()


def rev_returns(df, f, s, trail_k=0.0):
    c = df["close"]; bull = ema(c, f) > ema(c, s); a = atr(df, 14)
    cu = bull & (~bull.shift(1, fill_value=False)); cd = (~bull) & (bull.shift(1, fill_value=True))
    bal = 1.0; side = 0; entry = 0.0; ext = 0.0; eq = []; ts = []
    for i in range(s+5, len(df)-1):
        nxt = float(df.iloc[i+1]["open"]); cl = float(df.iloc[i]["close"]); av = float(a.iloc[i])
        base = 1 if bool(bull.iloc[i]) else -1
        want = base
        if trail_k > 0:
            if side != 0:
                ext = max(ext, cl) if side == 1 else min(ext, cl)
                if (side == 1 and cl < ext-trail_k*av) or (side == -1 and cl > ext+trail_k*av):
                    want = 0
            if side == 0:
                want = base if ((base == 1 and bool(cu.iloc[i])) or (base == -1 and bool(cd.iloc[i]))) else 0
        if side != want:
            if side == 1:
                fpx = nxt*(1-SLIP_PCT); bal = bal*(fpx/entry)*(1-2*FEE_PCT)
            elif side == -1:
                fpx = nxt*(1+SLIP_PCT); bal = bal*((2*entry-fpx)/entry)*(1-2*FEE_PCT)
            if want == 1:
                entry = nxt*(1+SLIP_PCT); ext = nxt
            elif want == -1:
                entry = nxt*(1-SLIP_PCT); ext = nxt
            side = want
        nc = float(df.iloc[i+1]["close"])
        eq.append(bal if side == 0 else (bal*nc/entry if side == 1 else bal*(2*entry-nc)/entry))
        ts.append(df.iloc[i+1]["timestamp"])
    return pd.Series(eq, index=pd.to_datetime(ts)).pct_change()


def port(retdf, names):
    r = retdf[list(names)].mean(axis=1, skipna=True).fillna(0)
    e = (1+r).cumprod()
    net = (e.iloc[-1]-1)*100; dd = (e/e.cummax()-1).min()*100
    mo = e.resample("ME").last().pct_change().dropna()*100; pos = (mo > 0.2).mean()*100
    ys = e.resample("YE").last(); ych = ys.pct_change()*100
    by = {idx.year: float(ych.loc[idx]) for idx in ys.index if pd.notna(ych.loc[idx])}
    return net, float(dd), pos, (min(by.values()) if by else 0), by


def main():
    coins = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "BNB": "BNBUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT",
             "ADA": "ADAUSDT", "DOGE": "DOGEUSDT", "AVAX": "AVAXUSDT", "LINK": "LINKUSDT", "LTC": "LTCUSDT"}
    start = int(pd.Timestamp("2020-01-01").timestamp()*1000)
    plain = {}; trail = {}
    for k, sym in coins.items():
        df = fetch_binance(sym, "4h", start)
        if df is None or len(df) < 500:
            print(f"  (skip {k})"); continue
        plain[k] = rev_returns(df, 8, 200, 0.0)
        trail[k] = rev_returns(df, 8, 200, 3.0)
    P = pd.DataFrame(plain).sort_index(); T = pd.DataFrame(trail).sort_index()
    four = ["BTC", "ETH", "BNB", "SOL"]; ten = list(plain)

    print("EMA8/200 reverse 1x — diversification + trailing-TP\n")
    print(f"{'basket/mode':26s} {'net%':>9} {'DD%':>7} {'ret/DD':>7} {'+mo%':>5} {'worstYr%':>9} {'allGreen':>9}")
    for label, names, src in (("4-coin (plain)", four, P), ("10-coin (plain)", ten, P), ("10-coin (trailing-TP)", ten, T)):
        net, dd, pos, worst, by = port(src, names)
        print(f"{label:26s} {net:9.0f} {dd:7.1f} {net/abs(dd):7.1f} {pos:5.0f} {worst:9.1f} {'YES' if worst > 0 else 'no':>9}")
        if label == "10-coin (plain)":
            print("   by year: " + " ".join(f"{y}:{v:+.0f}%" for y, v in by.items()))


if __name__ == "__main__":
    main()
