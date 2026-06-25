#!/usr/bin/env python3
"""backtest_trend_dd.py — finetune the DRAWDOWN of the 4h trend (golden cross) baseline.

Base: long when EMA50>EMA200, flat otherwise (the +125%/-30%/4.12 ret-DD baseline).
DD-control overlays (all keep the same entry, just exit sooner to cut giveback):
  flip        : exit only on EMA50<EMA200 (baseline)
  chandelier  : also exit if close < (highest close since entry) - k*ATR  [trailing stop]
  fast_ema    : also exit if close < EMA50 (faster than the full cross)
  half        : baseline at 0.5x size (shows sizing cuts DD but NOT ret/DD)
Re-entry after an early exit: when still bull AND close>EMA50 (pullback re-entry).
Goal = raise ret/DD and improve OOS, not just shrink DD by trading smaller.
Long/flat, next-open fills, fee+slippage, IS/OOS 60/40.
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
        return 0.0, 0.0
    dd = e/e.cummax()-1
    return (e.iloc[-1]-1)*100, float(dd.min()*100)


def run(df, *, mode, k=3.0, size=1.0):
    c = df["close"]; e50 = ema(c, 50); e200 = ema(c, 200); a = atr(df, 14)
    bull = e50 > e200
    cash, qty, entry, peak = 1.0, 0.0, 0.0, 0.0
    eq = []
    for i in range(200, len(df)-1):
        cl = float(df.iloc[i]["close"]); nxt = float(df.iloc[i+1]["open"])
        long_now = bool(bull.iloc[i])
        if qty > 0:
            peak = max(peak, cl)
            exit_ = (not long_now)
            if mode == "chandelier" and cl < peak - k*float(a.iloc[i]):
                exit_ = True
            if mode == "fast_ema" and cl < float(e50.iloc[i]):
                exit_ = True
            if exit_:
                fill = nxt*(1-SLIP_PCT); val = qty*fill; cash = val - val*FEE_PCT*size - (1-size)*0  # fee on traded
                cash = (qty*fill)*(1-FEE_PCT)  # qty already reflects size
                qty = 0.0
        if qty == 0 and long_now:
            ok = (mode == "flip") or (cl > float(e50.iloc[i]))   # don't re-enter into weakness
            if ok:
                fill = nxt*(1+SLIP_PCT)
                invest = cash*size
                cash -= invest; qty = (invest - invest*FEE_PCT)/fill
                entry = fill; peak = cl
        nc = float(df.iloc[i+1]["close"])
        eq.append(cash + qty*nc)
    if qty > 0:
        cash += qty*float(df.iloc[-1]["close"])*(1-SLIP_PCT)*(1-FEE_PCT)
    return metrics(eq)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--bars", type=int, default=6000)
    ap.add_argument("--interval", default="240"); a = ap.parse_args()
    df = fetch("BTCUSDT", a.interval, a.bars)
    days = (df.timestamp.iloc[-1]-df.timestamp.iloc[0]).days
    split = int(len(df)*0.6); oos = df.iloc[split:].reset_index(drop=True)
    print(f"BTCUSDT {a.interval} bars={len(df)} (~{days}d) {df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()}")
    p0 = float(df.iloc[0]['close']); bh = (float(df.iloc[-1]['close'])/p0-1)*100
    op0 = float(oos.iloc[0]['close']); bho = (float(oos.iloc[-1]['close'])/op0-1)*100
    print(f"buy&hold FULL {bh:.1f}% | OOS {bho:.1f}%\n")
    cfgs = [
        ("baseline (flip exit)", dict(mode="flip")),
        ("half size 0.5x", dict(mode="flip", size=0.5)),
        ("chandelier k=4", dict(mode="chandelier", k=4.0)),
        ("chandelier k=3", dict(mode="chandelier", k=3.0)),
        ("chandelier k=2", dict(mode="chandelier", k=2.0)),
        ("fast_ema exit", dict(mode="fast_ema")),
    ]
    print(f"{'config':24s} {'FULL net%':>9} {'DD%':>7} {'ret/DD':>7} | {'OOS net%':>8} {'OOS DD%':>8} {'OOS r/DD':>8}")
    for name, kw in cfgs:
        fn, fd = run(df, **kw); on, od = run(oos, **kw)
        rdf = fn/abs(fd) if fd else 0; rdo = on/abs(od) if od else 0
        print(f"{name:24s} {fn:9.1f} {fd:7.1f} {rdf:7.2f} | {on:8.1f} {od:8.1f} {rdo:8.2f}")


if __name__ == "__main__":
    main()
