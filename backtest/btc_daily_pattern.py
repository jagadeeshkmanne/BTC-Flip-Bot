"""Analyze BTC daily H/L/M behavior to find actionable patterns for SR DCA / divflip.

Questions:
1. How often does today touch prev_H / prev_L / prev_mid (within 0.05% zone)?
2. After touching prev_H, does price REVERSE (go down) or BREAK OUT (continue up)?
3. After touching prev_L, does price REVERSE (bounce) or CONTINUE (break down)?
4. What % of days hit BOTH prev_H and prev_L (range days)?
5. How does prev-day RANGE size relate to today's outcome?
6. Time-of-day patterns: when do touches usually happen?

Uses 6.7y of 15m BTC bars. Outputs actionable stats.
"""
from __future__ import annotations
from pathlib import Path
import pandas as pd
import numpy as np

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "data" / "cache"

TOUCH_ZONE = 0.0005   # 0.05% zone


def load():
    df = pd.read_csv(CACHE / "BTCUSDT_15m.csv")
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["date"] = df["timestamp"].dt.date
    df["hour"] = df["timestamp"].dt.hour
    return df


def build_daily(df):
    daily = df.groupby("date").agg(
        d_open=("open", "first"),
        d_high=("high", "max"),
        d_low=("low", "min"),
        d_close=("close", "last"),
        vol=("volume", "sum"),
    ).reset_index()
    daily["d_mid"] = (daily["d_high"] + daily["d_low"]) / 2
    daily["d_range_pct"] = (daily["d_high"] - daily["d_low"]) / daily["d_low"] * 100
    daily["prev_h"] = daily["d_high"].shift(1)
    daily["prev_l"] = daily["d_low"].shift(1)
    daily["prev_mid"] = daily["d_mid"].shift(1)
    daily["prev_range_pct"] = daily["d_range_pct"].shift(1)
    daily["d_day"] = pd.to_datetime(daily["date"]).dt.day_name()
    return daily


def analyze_day(day_bars, prev_h, prev_l, prev_mid):
    """For a single day, find first touches and what price did after."""
    if pd.isna(prev_h) or pd.isna(prev_l):
        return None

    result = {
        "touched_h": False, "touch_h_hour": None, "h_after_24h": None,
        "touched_l": False, "touch_l_hour": None, "l_after_24h": None,
        "touched_mid": False,
    }

    # Find first touches
    h_touched = False
    l_touched = False
    mid_touched = False
    h_touch_idx = None
    l_touch_idx = None

    for i, row in day_bars.reset_index(drop=True).iterrows():
        if not h_touched and row["high"] >= prev_h * (1 - TOUCH_ZONE) and row["high"] < prev_h * (1 + 0.01):
            h_touched = True
            h_touch_idx = i
            result["touch_h_hour"] = int(row["hour"])
        if not l_touched and row["low"] <= prev_l * (1 + TOUCH_ZONE) and row["low"] > prev_l * (1 - 0.01):
            l_touched = True
            l_touch_idx = i
            result["touch_l_hour"] = int(row["hour"])
        # Mid zone
        if not mid_touched:
            in_mid = abs(row["close"] - prev_mid) / prev_mid < TOUCH_ZONE * 2
            if in_mid:
                mid_touched = True

    result["touched_h"] = h_touched
    result["touched_l"] = l_touched
    result["touched_mid"] = mid_touched

    # After touching H, what did price do over next 24h (from the touch bar)?
    if h_touched and h_touch_idx is not None:
        bars_after_h = day_bars.reset_index(drop=True).iloc[h_touch_idx:]
        # Did price reverse (go down) or break out (go up) by next 24h end?
        if not bars_after_h.empty:
            touch_px = day_bars.reset_index(drop=True).iloc[h_touch_idx]["high"]
            end_px = bars_after_h.iloc[-1]["close"]
            min_px = bars_after_h["low"].min()
            max_px = bars_after_h["high"].max()
            result["h_after_24h"] = (end_px - touch_px) / touch_px * 100
            result["h_max_up_pct"] = (max_px - touch_px) / touch_px * 100   # breakout
            result["h_max_down_pct"] = (min_px - touch_px) / touch_px * 100  # reversal

    if l_touched and l_touch_idx is not None:
        bars_after_l = day_bars.reset_index(drop=True).iloc[l_touch_idx:]
        if not bars_after_l.empty:
            touch_px = day_bars.reset_index(drop=True).iloc[l_touch_idx]["low"]
            end_px = bars_after_l.iloc[-1]["close"]
            min_px = bars_after_l["low"].min()
            max_px = bars_after_l["high"].max()
            result["l_after_24h"] = (end_px - touch_px) / touch_px * 100
            result["l_max_up_pct"] = (max_px - touch_px) / touch_px * 100   # reversal (bounce)
            result["l_max_down_pct"] = (min_px - touch_px) / touch_px * 100  # breakdown

    return result


def main():
    print("Loading 6.7y BTC 15m data...")
    df = load()
    daily = build_daily(df)
    print(f"  {len(daily):,} days  ({daily['date'].iloc[0]} → {daily['date'].iloc[-1]})\n")

    # Q1: prev range distribution
    print("=" * 70)
    print("Q1: prev-day RANGE distribution")
    print("=" * 70)
    pr = daily["prev_range_pct"].dropna()
    print(f"  mean={pr.mean():.2f}%  median={pr.median():.2f}%  std={pr.std():.2f}%")
    print(f"  p10={pr.quantile(0.1):.2f}%  p25={pr.quantile(0.25):.2f}%  p75={pr.quantile(0.75):.2f}%  p90={pr.quantile(0.9):.2f}%")

    # Q2: today range vs prev range correlation
    today_r = daily["d_range_pct"].iloc[1:]
    prev_r = daily["prev_range_pct"].iloc[1:]
    corr = today_r.corr(prev_r)
    print(f"\n  Correlation today_range vs prev_range: {corr:.3f}")
    # Tight prev day → big today move?
    tight_days = daily[daily["prev_range_pct"] < 1.5].dropna(subset=["d_range_pct"])
    wide_days = daily[daily["prev_range_pct"] > 4.0].dropna(subset=["d_range_pct"])
    if len(tight_days) > 5:
        print(f"  After tight prev (<1.5%): today avg range = {tight_days['d_range_pct'].mean():.2f}%  (n={len(tight_days)})")
    if len(wide_days) > 5:
        print(f"  After wide  prev (>4.0%): today avg range = {wide_days['d_range_pct'].mean():.2f}%  (n={len(wide_days)})")

    # Q3-5: per-day touch analysis
    print("\n" + "=" * 70)
    print("Q2-4: TOUCH PATTERNS — how often does today touch prev levels?")
    print("=" * 70)
    results = []
    day_groups = df.groupby("date")
    daily_map = dict(zip(daily["date"], zip(daily["prev_h"], daily["prev_l"], daily["prev_mid"], daily["d_day"])))
    for date, bars in day_groups:
        if date not in daily_map: continue
        prev_h, prev_l, prev_mid, day_name = daily_map[date]
        r = analyze_day(bars, prev_h, prev_l, prev_mid)
        if r is None: continue
        r["date"] = date
        r["day_name"] = day_name
        r["prev_range_pct"] = daily.set_index("date").loc[date, "prev_range_pct"] if date in daily["date"].values else None
        results.append(r)

    rdf = pd.DataFrame(results)
    n = len(rdf)
    print(f"\nTotal days analyzed: {n:,}")
    print(f"  Touched prev_H:   {rdf['touched_h'].sum():>5d} = {rdf['touched_h'].mean()*100:5.1f}%")
    print(f"  Touched prev_L:   {rdf['touched_l'].sum():>5d} = {rdf['touched_l'].mean()*100:5.1f}%")
    print(f"  Touched prev_mid: {rdf['touched_mid'].sum():>5d} = {rdf['touched_mid'].mean()*100:5.1f}%")
    print(f"  Touched BOTH H+L (range day):  {(rdf['touched_h'] & rdf['touched_l']).sum():>5d} = {(rdf['touched_h'] & rdf['touched_l']).mean()*100:5.1f}%")
    print(f"  Touched NEITHER (trend day):   {((~rdf['touched_h']) & (~rdf['touched_l'])).sum():>5d} = {((~rdf['touched_h']) & (~rdf['touched_l'])).mean()*100:5.1f}%")

    # Q5: When prev_H touched, does it REVERSE (SHORT works) or BREAK OUT (LONG works)?
    print("\n" + "=" * 70)
    print("Q5: AFTER touching prev_H — does it REVERSE or BREAK OUT?")
    print("=" * 70)
    h_touched_df = rdf[rdf["touched_h"]].dropna(subset=["h_after_24h"])
    if len(h_touched_df) > 0:
        rev = (h_touched_df["h_after_24h"] < -0.5).sum()
        flat = ((h_touched_df["h_after_24h"] >= -0.5) & (h_touched_df["h_after_24h"] <= 0.5)).sum()
        breakout = (h_touched_df["h_after_24h"] > 0.5).sum()
        total = len(h_touched_df)
        print(f"  Out of {total:,} prev_H touches:")
        print(f"    REVERSED (down >0.5%):   {rev:>5d} ({rev/total*100:5.1f}%)  — SHORT wins")
        print(f"    FLAT (-0.5 to +0.5%):    {flat:>5d} ({flat/total*100:5.1f}%)  — chop")
        print(f"    BREAKOUT (up >0.5%):     {breakout:>5d} ({breakout/total*100:5.1f}%)  — LONG wins (against SR)")
        print(f"  Mean move after touch: {h_touched_df['h_after_24h'].mean():+.3f}%")
        print(f"  Median: {h_touched_df['h_after_24h'].median():+.3f}%")
        # Max bounce vs max breakout
        print(f"  Mean MAX bounce down (reversal): {h_touched_df['h_max_down_pct'].mean():.3f}%")
        print(f"  Mean MAX continuation up:        {h_touched_df['h_max_up_pct'].mean():+.3f}%")

    # Q6: Same for prev_L touches
    print("\n" + "=" * 70)
    print("Q6: AFTER touching prev_L — does it BOUNCE or BREAKDOWN?")
    print("=" * 70)
    l_touched_df = rdf[rdf["touched_l"]].dropna(subset=["l_after_24h"])
    if len(l_touched_df) > 0:
        bounce = (l_touched_df["l_after_24h"] > 0.5).sum()
        flat = ((l_touched_df["l_after_24h"] >= -0.5) & (l_touched_df["l_after_24h"] <= 0.5)).sum()
        breakdown = (l_touched_df["l_after_24h"] < -0.5).sum()
        total = len(l_touched_df)
        print(f"  Out of {total:,} prev_L touches:")
        print(f"    BOUNCED (up >0.5%):      {bounce:>5d} ({bounce/total*100:5.1f}%)  — LONG wins")
        print(f"    FLAT (-0.5 to +0.5%):    {flat:>5d} ({flat/total*100:5.1f}%)  — chop")
        print(f"    BREAKDOWN (down >0.5%):  {breakdown:>5d} ({breakdown/total*100:5.1f}%)  — SHORT wins (against SR)")
        print(f"  Mean move after touch: {l_touched_df['l_after_24h'].mean():+.3f}%")
        print(f"  Median: {l_touched_df['l_after_24h'].median():+.3f}%")
        print(f"  Mean MAX bounce up (reversal): {l_touched_df['l_max_up_pct'].mean():+.3f}%")
        print(f"  Mean MAX continuation down:    {l_touched_df['l_max_down_pct'].mean():.3f}%")

    # Q7: prev range size predicts touch behavior?
    print("\n" + "=" * 70)
    print("Q7: Does prev RANGE SIZE predict reversal vs breakout?")
    print("=" * 70)
    print("\n  prev_H touch outcome by prev range bucket:")
    print(f"  {'prev_range':<15} {'n':>5} {'reversal_rate':>14} {'mean_move':>11}")
    for lo, hi, label in [(0, 1.5, "tight (<1.5%)"), (1.5, 3.0, "normal 1.5-3%"), (3.0, 5.0, "wide 3-5%"), (5.0, 100, "extreme (>5%)")]:
        sub = h_touched_df[(rdf.loc[h_touched_df.index, "prev_range_pct"] >= lo) &
                          (rdf.loc[h_touched_df.index, "prev_range_pct"] < hi)]
        if len(sub) > 5:
            rev_rate = (sub["h_after_24h"] < -0.5).mean() * 100
            mean_move = sub["h_after_24h"].mean()
            print(f"  {label:<15} {len(sub):>5d} {rev_rate:>13.1f}% {mean_move:>+10.3f}%")

    print("\n  prev_L touch outcome by prev range bucket:")
    print(f"  {'prev_range':<15} {'n':>5} {'bounce_rate':>13} {'mean_move':>11}")
    for lo, hi, label in [(0, 1.5, "tight (<1.5%)"), (1.5, 3.0, "normal 1.5-3%"), (3.0, 5.0, "wide 3-5%"), (5.0, 100, "extreme (>5%)")]:
        sub = l_touched_df[(rdf.loc[l_touched_df.index, "prev_range_pct"] >= lo) &
                          (rdf.loc[l_touched_df.index, "prev_range_pct"] < hi)]
        if len(sub) > 5:
            bounce_rate = (sub["l_after_24h"] > 0.5).mean() * 100
            mean_move = sub["l_after_24h"].mean()
            print(f"  {label:<15} {len(sub):>5d} {bounce_rate:>12.1f}% {mean_move:>+10.3f}%")

    # Q8: Touch hour distribution
    print("\n" + "=" * 70)
    print("Q8: TOUCH HOUR distribution (when do touches happen?)")
    print("=" * 70)
    h_hour = rdf[rdf["touched_h"]]["touch_h_hour"].dropna().astype(int)
    l_hour = rdf[rdf["touched_l"]]["touch_l_hour"].dropna().astype(int)
    print("\n  Hour | prev_H touches | prev_L touches")
    print("  " + "-"*42)
    for h in range(24):
        h_count = (h_hour == h).sum()
        l_count = (l_hour == h).sum()
        bar_h = "█" * int(h_count / max(h_hour.value_counts().max(), 1) * 20)
        bar_l = "▓" * int(l_count / max(l_hour.value_counts().max(), 1) * 20)
        session = "ASIA" if h < 7 else ("EU" if h < 13 else "US" if h < 20 else "LATE")
        print(f"  {h:>2}h  | {h_count:>4d} {bar_h:<22} | {l_count:>4d} {bar_l:<22} {session}")

    # Q9: Day-of-week pattern
    print("\n" + "=" * 70)
    print("Q9: DAY-OF-WEEK pattern")
    print("=" * 70)
    print(f"  {'day':<11} {'days':>5} {'touch_H%':>10} {'touch_L%':>10} {'avg_range%':>11}")
    for dow in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]:
        sub = rdf[rdf["day_name"] == dow]
        if len(sub) == 0: continue
        avg_range = daily[daily["d_day"] == dow]["d_range_pct"].mean()
        print(f"  {dow:<11} {len(sub):>5d} {sub['touched_h'].mean()*100:>9.1f}% {sub['touched_l'].mean()*100:>9.1f}% {avg_range:>10.2f}%")


if __name__ == "__main__":
    main()
