#!/usr/bin/env python3
"""backtest_ema_pullback.py — EMA-crossover + pullback-to-EMA entry, tuned per timeframe.

Video strategy: EMA_fast>EMA_slow = uptrend; enter long on a pullback to the fast EMA;
stop below the slow EMA; target 3:1 (or exit on cross-down). Long/flat. EMA periods are
swept per timeframe so we 'finetune the numbers based on the timeframe'.
HONESTY: signal on closed bar, next-open fill, stop/target on REAL intrabar (stop-first),
fee+slippage, IS/OOS 60/40. Data: Binance.
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
def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"]-df["low"], (df["high"]-pc).abs(), (df["low"]-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()
def metrics(eq):
    e = pd.Series(eq)
    if len(e) < 2:
        return 0.0, 0.0
    dd = e/e.cummax()-1
    return (e.iloc[-1]-1)*100, float(dd.min()*100)


def run(df, *, f, s, rr):
    c = df["close"]; ef = ema(c, f); es = ema(c, s); a = atr(df, 14)
    up = ef > es
    bal = 1.0; pos = None; eq = []; trades = wins = 0
    warm = s + 5
    for i in range(warm, len(df)-1):
        hi = float(df.iloc[i]["high"]); lo = float(df.iloc[i]["low"]); cl = float(df.iloc[i]["close"])
        if pos is not None:
            ex = pos["stop"] if lo <= pos["stop"] else (pos["tp"] if hi >= pos["tp"] else None)
            if ex is None and not bool(up.iloc[i]):
                ex = float(df.iloc[i+1]["open"])      # exit on trend flip
            if ex is not None:
                fill = ex*(1-SLIP_PCT); proc = pos["qty"]*fill*(1-FEE_PCT); pnl = proc-pos["cost"]
                bal = proc; trades += 1; wins += int(pnl > 0); pos = None
        if pos is None and bool(up.iloc[i]) and lo <= float(ef.iloc[i]):   # pullback touched fast EMA
            entry = float(df.iloc[i+1]["open"])*(1+SLIP_PCT)
            stop = float(es.iloc[i]) - 0.5*float(a.iloc[i])
            if stop < entry:
                tp = entry + rr*(entry-stop)
                fee = bal*FEE_PCT; qty = (bal-fee)/entry
                pos = {"entry": entry, "stop": stop, "tp": tp, "qty": qty, "cost": bal}; bal = 0.0
        nc = float(df.iloc[i+1]["close"])
        eq.append(bal if pos is None else pos["qty"]*nc)
    if pos is not None:
        bal = pos["qty"]*float(df.iloc[-1]["close"])*(1-SLIP_PCT)*(1-FEE_PCT)
    net, dd = metrics(eq); wr = wins/trades*100 if trades else 0.0
    return {"net": net, "dd": dd, "trades": trades, "wr": wr}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--interval", default="4h"); ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--pairs", default="20/50,50/200,13/200,100/200"); a = ap.parse_args()
    df = fetch_binance(a.symbol, a.interval, int(pd.Timestamp(a.start).timestamp()*1000))
    if len(df) < 500:
        print("insufficient data"); return
    days = (df.timestamp.iloc[-1]-df.timestamp.iloc[0]).days
    split = int(len(df)*0.6); oos = df.iloc[split:].reset_index(drop=True)
    bh = (float(df.iloc[-1]['close'])/float(df.iloc[0]['close'])-1)*100
    bho = (float(oos.iloc[-1]['close'])/float(oos.iloc[0]['close'])-1)*100
    print(f"{a.symbol} {a.interval} bars={len(df)} (~{days}d) {df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()}")
    print(f"buy&hold FULL {bh:.1f}% | OOS {bho:.1f}%\n")
    print(f"{'pair':10s} {'rr':>3} | {'FULL net%':>9} {'DD%':>7} {'tr':>5} {'wr%':>5} | {'OOS net%':>8} {'tr':>5}")
    for pair in a.pairs.split(","):
        f, s = (int(x) for x in pair.split("/"))
        for rr in (2.0, 3.0):
            rf = run(df, f=f, s=s, rr=rr); ro = run(oos, f=f, s=s, rr=rr)
            print(f"{pair:10s} {rr:3.0f} | {rf['net']:9.1f} {rf['dd']:7.1f} {rf['trades']:5d} {rf['wr']:5.0f} | {ro['net']:8.1f} {ro['trades']:5d}")


if __name__ == "__main__":
    main()
