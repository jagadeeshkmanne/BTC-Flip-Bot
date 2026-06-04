"""For each bar during the day, simulate entry LONG and SHORT, hold to EOD (20:00 UTC).
Bucket by range_pos against 1d/2d/3d daily H/L envelope.

This answers: "where in the N-day range should we enter at any time during the day?"
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd
import numpy as np

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "data" / "cache"

EOD_HOUR = 20

BUCKETS = [
    (0, 10, "VERY LOW (0-10)"),
    (10, 20, "LOW (10-20)"),
    (20, 40, "M-LOW (20-40)"),
    (40, 60, "MID (40-60)"),
    (60, 80, "M-HIGH (60-80)"),
    (80, 90, "HIGH (80-90)"),
    (90, 100, "VERY HIGH (90-100)"),
]


def load():
    df = pd.read_csv(CACHE / "BTCUSDT_15m.csv")
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["date"] = df["timestamp"].dt.date
    df["hour"] = df["timestamp"].dt.hour
    return df


def build_daily(df):
    daily = df.groupby("date").agg(d_high=("high", "max"), d_low=("low", "min"), d_close=("close", "last")).reset_index()
    daily["date"] = pd.to_datetime(daily["date"])
    # N-day rolling high/low (shifted so today uses ONLY past N completed days)
    for n in [1, 2, 3]:
        daily[f"h_{n}d"] = daily["d_high"].rolling(n).max().shift(1)
        daily[f"l_{n}d"] = daily["d_low"].rolling(n).min().shift(1)
    return daily


def main():
    print("Loading 6.7y BTC 15m data...")
    df = load()
    daily = build_daily(df)
    print(f"  {len(df):,} bars, {len(daily):,} days\n")

    # Map daily H/L by date back into each 5m bar
    df["date_pd"] = pd.to_datetime(df["date"])
    for n in [1, 2, 3]:
        h_map = dict(zip(daily["date"], daily[f"h_{n}d"]))
        l_map = dict(zip(daily["date"], daily[f"l_{n}d"]))
        df[f"h_{n}d"] = df["date_pd"].map(h_map)
        df[f"l_{n}d"] = df["date_pd"].map(l_map)
        df[f"rp_{n}d"] = (df["close"] - df[f"l_{n}d"]) / (df[f"h_{n}d"] - df[f"l_{n}d"]) * 100

    # EOD close: for each bar, find the NEXT bar at hour=20:00 (or last bar of next 24h)
    # Simpler: shift forward and find first bar with hour >= EOD_HOUR
    # Compute next-day's EOD close for hold-to-EOD exit
    daily["next_eod_close"] = None
    # For each date, EOD close = close at 19:45 UTC (last bar before 20:00) on the SAME date
    # if entered during the day, OR if entered after 20:00, use next day's EOD close
    eod_per_date = df[df["hour"] == 19].groupby("date")["close"].last()
    # Drop bars without an EOD on the day (e.g. last day of data)
    df["eod_close_same_day"] = df["date"].map(eod_per_date.to_dict())

    # For trades entered during the day (hour < EOD_HOUR), exit at eod_close_same_day
    # For trades entered after EOD (hour >= EOD_HOUR), exit at next day's EOD (skip for simplicity)
    df_in_day = df[df["hour"] < EOD_HOUR].copy()
    df_in_day = df_in_day.dropna(subset=["eod_close_same_day", "rp_1d", "rp_2d", "rp_3d"]).reset_index(drop=True)

    df_in_day["pnl_long_pct"] = (df_in_day["eod_close_same_day"] - df_in_day["close"]) / df_in_day["close"] * 100
    df_in_day["pnl_short_pct"] = -df_in_day["pnl_long_pct"]

    print(f"Usable entry-bars: {len(df_in_day):,}\n")

    # Report
    for lb in ["1d", "2d", "3d"]:
        col = f"rp_{lb}"
        print("=" * 96)
        print(f"  {lb.upper()} RANGE — entry at any time during day, hold to 19:45 UTC (EOD close)")
        print("=" * 96)
        print(f"{'Bucket':<22} {'n':>7} {'LONG WR%':>9} {'LONG mean':>10} {'LONG med':>10}  {'SHORT WR%':>9} {'SHORT mean':>11}")
        print("-" * 95)
        for lo, hi, label in BUCKETS:
            sub = df_in_day[(df_in_day[col] >= lo) & (df_in_day[col] < hi)]
            if len(sub) < 200:
                continue
            long_wr = (sub["pnl_long_pct"] > 0).mean() * 100
            long_mean = sub["pnl_long_pct"].mean()
            long_med = sub["pnl_long_pct"].median()
            short_wr = (sub["pnl_short_pct"] > 0).mean() * 100
            short_mean = sub["pnl_short_pct"].mean()
            print(f"{label:<22} {len(sub):>7,} {long_wr:>8.1f}% {long_mean:>+9.3f}% {long_med:>+9.3f}%  {short_wr:>8.1f}% {short_mean:>+10.3f}%")
        print()

    # Ranked best entries (by mean × WR signal)
    print("=" * 96)
    print(" RANKED — best entry zones across 1d/2d/3d (held to EOD)")
    print("=" * 96)
    rows = []
    for lb in ["1d", "2d", "3d"]:
        col = f"rp_{lb}"
        for lo, hi, label in BUCKETS:
            sub = df_in_day[(df_in_day[col] >= lo) & (df_in_day[col] < hi)]
            if len(sub) < 500: continue
            for side in ["LONG", "SHORT"]:
                pnl_col = "pnl_long_pct" if side == "LONG" else "pnl_short_pct"
                wr = (sub[pnl_col] > 0).mean() * 100
                mean = sub[pnl_col].mean()
                med = sub[pnl_col].median()
                # Edge score: combine WR and mean magnitude
                if mean > 0:
                    rows.append({"lb": lb, "bucket": label, "side": side, "n": len(sub),
                                 "wr": wr, "mean": mean, "med": med})
    rdf = pd.DataFrame(rows).sort_values("mean", ascending=False)
    print(f"{'Rank':<5} {'LB':<3} {'Bucket':<22} {'Dir':<6} {'n':>6} {'WR%':>6} {'mean%':>8} {'med%':>8}")
    print("-" * 70)
    for i, r in enumerate(rdf.head(15).itertuples(index=False), 1):
        print(f"{i:<5} {r.lb:<3} {r.bucket:<22} {r.side:<6} {r.n:>6,} {r.wr:>5.1f}% {r.mean:>+7.3f}% {r.med:>+7.3f}%")


if __name__ == "__main__":
    main()
