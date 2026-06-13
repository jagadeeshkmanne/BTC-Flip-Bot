"""bbpct_scaled_tf.py — fair 5m test: scale all lookbacks to keep the SAME time
horizons as the 15m version (15m->5m = x3). Compare native-15m vs scaled-5m.
"""
import numpy as np
import pandas as pd
from replicate_bbpct_scalp import wma, hma, pctrank, backtest, random_compare

RT_TAKER, RT_MAKER = 0.0014, 0.0005


def load_scaled(sym, tf, bb_len, pr_lb, hma_n, ema_n):
    df = pd.read_csv(f"/Users/jags/Desktop/BTC-Flip-Bot/data/cache/{sym}_{tf}.csv",
                     parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    c, v = df["close"].values, df["volume"].values
    vwma = (pd.Series(c * v).rolling(bb_len).sum() / pd.Series(v).rolling(bb_len).sum()).values
    std = pd.Series(c).rolling(bb_len).std().values
    pctB = (c - (vwma - 2 * std)) / ((vwma + 2 * std) - (vwma - 2 * std))
    df["pr"] = pctrank(pctB, pr_lb)
    df["ratio"] = hma(c, hma_n) / pd.Series(c).ewm(span=ema_n, adjust=False).mean().values
    dd = df.set_index("timestamp")["close"].resample("1D").last()
    df["dayup"] = (dd > dd.rolling(20).mean()).shift(1).reindex(df["timestamp"], method="ffill").values
    return df


CONFIGS = [
    ("BTC 15m native", "BTCUSDT", "15m", 20, 350, 10, 55),
    ("BTC 5m  scaled x3", "BTCUSDT", "5m", 60, 1050, 30, 165),
    ("BTC 5m  native(unfair)", "BTCUSDT", "5m", 20, 350, 10, 55),
    ("ETH 15m native", "ETHUSDT", "15m", 20, 350, 10, 55),
    ("ETH 5m  scaled x3", "ETHUSDT", "5m", 60, 1050, 30, 165),
]
print(f"  {'config':>24} {'trades':>7} {'hold':>6} {'gross/t':>9} {'T':>6} {'net taker':>10} {'net maker':>10}")
for name, sym, tf, bb, pl, hm, em in CONFIGS:
    df = load_scaled(sym, tf, bb, pl, hm, em)
    r, hold = backtest(df)
    if len(r) == 0:
        print(f"  {name:>24}  no trades"); continue
    rnd = random_compare(df, len(r), max(1, int(np.median(hold))))
    t = (r.mean() - rnd.mean()) / (rnd.std() + 1e-12)
    nt = (np.prod(1 + (r - RT_TAKER)) - 1) * 100
    nm = (np.prod(1 + (r - RT_MAKER)) - 1) * 100
    hh = np.median(hold) * (5 if tf == "5m" else 15) / 60
    print(f"  {name:>24} {len(r):>7} {hh:>5.1f}h {r.mean()*100:>+8.4f}% {t:>+6.2f} {nt:>+9.0f}% {nm:>+9.0f}%")
