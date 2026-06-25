#!/usr/bin/env python3
"""backtest_sol_finetune.py — sweep SOL EMA params, but show IN-SAMPLE vs OUT-OF-SAMPLE.

Demonstrates whether 'fine-tuning SOL to get more' is real edge or overfitting: optimize on
the first 60% (IS), then check the SAME params on the unseen last 40% (OOS). If the IS-best
params don't win OOS, it's curve-fitting. EMA8/200 reverse is the reference. Binance 4h.
"""
from __future__ import annotations
import time
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
                         "open": [float(x[1]) for x in rows], "close": [float(x[4]) for x in rows]}).reset_index(drop=True)


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def run(df, f, s):
    c = df["close"]; bull = ema(c, f) > ema(c, s)
    bal = 1.0; side = 0; entry = 0.0; eq = []
    for i in range(s+5, len(df)-1):
        nxt = float(df.iloc[i+1]["open"]); want = 1 if bool(bull.iloc[i]) else -1
        if side != want:
            if side == 1:
                fpx = nxt*(1-SLIP_PCT); bal = bal*(fpx/entry)*(1-2*FEE_PCT)
            elif side == -1:
                fpx = nxt*(1+SLIP_PCT); bal = bal*((2*entry-fpx)/entry)*(1-2*FEE_PCT)
            entry = nxt*(1+SLIP_PCT) if want == 1 else nxt*(1-SLIP_PCT); side = want
        nc = float(df.iloc[i+1]["close"])
        eq.append(bal*nc/entry if side == 1 else bal*(2*entry-nc)/entry)
    e = pd.Series(eq)
    if len(e) < 2:
        return 0.0, 0.0
    dd = (e/e.cummax()-1).min()*100
    return (e.iloc[-1]-1)*100, float(dd)


def main():
    df = fetch_binance("SOLUSDT", "4h", int(pd.Timestamp("2020-08-01").timestamp()*1000))
    split = int(len(df)*0.6)
    is_df = df.iloc[:split].reset_index(drop=True)
    oos_df = df.iloc[split:].reset_index(drop=True)
    print(f"SOL 4h  IS {is_df.timestamp.iloc[0].date()}->{is_df.timestamp.iloc[-1].date()} | "
          f"OOS {oos_df.timestamp.iloc[0].date()}->{oos_df.timestamp.iloc[-1].date()}\n")
    rows = []
    for f in (5, 8, 13, 21, 34):
        for s in (100, 150, 200, 250):
            fn, fd = run(df, f, s)
            isn, _ = run(is_df, f, s)
            on, od = run(oos_df, f, s)
            rows.append((fn, f, s, fd, isn, on, od))
    # sort by IN-SAMPLE return (what an optimizer would pick)
    rows.sort(key=lambda r: r[4], reverse=True)
    print("ranked by IN-SAMPLE return (what fine-tuning would choose):")
    print(f"{'EMA':10s} {'FULL net%':>10} {'FULL DD%':>9} {'IS net%':>10} {'OOS net%':>10} {'OOS DD%':>9}")
    for fn, f, s, fd, isn, on, od in rows:
        print(f"EMA{f}/{s:<6} {fn:10.0f} {fd:9.1f} {isn:10.0f} {on:10.0f} {od:9.1f}")
    best_is = rows[0]
    best_oos = max(rows, key=lambda r: r[5])
    print(f"\nIN-SAMPLE best: EMA{best_is[1]}/{best_is[2]}  (IS {best_is[4]:.0f}%) -> its OOS = {best_is[5]:.0f}%")
    print(f"OUT-SAMPLE best: EMA{best_oos[1]}/{best_oos[2]}  (OOS {best_oos[5]:.0f}%) -> its IS = {best_oos[4]:.0f}%")
    print("If these are DIFFERENT pairs, fine-tuning on the past does NOT pick the future winner = overfitting.")


if __name__ == "__main__":
    main()
