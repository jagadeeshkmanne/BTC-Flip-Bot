#!/usr/bin/env python3
"""backtest_signal_confirm.py — symmetric long/short on multi-layer signal+confirmation.

Long  : EMA50>EMA200 (trend up) AND close>EMA50 (signal) AND RSI>50 (momentum confirm)
Short : EMA50<EMA200 (trend down) AND close<EMA50 AND RSI<50
Compares LONG-ONLY vs LONG+SHORT so we isolate exactly what the short side contributes.
4h, full history, IS/OOS, fee+slippage, next-open fills. Data: Binance.
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
def rsi(s, n=14):
    d = s.diff(); up = d.clip(lower=0); dn = (-d).clip(lower=0)
    rs = up.ewm(alpha=1/n, adjust=False).mean()/dn.ewm(alpha=1/n, adjust=False).mean().replace(0, 1e-9)
    return 100-100/(1+rs)
def metrics(eq):
    e = pd.Series(eq)
    if len(e) < 2:
        return 0.0, 0.0
    dd = e/e.cummax()-1
    return (e.iloc[-1]-1)*100, float(dd.min()*100)


def run(df, *, allow_short, f=50, s=200):
    c = df["close"]; e50 = ema(c, f); e200 = ema(c, s); r = rsi(c, 14)
    long_ok = (e50 > e200) & (c > e50) & (r > 50)
    short_ok = (e50 < e200) & (c < e50) & (r < 50)
    bal = 1.0; side = 0; entry = 0.0; eq = []; trades = 0; lp = sp = 0.0
    for i in range(205, len(df)-1):
        nxt = float(df.iloc[i+1]["open"])
        if bool(long_ok.iloc[i]):
            want = 1
        elif allow_short and bool(short_ok.iloc[i]):
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
            if side != want:
                trades += 1
            side = want
        nc = float(df.iloc[i+1]["close"])
        eq.append(bal if side == 0 else (bal*nc/entry if side == 1 else bal*(2*entry-nc)/entry))
    net, dd = metrics(eq)
    return {"net": net, "dd": dd, "trades": trades, "lp": lp*100, "sp": sp*100}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--start", default="2019-01-01"); a = ap.parse_args()
    df = fetch_binance(a.symbol, "4h", int(pd.Timestamp(a.start).timestamp()*1000))
    if len(df) < 500:
        print("insufficient data"); return
    split = int(len(df)*0.6); oos = df.iloc[split:].reset_index(drop=True)
    bh = (float(df.iloc[-1]['close'])/float(df.iloc[0]['close'])-1)*100
    print(f"{a.symbol} 4h signal+confirm (EMA f/s + close>EMAf + RSI) bars={len(df)} buy&hold {bh:.0f}%\n")
    print(f"{'cfg':22s} {'FULL%':>9} {'DD%':>7} {'ret/DD':>7} {'Spnl%':>8} {'tr':>4} | {'OOS%':>8} {'OOSdd%':>7}")
    rows = []
    for f, s in ((8, 200), (13, 200), (21, 200), (50, 200), (8, 100), (13, 100), (21, 50)):
        for sh in (False, True):
            rf = run(df, allow_short=sh, f=f, s=s); ro = run(oos, allow_short=sh, f=f, s=s)
            rd = rf['net']/abs(rf['dd']) if rf['dd'] else 0
            rows.append((rd, f"EMA{f}/{s} {'LS' if sh else 'LF'}", rf, ro))
    rows.sort(reverse=True, key=lambda x: x[0])
    for rd, name, rf, ro in rows:
        print(f"{name:22s} {rf['net']:9.0f} {rf['dd']:7.1f} {rd:7.2f} {rf['sp']:8.0f} {rf['trades']:4d} | {ro['net']:8.1f} {ro['dd']:7.1f}")


if __name__ == "__main__":
    main()
