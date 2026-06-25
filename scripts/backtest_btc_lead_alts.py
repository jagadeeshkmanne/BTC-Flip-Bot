#!/usr/bin/env python3
"""backtest_btc_lead_alts.py — use BTC's trend as the SIGNAL, trade the higher-beta ALTS.

User's thesis: BTC leads; alts amplify ("if BTC down, alts down more"). So take BTC's
(cleaner) trend signal and apply it long/short to ETH/BNB/SOL (higher beta) — BTC's signal
quality + alt amplitude. Tests whether that beats (a) trading BTC on its own signal, and
(b) trading each alt on its OWN signal.

Signal: BTC EMA(fast) vs EMA(slow) -> +1 long / -1 short (reverse, always in market).
Targets: ETH, BNB, SOL individually + equal-weight alt basket, all driven by the BTC signal.

Honesty: signal CLOSED bar, fill NEXT open (open-to-open), fee 0.055%/side + 0.05% slip on
turnover, 60/40 OOS. Data: Binance 1h (cached), 4h resampled.
"""
from __future__ import annotations
import os
import pandas as pd
import numpy as np

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALTS = ["ETHUSDT", "BNBUSDT", "SOLUSDT"]


def load(symbol):
    for name in (f"{symbol}_1h_binance.csv", f"{symbol}_1h_binance_full.csv"):
        p = os.path.join(HERE, "data/cache", name)
        if os.path.exists(p):
            return pd.read_csv(p, parse_dates=["timestamp"])
    raise FileNotFoundError(symbol)


def to_tf(df, tf):
    if tf == "1h":
        return df
    return df.set_index("timestamp").resample(tf).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def strat_returns(signal, target_df):
    """Apply a +/-1 signal (decided at close[t]) to a target coin's open-to-open returns."""
    held = signal.shift(1).reindex(target_df.index).fillna(0)
    oo = (target_df["open"].shift(-1) / target_df["open"] - 1).fillna(0).values
    turn = held.diff().abs().fillna(held.abs()).values
    return held.values * oo - turn * (FEE_PCT + SLIP_PCT)


def metrics(eq, idx, cut_ts):
    s = pd.Series(eq, index=idx).replace([np.inf, -np.inf], np.nan).dropna()
    def m(e):
        if len(e) < 30: return (0, 0, 0)
        yrs = max((e.index[-1] - e.index[0]).days / 365.25, 1e-9)
        cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1
        dd = (e / e.cummax() - 1).min()
        return cagr, dd, (cagr / abs(dd) if dd < -1e-9 else 0.0)
    return m(s), m(s[s.index >= cut_ts])


def main():
    raw = {s: load(s) for s in (["BTCUSDT"] + ALTS)}
    start = max(df["timestamp"].iloc[0] for df in raw.values())
    raw = {s: df[df["timestamp"] >= start].reset_index(drop=True) for s, df in raw.items()}

    for tf in ("4h", "1h"):
        frames = {s: to_tf(df, tf).set_index("timestamp") for s, df in raw.items()}
        # align all on BTC's index
        btc = frames["BTCUSDT"]
        idx = pd.to_datetime(btc.index)
        cut_ts = idx[int(len(idx) * 0.6)]
        span = f"{idx[0]:%Y-%m-%d}->{idx[-1]:%Y-%m-%d}"
        print("\n" + "=" * 100)
        print(f"BTC-LED ALTS on {tf}  ({span}, OOS {cut_ts:%Y-%m-%d})")
        print("=" * 100)

        # pick best BTC EMA pair by OOS ret/DD of the alt basket
        fasts = [20, 32, 50] if tf == "1h" else [8, 13, 21]
        slows = [400, 600, 800] if tf == "1h" else [100, 150, 200]
        best = None
        for f in fasts:
            for s in slows:
                sig = (ema(btc["close"], f) > ema(btc["close"], s)).astype(float) * 2 - 1
                alt_rets = np.mean([strat_returns(sig, frames[a]) for a in ALTS], axis=0)
                eq = (1 + pd.Series(alt_rets, index=idx).fillna(0)).cumprod()
                mis, _ = metrics(eq[idx < cut_ts].values, idx[idx < cut_ts], cut_ts)  # select on IN-SAMPLE only
                if best is None or mis[2] > best[0]:
                    best = (mis[2], f, s)
        _, bf, bs = best
        sig = (ema(btc["close"], bf) > ema(btc["close"], bs)).astype(float) * 2 - 1
        print(f"  Best BTC signal EMA pair (chosen on IN-SAMPLE only): {bf}/{bs}\n")
        print(f"  {'driver -> target':<34}{'CAGR':>8}{'DD':>6}{'r/DD':>6}   {'oCAGR':>7}{'oDD':>6}{'or/DD':>7}")

        def show(label, rets):
            eq = (1 + pd.Series(rets, index=idx).fillna(0)).cumprod()
            m, mo = metrics(eq.values, idx, cut_ts)
            print(f"  {label:<34}{m[0]*100:>7.0f}%{m[1]*100:>5.0f}%{m[2]:>6.2f}   {mo[0]*100:>6.0f}%{mo[1]*100:>5.0f}%{mo[2]:>7.2f}")

        # BTC signal applied to each target
        for a in ALTS:
            show(f"BTC sig -> {a[:-4]}", strat_returns(sig, frames[a]))
        show("BTC sig -> ALT BASKET (eqw)", np.mean([strat_returns(sig, frames[a]) for a in ALTS], axis=0))
        show("BTC sig -> BTC (baseline)", strat_returns(sig, btc))

        # comparison: each alt on its OWN signal
        print("  " + "-" * 70)
        for a in ALTS:
            own = (ema(frames[a]["close"], bf) > ema(frames[a]["close"], bs)).astype(float) * 2 - 1
            show(f"{a[:-4]} own sig -> {a[:-4]}", strat_returns(own, frames[a]))


if __name__ == "__main__":
    main()
