"""trend_lower_dd.py — how to cut the -53% drawdown HONESTLY.
4h trend portfolio (BTC/ETH/SOL/BNB). Two levers:
  (A) faster exit  -> get out of downturns sooner
  (B) position size -> scale exposure (DD scales ~linearly)
"""
import numpy as np
import pandas as pd

COST = 0.00075
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
ANN = 2190.0


def coin_pos(sym, rule):
    df = pd.read_csv(f"/Users/jags/Desktop/BTC-Flip-Bot/data/cache/{sym}_4h.csv",
                     parse_dates=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    c = df["close"]
    e20 = c.ewm(span=20, adjust=False, min_periods=20).mean()
    e50 = c.ewm(span=50, adjust=False, min_periods=50).mean()
    e100 = c.ewm(span=100, adjust=False, min_periods=100).mean()
    e200 = c.ewm(span=200, adjust=False, min_periods=200).mean()
    if rule == "golden(50>200)":
        pos = (e50 > e200)
    elif rule == "golden + px>e50":
        pos = (e50 > e200) & (c > e50)
    elif rule == "px>e100":
        pos = c > e100
    elif rule == "e20>e50 (fast)":
        pos = e20 > e50
    ret = c.pct_change()
    held = pos.astype(float).shift(1).fillna(0.0)
    flips = held.diff().abs().fillna(0.0)
    return (held * ret - COST * flips).dropna()


def metrics(sr, scale=1.0):
    s = sr * scale
    eq = (1 + s).cumprod()
    cagr = eq.iloc[-1] ** (1 / (len(s) / ANN)) - 1 if eq.iloc[-1] > 0 else -1
    sh = sr.mean() / sr.std() * np.sqrt(ANN) if sr.std() > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    y22 = (1 + s[s.index.year == 2022]).prod() - 1
    return eq.iloc[-1] - 1, cagr, sh, dd, y22


print("(A) FASTER EXIT RULES — full size")
print(f"  {'rule':>20} {'total':>9} {'CAGR':>6} {'Sharpe':>7} {'maxDD':>7} {'2022 bear':>10}")
base = None
for rule in ("golden(50>200)", "golden + px>e50", "px>e100", "e20>e50 (fast)"):
    port = pd.DataFrame({s: coin_pos(s, rule) for s in COINS}).mean(axis=1, skipna=True).dropna()
    if rule == "golden(50>200)":
        base = port
    tot, cg, sh, dd, y22 = metrics(port)
    print(f"  {rule:>20} {tot*100:>+8.0f}% {cg*100:>5.0f}% {sh:>7.2f} {dd*100:>6.0f}% {y22*100:>+9.0f}%")

print("\n(B) POSITION SIZE on the best faster-exit rule (golden + px>e50) — DD scales with size")
best = pd.DataFrame({s: coin_pos(s, "golden + px>e50") for s in COINS}).mean(axis=1, skipna=True).dropna()
print(f"  {'size':>8} {'total':>10} {'CAGR':>6} {'maxDD':>7}")
for sc in (1.0, 0.5, 0.33, 0.25):
    tot, cg, sh, dd, y22 = metrics(best, sc)
    print(f"  {sc*100:>6.0f}% {tot*100:>+9.0f}% {cg*100:>5.0f}% {dd*100:>6.0f}%")
