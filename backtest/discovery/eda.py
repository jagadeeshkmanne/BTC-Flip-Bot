"""EDA: find statistical patterns in 5y BTC data.

Goal: surface edges (hour-of-day, day-of-week, vol regime, gap behavior, etc.)
before building any strategy. No strategies yet — just describe the data.
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
DATA = REPO / "data" / "cache"


def load(tf: str) -> pd.DataFrame:
    df = pd.read_csv(DATA / f"BTCUSDT_{tf}.csv", parse_dates=["timestamp"])
    df = df.set_index("timestamp").astype(float)
    df["ret"] = df["close"].pct_change()
    return df


def summary(df: pd.DataFrame, tf: str):
    print(f"\n=== {tf} ===")
    print(f"rows: {len(df):,}  range: {df.index[0]} .. {df.index[-1]}")
    print(f"mean bar ret: {df['ret'].mean()*100:.4f}%  std: {df['ret'].std()*100:.3f}%")
    print(f"sharpe (annualized, bar): {df['ret'].mean()/df['ret'].std() * np.sqrt(365*24*60/int(tf.replace('m','').replace('h','60').replace('d','1440'))):.3f}")


def hour_of_day(df_5m: pd.DataFrame):
    print("\n=== Edge by hour-of-day (5m bars, UTC) ===")
    h = df_5m.copy()
    h["hour"] = h.index.hour
    g = h.groupby("hour")["ret"].agg(["mean", "std", "count"])
    g["sharpe_per_bar"] = g["mean"] / g["std"]
    g["mean_bps"] = g["mean"] * 10000
    print(g[["count", "mean_bps", "sharpe_per_bar"]].round(3))


def dow(df_1d: pd.DataFrame):
    print("\n=== Edge by day-of-week (1d bars) ===")
    d = df_1d.copy()
    d["dow"] = d.index.dayofweek  # Mon=0
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    g = d.groupby("dow")["ret"].agg(["mean", "std", "count"])
    g.index = [names[i] for i in g.index]
    g["mean_pct"] = g["mean"] * 100
    g["sharpe"] = g["mean"] / g["std"]
    g["positive_rate"] = d.groupby("dow")["ret"].apply(lambda x: (x > 0).mean()).values
    print(g[["count", "mean_pct", "sharpe", "positive_rate"]].round(3))


def session_blocks(df_1h: pd.DataFrame):
    print("\n=== Edge by session block (1h bars, UTC) ===")
    h = df_1h.copy()
    def session(hr):
        if 0 <= hr < 7:  return "Asia"
        if 7 <= hr < 13: return "EU_pre"
        if 13 <= hr < 21: return "US"
        return "Asia_close"
    h["sess"] = [session(t.hour) for t in h.index]
    g = h.groupby("sess")["ret"].agg(["mean", "std", "count"])
    g["mean_pct"] = g["mean"] * 100
    g["sharpe"] = g["mean"] / g["std"]
    print(g[["count", "mean_pct", "sharpe"]].round(4))


def vol_regime(df_1d: pd.DataFrame):
    print("\n=== Forward 5d return by current vol regime (1d) ===")
    d = df_1d.copy()
    d["vol20"] = d["ret"].rolling(20).std()
    d["fwd5"] = d["close"].pct_change(5).shift(-5)
    # bucket by vol quintile
    d["bucket"] = pd.qcut(d["vol20"], 5, labels=["Q1_low", "Q2", "Q3", "Q4", "Q5_high"])
    g = d.groupby("bucket", observed=True)["fwd5"].agg(["mean", "std", "count"])
    g["mean_pct"] = g["mean"] * 100
    g["sharpe"] = g["mean"] / g["std"]
    print(g[["count", "mean_pct", "sharpe"]].round(3))


def trend_regime(df_1d: pd.DataFrame):
    print("\n=== Forward 5d return by trend regime (1d EMA50/200) ===")
    d = df_1d.copy()
    d["ema50"] = d["close"].ewm(span=50, adjust=False).mean()
    d["ema200"] = d["close"].ewm(span=200, adjust=False).mean()
    d["trend"] = np.where(d["ema50"] > d["ema200"], "up", "down")
    d["fwd5"] = d["close"].pct_change(5).shift(-5)
    d["fwd20"] = d["close"].pct_change(20).shift(-20)
    g = d.groupby("trend")[["fwd5", "fwd20"]].agg(["mean", "std", "count"])
    print((g * 100).round(3))


def gap_behavior(df_1d: pd.DataFrame):
    print("\n=== After big up/down day, next-day mean return ===")
    d = df_1d.copy()
    d["next_ret"] = d["ret"].shift(-1)
    # bucket today's return
    d["bucket"] = pd.cut(
        d["ret"] * 100,
        bins=[-100, -5, -2, -0.5, 0.5, 2, 5, 100],
        labels=["<-5%", "-5..-2%", "-2..-0.5%", "flat", "0.5..2%", "2..5%", ">5%"],
    )
    g = d.groupby("bucket", observed=True)["next_ret"].agg(["mean", "std", "count"])
    g["mean_pct"] = g["mean"] * 100
    g["positive_rate"] = d.groupby("bucket", observed=True)["next_ret"].apply(lambda x: (x > 0).mean()).values
    print(g[["count", "mean_pct", "positive_rate"]].round(3))


def yearly_drift(df_1d: pd.DataFrame):
    print("\n=== Yearly buy-hold (sanity check) ===")
    d = df_1d.copy()
    d["year"] = d.index.year
    yr = d.groupby("year")["close"].agg(["first", "last"])
    yr["return_pct"] = (yr["last"] / yr["first"] - 1) * 100
    print(yr[["return_pct"]].round(2))


if __name__ == "__main__":
    df_5m = load("5m")
    df_1h = load("1h")
    df_1d = load("1d")

    for tf, df in [("5m", df_5m), ("1h", df_1h), ("1d", df_1d)]:
        summary(df, tf)

    yearly_drift(df_1d)
    dow(df_1d)
    hour_of_day(df_5m)
    session_blocks(df_1h)
    vol_regime(df_1d)
    trend_regime(df_1d)
    gap_behavior(df_1d)
