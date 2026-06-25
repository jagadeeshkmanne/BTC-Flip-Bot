#!/usr/bin/env python3
"""backtest_famous_trend.py — famous trader trend systems on BTC DAILY, long/flat.

These are the canonical, parameter-light, track-record-proven trend systems:
  - SMA/EMA golden cross 50/200          (classic)
  - price > 200d SMA                      (Paul Tudor Jones / Meb Faber timing)
  - Turtle System 1: 20-high entry / 10-low exit   (Richard Dennis)
  - Turtle System 2: 55-high entry / 20-low exit   (Richard Dennis)
  - Supertrend (ATR 10, mult 3)           (popular TV trend filter)
  - your live bot rule (EMA13>20 & close>EMA200)

Long/flat (spot, no shorts), decide on CLOSED daily bar, fill next open, fee+slippage.
IS/OOS 60/40. Fixed rules, NOTHING tuned -> results are trustworthy, not curve-fit.
"""
from __future__ import annotations
import time
import pandas as pd
import requests

BYBIT_BASE = "https://api.bybit.com"
FEE_PCT = 0.00055
SLIP_PCT = 0.0005


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


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"]-df["low"], (df["high"]-pc).abs(), (df["low"]-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def metrics(eq):
    e = pd.Series(eq)
    if len(e) < 2:
        return 0.0, 0.0, 0.0
    dd = e/e.cummax()-1
    yrs = max(len(e)/365.0, 1e-9)
    cagr = ((e.iloc[-1])**(1/yrs)-1)*100
    return (e.iloc[-1]-1)*100, float(dd.min()*100), cagr


def run(df, want_long):
    cash, qty = 1.0, 0.0; eq = []; trades = 0
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
    n, d, c = metrics(eq); return n, d, c, trades


def donchian(df, n_in, n_out):
    hi = df["high"].rolling(n_in).max().shift(1)
    lo = df["low"].rolling(n_out).min().shift(1)
    c = df["close"]
    held = pd.Series(False, index=df.index); h = False
    for i in range(len(df)):
        if pd.isna(hi.iloc[i]) or pd.isna(lo.iloc[i]):
            held.iloc[i] = False; continue
        if not h and c.iloc[i] > hi.iloc[i]:
            h = True
        elif h and c.iloc[i] < lo.iloc[i]:
            h = False
        held.iloc[i] = h
    return held


def supertrend(df, n=10, mult=3.0):
    a = atr(df, n); hl2 = (df["high"]+df["low"])/2
    up = hl2 - mult*a; dn = hl2 + mult*a
    c = df["close"]; trend = pd.Series(True, index=df.index)
    fu = up.copy(); fd = dn.copy()
    for i in range(1, len(df)):
        fu.iloc[i] = max(up.iloc[i], fu.iloc[i-1]) if c.iloc[i-1] > fu.iloc[i-1] else up.iloc[i]
        fd.iloc[i] = min(dn.iloc[i], fd.iloc[i-1]) if c.iloc[i-1] < fd.iloc[i-1] else dn.iloc[i]
        if c.iloc[i] > fd.iloc[i-1]:
            trend.iloc[i] = True
        elif c.iloc[i] < fu.iloc[i-1]:
            trend.iloc[i] = False
        else:
            trend.iloc[i] = trend.iloc[i-1]
    return trend


def main():
    df = fetch("BTCUSDT", "D", 1100)
    days = (df.timestamp.iloc[-1]-df.timestamp.iloc[0]).days
    print(f"BTCUSDT DAILY bars={len(df)} (~{days}d) {df.timestamp.iloc[0].date()} -> {df.timestamp.iloc[-1].date()}")
    split = int(len(df)*0.6)
    oos = df.iloc[split:].reset_index(drop=True)
    c = df["close"]
    bh = (float(df.iloc[-1]["close"])/float(df.iloc[0]["close"])-1)*100
    bh_o = (float(oos.iloc[-1]["close"])/float(oos.iloc[0]["close"])-1)*100
    *_, bh_cagr = (0, 0, 0), metrics([float(x)/float(c.iloc[0]) for x in c])[2]
    print(f"buy&hold: full={bh:.1f}% (CAGR {bh_cagr:.0f}%) OOS={bh_o:.1f}%\n")

    def sigs(d):
        cc = d["close"]
        return {
            "SMA golden 50/200":      cc.rolling(50).mean() > cc.rolling(200).mean(),
            "EMA golden 50/200":      ema(cc, 50) > ema(cc, 200),
            "price > 200d SMA (PTJ)": cc > cc.rolling(200).mean(),
            "Turtle S1 20/10":        donchian(d, 20, 10),
            "Turtle S2 55/20":        donchian(d, 55, 20),
            "Supertrend(10,3)":       supertrend(d, 10, 3.0),
            "your-bot e13>e20&c>e200": (ema(cc, 13) > ema(cc, 20)) & (cc > ema(cc, 200)),
        }

    full_s, oos_s = sigs(df), sigs(oos)
    print(f"{'strategy':26s} {'FULL net%':>9} {'CAGR%':>6} {'DD%':>6} {'trades':>6} | {'OOS net%':>8} {'OOS DD%':>7}")
    for name in full_s:
        fn, fd, fc, ft = run(df, full_s[name])
        on, od, oc, ot = run(oos, oos_s[name])
        print(f"{name:26s} {fn:9.1f} {fc:6.0f} {fd:6.1f} {ft:6d} | {on:8.1f} {od:7.1f}")


if __name__ == "__main__":
    main()
