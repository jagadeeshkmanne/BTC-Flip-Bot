#!/usr/bin/env python3
"""backtest_ema9_1h.py — 9 EMA strategies on 1h BTC, incl. 3-candle confirmation.

Variants:
  cross_LS      : long if close>EMA9, short if close<EMA9 (always in, single bar)
  cross_LF      : long if close>EMA9, else flat
  3close_LS     : long after 3 consecutive closes > EMA9, short after 3 closes < EMA9
  3color_LS     : long after 3 consecutive green candles, short after 3 red (the user's
                  '3 bear candles -> sell' idea, symmetric)
  3color_LF     : long after 3 green, flat after 3 red (long/flat)
  3close_LF_t   : 3close long/flat, but only long when also above EMA200 (trend filter)
HONESTY: signal on closed bar, next-open fill, fee+slippage, IS/OOS 60/40. Data: Binance 1h.
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
                         "open": [float(x[1]) for x in rows], "high": [float(x[2]) for x in rows],
                         "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows]}).reset_index(drop=True)


def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def metrics(eq):
    e = pd.Series(eq)
    if len(e) < 2:
        return 0.0, 0.0
    dd = e/e.cummax()-1
    return (e.iloc[-1]-1)*100, float(dd.min()*100)


def want_series(df, mode):
    c = df["close"]; e9 = ema(c, 9); e200 = ema(c, 200)
    above = (c > e9)
    green = (c > df["open"])
    bel = ~above; red = ~green
    three_up_close = above & above.shift(1, fill_value=False) & above.shift(2, fill_value=False)
    three_dn_close = bel & bel.shift(1, fill_value=False) & bel.shift(2, fill_value=False)
    three_green = green & green.shift(1, fill_value=False) & green.shift(2, fill_value=False)
    three_red = red & red.shift(1, fill_value=False) & red.shift(2, fill_value=False)
    w = pd.Series(0, index=df.index)
    if mode == "cross_LS":
        w = above.map({True: 1, False: -1})
    elif mode == "cross_LF":
        w = above.map({True: 1, False: 0})
    elif mode in ("3close_LS", "3color_LS", "3color_LF", "3close_LF_t"):
        up = three_up_close if "close" in mode else three_green
        dn = three_dn_close if "close" in mode else three_red
        state = 0; out = []
        for i in range(len(df)):
            if bool(up.iloc[i]):
                state = 1
            elif bool(dn.iloc[i]):
                state = -1 if mode.endswith("LS") else 0
            out.append(state)
        w = pd.Series(out, index=df.index)
        if mode == "3close_LF_t":
            w = w.where((c > e200) | (w <= 0), 0)   # only allow long if above EMA200
            w = w.clip(lower=0)                       # long/flat
    return w.fillna(0).astype(int)


def run(df, mode):
    w = want_series(df, mode)
    bal = 1.0; side = 0; entry = 0.0; eq = []; trades = 0
    for i in range(205, len(df)-1):
        nxt = float(df.iloc[i+1]["open"]); want = int(w.iloc[i])
        if side != want:
            if side == 1:
                f = nxt*(1-SLIP_PCT); bal = bal*(f/entry)*(1-2*FEE_PCT); trades += 1
            elif side == -1:
                f = nxt*(1+SLIP_PCT); bal = bal*((2*entry-f)/entry)*(1-2*FEE_PCT); trades += 1
            if want == 1:
                entry = nxt*(1+SLIP_PCT)
            elif want == -1:
                entry = nxt*(1-SLIP_PCT)
            side = want
        nc = float(df.iloc[i+1]["close"])
        eq.append(bal if side == 0 else (bal*nc/entry if side == 1 else bal*(2*entry-nc)/entry))
    net, dd = metrics(eq)
    return {"net": net, "dd": dd, "trades": trades}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--start", default="2023-01-01"); a = ap.parse_args()
    df = fetch_binance(a.symbol, "1h", int(pd.Timestamp(a.start).timestamp()*1000))
    if len(df) < 1000:
        print("insufficient data"); return
    days = (df.timestamp.iloc[-1]-df.timestamp.iloc[0]).days
    split = int(len(df)*0.6); oos = df.iloc[split:].reset_index(drop=True)
    bh = (float(df.iloc[-1]['close'])/float(df.iloc[0]['close'])-1)*100
    bho = (float(oos.iloc[-1]['close'])/float(oos.iloc[0]['close'])-1)*100
    print(f"{a.symbol} 1h 9EMA  bars={len(df)} (~{days}d) {df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()}")
    print(f"buy&hold FULL {bh:.1f}% | OOS {bho:.1f}%\n")
    print(f"{'mode':14s} {'FULL net%':>9} {'DD%':>7} {'trades':>7} | {'OOS net%':>8} {'OOS DD%':>8}")
    for m in ("cross_LS", "cross_LF", "3close_LS", "3color_LS", "3color_LF", "3close_LF_t"):
        rf = run(df, m); ro = run(oos, m)
        print(f"{m:14s} {rf['net']:9.1f} {rf['dd']:7.1f} {rf['trades']:7d} | {ro['net']:8.1f} {ro['dd']:8.1f}")


if __name__ == "__main__":
    main()
