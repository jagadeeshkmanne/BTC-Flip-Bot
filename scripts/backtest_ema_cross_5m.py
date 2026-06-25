#!/usr/bin/env python3
"""backtest_ema_cross_5m.py — 5m EMA-cross scalper: enter on cross, SL below the cross,
dynamic TP (trail / trend-flip / next S/R). Long/flat. Honest fees+slippage.

Entry : bullish EMA cross (ema_f crosses above ema_s) -> buy next open.
Stop  : below the cross candle low - buffer*ATR ('keep SL below that cross').
TP    : dynamic, three modes ->
          flip  : exit on opposite EMA cross (ride the micro-trend)
          trail : chandelier — exit when price < highest_high_since_entry - k*ATR
          sr    : target = nearest pivot resistance above entry (S/R), SL as above
HONESTY: signal on CLOSED bar, fill next open; stop/target/trail on REAL intrabar high/low
(stop-first); fee 0.055%/side + slippage on every fill; IS/OOS 60/40; all-in sizing.
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


def buy_hold(df):
    p0 = float(df.iloc[0]["close"]); return metrics([float(df.iloc[i]["close"])/p0 for i in range(len(df))])


def resistances(df, left, right):
    h = df["high"].values; out = [None]*len(df)
    last = None
    for i in range(len(df)):
        out[i] = last
        if left <= i-right and i-right >= left:
            j = i-right
            w = h[j-left:j+right+1]
            if len(w) and h[j] == w.max():
                last = float(h[j])
    return out  # most recent confirmed pivot-high known at bar i


def backtest(df, *, ema_f, ema_s, tp_mode, stop_buf_atr, trail_k, trend_filter):
    c = df["close"]; ef = ema(c, ema_f); es = ema(c, ema_s); a = atr(df, 14).values
    eg = ema(c, 200)
    cross_up = (ef > es) & (ef.shift(1) <= es.shift(1))
    cross_dn = (ef < es) & (ef.shift(1) >= es.shift(1))
    res = resistances(df, 12, 12) if tp_mode == "sr" else None
    bal = 1.0; pos = None; eq = []; trades = wins = 0
    for i in range(200, len(df)-1):
        hgh = float(df.iloc[i]["high"]); low = float(df.iloc[i]["low"]); cl = float(df.iloc[i]["close"])
        if pos is not None:
            pos["hh"] = max(pos["hh"], hgh)
            if tp_mode == "trail":
                pos["stop"] = max(pos["stop"], pos["hh"] - trail_k*float(a[i]))
            ex = None
            if low <= pos["stop"]:
                ex = pos["stop"]
            elif tp_mode == "sr" and pos.get("tp") and hgh >= pos["tp"]:
                ex = pos["tp"]
            elif tp_mode == "flip" and bool(cross_dn.iloc[i]):
                ex = cl
            if ex is not None:
                fill = ex*(1-SLIP_PCT); factor = fill/pos["entry"]
                new = bal*factor*(1-2*FEE_PCT); pnl = new-bal; bal = new
                trades += 1; wins += int(pnl > 0); pos = None
        if pos is None and bool(cross_up.iloc[i]):
            if trend_filter and not (cl > float(eg.iloc[i])):
                pass
            else:
                entry = float(df.iloc[i+1]["open"])*(1+SLIP_PCT)
                stop = min(low, float(df.iloc[i-1]["low"])) - stop_buf_atr*float(a[i])
                if stop < entry:
                    pos = {"entry": entry, "stop": stop, "hh": entry}
                    if tp_mode == "sr":
                        r = res[i]
                        pos["tp"] = r if (r and r > entry) else None
        nc = float(df.iloc[i+1]["close"])
        eq.append(bal if pos is None else bal*nc/pos["entry"])
    if pos is not None:
        bal = bal*float(df.iloc[-1]["close"])/pos["entry"]*(1-2*FEE_PCT)
    net, dd = metrics(eq); wr = wins/trades*100 if trades else 0.0
    return {"net": net, "dd": dd, "trades": trades, "wr": wr}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--bars", type=int, default=20000)
    ap.add_argument("--interval", default="5"); a = ap.parse_args()
    df = fetch("BTCUSDT", a.interval, a.bars)
    days = (df.timestamp.iloc[-1]-df.timestamp.iloc[0]).days
    split = int(len(df)*0.6); oos = df.iloc[split:].reset_index(drop=True)
    print(f"BTCUSDT {a.interval}m bars={len(df)} (~{days}d) {df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()}")
    bh = buy_hold(df); bho = buy_hold(oos)
    print(f"buy&hold: FULL {bh[0]:.1f}% | OOS {bho[0]:.1f}%\n")
    print(f"{'variant':34s} {'FULL net%':>9} {'dd%':>6} {'tr':>5} {'wr%':>5} | {'OOS net%':>8} {'tr':>5} {'wr%':>5}")
    for ef, es in ((9, 21), (20, 50)):
        for tp in ("flip", "trail", "sr"):
            for tfil in (False, True):
                rf = backtest(df, ema_f=ef, ema_s=es, tp_mode=tp, stop_buf_atr=0.3, trail_k=3.0, trend_filter=tfil)
                ro = backtest(oos, ema_f=ef, ema_s=es, tp_mode=tp, stop_buf_atr=0.3, trail_k=3.0, trend_filter=tfil)
                name = f"ema{ef}/{es} {tp}{' +200f' if tfil else ''}"
                print(f"{name:34s} {rf['net']:9.1f} {rf['dd']:6.1f} {rf['trades']:5d} {rf['wr']:5.0f} | "
                      f"{ro['net']:8.1f} {ro['trades']:5d} {ro['wr']:5.0f}")


if __name__ == "__main__":
    main()
