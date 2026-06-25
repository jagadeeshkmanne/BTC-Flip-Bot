#!/usr/bin/env python3
"""backtest_kalman_knn.py — TradingView's Kalman filter + KNN bands, honestly tested.

Two TradingView-popular ideas, implemented faithfully and pushed through the same honest
engine (bt_helpers: signal on closed bar, fill next open, 0.055%+0.05%/side, IS/OOS 60/40).

KALMAN  — 2-state (position+velocity) scalar Kalman filter on close. Unlike an EMA it carries
          a velocity term, so it lags less and can project the trend. Tunable process-noise q
          (smaller q = smoother). Signals:
            kf_trend : long when close > kf_pos                  (price vs filter)
            kf_slope : long when kf_vel > 0                      (filter rising)
            kf_both  : long when close > kf_pos AND kf_vel > 0
          Also a fast/slow Kalman cross.

KNN     — directional k-nearest-neighbours classifier (the "ML kNN strategy" family). Features
          per bar = z-scored [momentum-fast, momentum-slow, RSI]. Label of a PAST bar = sign of
          its next-bar return. For the current bar, vote the k nearest past bars (by feature
          distance) and go long if the vote is up, else flat. Strictly causal (neighbours only
          from bars whose label is already known).
KNN-BAND— KNN-smoothed midline (mean of the k nearest-by-value closes in a rolling window) ±
          ATR band, tested both as a trend filter (price vs midline) and mean-reversion (buy
          lower band).

Long/flat only (shorts lose on BTC — established). Benchmarks: buy&hold + EMA50/200 trend bot.
"""
from __future__ import annotations
import argparse
import numpy as np
import bt_helpers as bt


# ── Kalman: 2-state constant-velocity ───────────────────────────────────────
def kalman_pv(close, q=1e-3, r=1.0):
    """Returns (pos, vel) arrays. q=process noise (smoothness), r=measurement noise."""
    z = np.asarray(close, float)
    n = len(z)
    pos = np.empty(n); vel = np.empty(n)
    x = np.array([z[0], 0.0])                       # [position, velocity]
    P = np.eye(2) * 1.0
    F = np.array([[1.0, 1.0], [0.0, 1.0]])
    H = np.array([[1.0, 0.0]])
    Q = np.array([[q, 0.0], [0.0, q]])
    R = np.array([[r]])
    I = np.eye(2)
    for i in range(n):
        # predict
        x = F @ x
        P = F @ P @ F.T + Q
        # update
        y = z[i] - (H @ x)[0]
        S = (H @ P @ H.T)[0, 0] + R[0, 0]
        K = (P @ H.T) / S                            # 2x1
        x = x + (K[:, 0] * y)
        P = (I - K @ H) @ P
        pos[i] = x[0]; vel[i] = x[1]
    return pos, vel


# ── KNN directional classifier (causal) ─────────────────────────────────────
def knn_signal(df, k=15, win=1500, mom_f=5, mom_s=20):
    c = df["close"].values
    n = len(c)
    momf = np.concatenate([np.zeros(mom_f), c[mom_f:] / c[:-mom_f] - 1])
    moms = np.concatenate([np.zeros(mom_s), c[mom_s:] / c[:-mom_s] - 1])
    r = bt.rsi(df["close"], 14).values / 100.0
    feats = np.column_stack([momf, moms, np.nan_to_num(r)])
    # z-score features on a trailing basis (use full-sample std as scale; mean removed rolling-ish)
    fs = (feats - np.nanmean(feats, axis=0)) / (np.nanstd(feats, axis=0) + 1e-9)
    label = np.sign(np.concatenate([c[1:] / c[:-1] - 1, [0.0]]))   # label[j] = sign(ret j->j+1)
    sig = np.zeros(n)
    start = max(mom_s, 50)
    for i in range(start, n):
        lo = max(start, i - win)
        hi = i - 1                                   # labels known only up to bar i-2 -> use j<=i-2
        if hi - lo < k + 1:
            continue
        idx = np.arange(lo, hi)                       # j in [lo, i-2]
        d = np.sum((fs[idx] - fs[i]) ** 2, axis=1)
        nn = idx[np.argpartition(d, k)[:k]]
        vote = label[nn].sum()
        sig[i] = 1.0 if vote > 0 else 0.0
    return sig


# ── KNN bands ───────────────────────────────────────────────────────────────
def knn_band_mid(close, k=10, win=50):
    c = np.asarray(close, float)
    n = len(c)
    mid = np.copy(c)
    for i in range(win, n):
        w = c[i - win:i]
        d = np.abs(w - c[i])
        nn = w[np.argpartition(d, k)[:k]]
        mid[i] = nn.mean()
    return mid


def report(name, eq, n):
    full = bt.metrics(eq)
    is_, oos = bt.oos_split(eq)
    im, om = bt.metrics(is_), bt.metrics(oos)
    print(f"{name:<32} {n:>4}tr | IS retdd {im[2]:>5.2f} | "
          f"OOS cagr {om[0]*100:>7.1f}% dd {om[1]*100:>6.1f}% retdd {om[2]:>5.2f} | "
          f"FULL cagr {full[0]*100:>6.1f}% retdd {full[2]:>5.2f}")


def run(coin, tf):
    df = bt.load(coin, tf)
    c = df["close"]
    print(f"\n════════ {coin} {tf}  ({len(df):,} bars, {df['timestamp'].iloc[0].date()} → {df['timestamp'].iloc[-1].date()}) ════════")
    report("BUY & HOLD", bt.buyhold(df), 0)
    eqb, nb = bt.backtest_signal(df, (bt.ema(c, 50) > bt.ema(c, 200)).astype(float).values)
    report("EMA50/200 long/flat (trendbot)", eqb, nb)
    print("─" * 104)

    # Kalman
    for q in (1e-4, 1e-3, 1e-2):
        pos, vel = kalman_pv(c.values, q=q)
        kf_trend = (c.values > pos).astype(float)
        kf_slope = (vel > 0).astype(float)
        kf_both = ((c.values > pos) & (vel > 0)).astype(float)
        for nm, s in [("trend", kf_trend), ("slope", kf_slope), ("both", kf_both)]:
            eq, n = bt.backtest_signal(df, s)
            report(f"Kalman q={q:g} {nm}", eq, n)
    # Kalman fast/slow cross
    pf, _ = kalman_pv(c.values, q=1e-2)
    ps, _ = kalman_pv(c.values, q=1e-4)
    eq, n = bt.backtest_signal(df, (pf > ps).astype(float))
    report("Kalman cross (q1e-2>q1e-4)", eq, n)
    print("·" * 104)

    # KNN directional
    for k in (10, 25, 50):
        s = knn_signal(df, k=k)
        eq, n = bt.backtest_signal(df, s)
        report(f"KNN classifier k={k}", eq, n)
    # KNN bands
    mid = knn_band_mid(c.values, k=10, win=50)
    a = bt.atr(df, 14).values
    eqf, nf = bt.backtest_signal(df, (c.values > mid).astype(float))
    report("KNN-band trend (px>mid)", eqf, nf)
    eqm, nm = bt.backtest_signal(df, (c.values < mid - 1.0 * a).astype(float))
    report("KNN-band mean-rev (buy low)", eqm, nm)


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
