"""bbpct_5m_vs_15m.py — does the BB%B long strategy survive on 5m vs 15m?"""
import numpy as np
import pandas as pd
from replicate_bbpct_scalp import wma, hma, pctrank, backtest, random_compare

RT_TAKER, RT_MAKER = 0.0014, 0.0005


def load_tf(sym, tf):
    df = pd.read_csv(f"/Users/jags/Desktop/BTC-Flip-Bot/data/cache/{sym}_{tf}.csv",
                     parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    c, v = df["close"].values, df["volume"].values
    L = 20
    vwma = (pd.Series(c * v).rolling(L).sum() / pd.Series(v).rolling(L).sum()).values
    std = pd.Series(c).rolling(L).std().values
    up, lo = vwma + 2 * std, vwma - 2 * std
    pctB = (c - lo) / (up - lo)
    df["pr"] = pctrank(pctB, 350)
    df["ratio"] = hma(c, 10) / pd.Series(c).ewm(span=55, adjust=False).mean().values
    dd = df.set_index("timestamp")["close"].resample("1D").last()
    dup = (dd > dd.rolling(20).mean()).shift(1)
    df["dayup"] = dup.reindex(df["timestamp"], method="ffill").values
    return df


print(f"  {'coin':>5} {'TF':>4} {'trades':>7} {'hold':>6} {'gross/t':>9} {'T':>6} {'net taker':>10} {'net maker':>10}")
for sym in ("BTCUSDT", "ETHUSDT"):
    for tf in ("5m", "15m"):
        df = load_tf(sym, tf)
        r, hold = backtest(df)
        if len(r) == 0:
            print(f"  {sym[:3]:>5} {tf:>4}  no trades"); continue
        rnd = random_compare(df, len(r), max(1, int(np.median(hold))))
        t = (r.mean() - rnd.mean()) / (rnd.std() + 1e-12)
        nt = (np.prod(1 + (r - RT_TAKER)) - 1) * 100
        nm = (np.prod(1 + (r - RT_MAKER)) - 1) * 100
        hh = np.median(hold) * (5 if tf == "5m" else 15) / 60
        print(f"  {sym[:3]:>5} {tf:>4} {len(r):>7} {hh:>5.1f}h {r.mean()*100:>+8.4f}% "
              f"{t:>+6.2f} {nt:>+9.0f}% {nm:>+9.0f}%")
