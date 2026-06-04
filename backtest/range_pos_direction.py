"""Range-position vs forward-return analysis — multiple lookback windows.

For each bar, compute range_pos = (close − rolling_low) / (rolling_high − rolling_low) × 100
across multiple lookbacks (1d, 3d, 7d, 14d, 30d). Then measure the FORWARD return
(next 24h close vs current close).

Buckets price into LOW (<20), MID-LOW (20-40), MID (40-60), MID-HIGH (60-80), HIGH (>80).

Reports per-bucket per-lookback:
  - Mean forward return %
  - LONG WR (forward > 0%)
  - SHORT WR (forward < 0%)
  - Median forward
  - Std dev (how noisy)

Directional rule extraction:
  - If LOW position → high LONG WR + positive mean = LONG bias
  - If HIGH position → high SHORT WR + negative mean = SHORT bias
  - If pattern reverses (momentum regime), opposite direction
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd
import numpy as np

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "data" / "cache"

# Lookbacks in 15m bars
LOOKBACKS = {
    "1d":  96,
    "3d":  288,
    "7d":  672,
    "14d": 1344,
    "30d": 2880,
}

# Forward windows to measure (also in 15m bars)
FWD_WINDOWS = {
    "1h":  4,
    "4h":  16,
    "24h": 96,
}

# Buckets (range_pos)
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
    return df.sort_values("timestamp").reset_index(drop=True)


def main():
    print("Loading 6.7y BTC 15m data...")
    df = load()
    print(f"  {len(df):,} bars  ({df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]})\n")

    h, l, c = df["high"], df["low"], df["close"]

    # Compute range_pos for each lookback
    for lb_name, lb_bars in LOOKBACKS.items():
        rh = h.rolling(lb_bars).max()
        rl = l.rolling(lb_bars).min()
        df[f"rp_{lb_name}"] = (c - rl) / (rh - rl) * 100

    # Compute forward returns
    for fw_name, fw_bars in FWD_WINDOWS.items():
        fwd_close = c.shift(-fw_bars)
        df[f"fwd_{fw_name}"] = (fwd_close - c) / c * 100

    df_clean = df.dropna(subset=[f"rp_{lb}" for lb in LOOKBACKS] +
                                [f"fwd_{fw}" for fw in FWD_WINDOWS]).reset_index(drop=True)
    print(f"Bars usable: {len(df_clean):,}\n")

    # ── PRIMARY ANALYSIS: matrix of (lookback × bucket × fwd window) ──
    for fw_name in FWD_WINDOWS:
        print("=" * 90)
        print(f" RANGE_POS  vs  FORWARD {fw_name} RETURN")
        print("=" * 90)
        print(f"{'Lookback':<8} {'Bucket':<16} {'n':>6} {'meanFwd%':>9} {'medFwd%':>9} {'LONG WR%':>9} {'SHORT WR%':>10} {'edge':>8}")
        print("-" * 90)
        for lb_name in LOOKBACKS:
            for lo, hi, label in BUCKETS:
                col = f"rp_{lb_name}"
                fcol = f"fwd_{fw_name}"
                sub = df_clean[(df_clean[col] >= lo) & (df_clean[col] < hi)][fcol]
                if len(sub) < 100:
                    continue
                mean_fwd = sub.mean()
                med_fwd = sub.median()
                long_wr = (sub > 0).mean() * 100
                short_wr = (sub < 0).mean() * 100
                # Edge = |mean| / std (signal-to-noise)
                edge = mean_fwd / sub.std() * 100 if sub.std() > 0 else 0
                print(f"{lb_name:<8} {label:<16} {len(sub):>6,} {mean_fwd:>+8.3f}% {med_fwd:>+8.3f}% {long_wr:>8.1f}% {short_wr:>9.1f}% {edge:>+7.2f}")
            print()

    # ── BEST DIRECTIONAL EDGES (24h fwd) ──
    print("=" * 90)
    print(" RANKED DIRECTIONAL EDGES (24h forward)")
    print(" Edge = mean_forward / std × 100 (higher = stronger signal-to-noise)")
    print("=" * 90)
    edges = []
    for lb_name in LOOKBACKS:
        for lo, hi, label in BUCKETS:
            col = f"rp_{lb_name}"
            fcol = "fwd_24h"
            sub = df_clean[(df_clean[col] >= lo) & (df_clean[col] < hi)][fcol]
            if len(sub) < 200:
                continue
            mean = sub.mean()
            std = sub.std()
            edge = mean / std * 100 if std > 0 else 0
            long_wr = (sub > 0).mean() * 100
            edges.append({
                "lookback": lb_name, "bucket": label,
                "n": len(sub), "mean": mean, "std": std,
                "edge": edge, "long_wr": long_wr,
                "direction": "LONG" if mean > 0 else "SHORT",
            })
    edges_df = pd.DataFrame(edges).sort_values("edge", key=abs, ascending=False)
    print(f"\n{'Rank':<5} {'Lookback':<8} {'Bucket':<16} {'n':>6} {'dir':<5} {'meanFwd%':>9} {'LONG WR%':>9} {'edge':>8}")
    print("-" * 80)
    for i, r in enumerate(edges_df.head(15).itertuples(index=False), 1):
        print(f"{i:<5} {r.lookback:<8} {r.bucket:<16} {r.n:>6,} {r.direction:<5} {r.mean:>+8.3f}% {r.long_wr:>8.1f}% {r.edge:>+7.2f}")

    # ── EDGE PATTERN: mean-reversion vs momentum at extremes ──
    print("\n" + "=" * 90)
    print(" EXTREMES INTERPRETATION (24h fwd)")
    print(" LOW + positive mean = MEAN-REVERSION (bounce). LOW + negative = MOMENTUM (breakdown).")
    print("=" * 90)
    for lb_name in LOOKBACKS:
        col = f"rp_{lb_name}"
        low_sub = df_clean[df_clean[col] < 10]["fwd_24h"]
        high_sub = df_clean[df_clean[col] > 90]["fwd_24h"]
        if len(low_sub) < 50 or len(high_sub) < 50: continue
        low_mean = low_sub.mean()
        high_mean = high_sub.mean()
        low_dir = "BOUNCE (LONG)" if low_mean > 0 else "BREAKDOWN (SHORT)"
        high_dir = "REVERSAL (SHORT)" if high_mean < 0 else "BREAKOUT (LONG)"
        print(f"\n  {lb_name:<8} VERY-LOW (<10):  n={len(low_sub):>5,}  mean={low_mean:+.3f}%  → {low_dir}")
        print(f"  {lb_name:<8} VERY-HIGH (>90): n={len(high_sub):>5,}  mean={high_mean:+.3f}%  → {high_dir}")


if __name__ == "__main__":
    main()
