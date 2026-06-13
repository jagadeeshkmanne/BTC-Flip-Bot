#!/usr/bin/env python3
"""ic_daily_scan.py — systematic daily-scale predictor scan on BTC (user
2026-06-12: 'find a completely new fresh strategy from 6y of BTC movement').

Method (pre-registered, honest):
  - ~15 features computed at each daily close (no lookahead), 3 forward
    horizons (1/3/7d close-to-close) -> Spearman rank IC.
  - ~45 tests => expect a couple of |IC|~0.04 by pure chance. A feature only
    counts if |IC| >= 0.05 full-sample AND the sign agrees in >= 6 of 7
    calendar years. Day-of-week tested separately.
  - Any survivor gets a decile-spread check and a simple honest long/flat
    strategy test (fills next open, 0.10% fee + 0.02% slip per side,
    IS <=2023 / OOS 2024+) against buy-hold.
"""
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_1d.csv"
FEE, SLIP = 0.0010, 0.0002


def wilder_rsi(c, n=14):
    d = c.diff()
    ag = d.clip(lower=0).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    return 100 - 100/(1 + ag/al)


def main():
    df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    c, h, l = df["close"], df["high"], df["low"]
    r1 = c.pct_change()

    F = pd.DataFrame(index=df.index)
    for k in (1, 3, 7, 14, 30, 90):
        F[f"mom_{k}d"] = c.pct_change(k)
    F["vol20"] = r1.rolling(20).std()
    F["vol_ratio"] = r1.rolling(5).std() / r1.rolling(60).std()
    F["rp30"] = (c - l.rolling(30).min()) / (h.rolling(30).max() - l.rolling(30).min())
    F["rsi14"] = wilder_rsi(c)
    F["dist_sma200"] = c / c.rolling(200).mean() - 1
    F["dd_from_peak"] = c / c.cummax() - 1
    F["range_pct"] = (h - l) / c
    F["gap_7d_high"] = c / h.rolling(7).max().shift(1) - 1
    F["updays_10"] = (r1 > 0).rolling(10).sum()

    fwd = {hz: c.pct_change(hz).shift(-hz) for hz in (1, 3, 7)}
    years = df["timestamp"].dt.year

    print(f"BTC 1d {df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()} "
          f"({len(df)} bars) | {len(F.columns)} features x 3 horizons = {len(F.columns)*3} tests")
    print(f"bar to clear: |IC| >= 0.05 full-sample AND sign-consistent >= 6/7 years\n")
    print(f"{'feature':<14}{'hz':>4}{'IC':>8}{'|years agree|':>14}{'IC by year':>40}")
    survivors = []
    for col in F.columns:
        for hz, f in fwd.items():
            m = F[col].notna() & f.notna()
            if m.sum() < 500:
                continue
            ic, _ = spearmanr(F[col][m], f[m])
            ics = []
            for y in sorted(years.unique()):
                my = m & (years == y)
                if my.sum() >= 60:
                    icy, _ = spearmanr(F[col][my], f[my])
                    ics.append(icy)
            agree = sum(1 for x in ics if np.sign(x) == np.sign(ic))
            flag = abs(ic) >= 0.05 and agree >= 6 and len(ics) >= 7
            if abs(ic) >= 0.04:
                print(f"{col:<14}{hz:>3}d{ic:>+8.3f}{agree:>7}/{len(ics):<6}"
                      + " ".join(f"{x:+.2f}" for x in ics) + ("   << SURVIVOR" if flag else ""))
            if flag:
                survivors.append((col, hz, ic))

    # day-of-week (separate, categorical)
    print("\nday-of-week mean next-day return:")
    dow = df["timestamp"].dt.dayofweek
    f1 = fwd[1]
    for d in range(7):
        m = (dow == d) & f1.notna()
        mu = f1[m].mean() * 100
        per_year = [np.sign(f1[m & (years == y)].mean()) for y in sorted(years.unique())
                    if (m & (years == y)).sum() > 20]
        agree = max(per_year.count(1.0), per_year.count(-1.0))
        print(f"  {['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][d]}: {mu:+.3f}%/d  "
              f"(sign stable {agree}/{len(per_year)} years)")

    if not survivors:
        print("\nNo feature passed the pre-registered bar.")
        return
    print("\n── survivors: decile spread + honest long/flat strategy ──")
    o = df["open"].values
    for col, hz, ic in survivors:
        m = F[col].notna() & fwd[hz].notna()
        q = pd.qcut(F[col][m], 10, labels=False, duplicates="drop")
        spread = (fwd[hz][m][q == q.max()].mean() - fwd[hz][m][q == 0].mean()) * 100
        # honest long/flat: long while feature in its favorable tercile
        sig = pd.qcut(F[col], 3, labels=False, duplicates="drop")
        want = (sig == (2 if ic > 0 else 0)).fillna(False).values
        eq, pos, trades = 1.0, None, 0
        eqs = []
        for i in range(201, len(df)):
            t = want[i - 1]
            if pos is None and t:
                pos = eq * (1 - FEE) / (o[i] * (1 + SLIP)); trades += 1
            elif pos is not None and not t:
                eq = pos * o[i] * (1 - SLIP) * (1 - FEE); pos = None
            eqs.append((df["timestamp"].iloc[i], pos * c.iloc[i] * (1 - FEE - SLIP) if pos else eq))
        if pos is not None:
            eq = pos * c.iloc[-1] * (1 - FEE - SLIP)
        e = pd.DataFrame(eqs, columns=["ts", "eq"]).set_index("ts")["eq"]
        is_r = e[e.index < "2024-01-01"]
        oos_r = e[e.index >= "2024-01-01"]
        hold = c.iloc[-1] / c.iloc[201]
        print(f"{col} ({hz}d, IC {ic:+.3f}): decile spread {spread:+.2f}%/{hz}d | "
              f"long/flat x{eq:.2f} ({trades} trades) vs hold x{hold:.2f} | "
              f"IS x{is_r.iloc[-1]/is_r.iloc[0]:.2f} OOS x{oos_r.iloc[-1]/oos_r.iloc[0]:.2f}")


if __name__ == "__main__":
    main()
