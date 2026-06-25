#!/usr/bin/env python3
"""backtest_topN_basket.py — BTC-led LONG/SHORT basket of the top N alts (N=3/10/15/20).

BTC slow-trend signal (32/800, vol-scaled) -> applied long/short to an equal-weight basket of
the top N alts. Tests whether a wider basket (10/15/20) beats the 3-coin one, long-only vs
long+short. Per-bar basket return = mean over the alts that HAVE data that bar (so early
history isn't thrown away).

CAVEAT printed in output: 'top coins as of 2026' = SURVIVORSHIP BIAS (LUNA/FTT-style blowups
are excluded), so live forward returns will be lower than this backtest.

Honest: signal CLOSED bar, fill next open, fee 0.055%/side + 0.05% slip on turnover, walk-forward.
"""
from __future__ import annotations
import os, time
import numpy as np
import pandas as pd
import requests

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]

# ordered ~by established market cap / liquidity (alts only; BTC is the signal)
ALTS = ["ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT", "DOTUSDT",
        "LINKUSDT", "TRXUSDT", "AVAXUSDT", "LTCUSDT", "BCHUSDT", "ATOMUSDT", "XLMUSDT",
        "ETCUSDT", "FILUSDT", "MATICUSDT", "NEARUSDT", "UNIUSDT", "AAVEUSDT"]


def fetch_1h(sym, start="2021-01-01"):
    cache = os.path.join(HERE, "data/cache", f"{sym}_1h_binance.csv")
    if sym == "BTCUSDT":
        cache = os.path.join(HERE, "data/cache", "BTCUSDT_1h_binance_full.csv")
    if os.path.exists(cache):
        return pd.read_csv(cache, parse_dates=["timestamp"])
    cur = int(pd.Timestamp(start).timestamp() * 1000); rows, ok = [], None
    while True:
        p = {"symbol": sym, "interval": "1h", "startTime": cur, "limit": 1000}
        data = None
        for h in ([ok] if ok else HOSTS):
            try:
                r = requests.get(f"{h}/api/v3/klines", params=p, timeout=20)
                if r.status_code == 200:
                    data = r.json(); ok = h; break
            except Exception:
                continue
        if not data:
            break
        rows.extend(data)
        if len(data) < 1000:
            break
        cur = data[-1][0] + 1; time.sleep(0.06)
    if not rows:
        return None
    seen = {int(x[0]): x for x in rows}; rows = [seen[k] for k in sorted(seen)]
    df = pd.DataFrame({"timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
                       "open": [float(x[1]) for x in rows], "high": [float(x[2]) for x in rows],
                       "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows]})
    df.to_csv(cache, index=False)
    return df


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def met(r, bpy=24 * 365.25):
    r = np.nan_to_num(np.asarray(r))
    eq = np.cumprod(1 + r)
    if eq[-1] <= 0:
        return -1, -1, -1
    dd = ((eq / np.maximum.accumulate(eq)) - 1).min()
    cagr = eq[-1] ** (1 / max(len(r) / bpy, 1e-9)) - 1
    return cagr, dd, (cagr / abs(dd) if dd < -1e-9 else 0.0)


def main():
    print("Fetching top-cap coins (cached after first run)...")
    btc = fetch_1h("BTCUSDT")
    frames = {}
    for a in ALTS:
        d = fetch_1h(a)
        if d is not None and len(d) > 5000:
            frames[a] = d
    avail = list(frames.keys())
    print(f"got {len(avail)} alts: {', '.join(x[:-4] for x in avail)}")

    # master 1h timeline from BTC
    base_idx = btc["timestamp"]
    btc = btc.set_index("timestamp")
    F, S = 32, 800
    rv = btc["close"].pct_change().rolling(24).std().bfill()
    vscale = np.clip(np.nanmedian(rv) / (rv + 1e-9), 0.2, 1.0)
    bull = (ema(btc["close"], F) > ema(btc["close"], S)).astype(float)

    # build per-alt strat returns aligned to BTC timeline (NaN where no data)
    def alt_strat(df, longonly):
        df = df.set_index("timestamp").reindex(base_idx)
        pos = (bull.values if longonly else (bull.values * 2 - 1)) * vscale.values
        held = pd.Series(pos, index=base_idx).shift(1).fillna(0).values
        oo = (df["open"].shift(-1) / df["open"] - 1).values
        valid = ~np.isnan(oo)
        turn = np.abs(np.diff(held, prepend=0.0))
        r = np.where(valid, held * oo - turn * (FEE_PCT + SLIP_PCT), np.nan)
        return r

    yr = base_idx.dt.year.values
    print("\n" + "=" * 80)
    print("BTC-LED TOP-N ALT BASKET (BTC 32/800 signal, vol-scaled) — basket size sweep")
    print("=" * 80)
    print("  *** SURVIVORSHIP BIAS: these are 2026's survivors; forward returns will be LOWER ***\n")
    for longonly in (True, False):
        tag = "LONG-only" if longonly else "LONG+SHORT"
        strat = {a: alt_strat(frames[a], longonly) for a in avail}
        print(f"  [{tag}]   {'N':>3}{'CAGR':>8}{'maxDD':>8}{'ret/DD':>8}   {'WF ret/DD':>10}")
        for N in (3, 10, 15, 20):
            sub = avail[:N]
            mat = np.vstack([strat[a] for a in sub])              # N x bars, NaN where absent
            basket = np.nanmean(mat, axis=0)                      # per-bar mean over available
            c, d, rd = met(basket)
            # walk-forward on basket: re-opt BTC EMA each fold
            wf = walk_forward_basket(btc, frames, sub, base_idx, vscale, longonly)
            print(f"           {N:>3}{c*100:>7.0f}%{d*100:>7.0f}%{rd:>8.2f}   {wf:>10.2f}")
        print()


def walk_forward_basket(btc, frames, sub, base_idx, vscale, longonly, train=8000, test=2000):
    grid = [(f, s) for f in (20, 32, 50) for s in (400, 600, 800)]
    series = {}
    for (f, s) in grid:
        bull = (ema(btc["close"], f) > ema(btc["close"], s)).astype(float)
        pos = (bull.values if longonly else (bull.values * 2 - 1)) * vscale.values
        held = pd.Series(pos, index=base_idx).shift(1).fillna(0).values
        rr = []
        for a in sub:
            df = frames[a].set_index("timestamp").reindex(base_idx)
            oo = (df["open"].shift(-1) / df["open"] - 1).values
            turn = np.abs(np.diff(held, prepend=0.0))
            rr.append(np.where(~np.isnan(oo), held * oo - turn * (FEE_PCT + SLIP_PCT), np.nan))
        series[(f, s)] = np.nanmean(np.vstack(rr), axis=0)
    n = len(base_idx); out = np.zeros(n); mask = np.zeros(n, bool); start = train
    while start + test <= n:
        best, bp = -9, None
        for p in grid:
            _, _, r = met(series[p][start - train:start])
            if r > best:
                best, bp = r, p
        out[start:start + test] = np.nan_to_num(series[bp][start:start + test]); mask[start:start + test] = True
        start += test
    return met(out[mask])[2]


if __name__ == "__main__":
    main()
