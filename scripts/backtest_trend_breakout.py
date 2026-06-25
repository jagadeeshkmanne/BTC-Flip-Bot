#!/usr/bin/env python3
"""backtest_trend_breakout.py — TREND-CONTINUATION breakout (the never-tested angle).

Prior breakout tests all FAILED because they were either (a) low-TF many-trade fee bleed,
or (b) channel-EXIT that gives the trend back in the drawdown. This tests the opposite:

  TREND FILTER  : only act in an established uptrend (close > EMA_trend). Long/flat only
                  (shorts lose on BTC — established).
  ENTRY         : breakout as a CONTINUATION trigger — close breaks the prior N-bar high
                  while already in the uptrend (buy strength, not reversion).
  EXIT          : TREND-FOLLOWING, let winners run — NOT a tight channel. Three modes:
                    ema       — exit when close < EMA_trend (trend death)
                    chandelier— exit when close < highest_high_since_entry - k*ATR
                    donchian  — exit when close < prior M-bar low (wide)

Question: does a breakout ENTRY on top of the trend filter beat just being long the whole
uptrend (the validated EMA trend bot)? Breakout should cut DD (waits for momentum) — does it
keep enough return to be worth it, OOS, after honest costs?

Engine: bt_helpers (signals on closed bar, fill next open, 0.055%+0.05% per side). IS/OOS 60/40.
Benchmarks: buy&hold + plain EMA50/200 long/flat (the trend-bot rule).
"""
from __future__ import annotations
import argparse
import numpy as np
import bt_helpers as bt


def trend_breakout_pos(df, n_entry, ema_trend, exit_mode, atr_k=3.0, n_exit=10):
    c = df["close"].values
    h = df["high"].values
    et = bt.ema(df["close"], ema_trend).values
    a = bt.atr(df, 14).values
    dhi = df["high"].rolling(n_entry).max().shift(1).values   # prior N-bar high (no lookahead)
    dlo = df["low"].rolling(n_exit).min().shift(1).values
    pos = np.zeros(len(df))
    cur = 0.0
    hh = 0.0
    for i in range(len(df)):
        if cur == 1.0:
            hh = max(hh, h[i])
            ex = c[i] < et[i]                                   # trend death (all modes honor this)
            if exit_mode == "chandelier" and c[i] < hh - atr_k * a[i]:
                ex = True
            if exit_mode == "donchian" and not np.isnan(dlo[i]) and c[i] < dlo[i]:
                ex = True
            if ex:
                cur = 0.0
        else:
            if c[i] > et[i] and not np.isnan(dhi[i]) and c[i] > dhi[i]:
                cur = 1.0
                hh = h[i]
        pos[i] = cur
    return pos


def ema_trend_pos(df, fast=50, slow=200):
    return (bt.ema(df["close"], fast) > bt.ema(df["close"], slow)).astype(float).values


def report(name, eq, n):
    full = bt.metrics(eq)
    is_, oos = bt.oos_split(eq)
    im, om = bt.metrics(is_), bt.metrics(oos)
    print(f"{name:<34} {n:>4}tr | IS retdd {im[2]:>5.2f} | "
          f"OOS cagr {om[0]*100:>7.1f}% dd {om[1]*100:>7.1f}% retdd {om[2]:>5.2f} | "
          f"FULL cagr {full[0]*100:>6.1f}% dd {full[1]*100:>6.1f}% retdd {full[2]:>5.2f}")


def run(coin, tf):
    df = bt.load(coin, tf)
    print(f"\n════════ {coin} {tf}  ({len(df):,} bars, {df['timestamp'].iloc[0].date()} → {df['timestamp'].iloc[-1].date()}) ════════")

    bh = bt.buyhold(df)
    report("BUY & HOLD", bh, 0)
    eqb, nb = bt.backtest_signal(df, ema_trend_pos(df))
    report("EMA50/200 long/flat (trendbot)", eqb, nb)
    print("─" * 116)

    best = None
    for ema_trend in (100, 200):
        for n_entry in (20, 30, 55):
            for mode in ("ema", "chandelier", "donchian"):
                pos = trend_breakout_pos(df, n_entry, ema_trend, mode)
                eq, n = bt.backtest_signal(df, pos)
                label = f"BO N{n_entry} ema{ema_trend} {mode}"
                report(label, eq, n)
                _, oos = bt.oos_split(eq)
                _, _, rdd = bt.metrics(oos)
                # rank by IN-SAMPLE only (honest selection), keep OOS for the verdict
                is_, _ = bt.oos_split(eq)
                isrdd = bt.metrics(is_)[2]
                if best is None or isrdd > best[0]:
                    best = (isrdd, label, eq, n)
    if best:
        print("─" * 116)
        print(f"IS-selected winner → {best[1]}")
        report("  (its OOS)", best[2], best[3])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", default="BTCUSDT")
    ap.add_argument("--tfs", default="4h")
    a = ap.parse_args()
    for coin in a.coins.split(","):
        for tf in a.tfs.split(","):
            run(coin, tf)


if __name__ == "__main__":
    main()
