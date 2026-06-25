#!/usr/bin/env python3
"""backtest_ema_pullback_v2.py — 4h EMA pullback, FINE-TUNED: +shorts, faster legs, tighter stop.

Long  : ema_f>ema200 (uptrend), pullback to ema_f -> long; target rr; exit flip.
Short : ema_f<ema200 (downtrend), rally to ema_f -> short; target rr; exit flip.
Stop modes: 'wide' = beyond ema200 +/- 0.5ATR ; 'tight' = beyond recent swing (10-bar) +/- 0.5ATR.
Goal: explore more-trades / more-profit / less-DD trade-offs. Long/flat vs long/short.
IS/OOS, fee+slippage, intrabar stop/target (stop-first). Data: Binance 4h.
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


def run(df, *, f, rr, allow_short, stop_mode):
    c = df["close"]; ef = ema(c, f); e200 = ema(c, 200); a = atr(df, 14)
    swlo = df["low"].rolling(10).min(); swhi = df["high"].rolling(10).max()
    up = ef > e200
    bal = 1.0; pos = None; eq = []; trades = wins = 0; lp = sp = 0.0
    for i in range(205, len(df)-1):
        hi = float(df.iloc[i]["high"]); lo = float(df.iloc[i]["low"])
        if pos is not None:
            if pos["side"] == 1:
                ex = pos["stop"] if lo <= pos["stop"] else (pos["tp"] if hi >= pos["tp"] else None)
                if ex is None and not bool(up.iloc[i]):
                    ex = float(df.iloc[i+1]["open"])
                if ex is not None:
                    fill = ex*(1-SLIP_PCT); new = bal*(fill/pos["entry"])*(1-2*FEE_PCT); pnl = new-bal
                    bal = new; lp += pnl; trades += 1; wins += int(pnl > 0); pos = None
            else:
                ex = pos["stop"] if hi >= pos["stop"] else (pos["tp"] if lo <= pos["tp"] else None)
                if ex is None and bool(up.iloc[i]):
                    ex = float(df.iloc[i+1]["open"])
                if ex is not None:
                    fill = ex*(1+SLIP_PCT); new = bal*((2*pos["entry"]-fill)/pos["entry"])*(1-2*FEE_PCT); pnl = new-bal
                    bal = new; sp += pnl; trades += 1; wins += int(pnl > 0); pos = None
        if pos is None:
            efv = float(ef.iloc[i]); av = float(a.iloc[i])
            if bool(up.iloc[i]) and lo <= efv:                       # long pullback
                entry = float(df.iloc[i+1]["open"])*(1+SLIP_PCT)
                stop = (float(e200.iloc[i]) if stop_mode == "wide" else float(swlo.iloc[i])) - 0.5*av
                if stop < entry:
                    pos = {"side": 1, "entry": entry, "stop": stop, "tp": entry+rr*(entry-stop)}
            elif allow_short and (not bool(up.iloc[i])) and hi >= efv:  # short rally
                entry = float(df.iloc[i+1]["open"])*(1-SLIP_PCT)
                stop = (float(e200.iloc[i]) if stop_mode == "wide" else float(swhi.iloc[i])) + 0.5*av
                if stop > entry:
                    pos = {"side": -1, "entry": entry, "stop": stop, "tp": entry-rr*(stop-entry)}
        nc = float(df.iloc[i+1]["close"])
        if pos is None:
            eq.append(bal)
        elif pos["side"] == 1:
            eq.append(bal*nc/pos["entry"])
        else:
            eq.append(bal*(2*pos["entry"]-nc)/pos["entry"])
    net, dd = metrics(eq); wr = wins/trades*100 if trades else 0.0
    return {"net": net, "dd": dd, "trades": trades, "wr": wr, "lp": lp*100, "sp": sp*100}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--start", default="2019-01-01"); a = ap.parse_args()
    df = fetch_binance(a.symbol, "4h", int(pd.Timestamp(a.start).timestamp()*1000))
    if len(df) < 500:
        print("insufficient data"); return
    split = int(len(df)*0.6); oos = df.iloc[split:].reset_index(drop=True)
    bh = (float(df.iloc[-1]['close'])/float(df.iloc[0]['close'])-1)*100
    print(f"{a.symbol} 4h bars={len(df)} ({df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()}) buy&hold {bh:.0f}%\n")
    print(f"{'cfg':28s} {'FULL%':>9} {'DD%':>7} {'tr':>4} {'wr%':>4} {'Spnl%':>7} | {'OOS%':>8} {'ret/DD':>6}")
    rows = []
    for f in (8, 13, 21, 34):
        for sm in ("wide", "tight"):
            for short in (False, True):
                rf = run(df, f=f, rr=3.0, allow_short=short, stop_mode=sm)
                ro = run(oos, f=f, rr=3.0, allow_short=short, stop_mode=sm)
                rd = rf['net']/abs(rf['dd']) if rf['dd'] else 0
                name = f"EMA{f}/200 {sm} {'LS' if short else 'LF'}"
                rows.append((rd, name, rf, ro))
    rows.sort(reverse=True, key=lambda x: x[0])
    for rd, name, rf, ro in rows:
        print(f"{name:28s} {rf['net']:9.0f} {rf['dd']:7.1f} {rf['trades']:4d} {rf['wr']:4.0f} {rf['sp']:7.1f} | {ro['net']:8.1f} {rd:6.1f}")


if __name__ == "__main__":
    main()
