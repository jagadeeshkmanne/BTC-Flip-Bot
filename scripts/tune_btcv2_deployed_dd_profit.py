#!/usr/bin/env python3
"""tune_btcv2_deployed_dd_profit.py — focused sweep around the deployed BTC V2 bot.

Baseline is the deployed dashboard config:
  - 4h+daily EMA50/200 + 9mo macro long gate
  - drop10/40d + daily MACD bear short gate
  - conviction long leverage 1.0-2.5x, short 1x
  - long pyramid +100% at +2R, lock 33% at +6R, parabolic de-risk 50%

Goal: search nearby, honest variants that either:
  A) reduce max drawdown while keeping CAGR strong, or
  B) increase CAGR without worsening DD too much.

All signals are closed-bar, fills are next-bar open, fees and slippage are inherited from
bt_helpers/backtest_conviction_lev.
"""
from __future__ import annotations

import os
import sys
from itertools import product

import numpy as np
import pandas as pd

pd.set_option("future.no_silent_downcasting", True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bt_helpers as bt
from backtest_conviction_lev import run_conv
from backtest_myv3_ext import build
from backtest_myv3_final import m
from backtest_short_aggressive import daily_macd_bear


BPD = 6


def yr(s: pd.Series, y: int) -> float:
    seg = s[s.index.year == y]
    return (seg.iloc[-1] / seg.iloc[0] - 1) * 100 if len(seg) > 20 else 0.0


def all_green(s: pd.Series) -> bool:
    return all(yr(s, y) >= -0.5 for y in range(2018, 2027))


def metrics(s: pd.Series) -> dict:
    cg, dd, rr = m(s)
    o = m(s[s.index >= s.index[int(len(s) * 0.6)]])
    return {
        "cagr": cg * 100,
        "dd": dd * 100,
        "rdd": rr,
        "oos_rdd": o[2],
        "finalx": s.iloc[-1] / s.iloc[0],
        "green": all_green(s),
        "y18": yr(s, 2018),
        "y22": yr(s, 2022),
        "y26": yr(s, 2026),
        "y25": yr(s, 2025),
        "y26_only": yr(s, 2026),
    }


def build_inputs():
    df, bull = build()
    c = df["close"]
    long_gate = bull & (c > bt.sma(c, 9 * 30 * BPD).shift(1)).fillna(False).values
    bear = ((c / c.rolling(40 * BPD).max().shift(1) - 1) < -0.10).fillna(False).values
    bear &= daily_macd_bear(df)
    ddh = (c / c.rolling(180 * BPD).max().shift(1) - 1).fillna(0).values
    ssize_base = np.where(ddh <= -0.30, 1.0, np.where(ddh <= -0.20, 0.50, 0.25))
    parab = (c > 2.2 * bt.sma(c, 140 * BPD).shift(1)).fillna(False).values

    adx = bt.adx(df, 14).shift(1).fillna(0).values
    egap = ((bt.ema(c, 50) - bt.ema(c, 200)) / bt.ema(c, 200)).shift(1).fillna(0).values
    conv = np.clip(adx / 35.0, 0, 1) * 0.5 + np.clip(egap / 0.12, 0, 1) * 0.5
    return df, long_gate, bear, ssize_base, parab, conv


def long_lev(conv: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return lo + (hi - lo) * conv


def run_case(df, long_gate, bear, ssize_base, parab, conv, cfg: dict):
    lv = long_lev(conv, cfg["lev_lo"], cfg["lev_hi"])
    ssize = ssize_base * cfg["short_scale"]
    s, avg_lev = run_conv(
        df,
        long_gate,
        bear,
        ssize,
        parab,
        lv,
        short_lev=1.0,
        lock_frac=cfg["lock_frac"],
        lock_r=cfg["lock_r"],
        pyr_frac=cfg["pyr_frac"],
        pyr_r=cfg["pyr_r"],
        be_buf=cfg["be_buf"],
    )
    out = metrics(s)
    out.update(cfg)
    out["avg_long_lev"] = avg_lev
    return out


def fmt(r: dict) -> str:
    return (
        f"{r['name']:<34}{r['cagr']:>6.1f}%{r['dd']:>7.1f}%{r['rdd']:>6.2f}"
        f"{r['oos_rdd']:>7.2f}{r['finalx']:>9.0f}x"
        f"  {r['y18']:+5.0f}/{r['y22']:+4.0f}/{r['y26']:+4.0f}"
        f"  L{r['lev_lo']:.1f}-{r['lev_hi']:.1f} Sx{r['short_scale']:.2f}"
        f" lock{r['lock_frac']:.2f}@{r['lock_r']:.0f}R pyr{r['pyr_frac']:.2f}@{r['pyr_r']:.0f}R"
    )


def main():
    df, long_gate, bear, ssize_base, parab, conv = build_inputs()

    base_cfg = {
        "name": "DEPLOYED conv1-2.5 Sx1",
        "lev_lo": 1.0,
        "lev_hi": 2.5,
        "short_scale": 1.0,
        "lock_frac": 0.33,
        "lock_r": 6.0,
        "pyr_frac": 1.0,
        "pyr_r": 2.0,
        "be_buf": 0.01,
    }
    base = run_case(df, long_gate, bear, ssize_base, parab, conv, base_cfg)

    rows = [base]
    lev_ranges = [(1.0, 2.0), (1.0, 2.25), (1.0, 2.5), (1.0, 2.75), (1.0, 3.0),
                  (1.2, 2.5), (1.2, 2.75), (1.3, 2.75)]
    short_scales = [0.50, 0.60, 0.75, 0.90, 1.00]
    locks = [(0.25, 6.0), (0.33, 5.0), (0.33, 6.0), (0.50, 5.0), (0.50, 6.0)]
    pyrs = [(1.0, 2.0), (1.0, 3.0), (0.5, 2.0), (0.5, 3.0)]

    for (lev_lo, lev_hi), short_scale, (lock_frac, lock_r), (pyr_frac, pyr_r) in product(
        lev_ranges, short_scales, locks, pyrs
    ):
        cfg = {
            "name": "sweep",
            "lev_lo": lev_lo,
            "lev_hi": lev_hi,
            "short_scale": short_scale,
            "lock_frac": lock_frac,
            "lock_r": lock_r,
            "pyr_frac": pyr_frac,
            "pyr_r": pyr_r,
            "be_buf": 0.01,
        }
        rows.append(run_case(df, long_gate, bear, ssize_base, parab, conv, cfg))

    for i, r in enumerate(rows):
        if r["name"] == "sweep":
            r["name"] = f"#{i}"

    def valid(r):
        return r["green"] and r["y18"] > 0 and r["y22"] > 0 and r["y26"] > 0 and r["oos_rdd"] > 1.0

    print("=" * 128)
    print("BTC V2 DEPLOYED NEARBY SWEEP — lower DD / higher profit, honest 2017-2026")
    print("=" * 128)
    print(f"{'config':<34}{'CAGR':>7}{'DD':>8}{'r/DD':>6}{'OOS':>7}{'final':>10}  {'18/22/26':>15}  params")
    print("-" * 128)
    print(fmt(base))

    print("\nLOWEST DD with CAGR >= 100%, all years green, all bear years positive:")
    low_dd = [r for r in rows if valid(r) and r["cagr"] >= 100]
    low_dd.sort(key=lambda r: (r["dd"], r["cagr"]), reverse=True)
    for r in low_dd[:10]:
        print(fmt(r))

    print("\nBEST ret/DD with CAGR >= 120%, DD no worse than deployed + 2pt:")
    best_ratio = [r for r in rows if valid(r) and r["cagr"] >= 120 and r["dd"] >= base["dd"] - 2.0]
    best_ratio.sort(key=lambda r: (r["rdd"], r["cagr"]), reverse=True)
    for r in best_ratio[:10]:
        print(fmt(r))

    print("\nHIGHER PROFIT than deployed while DD <= deployed:")
    higher_profit = [r for r in rows if valid(r) and r["cagr"] > base["cagr"] and r["dd"] >= base["dd"]]
    higher_profit.sort(key=lambda r: (r["cagr"], r["rdd"]), reverse=True)
    if not higher_profit:
        print("  NONE. Higher CAGR required accepting more drawdown in this sweep.")
    else:
        for r in higher_profit[:10]:
            print(fmt(r))

    print("\nPARETO frontier, sorted by DD then CAGR:")
    cand = [r for r in rows if valid(r)]
    frontier = []
    for r in sorted(cand, key=lambda x: x["dd"], reverse=True):
        if not frontier or r["cagr"] > max(f["cagr"] for f in frontier):
            frontier.append(r)
    for r in frontier[:20]:
        print(fmt(r))


if __name__ == "__main__":
    main()
