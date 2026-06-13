"""leverage_allweather.py — can a 3x long/short trend strategy work in ALL regimes?
4h, BTC/ETH/SOL/BNB. long/short = profit in bull AND bear (always in market).
Test 1x/2x/3x with realistic liquidation (equity hits 0 -> wiped). Per-year.
"""
import numpy as np
import pandas as pd

COST = 0.00075
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
ANN = 2190.0


def coin_ret(sym, stance):
    df = pd.read_csv(f"/Users/jags/Desktop/BTC-Flip-Bot/data/cache/{sym}_4h.csv",
                     parse_dates=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    c = df["close"]
    e50 = c.ewm(span=50, adjust=False, min_periods=50).mean()
    e200 = c.ewm(span=200, adjust=False, min_periods=200).mean()
    up = e50 > e200
    if stance == "LF":
        pos = (up & (c > e50)).astype(float)         # long/flat, faster exit
    else:
        pos = pd.Series(np.where(up, 1.0, -1.0), index=c.index)  # long/short always-in
    ret = c.pct_change()
    held = pos.shift(1).fillna(0.0)
    flips = held.diff().abs().fillna(0.0)
    return (held * ret - COST * flips).dropna()


def lever(port, lev):
    eq = 1.0; series = []
    liq = False
    for x in port.values:
        eq *= (1 + lev * x)
        if eq <= 0:
            eq = 0.0; liq = True; series.append(eq); break
        series.append(eq)
    s = pd.Series(series, index=port.index[:len(series)])
    dd = (s / s.cummax() - 1).min() * 100
    return (s.iloc[-1] - 1) * 100, dd, liq


for stance, lab in [("LS", "LONG/SHORT (all-weather: bull+bear)"),
                    ("LF", "LONG/FLAT (bull only, sit out bear)")]:
    port = pd.DataFrame({s: coin_ret(s, stance) for s in COINS}).mean(axis=1, skipna=True).dropna()
    print(f"\n{lab}")
    print(f"  {'lev':>4} {'total':>12} {'maxDD':>8} {'status':>12}")
    for L in (1, 2, 3):
        tot, dd, liq = lever(port, L)
        print(f"  {L:>3}x {tot:>+11,.0f}% {dd:>7.0f}% {'LIQUIDATED' if liq else 'survived':>12}")
    py = port.groupby(port.index.year).apply(lambda s: (1 + s).prod() - 1)
    print("  per-year (1x): " + "  ".join(f"{y}:{v*100:+.0f}%" for y, v in py.items()))
