"""Range-position vs forward-return analysis — DAY TRADING focus.

Day trading: enters during day, exits at EOD (20:00 UTC) or via TP/SL within ~5-12h.
Relevant lookbacks:
  - 1d (yesterday's range, what SR DCA already uses)
  - 3d (recent context)
  - intraday (today's developing range — how far has price come today)

Forward windows that match day-trade hold periods:
  - 1h, 2h, 4h, 6h, "to_eod" (close at next 20:00 UTC)

Reports per-bucket × lookback × forward.
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd
import numpy as np

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "data" / "cache"

# Day-trading-relevant lookbacks (15m bars)
LOOKBACKS = {
    "1d":  96,
    "3d":  288,
}

# Forward windows matching day-trade hold periods
FWD_WINDOWS = {
    "1h":  4,
    "2h":  8,
    "4h":  16,
    "6h":  24,
}

BUCKETS = [
    (0,   20,  "LOW    (0-20)"),
    (20,  40,  "M-LOW  (20-40)"),
    (40,  60,  "MID    (40-60)"),
    (60,  80,  "M-HIGH (60-80)"),
    (80,  100, "HIGH   (80-100)"),
]


def load():
    df = pd.read_csv(CACHE / "BTCUSDT_15m.csv")
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["hour"] = df["timestamp"].dt.hour
    df["date"] = df["timestamp"].dt.date
    return df


def main():
    print("Loading 6.7y BTC 15m data...")
    df = load()
    print(f"  {len(df):,} bars\n")

    h, l, c = df["high"], df["low"], df["close"]

    # Rolling N-day range_pos (1d, 3d)
    for lb_name, lb_bars in LOOKBACKS.items():
        rh = h.rolling(lb_bars).max()
        rl = l.rolling(lb_bars).min()
        df[f"rp_{lb_name}"] = (c - rl) / (rh - rl) * 100

    # Intraday range_pos (today's H/L so far, from 00:00 UTC)
    # For each bar, compute today's H/L from all bars with same date earlier
    df_sorted = df.sort_values("timestamp").reset_index(drop=True)
    df_sorted["today_h_sofar"] = df_sorted.groupby("date")["high"].cummax()
    df_sorted["today_l_sofar"] = df_sorted.groupby("date")["low"].cummin()
    df_sorted["rp_intraday"] = (df_sorted["close"] - df_sorted["today_l_sofar"]) / (df_sorted["today_h_sofar"] - df_sorted["today_l_sofar"]) * 100
    df = df_sorted

    # Forward returns
    for fw_name, fw_bars in FWD_WINDOWS.items():
        fwd_close = c.shift(-fw_bars)
        df[f"fwd_{fw_name}"] = (fwd_close - c) / c * 100

    needed = [f"rp_{lb}" for lb in LOOKBACKS] + ["rp_intraday"] + [f"fwd_{fw}" for fw in FWD_WINDOWS]
    df_clean = df.dropna(subset=needed).reset_index(drop=True)
    # Drop intraday rp where today's range is degenerate (first bar of day)
    df_clean = df_clean[df_clean["today_h_sofar"] > df_clean["today_l_sofar"]].reset_index(drop=True)
    print(f"Usable bars: {len(df_clean):,}\n")

    # Per-lookback, per-bucket, per-forward
    all_lookbacks = list(LOOKBACKS.keys()) + ["intraday"]
    for fw_name in FWD_WINDOWS:
        print("=" * 96)
        print(f"  FORWARD {fw_name}  — range_pos vs return  (day-trading focus)")
        print("=" * 96)
        print(f"{'Lookback':<10} {'Bucket':<16} {'n':>6} {'mean%':>8} {'med%':>8} {'LONG WR%':>9} {'SHORT WR%':>10} {'edge':>7}")
        print("-" * 88)
        for lb_name in all_lookbacks:
            col = f"rp_{lb_name}"
            fcol = f"fwd_{fw_name}"
            for lo, hi, label in BUCKETS:
                sub = df_clean[(df_clean[col] >= lo) & (df_clean[col] < hi)][fcol]
                if len(sub) < 200:
                    continue
                mean_fwd = sub.mean()
                med_fwd = sub.median()
                long_wr = (sub > 0).mean() * 100
                short_wr = (sub < 0).mean() * 100
                edge = mean_fwd / sub.std() * 100 if sub.std() > 0 else 0
                print(f"{lb_name:<10} {label:<16} {len(sub):>6,} {mean_fwd:>+7.3f}% {med_fwd:>+7.3f}% {long_wr:>8.1f}% {short_wr:>9.1f}% {edge:>+6.2f}")
            print()

    # ── BEST EDGES (4h forward, the typical day-trade hold) ──
    print("=" * 88)
    print(" TOP 15 EDGES (4h forward — typical day-trade hold)")
    print("=" * 88)
    edges = []
    for lb_name in all_lookbacks:
        col = f"rp_{lb_name}"
        fcol = "fwd_4h"
        for lo, hi, label in BUCKETS:
            sub = df_clean[(df_clean[col] >= lo) & (df_clean[col] < hi)][fcol]
            if len(sub) < 200: continue
            mean = sub.mean(); std = sub.std()
            edges.append({
                "lb": lb_name, "bucket": label, "n": len(sub),
                "mean": mean, "long_wr": (sub > 0).mean() * 100,
                "edge": mean / std * 100 if std > 0 else 0,
                "dir": "LONG" if mean > 0 else "SHORT",
            })
    edges_df = pd.DataFrame(edges).sort_values("edge", key=abs, ascending=False)
    print(f"\n{'Rank':<5} {'LB':<10} {'Bucket':<16} {'n':>6} {'Dir':<5} {'mean4h%':>9} {'WR%':>6} {'edge':>7}")
    print("-" * 75)
    for i, r in enumerate(edges_df.head(15).itertuples(index=False), 1):
        print(f"{i:<5} {r.lb:<10} {r.bucket:<16} {r.n:>6,} {r.dir:<5} {r.mean:>+8.3f}% {r.long_wr:>5.1f}% {r.edge:>+6.2f}")

    # ── DIRECTIONAL RULES ──
    print("\n" + "=" * 88)
    print(" DIRECTIONAL CONCLUSIONS")
    print("=" * 88)
    for lb_name in all_lookbacks:
        col = f"rp_{lb_name}"
        print(f"\n  ─── {lb_name} range_pos × forward 4h ───")
        for lo, hi, label in BUCKETS:
            sub = df_clean[(df_clean[col] >= lo) & (df_clean[col] < hi)]["fwd_4h"]
            if len(sub) < 200: continue
            mean = sub.mean()
            wr_l = (sub > 0).mean() * 100
            wr_s = (sub < 0).mean() * 100
            recommendation = "LONG" if mean > 0.05 else ("SHORT" if mean < -0.05 else "AVOID/NEUTRAL")
            print(f"    {label:<16} mean={mean:+.3f}%  LONG_WR={wr_l:.1f}%  SHORT_WR={wr_s:.1f}%  → {recommendation}")


if __name__ == "__main__":
    main()
