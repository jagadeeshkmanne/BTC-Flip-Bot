#!/usr/bin/env python3
"""backtest_breakout.py — trend-following S/R breakout on 5m/15m, honest.

Entry : close breaks above the prior-N-bar high (Donchian resistance breakout = with-trend).
Filters (single vs multiple indicator confirmation):
  trend : only long if close > EMA200 (trade breakouts in the higher trend)
  vol   : only long if volume > vol_mult * SMA(volume,20) (real participation, not a fakeout)
Stop/exit: ATR chandelier (close < highest_close_since_entry - k*ATR) = the 'best SL' trail.
Long/flat. Compares plain vs +trend vs +trend+vol. IS/OOS 60/40. fee+slippage, next-open.
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
                         "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows],
                         "vol": [float(x[5]) for x in rows]}).reset_index(drop=True)


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


def run(df, *, N, k, use_trend, use_vol, vol_mult=1.2):
    c = df["close"]; e200 = ema(c, 200); a = atr(df, 14)
    hh = df["high"].rolling(N).max().shift(1)
    vsma = df["vol"].rolling(20).mean()
    bal = 1.0; qty = 0.0; entry = 0.0; peak = 0.0; eq = []; trades = wins = 0
    for i in range(max(N, 200), len(df)-1):
        cl = float(df.iloc[i]["close"]); nxt = float(df.iloc[i+1]["open"]); lo = float(df.iloc[i]["low"])
        if qty > 0:
            peak = max(peak, cl)
            if cl < peak - k*float(a.iloc[i]):
                fill = nxt*(1-SLIP_PCT); proc = qty*fill*(1-FEE_PCT); pnl = proc-bal_at; bal = proc
                trades += 1; wins += int(pnl > 0); qty = 0.0
        if qty == 0:
            brk = cl > float(hh.iloc[i]) if pd.notna(hh.iloc[i]) else False
            tok = (not use_trend) or (cl > float(e200.iloc[i]))
            vok = (not use_vol) or (pd.notna(vsma.iloc[i]) and float(df.iloc[i]["vol"]) > vol_mult*float(vsma.iloc[i]))
            if brk and tok and vok:
                fill = nxt*(1+SLIP_PCT); bal_at = bal
                qty = (bal - bal*FEE_PCT)/fill; entry = fill; peak = cl
        eq.append(bal if qty == 0 else qty*float(df.iloc[i+1]["close"]))
    if qty > 0:
        bal = qty*float(df.iloc[-1]["close"])*(1-SLIP_PCT)*(1-FEE_PCT)
    net, dd = metrics(eq); wr = wins/trades*100 if trades else 0.0
    return {"net": net, "dd": dd, "trades": trades, "wr": wr}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--interval", default="15")
    ap.add_argument("--bars", type=int, default=16000); a = ap.parse_args()
    df = fetch("BTCUSDT", a.interval, a.bars)
    days = (df.timestamp.iloc[-1]-df.timestamp.iloc[0]).days
    split = int(len(df)*0.6); oos = df.iloc[split:].reset_index(drop=True)
    bh = (float(df.iloc[-1]['close'])/float(df.iloc[0]['close'])-1)*100
    bho = (float(oos.iloc[-1]['close'])/float(oos.iloc[0]['close'])-1)*100
    print(f"BTCUSDT {a.interval}m bars={len(df)} (~{days}d) {df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()}")
    print(f"buy&hold FULL {bh:.1f}% | OOS {bho:.1f}%\n")
    print(f"{'config':32s} {'FULL net%':>9} {'DD%':>7} {'tr':>5} {'wr%':>5} | {'OOS net%':>8} {'tr':>5}")
    for N in (20, 48):
        for ut, uv, label in ((False, False, "plain"), (True, False, "+trend200"), (True, True, "+trend+vol")):
            rf = run(df, N=N, k=3.0, use_trend=ut, use_vol=uv)
            ro = run(oos, N=N, k=3.0, use_trend=ut, use_vol=uv)
            name = f"breakout N{N} {label}"
            print(f"{name:32s} {rf['net']:9.1f} {rf['dd']:7.1f} {rf['trades']:5d} {rf['wr']:5.0f} | {ro['net']:8.1f} {ro['trades']:5d}")


if __name__ == "__main__":
    main()
