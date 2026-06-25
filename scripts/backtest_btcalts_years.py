#!/usr/bin/env python3
"""backtest_btcalts_years.py — BTC-led ALT basket, year by year (fixed params, honest).

BTC slow-trend signal -> equal-weight ETH/BNB/SOL, LONG-ONLY, vol-scaled, 1h. FIXED params
(not re-optimised per year) so the per-year numbers are honest. Compares each year to holding
the alt basket and to holding BTC -- to see whether it earns its keep every year or only in
down years (the demand-zone trap).
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALTS = ["ETHUSDT", "BNBUSDT", "SOLUSDT"]
F, S = 32, 800           # fixed BTC signal (slow trend, the WF-favoured region)


def load(sym):
    for nm in (f"{sym}_1h_binance.csv", f"{sym}_1h_binance_full.csv"):
        p = os.path.join(HERE, "data/cache", nm)
        if os.path.exists(p):
            return pd.read_csv(p, parse_dates=["timestamp"])
    raise FileNotFoundError(sym)


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def alt_returns(btc, frames, longonly=True, volscale=True):
    base = (ema(btc["close"], F) > ema(btc["close"], S)).astype(float).values
    pos = base if longonly else (base * 2 - 1)
    if volscale:
        rv = btc["close"].pct_change().rolling(24).std().bfill().values
        pos = pos * np.clip(np.nanmedian(rv) / (rv + 1e-9), 0.2, 1.0)
    sig = pd.Series(pos, index=btc.index)
    rets = []
    for a in ALTS:
        held = sig.shift(1).fillna(0).values
        oo = (frames[a]["open"].shift(-1) / frames[a]["open"] - 1).fillna(0).values
        turn = np.abs(np.diff(held, prepend=0.0))
        rets.append(held * oo - turn * (FEE_PCT + SLIP_PCT))
    return np.mean(rets, axis=0)


def yr_metrics(r):
    eq = np.cumprod(1 + np.nan_to_num(r))
    if len(eq) < 5:
        return 0, 0, 0
    net = (eq[-1] - 1) * 100
    dd = ((eq / np.maximum.accumulate(eq)) - 1).min() * 100
    return net, dd, (net / abs(dd) if dd < -1e-9 else 0.0)


def main():
    raw = {s: load(s) for s in (["BTCUSDT"] + ALTS)}
    common = max(df["timestamp"].iloc[0] for df in raw.values())
    raw = {s: df[df["timestamp"] >= common].reset_index(drop=True) for s, df in raw.items()}
    n = min(len(df) for df in raw.values())
    raw = {s: df.iloc[:n].reset_index(drop=True) for s, df in raw.items()}
    btc = raw["BTCUSDT"]; yr = btc["timestamp"].dt.year.values
    alt_bh = np.mean([(raw[a]["open"].shift(-1) / raw[a]["open"] - 1).fillna(0).values for a in ALTS], axis=0)
    btc_bh = (btc["open"].shift(-1) / btc["open"] - 1).fillna(0).values

    r_lo = alt_returns(btc, raw, longonly=True, volscale=True)
    r_ls = alt_returns(btc, raw, longonly=False, volscale=True)

    print("=" * 84)
    print(f"BTC-LED ALT BASKET (BTC EMA{F}/{S} -> eqw ETH/BNB/SOL), year by year — FIXED params")
    print("=" * 84)
    print(f"  {'year':<6}{'LONG-only%':>11}{'DD%':>7}{'rDD':>6}   {'L+S%':>8}   {'altHODL%':>10}{'btcHODL%':>10}")
    comp_lo = comp_ls = comp_alt = comp_btc = 1.0; green = 0; ny = 0
    for y in sorted(set(yr)):
        m = yr == y
        if m.sum() < 200:
            continue
        ny += 1
        nlo, dlo, rlo = yr_metrics(r_lo[m]); nls, _, _ = yr_metrics(r_ls[m])
        nab, _, _ = yr_metrics(alt_bh[m]); nbt, _, _ = yr_metrics(btc_bh[m])
        comp_lo *= 1 + nlo / 100; comp_ls *= 1 + nls / 100; comp_alt *= 1 + nab / 100; comp_btc *= 1 + nbt / 100
        green += int(nlo > 0)
        print(f"  {y:<6}{nlo:>11.1f}{dlo:>7.1f}{rlo:>6.2f}   {nls:>8.1f}   {nab:>10.1f}{nbt:>10.1f}")
    print("-" * 84)
    print(f"  {'comp':<6}{(comp_lo-1)*100:>11.0f}{'':>13}{(comp_ls-1)*100:>8.0f}   {(comp_alt-1)*100:>10.0f}{(comp_btc-1)*100:>10.0f}")
    print(f"  green years: {green}/{ny} (LONG-only)")


if __name__ == "__main__":
    main()
