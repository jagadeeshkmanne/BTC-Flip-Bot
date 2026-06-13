"""trend_long_short.py — trend portfolio: long/flat vs long/SHORT (short in bear).
Does shorting the bear (EMA50<EMA200) help or hurt? 4h, BTC/ETH/SOL/BNB, per-year.
"""
import numpy as np
import pandas as pd

COST = 0.00075
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
ANN = 2190.0


def coin_returns(sym, stance):
    df = pd.read_csv(f"/Users/jags/Desktop/BTC-Flip-Bot/data/cache/{sym}_4h.csv",
                     parse_dates=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    c = df["close"]
    e50 = c.ewm(span=50, adjust=False, min_periods=50).mean()
    e200 = c.ewm(span=200, adjust=False, min_periods=200).mean()
    up = e50 > e200
    if stance == "LF":
        pos = up.astype(float)                      # long / flat
    else:
        pos = np.where(up, 1.0, -1.0)               # long / SHORT
        pos = pd.Series(pos, index=c.index)
    ret = c.pct_change()
    held = pos.shift(1).fillna(0.0)
    flips = held.diff().abs().fillna(0.0)
    return (held * ret - COST * flips).dropna()


def metrics(sr):
    eq = (1 + sr).cumprod()
    cagr = eq.iloc[-1] ** (1 / (len(sr) / ANN)) - 1 if eq.iloc[-1] > 0 else -1
    sh = sr.mean() / sr.std() * np.sqrt(ANN) if sr.std() > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    return eq.iloc[-1] - 1, cagr, sh, dd


for stance, label in [("LF", "LONG / FLAT (sit out bear)"), ("LS", "LONG / SHORT (short the bear)")]:
    port = pd.DataFrame({s: coin_returns(s, stance) for s in COINS}).mean(axis=1, skipna=True).dropna()
    tot, cg, sh, dd = metrics(port)
    print(f"\n{label}")
    print(f"  total {tot*100:+.0f}%   CAGR {cg*100:.0f}%   Sharpe {sh:.2f}   maxDD {dd*100:.0f}%")
    py = port.groupby(port.index.year).apply(lambda s: (1 + s).prod() - 1)
    print("  per-year: " + "  ".join(f"{y}:{v*100:+.0f}%" for y, v in py.items()))
