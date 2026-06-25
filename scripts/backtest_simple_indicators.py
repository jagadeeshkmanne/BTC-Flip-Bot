#!/usr/bin/env python3
"""backtest_simple_indicators.py — plain single-indicator strategies on BTC 4h, long/flat.

Tests the obvious 'why not just RSI/MACD/EMA' question head-on. Long/flat only (spot,
no shorts), decide on CLOSED bar, fill next open, fee+slippage. IS/OOS 60/40 split.

Two families:
  TREND  (trade WITH direction): EMA cross, price>EMA200, MACD>signal, RSI>50 momentum
  REVERT (trade AGAINST extremes): RSI<30 oversold, Bollinger lower-band bounce
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
    """want_long: boolean Series indexed like df; long/flat, decide bar i, fill i+1 open."""
    cash, qty = 1.0, 0.0; eq = []
    for i in range(len(df)-1):
        if pd.isna(want_long.iloc[i]):
            eq.append(cash + qty*float(df.iloc[i+1]["close"])); continue
        px = float(df.iloc[i+1]["open"])
        if qty == 0 and bool(want_long.iloc[i]):
            cash -= cash*FEE_PCT; qty = cash/(px*(1+SLIP_PCT)); cash = 0.0
        elif qty > 0 and not bool(want_long.iloc[i]):
            proc = qty*px*(1-SLIP_PCT); cash = proc-proc*FEE_PCT; qty = 0.0
        eq.append(cash + qty*float(df.iloc[i+1]["close"]))
    if qty > 0:
        eq.append(cash + qty*float(df.iloc[-1]["close"]))
    return metrics(eq)


def signals(df):
    c = df["close"]
    e13, e20, e50, e200 = ema(c, 13), ema(c, 20), ema(c, 50), ema(c, 200)
    ml = ema(c, 12)-ema(c, 26); sig = ema(ml, 9)
    r = rsi(c, 14)
    mid = c.rolling(20).mean(); sd = c.rolling(20).std()
    bb_low = mid-2*sd
    # RSI mean-reversion needs stateful hold (enter <30, exit >50): build as a held series
    rsi_mr = pd.Series(index=df.index, dtype=object)
    holding = False
    for i in range(len(df)):
        ri = r.iloc[i]
        if pd.isna(ri):
            rsi_mr.iloc[i] = False; continue
        if not holding and ri < 30:
            holding = True
        elif holding and ri > 50:
            holding = False
        rsi_mr.iloc[i] = holding
    bb_mr = pd.Series(index=df.index, dtype=object); holding = False
    for i in range(len(df)):
        if pd.isna(bb_low.iloc[i]):
            bb_mr.iloc[i] = False; continue
        if not holding and c.iloc[i] <= bb_low.iloc[i]:
            holding = True
        elif holding and c.iloc[i] >= mid.iloc[i]:
            holding = False
        bb_mr.iloc[i] = holding
    return {
        "TREND ema50>ema200 (golden)": e50 > e200,
        "TREND ema20>ema50":           e20 > e50,
        "TREND close>ema200":          c > e200,
        "TREND macd>signal":           ml > sig,
        "TREND rsi>50 (momentum)":     r > 50,
        "TREND your-bot e13>e20&c>e200": (e13 > e20) & (c > e200),
        "REVERT rsi<30 oversold":      rsi_mr.astype(bool),
        "REVERT bollinger bounce":     bb_mr.astype(bool),
    }


def main():
    df = fetch("BTCUSDT", "240", 6000)
    days = (df.timestamp.iloc[-1]-df.timestamp.iloc[0]).total_seconds()/86400
    print(f"BTCUSDT 4h bars={len(df)} (~{days:.0f}d) {df.timestamp.iloc[0].date()} -> {df.timestamp.iloc[-1].date()}")
    split = int(len(df)*0.6)
    is_df, oos_df = df.iloc[:split], df.iloc[split:].reset_index(drop=True)
    p0 = float(df.iloc[0]["close"])
    bh = ((float(df.iloc[-1]["close"])/p0)-1)*100
    bh_oos = ((float(oos_df.iloc[-1]["close"])/float(oos_df.iloc[0]["close"]))-1)*100
    print(f"buy&hold full={bh:.1f}%  OOS={bh_oos:.1f}%\n")

    sig_full = signals(df)
    sig_is = {k: v.iloc[:split].reset_index(drop=True) for k, v in signals(df).items()}
    sig_oos = {k: v.iloc[split:].reset_index(drop=True) for k, v in signals(df).items()}

    print(f"{'strategy':34s} {'FULL net%':>9} {'FULL dd%':>9} {'OOS net%':>9} {'OOS dd%':>9}")
    for name in sig_full:
        fn, fd = run(df, sig_full[name])
        on, od = run(oos_df, sig_oos[name])
        print(f"{name:34s} {fn:9.1f} {fd:9.1f} {on:9.1f} {od:9.1f}")


if __name__ == "__main__":
    main()
