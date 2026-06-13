"""replicate_bbpct_scalp.py — honest replication of the YouTube 15m BB%B scalper.
(user 2026-06-12)

Strategy from the video:
  - Bollinger Bands %B with VOLUME-WEIGHTED basis (len 20, 2 std)
  - ENTRY (long): %B percentile-rank over 350 bars < 10th pct
  - EXIT: %B percentile-rank over 350 bars > 90th pct
  - FILTER 1: HMA(10)/EMA(55) ratio > 1.0005 (15m trend confirm)
  - FILTER 2: daily "combined ratings" bullish  (approximated: daily close>SMA20)
  - 15m, long-only, futures
Honest: enter/exit at NEXT bar open; gross (0 fee) and net (futures taker
0.05% + slip 0.02% per side). Plus the creator's RANDOM-comparison T-test.
"""
import numpy as np
import pandas as pd

FEE_SIDE = 0.0005 + 0.0002      # futures taker + slip per side
RT = 2 * FEE_SIDE


def wma(a, n):
    w = np.arange(1, n + 1, dtype=float); w /= w.sum()
    return pd.Series(a).rolling(n).apply(lambda x: np.dot(x, w), raw=True).values


def hma(c, n):
    half = wma(c, n // 2); full = wma(c, n)
    raw = 2 * half - full
    return wma(raw, int(np.sqrt(n)))


def pctrank(s, w=350):
    return pd.Series(s).rolling(w).apply(lambda x: (x[:-1] <= x[-1]).mean(), raw=True).values


def load(sym):
    df = pd.read_csv(f"/Users/jags/Desktop/BTC-Flip-Bot/data/cache/{sym}_15m.csv", parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    c, v = df["close"].values, df["volume"].values
    L = 20
    vwma = (pd.Series(c * v).rolling(L).sum() / pd.Series(v).rolling(L).sum()).values
    std = pd.Series(c).rolling(L).std().values
    up, lo = vwma + 2 * std, vwma - 2 * std
    pctB = (c - lo) / (up - lo)
    df["pr"] = pctrank(pctB, 350)
    df["ratio"] = hma(c, 10) / pd.Series(c).ewm(span=55, adjust=False).mean().values
    # daily filter (approx of "combined ratings 1D"): daily close > daily SMA20, no lookahead
    dd = df.set_index("timestamp")["close"].resample("1D").last()
    dup = (dd > dd.rolling(20).mean()).shift(1)     # yesterday's completed daily state
    df["dayup"] = dup.reindex(df["timestamp"], method="ffill").values
    return df


def backtest(df):
    o, h, l = df["open"].values, df["high"].values, df["low"].values
    pr, ratio, dayup = df["pr"].values, df["ratio"].values, df["dayup"].values
    n = len(df); i = 1; rets = []; holds = []
    while i < n - 1:
        enter = (pr[i] < 0.10) and (ratio[i] > 1.0005) and (dayup[i] == True)
        if not (enter and not np.isnan(pr[i])):
            i += 1; continue
        entry = o[i + 1]; j = i + 1
        while j < n - 1:
            if pr[j] > 0.90:           # exit signal -> next open
                break
            j += 1
        exit_px = o[j + 1] if j + 1 < n else df["close"].values[-1]
        rets.append(exit_px / entry - 1); holds.append(j - i)
        i = j + 1
    return np.array(rets), np.array(holds)


def random_compare(df, n_trades, hold_med, reps=400):
    """random long entries, same count, hold = median holding bars. mean return dist."""
    c = df["close"].values; N = len(c)
    means = []
    for r in range(reps):
        # deterministic pseudo-random via index hashing (no Math.random); vary by rep
        starts = ((np.arange(n_trades) * 2654435761 + r * 40503) % (N - hold_med - 2)) + 1
        ex = np.minimum(starts + hold_med, N - 1)
        means.append(np.mean(c[ex] / c[starts] - 1))
    return np.array(means)


for sym in ("BTCUSDT", "ETHUSDT"):
    df = load(sym)
    r, hold = backtest(df)
    if len(r) == 0:
        print(f"{sym}: no trades"); continue
    wr = (r > 0).mean() * 100
    net = r - RT
    tot_g = np.prod(1 + r) - 1; tot_n = np.prod(1 + net) - 1
    eq = np.cumprod(1 + net); dd = (eq / np.maximum.accumulate(eq) - 1).min() * 100
    bh = df["close"].iloc[-1] / df["close"].iloc[0] - 1
    print(f"\n===== {sym} 15m  trades={len(r)}  median hold={np.median(hold):.0f} bars "
          f"({np.median(hold)*15/60:.1f}h) =====")
    print(f"  win rate           {wr:.1f}%")
    print(f"  GROSS  avg/trade   {r.mean()*100:+.4f}%   total {tot_g*100:+.0f}%")
    print(f"  NET    avg/trade   {net.mean()*100:+.4f}%   total {tot_n*100:+.0f}%   maxDD {dd:.0f}%")
    print(f"  buy & hold total   {bh*100:+.0f}%")
    # random comparison (creator's key test)
    rnd = random_compare(df, len(r), int(np.median(hold)))
    t = (r.mean() - rnd.mean()) / (rnd.std() + 1e-12)
    print(f"  RANDOM mean/trade  {rnd.mean()*100:+.4f}%  -> strategy GROSS edge T = {t:+.2f} "
          f"({'significant' if abs(t) > 2 else 'NOT diff from random'})")

    if sym == "BTCUSDT":
        print("  --- WHERE DOES '500%' COME FROM? fee x leverage (compounded, liquidate if equity<=0) ---")
        print(f"     {'fee assumption':>22} {'1x':>10} {'3x':>10} {'5x':>10}")
        for flabel, rt in [("taker+slip 0.14%", 0.0014), ("maker ~0.05%", 0.0005), ("zero fee (platform?)", 0.0)]:
            net = r - rt
            cells = []
            for lev in (1, 3, 5):
                eq = 1.0
                for x in net:
                    eq *= (1 + lev * x)
                    if eq <= 0:
                        eq = 0.0; break
                cells.append(f"{(eq-1)*100:>+9.0f}%" if eq > 0 else "  LIQUID.")
            print(f"     {flabel:>22} {cells[0]:>10} {cells[1]:>10} {cells[2]:>10}")
        print(f"     (BTC buy & hold over same period: {(df['close'].iloc[-1]/df['close'].iloc[0]-1)*100:+.0f}%, no leverage, no execution risk)")
