#!/usr/bin/env python3
"""short_gainers_listings.py — honest event study for two user-proposed shorts:

  A) NEW LISTINGS: short a freshly listed Bybit perp at the close of its first
     full daily bar; exit N days later (close-to-close).
  B) FADE TOP GAINERS: short any perp at the daily close after it gained
     >= +15% / +30% in one day; exit N days later (close-to-close).

Universe: ALL Bybit USDT linear perps (daily klines, up to 1000 bars each).
Costs: taker 0.055%/side + 0.02% slip/side = 0.15% per round trip.
NOT modeled (both hurt shorts): funding (new/pumping pairs often have deeply
negative funding — shorts PAY, sometimes >1%/day) and borrow/liquidity limits.
So results below are an UPPER bound for the short side.

Short return for horizon h: r = (entry_close / exit_close - 1) - costs.
No leverage, no compounding — pure per-event edge measurement.
"""
from __future__ import annotations
import sys, time
import numpy as np
import pandas as pd
import requests

BASE = "https://api.bybit.com"
COST = 0.0015                       # round-trip fees+slip
HORIZONS = [1, 2, 3, 7, 14]
GAIN_THRESHOLDS = [0.15, 0.30]


def all_perps() -> list[str]:
    r = requests.get(f"{BASE}/v5/market/tickers",
                     params={"category": "linear"}, timeout=15)
    rows = r.json()["result"]["list"]
    syms = [t["symbol"] for t in rows
            if t["symbol"].endswith("USDT") and "-" not in t["symbol"]]
    return sorted(syms)


def daily(sym: str) -> pd.DataFrame | None:
    try:
        r = requests.get(f"{BASE}/v5/market/kline",
                         params={"category": "linear", "symbol": sym,
                                 "interval": "D", "limit": 1000}, timeout=15)
        rows = r.json().get("result", {}).get("list", [])
        if not rows:
            return None
        rows = list(reversed(rows))
        df = pd.DataFrame([{"ts": pd.to_datetime(int(k[0]), unit="ms"),
                            "open": float(k[1]), "high": float(k[2]),
                            "low": float(k[3]), "close": float(k[4])}
                           for k in rows])
        return df.iloc[:-1]          # drop forming daily bar
    except Exception:
        return None


def stats(rets: list[float]) -> str:
    if not rets:
        return "  (no events)"
    a = np.array(rets)
    return (f"N={len(a):>5}  mean {a.mean()*100:+6.2f}%  med {np.median(a)*100:+6.2f}%  "
            f"win {(a > 0).mean()*100:5.1f}%  p5 {np.percentile(a,5)*100:+7.2f}%  "
            f"worst {a.min()*100:+8.2f}%")


def main() -> None:
    syms = all_perps()
    print(f"{len(syms)} USDT perps")
    listings = {h: [] for h in HORIZONS}          # A
    gainers = {(g, h): [] for g in GAIN_THRESHOLDS for h in HORIZONS}  # B
    control = {h: [] for h in HORIZONS}           # C: short ANY coin ANY day
    list_by_q = {}                                # listing events bucketed by quarter
    pump_by_q = {}                                # pump>=30% 3d-exit, by quarter
    n_listed = 0

    for i, s in enumerate(syms):
        df = daily(s)
        if df is None or len(df) < 3:
            continue
        c = df["close"].values
        # A) new listing: first bar visible only if history < the 1000-bar window
        if len(df) < 990:
            n_listed += 1
            for h in HORIZONS:
                if 1 + h < len(c):
                    r = c[1] / c[1 + h] - 1 - COST
                    listings[h].append(r)
                    if h == 7:
                        q = str(pd.Timestamp(df['ts'].iloc[1]).to_period('Q'))
                        list_by_q.setdefault(q, []).append(r)
        # B) fade top gainers
        ret1d = c[1:] / c[:-1] - 1
        for g in GAIN_THRESHOLDS:
            idx = np.where(ret1d >= g)[0] + 1     # event day index (close used as entry)
            for j in idx:
                for h in HORIZONS:
                    if j + h < len(c):
                        r = c[j] / c[j + h] - 1 - COST
                        gainers[(g, h)].append(r)
                        if g == 0.30 and h == 3:
                            q = str(pd.Timestamp(df['ts'].iloc[j]).to_period('Q'))
                            pump_by_q.setdefault(q, []).append(r)
        # C) control: short this coin on EVERY day (sampled every 3rd day to bound N)
        for j in range(1, len(c), 3):
            for h in HORIZONS:
                if j + h < len(c):
                    control[h].append(c[j] / c[j + h] - 1 - COST)
        if (i + 1) % 100 == 0:
            print(f"  …{i+1}/{len(syms)}")
        time.sleep(0.03)

    print(f"\nA) SHORT NEW LISTINGS (entry close of first full day; {n_listed} "
          f"listings in window) — gross of funding:")
    for h in HORIZONS:
        print(f"  exit +{h:>2}d: {stats(listings[h])}")
    print("\n   by listing quarter (7d horizon, mean short ret / N):")
    for q in sorted(list_by_q):
        a = np.array(list_by_q[q])
        print(f"     {q}: {a.mean()*100:+6.2f}%  (N={len(a)})")

    print("\nB) SHORT AFTER 1-DAY PUMP (entry at event-day close) — gross of funding:")
    for g in GAIN_THRESHOLDS:
        print(f"  pump >= +{g*100:.0f}%:")
        for h in HORIZONS:
            print(f"    exit +{h:>2}d: {stats(gainers[(g, h)])}")

    print("\nC) CONTROL — short ANY coin on ANY day (every 3rd day sampled):")
    for h in HORIZONS:
        print(f"  exit +{h:>2}d: {stats(control[h])}")
    print("\n   EXCESS of pump-fade over control (mean, per horizon):")
    for g in GAIN_THRESHOLDS:
        line = f"     pump>=+{g*100:.0f}%: "
        for h in HORIZONS:
            ev, ct = np.array(gainers[(g, h)]), np.array(control[h])
            line += f" +{h}d {(ev.mean()-ct.mean())*100:+.2f}% "
        print(line)

    print("\n   pump >= +30%, 3d exit, by quarter (mean / N):")
    for q in sorted(pump_by_q):
        a = np.array(pump_by_q[q])
        print(f"     {q}: {a.mean()*100:+6.2f}%  (N={len(a)})")


if __name__ == "__main__":
    main()
