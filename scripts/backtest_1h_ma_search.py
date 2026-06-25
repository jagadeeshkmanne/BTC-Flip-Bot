#!/usr/bin/env python3
"""backtest_1h_ma_search.py — exhaustive search for the BEST MA strategy on BTC 1h.

Sweeps EMA *and* SMA across many period combinations and 6 strategy families, ranks by
OUT-OF-SAMPLE risk-adjusted return. Answers "what is the best moving-average strategy on
the 1h timeframe?" with numbers, not opinion.

Strategy families (each tested with both EMA and SMA):
  cross        : MA(fast) > MA(slow)            -> long / flat
  cross_ls     : MA(fast) > MA(slow)            -> long / short (reverse)
  price        : close > MA(n)                  -> long / flat
  price_ls     : close > MA(n)                  -> long / short
  cross_f200   : cross AND close>MA200          -> long / flat   (200-trend filter)
  cross_f200_ls: cross & close>MA200 long; opp & close<MA200 short; else flat

Honesty: signal from CLOSED bar, position entered at NEXT bar OPEN (open-to-open returns),
fee 0.055%/side + 0.05% slippage charged on turnover, in-sample/out-of-sample 60/40 split.
Ranked by OOS ret/DD (CAGR ÷ |maxDD|) — the repo's risk-adjusted yardstick — vs buy&hold.

Data: Binance 1h since 2020 (cached to data/cache/ after first fetch).
"""
from __future__ import annotations
import os, time
import pandas as pd
import numpy as np
import requests

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "data/cache/BTCUSDT_1h_binance_full.csv")
HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]


def fetch_1h(start="2020-01-01"):
    if os.path.exists(CACHE):
        df = pd.read_csv(CACHE, parse_dates=["timestamp"])
        if (pd.Timestamp.utcnow().tz_localize(None) - df["timestamp"].iloc[-1]) < pd.Timedelta("2D"):
            return df
    cur = int(pd.Timestamp(start).timestamp() * 1000)
    rows, url_ok = [], None
    while True:
        params = {"symbol": "BTCUSDT", "interval": "1h", "startTime": cur, "limit": 1000}
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
        cur = data[-1][0] + 1; time.sleep(0.1)
    seen = {int(x[0]): x for x in rows}; rows = [seen[k] for k in sorted(seen)]
    df = pd.DataFrame({
        "timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
        "open": [float(x[1]) for x in rows], "high": [float(x[2]) for x in rows],
        "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows]})
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    df.to_csv(CACHE, index=False)
    return df


def ma(s, n, kind):
    return s.ewm(span=n, adjust=False).mean() if kind == "ema" else s.rolling(n).mean()


def evaluate(pos, oo_ret, idx):
    """Vectorized P&L. pos = target position decided at close[t] (entered at open[t+1]).
    oo_ret[t] = open[t+1]/open[t]-1 (return earned holding from this open to next open)."""
    held = pos.shift(1).fillna(0)               # position actually held over bar t's open->next open
    turn = held.diff().abs().fillna(held.abs())  # exposure changed at this open
    cost = turn * (FEE_PCT + SLIP_PCT)
    r = held * oo_ret - cost
    eq = (1 + r).cumprod()
    eq.index = idx
    return eq.dropna()


def met(eq):
    if len(eq) < 50:
        return None
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    return cagr, dd, (cagr / abs(dd) if dd < -1e-9 else 0.0)


def trades_of(pos):
    held = pos.shift(1).fillna(0)
    return int((held.diff().abs().fillna(held.abs()) > 0).sum())


def signal(df, fam, kind, fast, slow):
    c = df["close"]
    if fam in ("cross", "cross_ls"):
        s = (ma(c, fast, kind) > ma(c, slow, kind)).astype(float)
        return s if fam == "cross" else (2 * s - 1)
    if fam in ("price", "price_ls"):
        s = (c > ma(c, slow, kind)).astype(float)          # 'slow' carries the single-MA period
        return s if fam == "price" else (2 * s - 1)
    g = c > ma(c, 200, kind)
    up = ma(c, fast, kind) > ma(c, slow, kind)
    if fam == "cross_f200":
        return (up & g).astype(float)
    long_ = (up & g)
    short_ = (~up & ~g)
    return long_.astype(float) - short_.astype(float)


def main():
    df = fetch_1h()
    span = f"{df['timestamp'].iloc[0]:%Y-%m-%d} -> {df['timestamp'].iloc[-1]:%Y-%m-%d}"
    print("=" * 100)
    print(f"BTC 1h — BEST MOVING-AVERAGE STRATEGY SEARCH   ({len(df)} bars, {span})")
    print("Honest: next-open fills, 0.055%/side + 0.05% slip on turnover, 60/40 OOS. Ranked by OOS ret/DD.")
    print("=" * 100)

    oo_ret = (df["open"].shift(-1) / df["open"] - 1).fillna(0)
    idx = pd.to_datetime(df["timestamp"])
    cut_ts = idx.iloc[int(len(df) * 0.6)]    # timestamp-based OOS split (robust to dropped warmup rows)

    def oos(eq):
        return eq[eq.index >= cut_ts]

    # benchmark
    bh = (df["close"] / df["close"].iloc[0]); bh.index = idx
    bcagr, bdd, brr = met(bh)
    bo = met(oos(bh))
    print(f"\nBUY & HOLD: CAGR {bcagr*100:.0f}%/yr  maxDD {bdd*100:.0f}%  ret/DD {brr:.2f}  | "
          f"OOS CAGR {bo[0]*100:.0f}%  OOS ret/DD {bo[2]:.2f}  (split {cut_ts:%Y-%m-%d})")

    fasts = [5, 9, 10, 12, 13, 20, 21, 30, 50]
    slows = [20, 21, 30, 50, 100, 150, 200]
    singles = [20, 50, 100, 150, 200]

    results = []
    for kind in ("ema", "sma"):
        # cross families
        for fam in ("cross", "cross_ls", "cross_f200", "cross_f200_ls"):
            for f in fasts:
                for s in slows:
                    if f >= s:
                        continue
                    pos = signal(df, fam, kind, f, s)
                    eq = evaluate(pos, oo_ret, idx)
                    m = met(eq); mo = met(oos(eq))
                    if not m or not mo:
                        continue
                    results.append((fam, kind, f"{f}/{s}", m, mo, trades_of(pos)))
        # single-MA price families
        for fam in ("price", "price_ls"):
            for s in singles:
                pos = signal(df, fam, kind, 0, s)
                eq = evaluate(pos, oo_ret, idx)
                m = met(eq); mo = met(oos(eq))
                if not m or not mo:
                    continue
                results.append((fam, kind, f"{s}", m, mo, trades_of(pos)))

    results.sort(key=lambda x: x[4][2], reverse=True)   # by OOS ret/DD

    def show(title, rows):
        print(f"\n{title}")
        print(f"  {'family':<15}{'MA':<5}{'periods':<9}{'FULL CAGR':>10}{'DD':>7}{'ret/DD':>8}{'trades':>8}   {'OOS CAGR':>9}{'OOS DD':>7}{'OOS r/DD':>9}")
        for fam, kind, p, m, mo, n in rows:
            print(f"  {fam:<15}{kind:<5}{p:<9}{m[0]*100:>9.0f}%{m[1]*100:>6.0f}%{m[2]:>8.2f}{n:>8}   "
                  f"{mo[0]*100:>8.0f}%{mo[1]*100:>6.0f}%{mo[2]:>9.2f}")

    show("TOP 20 BY OOS ret/DD (risk-adjusted) — the answer:", results[:20])

    bycagr = sorted(results, key=lambda x: x[4][0], reverse=True)[:10]
    show("TOP 10 BY OOS CAGR (raw return):", bycagr)

    print("\nBest of each family (by OOS ret/DD):")
    seen = {}
    for row in results:
        if row[0] not in seen:
            seen[row[0]] = row
    show("", list(seen.values()))


if __name__ == "__main__":
    main()
