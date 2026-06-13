#!/usr/bin/env python3
"""momo_mtf_overlay.py — does a 1h/15m execution layer raise momo's daily rate?

Hypothesis: MOMO v1's leverage ceiling is set by intra-trade MAE (worst 17.8%).
A faster-TF gate that steps aside during crashes would cut MAE and unlock
higher leverage — IF the whipsaw + fee cost doesn't eat the edge first.

Variants (all decisions on closed bars, fills next bar open, real costs):
  BASE      — daily momo signal only (control, matches momo_lev_sweep)
  1H-EMA    — in market only while momo AND 1h EMA20>EMA50
  1H-SMA50  — in market only while momo AND 1h close>SMA50(1h)
  15M-EMA   — in market only while momo AND 15m EMA20>EMA50
Costs: perp taker 0.055%/side on notional, funding 0.01%/8h while long,
liquidation on intra-bar lows vs entry-based liq price, harvest 8% APR flat.
Daily momo signal built from resampled intraday closes (UTC days).
"""
import pandas as pd
import numpy as np

FEE, MMR = 0.00055, 0.005
FUND_PER_DAY, HARV_PER_DAY = 0.0003, 0.08 / 365

def wilder_rsi(close, n=14):
    d = close.diff()
    ag = d.clip(lower=0).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    return 100 - 100/(1 + ag/al)

def daily_signal(df):
    """momo signal per UTC day from intraday closes (close>SMA200 & RSI14>70 in 7d)."""
    df = df.copy()
    df["day"] = pd.to_datetime(df["timestamp"]).dt.floor("D")
    d = df.groupby("day")["close"].last()
    sma = d.rolling(200).mean()
    rsi = wilder_rsi(d)
    sig = (d > sma) & (rsi.rolling(7).max() > 70)
    return sig  # indexed by day; signal of day X usable from day X+1

def run(csv, gate_kind, L, bars_per_day):
    df = pd.read_csv(csv)
    sig = daily_signal(df)
    df["day"] = pd.to_datetime(df["timestamp"]).dt.floor("D")
    # daily signal from the PREVIOUS completed day
    df["momo"] = df["day"].map(sig.shift(1)).fillna(False).astype(bool)

    c = df["close"]
    if gate_kind == "BASE":
        gate = pd.Series(True, index=df.index)
    elif gate_kind == "EMA":
        gate = c.ewm(span=20, adjust=False).mean() > c.ewm(span=50, adjust=False).mean()
    elif gate_kind == "SMA50":
        gate = c > c.rolling(50).mean()
    want = (df["momo"] & gate).shift(1).fillna(False).values  # closed-bar decision
    o, h, lo, cl = df["open"].values, df["high"].values, df["low"].values, c.values

    eq, peak, maxdd, pos, trades, liqs = 5000.0, 5000.0, 0.0, None, 0, 0
    fund_h = FUND_PER_DAY / bars_per_day
    harv_h = HARV_PER_DAY / bars_per_day
    maes, cur_mae_low = [], None
    n = len(df)
    start = 201 * bars_per_day
    for i in range(start, n):
        if pos is None and want[i] and eq > 1:
            entry = o[i]
            notional = eq * L
            qty = notional / entry
            eq -= notional * FEE
            pos = {"e": entry, "m": eq, "q": qty, "liq": entry * (1 - 1/L + MMR)}
            trades += 1
            cur_mae_low = lo[i]
        if pos is not None:
            cur_mae_low = min(cur_mae_low, lo[i])
            if lo[i] <= pos["liq"]:
                eq, pos, liqs = 0.0, None, liqs + 1
                maes.append(1 - cur_mae_low / 0 if False else np.nan)
            elif not want[i] and pos["e"] != o[i]:
                px = o[i]
                eq = pos["m"] + pos["q"] * (px - pos["e"]) - pos["q"] * px * FEE
                maes.append(1 - cur_mae_low / pos["e"])
                pos = None
        if pos is not None:
            fund = pos["q"] * cl[i] * fund_h
            pos["m"] -= fund
            eq = max(pos["m"] + pos["q"] * (cl[i] - pos["e"]), 0.0)
            if eq <= 0:
                pos, liqs = None, liqs + 1
        elif eq > 0:
            eq *= 1 + harv_h
        peak = max(peak, eq)
        maxdd = max(maxdd, 1 - eq / peak if peak > 0 else 0)
        if eq <= 0:
            break
    days = (n - start) / bars_per_day
    g = (eq / 5000.0) ** (1 / max(days, 1)) - 1 if eq > 0 else -1
    worst_mae = max([m for m in maes if not np.isnan(m)], default=0)
    return (f"g/day {g*100:7.4f}% | maxDD {maxdd*100:5.1f}% | trades {trades:4d} | "
            f"liqs {liqs} | worst MAE {worst_mae*100:5.1f}%")

CSV_1H = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_1h.csv"
CSV_15M = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_15m.csv"
import sys
df15 = pd.read_csv(CSV_15M)
print(f"1h data thru 2026-04; 15m: {df15['timestamp'].iloc[0]} .. {df15['timestamp'].iloc[-1]} ({len(df15)} bars)")
for label, kind, csv, bpd in [("BASE(1h px)", "BASE", CSV_1H, 24),
                              ("1H-EMA20/50", "EMA", CSV_1H, 24),
                              ("1H-SMA50", "SMA50", CSV_1H, 24),
                              ("15M-EMA20/50", "EMA", CSV_15M, 96)]:
    for L in [2, 3, 5]:
        print(f"{label:>13} L={L}: {run(csv, kind, L, bpd)}")
    print()
