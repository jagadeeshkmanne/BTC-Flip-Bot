#!/usr/bin/env python3
"""screener.py — multi-pair crypto screener (Bybit USDT perps), two modes.

SWING mode (--swing, 4h): the validated v3 trend criteria, identical math to
bot/bot_v3_trend.py (OOS 2023-26 +262%@1x / Sharpe 1.82):
  LONG when EMA30 > EMA150 AND close > EMA50 AND ADX14 > 20 (+ BTC leader gate)

DAY mode (default, 15m): faster enter/exit for intraday — surfaces movers:
  LONG  when EMA20 > EMA50  AND close > EMA20  AND ADX14 > 20
  SHORT when EMA20 < EMA50  AND close < EMA20  AND ADX14 > 20
  plus context: volume surge vs 1-day median, 1h rate-of-change, RSI14.
  Suggested exit = price closes back through the 15m EMA20 (signal-off);
  typical hold = hours.

  ⚠ HONESTY NOTE (FINDINGS): every intraday STRATEGY honestly tested in this
  repo lost after taker fees (150-combo 5m sweep: 0 profitable, 0 gross edge
  even at zero fees; edge decays monotonically below 1D). Day mode is a
  candidate-surfacing tool for discretionary trading — it is NOT a validated
  auto-tradeable edge. The swing (4h) criteria are the only validated set.

"FRESH/NEW" = signal flipped on within the last FRESH_BARS closed bars
(a new opportunity rather than a trend you'd be chasing late).

Usage:
  python3 screener.py                # day mode, top 30 perps by 24h turnover
  python3 screener.py -n 50          # top 50
  python3 screener.py --swing        # validated 4h v3 criteria
  python3 screener.py --pairs BTCUSDT ETHUSDT SOLUSDT
"""
from __future__ import annotations
import argparse
import logging
import os
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot"))
from bybit_data import fetch_klines, BYBIT_BASE, CATEGORY

MODES = {
    # validated v3 set — keep identical to bot/bot_v3_trend.py
    "swing": dict(interval="4h", ema_fast=30, ema_slow=150, ema_px=50,
                  fresh_bars=3, bars=400, leader_gate=True),
    # intraday mover-surfacing — NOT a validated edge (see module docstring)
    "day":   dict(interval="15m", ema_fast=20, ema_slow=50, ema_px=20,
                  fresh_bars=2, bars=500, leader_gate=False),
}
ADX_LEN, ADX_MIN = 14, 20.0

log = logging.getLogger("screener")
log.setLevel(logging.WARNING)
log.addHandler(logging.StreamHandler(sys.stderr))


def adx_series(df: pd.DataFrame, n: int) -> pd.Series:
    import numpy as np
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus = pd.Series(((up > dn) & (up > 0)) * up, index=df.index).fillna(0.0)
    minus = pd.Series(((dn > up) & (dn > 0)) * dn, index=df.index).fillna(0.0)
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / n, adjust=False).mean()
    pdi = 100 * plus.ewm(alpha=1.0 / n, adjust=False).mean() / atr
    mdi = 100 * minus.ewm(alpha=1.0 / n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / n, adjust=False).mean()


def rsi_series(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    gain = d.clip(lower=0).ewm(alpha=1.0 / n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1.0 / n, adjust=False).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - 100 / (1 + rs)


def top_perps_by_turnover(n: int) -> list[dict]:
    """All USDT linear perps, ranked by 24h turnover, top n."""
    r = requests.get(f"{BYBIT_BASE}/v5/market/tickers",
                     params={"category": CATEGORY}, timeout=15)
    r.raise_for_status()
    rows = r.json()["result"]["list"]
    perps = [t for t in rows
             if t["symbol"].endswith("USDT") and "-" not in t["symbol"]]
    perps.sort(key=lambda t: float(t.get("turnover24h") or 0), reverse=True)
    return perps[:n]


def evaluate(df: pd.DataFrame, m: dict) -> dict | None:
    """Long/short state on the last CLOSED bar + intraday context columns."""
    if df is None or len(df) < m["ema_slow"] + 10:
        return None
    df = df.iloc[:-1]                                   # drop the forming bar
    c = df["close"]
    ef = c.ewm(span=m["ema_fast"], adjust=False, min_periods=m["ema_fast"]).mean()
    es = c.ewm(span=m["ema_slow"], adjust=False, min_periods=m["ema_slow"]).mean()
    ep = c.ewm(span=m["ema_px"], adjust=False, min_periods=m["ema_px"]).mean()
    ax = adx_series(df, ADX_LEN)
    rsi = rsi_series(c)
    long_s = (ef > es) & (c > ep) & (ax > ADX_MIN)
    short_s = (ef < es) & (c < ep) & (ax > ADX_MIN)

    def bars_on(sig: pd.Series) -> int:
        cnt = 0
        for v in sig.iloc[::-1]:
            if not bool(v):
                break
            cnt += 1
        return cnt

    i = -1
    if pd.isna(es.iloc[i]) or pd.isna(ax.iloc[i]):
        return None
    # context: volume surge vs trailing 1-day median, 1h rate of change
    day_bars = 96 if m["interval"] == "15m" else 6
    vol_med = df["volume"].rolling(day_bars).median().iloc[i]
    vol_surge = float(df["volume"].iloc[i] / vol_med) if vol_med and vol_med > 0 else 0.0
    roc_bars = 4 if m["interval"] == "15m" else 1
    roc = (float(c.iloc[i]) / float(c.iloc[i - roc_bars]) - 1) * 100
    return {
        "close": float(c.iloc[i]), "adx": float(ax.iloc[i]),
        "rsi": float(rsi.iloc[i]) if pd.notna(rsi.iloc[i]) else 50.0,
        "long": bool(long_s.iloc[i]), "short": bool(short_s.iloc[i]),
        "long_bars": bars_on(long_s), "short_bars": bars_on(short_s),
        "dist_px_pct": (float(c.iloc[i]) / float(ep.iloc[i]) - 1) * 100,
        "vol_surge": vol_surge, "roc_1h": roc,
        "bar_time": str(pd.Timestamp(df.iloc[i]["timestamp"])),
    }


def main() -> None:
    p = argparse.ArgumentParser(description="crypto screener (Bybit perps)")
    p.add_argument("-n", type=int, default=30, help="top N perps by 24h turnover")
    p.add_argument("--swing", action="store_true",
                   help="validated 4h v3 criteria instead of 15m day mode")
    p.add_argument("--pairs", nargs="*", help="explicit symbol list instead of top-N")
    args = p.parse_args()
    mode = "swing" if args.swing else "day"
    m = MODES[mode]

    if args.pairs:
        tickers = [{"symbol": s.upper(), "price24hPcnt": "0"} for s in args.pairs]
    else:
        tickers = top_perps_by_turnover(args.n)
    chg24 = {t["symbol"]: float(t.get("price24hPcnt") or 0) * 100 for t in tickers}
    symbols = [t["symbol"] for t in tickers]
    if "BTCUSDT" not in symbols:
        symbols.insert(0, "BTCUSDT")                     # leader / market context

    results = {}
    for s in symbols:
        r = evaluate(fetch_klines(m["interval"], m["bars"], s, log), m)
        if r is not None:
            results[s] = r
        time.sleep(0.05)                                 # stay friendly to the API

    btc = results.get("BTCUSDT")
    if btc is None:
        sys.exit("BTCUSDT data unavailable")
    btc_long = btc["long"]

    rows = []
    for s, r in results.items():
        gated_long = r["long"] and (not m["leader_gate"] or s == "BTCUSDT" or btc_long)
        if gated_long:
            sig, bars = "LONG", r["long_bars"]
        elif r["long"]:                                  # blocked only by leader gate
            sig, bars = "long*", r["long_bars"]
        elif r["short"]:
            sig, bars = "SHORT" if mode == "day" else "short~", r["short_bars"]
        else:
            sig, bars = "—", 0
        fresh = 0 < bars <= m["fresh_bars"]
        rows.append({"sym": s, "sig": sig, "fresh": fresh, "bars": bars, **r,
                     "chg24": chg24.get(s, 0.0)})

    order = {"LONG": 0, "long*": 1, "SHORT": 2, "short~": 2, "—": 3}
    rows.sort(key=lambda r: (order[r["sig"]], not r["fresh"], -r["adx"]))

    tf = m["interval"]
    btc_state = "LONG" if btc["long"] else ("SHORT" if btc["short"] else "FLAT")
    print(f"\n{mode.upper()} screener ({tf}) — closed bar {btc['bar_time']} UTC | "
          f"BTC: {btc_state} (ADX {btc['adx']:.1f}, RSI {btc['rsi']:.0f})\n")
    hdr = (f"{'SYMBOL':<14}{'SIGNAL':<8}{'FRESH':<7}{'BARS':>5}  {'PRICE':>12}"
           f"{'ADX':>7}{'RSI':>6}{'vsEMA':>8}{'1hROC':>8}{'VOLx':>6}{'24h%':>8}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['sym']:<14}{r['sig']:<8}{'NEW' if r['fresh'] else '':<7}"
              f"{r['bars'] or '':>5}  {r['close']:>12,.4g}{r['adx']:>7.1f}"
              f"{r['rsi']:>6.0f}{r['dist_px_pct']:>+7.2f}%{r['roc_1h']:>+7.2f}%"
              f"{r['vol_surge']:>6.1f}{r['chg24']:>+7.2f}%")
    n_long = sum(1 for r in rows if r["sig"] == "LONG")
    n_short = sum(1 for r in rows if r["sig"] in ("SHORT", "short~"))
    if mode == "day":
        print(f"\n{n_long} LONG / {n_short} SHORT of {len(rows)} scanned (15m). "
              f"Exit idea: close back through 15m EMA20 = signal off. "
              f"NEW = flipped within last {m['fresh_bars']} bars (≤30 min). "
              f"⚠ informational — no validated intraday edge after fees (FINDINGS).")
    else:
        print(f"\n{n_long} LONG of {len(rows)} scanned | long* = blocked by BTC "
              f"leader gate | short~ = info only — shorts failed 9/9 honest tests.")


if __name__ == "__main__":
    main()
