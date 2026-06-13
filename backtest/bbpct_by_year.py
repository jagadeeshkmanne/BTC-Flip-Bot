"""bbpct_by_year.py — BB%B long-only, full history, broken down BY YEAR
(bull / bear / sideways) for BTC/ETH/SOL/BNB. Honest fills + maker/taker.
"""
import numpy as np
import pandas as pd
from replicate_bbpct_scalp import wma, hma, pctrank

RT_TAKER, RT_MAKER = 0.0014, 0.0005
REGIME = {2019: "recover", 2020: "BULL", 2021: "BULL", 2022: "BEAR",
          2023: "recover", 2024: "BULL", 2025: "side/bull", 2026: "side"}


def load(sym):
    df = pd.read_csv(f"/Users/jags/Desktop/BTC-Flip-Bot/data/cache/{sym}_15m.csv",
                     parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    c, v = df["close"].values, df["volume"].values
    vwma = (pd.Series(c * v).rolling(20).sum() / pd.Series(v).rolling(20).sum()).values
    std = pd.Series(c).rolling(20).std().values
    df["pr"] = pctrank((c - (vwma - 2 * std)) / (4 * std), 350)
    df["ratio"] = hma(c, 10) / pd.Series(c).ewm(span=55, adjust=False).mean().values
    dd = df.set_index("timestamp")["close"].resample("1D").last()
    df["d20"] = (dd > dd.rolling(20).mean()).shift(1).reindex(df["timestamp"], method="ffill").values
    return df


def trades(df):
    o = df["open"].values; cl = df["close"].values; ts = df["timestamp"].values
    pr, ratio, d20 = df["pr"].values, df["ratio"].values, df["d20"].values
    n = len(df); i = 1; out = []
    while i < n - 1:
        if not (pr[i] < 0.10 and ratio[i] > 1.0005 and d20[i] == True) or np.isnan(pr[i]):
            i += 1; continue
        entry = o[i + 1]; j = i + 1
        while j < n - 1 and not pr[j] > 0.90:
            j += 1
        ex = o[j + 1] if j + 1 < n else cl[-1]
        out.append((pd.Timestamp(ts[j]).year, ex / entry - 1))
        i = j + 1
    return pd.DataFrame(out, columns=["year", "ret"])


for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"):
    df = load(sym)
    tr = trades(df)
    yr0, yr1 = df["timestamp"].iloc[0].year, df["timestamp"].iloc[-1].year
    print(f"\n===== {sym}  ({df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()}) =====")
    print(f"  {'year':>5} {'regime':>10} {'trades':>7} {'win%':>6} {'gross/t':>9} {'net taker':>10} {'net maker':>10}")
    for y in range(yr0, yr1 + 1):
        s = tr[tr.year == y]["ret"].values
        if len(s) == 0:
            continue
        g = s.mean() * 100
        nt = (np.prod(1 + (s - RT_TAKER)) - 1) * 100
        nm = (np.prod(1 + (s - RT_MAKER)) - 1) * 100
        print(f"  {y:>5} {REGIME.get(y,'?'):>10} {len(s):>7} {(s>0).mean()*100:>5.1f}% "
              f"{g:>+8.4f}% {nt:>+9.0f}% {nm:>+9.0f}%")
