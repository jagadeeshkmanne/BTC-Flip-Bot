#!/usr/bin/env python3
"""backtest_mtf.py — 15m execution gated by the 1h trend/RSI (multi-timeframe), honest.

Higher TF (1h) sets the BIAS; lower TF (15m) times the ENTRY:
  bias_long : 1h EMA50>EMA200 (trend) [+ optional 1h RSI>50 momentum]
  15m entry : a 15m trigger fires WHILE 1h bias is long
                rsi40  : 15m RSI crosses back above 40 (buy the pullback, with-trend)
                emax   : 15m EMA9 crosses above EMA21
  exit      : 1h bias turns off OR 15m chandelier stop (close < peak - k*ATR15)
Long/flat. NO lookahead: each 15m bar sees only the last CLOSED 1h bar (merge_asof on
the 1h close time). fee+slippage, IS/OOS 60/40. Data: Binance 15m.
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


def build(df15):
    d = df15.copy()
    d["rsi15"] = rsi(d["close"], 14); d["atr15"] = atr(d, 14)
    d["e9"] = ema(d["close"], 9); d["e21"] = ema(d["close"], 21)
    h = (d.set_index("timestamp").resample("1h", label="left", closed="left")
         .agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna())
    h["h_e50"] = ema(h["close"], 50); h["h_e200"] = ema(h["close"], 200); h["h_rsi"] = rsi(h["close"], 14)
    h = h.reset_index(); h["avail"] = h["timestamp"] + pd.Timedelta(hours=1)   # known only after close
    d = pd.merge_asof(d.sort_values("timestamp"), h[["avail", "h_e50", "h_e200", "h_rsi"]].sort_values("avail"),
                      left_on="timestamp", right_on="avail", direction="backward")
    return d


def run(df, *, trigger, use_h_rsi, k=3.0):
    bias = (df["h_e50"] > df["h_e200"])
    if use_h_rsi:
        bias = bias & (df["h_rsi"] > 50)
    r = df["rsi15"]
    trig_rsi = (r > 40) & (r.shift(1) <= 40)
    trig_emax = (df["e9"] > df["e21"]) & (df["e9"].shift(1) <= df["e21"].shift(1))
    trig = trig_rsi if trigger == "rsi40" else trig_emax
    bal = 1.0; qty = 0.0; peak = 0.0; bal_at = 1.0; eq = []; trades = wins = 0
    for i in range(210, len(df)-1):
        cl = float(df.iloc[i]["close"]); nxt = float(df.iloc[i+1]["open"])
        if pd.isna(df.iloc[i]["h_e200"]):
            eq.append(bal if qty == 0 else qty*float(df.iloc[i+1]["close"])); continue
        if qty > 0:
            peak = max(peak, cl)
            if (not bool(bias.iloc[i])) or cl < peak - k*float(df.iloc[i]["atr15"]):
                fill = nxt*(1-SLIP_PCT); proc = qty*fill*(1-FEE_PCT); pnl = proc-bal_at; bal = proc
                trades += 1; wins += int(pnl > 0); qty = 0.0
        if qty == 0 and bool(bias.iloc[i]) and bool(trig.iloc[i]):
            fill = nxt*(1+SLIP_PCT); bal_at = bal; qty = (bal-bal*FEE_PCT)/fill; peak = cl
        eq.append(bal if qty == 0 else qty*float(df.iloc[i+1]["close"]))
    if qty > 0:
        bal = qty*float(df.iloc[-1]["close"])*(1-SLIP_PCT)*(1-FEE_PCT)
    net, dd = metrics(eq); wr = wins/trades*100 if trades else 0.0
    return {"net": net, "dd": dd, "trades": trades, "wr": wr}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--start", default="2024-01-01"); a = ap.parse_args()
    raw = fetch_binance(a.symbol, "15m", int(pd.Timestamp(a.start).timestamp()*1000))
    if len(raw) < 1000:
        print("insufficient data"); return
    df = build(raw)
    days = (df.timestamp.iloc[-1]-df.timestamp.iloc[0]).days
    split = int(len(df)*0.6); oos = df.iloc[split:].reset_index(drop=True)
    bh = (float(df.iloc[-1]['close'])/float(df.iloc[0]['close'])-1)*100
    bho = (float(oos.iloc[-1]['close'])/float(oos.iloc[0]['close'])-1)*100
    print(f"{a.symbol} 15m exec / 1h bias  bars={len(df)} (~{days}d) {df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()}")
    print(f"buy&hold FULL {bh:.1f}% | OOS {bho:.1f}%\n")
    print(f"{'config':34s} {'FULL net%':>9} {'DD%':>7} {'tr':>5} {'wr%':>5} | {'OOS net%':>8} {'tr':>5}")
    for trig in ("rsi40", "emax"):
        for hr in (False, True):
            rf = run(df, trigger=trig, use_h_rsi=hr); ro = run(oos, trigger=trig, use_h_rsi=hr)
            name = f"15m {trig} | 1h trend" + ("+rsi" if hr else "")
            print(f"{name:34s} {rf['net']:9.1f} {rf['dd']:7.1f} {rf['trades']:5d} {rf['wr']:5.0f} | {ro['net']:8.1f} {ro['trades']:5d}")


if __name__ == "__main__":
    main()
