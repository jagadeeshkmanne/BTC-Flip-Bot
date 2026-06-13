"""best_trend_portfolio.py — best HONEST strategy: multi-coin trend portfolio.
Golden cross (EMA50>EMA200) long/flat spot, on 4h and daily, BTC/ETH/SOL/BNB,
equal-weight portfolio. Real fees. Full cycle + per-year. Shows REAL return AND
REAL drawdown (no 'realized-only' tricks), with modest leverage options.
"""
import numpy as np
import pandas as pd

COST = 0.00075
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]


def coin_returns(sym, tf):
    df = pd.read_csv(f"/Users/jags/Desktop/BTC-Flip-Bot/data/cache/{sym}_{tf}.csv",
                     parse_dates=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    c = df["close"]
    e50 = c.ewm(span=50, adjust=False, min_periods=50).mean()
    e200 = c.ewm(span=200, adjust=False, min_periods=200).mean()
    pos = (e50 > e200).astype(float)
    ret = c.pct_change()
    held = pos.shift(1).fillna(0.0)
    flips = held.diff().abs().fillna(0.0)
    return (held * ret - COST * flips).dropna()


def metrics(sr, ann, lev=1.0):
    s = sr * lev
    eq = (1 + s).cumprod()
    yrs = len(s) / ann
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if eq.iloc[-1] > 0 else -1
    sharpe = s.mean() / s.std() * np.sqrt(ann) if s.std() > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    return eq.iloc[-1] - 1, cagr, sharpe, dd


for tf, ann in [("4h", 2190.0), ("1d", 365.0)]:
    print(f"\n========== timeframe = {tf} ==========")
    cols = {}
    for sym in COINS:
        try:
            cols[sym] = coin_returns(sym, tf)
        except Exception:
            pass
    port = pd.DataFrame(cols).mean(axis=1, skipna=True).dropna()  # equal-weight, available coins
    print(f"  {'':16} {'total':>9} {'CAGR':>7} {'Sharpe':>7} {'maxDD':>7}")
    for sym in COINS:
        if sym in cols:
            tot, cg, sh, dd = metrics(cols[sym], ann)
            print(f"  {sym:16} {tot*100:>+8.0f}% {cg*100:>6.0f}% {sh:>7.2f} {dd*100:>6.0f}%")
    for lev in (1.0, 2.0):
        tot, cg, sh, dd = metrics(port, ann, lev)
        tag = "PORTFOLIO" if lev == 1 else f"PORTFOLIO {lev:.0f}x"
        print(f"  {tag:16} {tot*100:>+8.0f}% {cg*100:>6.0f}% {sh:>7.2f} {dd*100:>6.0f}%")
    # per-year portfolio (decay check)
    print("  per-year portfolio net return:")
    py = port.groupby(port.index.year).apply(lambda s: (1 + s).prod() - 1)
    print("   " + "  ".join(f"{y}:{v*100:+.0f}%" for y, v in py.items()))
