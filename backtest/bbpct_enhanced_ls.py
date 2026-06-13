"""bbpct_enhanced_ls.py — enhanced long/short BB%B with MACRO-REGIME gating.
Fix for the failed naive short (which fought the upward drift):
  long  : %B pctile<10 & 15m uptrend & daily BULL (close>SMAx)   -> exit %B>90
  short : %B pctile>90 & 15m downtrend & daily BEAR (close<SMAx)  -> exit %B<10
Only short when the macro trend is genuinely down. Honest fills, T-test, fees.
"""
import numpy as np
import pandas as pd
from replicate_bbpct_scalp import wma, hma, pctrank, random_compare

RT_TAKER, RT_MAKER = 0.0014, 0.0005


def load(sym):
    df = pd.read_csv(f"/Users/jags/Desktop/BTC-Flip-Bot/data/cache/{sym}_15m.csv",
                     parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    c, v = df["close"].values, df["volume"].values
    vwma = (pd.Series(c * v).rolling(20).sum() / pd.Series(v).rolling(20).sum()).values
    std = pd.Series(c).rolling(20).std().values
    pctB = (c - (vwma - 2 * std)) / (4 * std)
    df["pr"] = pctrank(pctB, 350)
    df["ratio"] = hma(c, 10) / pd.Series(c).ewm(span=55, adjust=False).mean().values
    dd = df.set_index("timestamp")["close"].resample("1D").last()
    df["d20"] = (dd > dd.rolling(20).mean()).shift(1).reindex(df["timestamp"], method="ffill").values
    df["d50"] = (dd > dd.rolling(50).mean()).shift(1).reindex(df["timestamp"], method="ffill").values
    df["d200"] = (dd > dd.rolling(200).mean()).shift(1).reindex(df["timestamp"], method="ffill").values
    return df


def bt(df, mode, bear_col="d200"):
    o = df["open"].values; cl = df["close"].values
    pr, ratio = df["pr"].values, df["ratio"].values
    d20, bear = df["d20"].values, df[bear_col].values
    n = len(df); i = 1; rets = []; holds = []
    while i < n - 1:
        sig = 0
        if mode in ("long", "both") and pr[i] < 0.10 and ratio[i] > 1.0005 and d20[i] == True:
            sig = 1
        elif mode in ("short", "both") and pr[i] > 0.90 and ratio[i] < 0.9995 and bear[i] == False:
            sig = -1
        if sig == 0 or np.isnan(pr[i]):
            i += 1; continue
        entry = o[i + 1]; j = i + 1
        while j < n - 1:
            if (sig == 1 and pr[j] > 0.90) or (sig == -1 and pr[j] < 0.10):
                break
            j += 1
        exit_px = o[j + 1] if j + 1 < n else cl[-1]
        rets.append((exit_px / entry - 1) * sig); holds.append(j - i); i = j + 1
    return np.array(rets), np.array(holds)


for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"):
    df = load(sym)
    print(f"\n===== {sym} =====")
    print(f"  {'variant':>28} {'trades':>7} {'win%':>6} {'gross/t':>9} {'T':>6} {'net taker':>10} {'net maker':>10}")
    runs = [("LONG-only", "long", "d50"),
            ("SHORT-only (regime-gated)", "short", "d50"),
            ("LONG+SHORT combined", "both", "d50")]
    for name, mode, bc in runs:
        r, hold = bt(df, mode, bc)
        if len(r) == 0:
            print(f"  {name:>28}  no trades"); continue
        rnd = random_compare(df, len(r), max(1, int(np.median(hold))))
        t = (r.mean() - rnd.mean()) / (rnd.std() + 1e-12)
        nt = (np.prod(1 + (r - RT_TAKER)) - 1) * 100
        nm = (np.prod(1 + (r - RT_MAKER)) - 1) * 100
        print(f"  {name:>28} {len(r):>7} {(r>0).mean()*100:>5.1f}% {r.mean()*100:>+8.4f}% "
              f"{t:>+6.2f} {nt:>+9.0f}% {nm:>+9.0f}%")
