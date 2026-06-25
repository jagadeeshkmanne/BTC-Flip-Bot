#!/usr/bin/env python3
"""backtest_ema_rsi_tf.py — does EMA trend get better on 15m, and does adding RSI help?

Tests EMA trend-following on 15m vs 1h vs 4h, plain and combined with RSI, long/flat,
IS/OOS 60/40. Decide on closed bar, fill next open, fee+slippage.
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
        rows.extend(batch); end_ms = min(int(x[0]) for x in batch)-1; time.sleep(0.04)
    rows = sorted({int(x[0]): x for x in rows}.values(), key=lambda x: int(x[0]))
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


def run(df, want_long):
    cash, qty, trades = 1.0, 0.0, 0; eq = []
    for i in range(len(df)-1):
        wl = want_long.iloc[i]
        if pd.isna(wl):
            eq.append(cash + qty*float(df.iloc[i+1]["close"])); continue
        px = float(df.iloc[i+1]["open"])
        if qty == 0 and bool(wl):
            cash -= cash*FEE_PCT; qty = cash/(px*(1+SLIP_PCT)); cash = 0.0; trades += 1
        elif qty > 0 and not bool(wl):
            proc = qty*px*(1-SLIP_PCT); cash = proc-proc*FEE_PCT; qty = 0.0
        eq.append(cash + qty*float(df.iloc[i+1]["close"]))
    if qty > 0:
        eq.append(cash + qty*float(df.iloc[-1]["close"]))
    n, d = metrics(eq); return n, d, trades


def variants(df):
    c = df["close"]; e50, e200 = ema(c, 50), ema(c, 200); e20 = ema(c, 20)
    r = rsi(c, 14)
    bull = e50 > e200
    return {
        "EMA50>200 plain":         bull,
        "EMA20>50 plain":          e20 > e50,
        "EMA bull + RSI>50":       bull & (r > 50),
        "EMA bull + RSI<70 filter": bull & (r < 70),          # avoid buying overbought
        "EMA bull + RSI pullback":  bull & (r < 45),          # buy dips within uptrend
        "EMA bull, exit RSI>75":   None,                       # special-cased below
    }


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--bars", type=int, default=16000); a = ap.parse_args()
    for interval, label, nbars in (("15", "15m", a.bars), ("60", "1h", a.bars//2), ("240", "4h", a.bars//8)):
        df = fetch("BTCUSDT", interval, nbars)
        days = (df.timestamp.iloc[-1]-df.timestamp.iloc[0]).days
        split = int(len(df)*0.6); oos = df.iloc[split:].reset_index(drop=True)
        bh = (float(df.iloc[-1]["close"])/float(df.iloc[0]["close"])-1)*100
        bh_o = (float(oos.iloc[-1]["close"])/float(oos.iloc[0]["close"])-1)*100
        print(f"\n===== {label} bars={len(df)} (~{days}d)  buy&hold full={bh:.1f}% OOS={bh_o:.1f}% =====")
        print(f"{'variant':26s} {'FULL%':>8} {'DD%':>7} {'trades':>6} | {'OOS%':>8} {'OOSdd%':>7}")
        v_full = variants(df); v_oos = variants(oos)
        for name in v_full:
            if name == "EMA bull, exit RSI>75":
                # stateful: enter on EMA bull, exit when EMA bear OR RSI>75
                def sig(d):
                    c = d["close"]; bull = ema(c, 50) > ema(c, 200); r = rsi(c, 14)
                    held = pd.Series(False, index=d.index); h = False
                    for i in range(len(d)):
                        if pd.isna(bull.iloc[i]):
                            held.iloc[i] = False; continue
                        if not h and bool(bull.iloc[i]):
                            h = True
                        elif h and (not bool(bull.iloc[i]) or r.iloc[i] > 75):
                            h = False
                        held.iloc[i] = h
                    return held
                fn, fd, ft = run(df, sig(df)); on, od, ot = run(oos, sig(oos))
            else:
                fn, fd, ft = run(df, v_full[name]); on, od, ot = run(oos, v_oos[name])
            print(f"{name:26s} {fn:8.1f} {fd:7.1f} {ft:6d} | {on:8.1f} {od:7.1f}")


if __name__ == "__main__":
    main()
