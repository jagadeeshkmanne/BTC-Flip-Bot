"""daily_funding_overlay.py — daily trend + funding overlay. (user 2026-06-12)
Combine the two real signals: daily trend (long/flat spot) and funding positioning
(skip/lighten when longs are over-crowded). Honest, fees on position changes,
IS/OOS, vs buy & hold and vs trend-alone. Spot => no funding paid; funding is
used as a SENTIMENT FILTER only (no lookahead: position uses prior-day info).
"""
import numpy as np
import pandas as pd

COST = 0.00075
ANN = 365.0
P = pd.read_csv("/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_1h.csv", parse_dates=["timestamp"]).set_index("timestamp")
F = pd.read_csv("/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_funding.csv", parse_dates=["timestamp"]).set_index("timestamp")

# daily price + daily mean funding (bp/8h)
g = pd.DataFrame({"close": P["close"].resample("1D").last()}).dropna()
fund_d = (F["funding_rate"] * 10000).resample("1D").mean()
g["fund"] = fund_d.reindex(g.index).ffill()
g = g.dropna()
c = g["close"]
g["sma200"] = c.rolling(200).mean()
g["e50"] = c.ewm(span=50, adjust=False, min_periods=50).mean()
g["e200"] = c.ewm(span=200, adjust=False, min_periods=200).mean()
d = c.diff(); ag = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean(); al = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
g["rsi"] = 100 - 100/(1 + ag/al.replace(0, np.nan))
# funding percentile (trailing, no lookahead): is today's funding extreme vs last 90d?
g["fund_pct"] = g["fund"].rolling(90).apply(lambda x: (x.iloc[-1] >= x).mean(), raw=False)
g = g.dropna()
c = g["close"]
ret = c.pct_change().fillna(0.0)
mid = g.index[len(g)//2]


def stats(pos, label):
    held = pos.shift(1).fillna(0.0)
    flips = held.diff().abs().fillna(0.0)
    sr = held * ret - COST * flips
    eq = (1 + sr).cumprod()
    yrs = len(sr)/ANN
    cagr = eq.iloc[-1]**(1/yrs) - 1 if eq.iloc[-1] > 0 else -1
    sharpe = sr.mean()/sr.std()*np.sqrt(ANN) if sr.std() > 0 else 0
    dd = (eq/eq.cummax()-1).min()
    is_ = (1+sr[sr.index < mid]).prod()-1
    oos = (1+sr[sr.index >= mid]).prod()-1
    expo = (held > 0).mean()*100
    print(f"  {label:38} CAGR {cagr*100:>6.1f}%  Sh {sharpe:>5.2f}  DD {dd*100:>4.0f}%  "
          f"expo {expo:>3.0f}%  IS {is_*100:>+7.0f}%  OOS {oos*100:>+7.0f}%")


bh_sh = ret.mean()/ret.std()*np.sqrt(ANN)
bh = (c.iloc[-1]/c.iloc[0]-1)
bh_dd = (c/c.cummax()-1).min()
print(f"Daily {g.index[0].date()}->{g.index[-1].date()} N={len(g)}  IS/OOS@{mid.date()}")
print(f"  {'BUY & HOLD':38} total {bh*100:+.0f}%   Sh {bh_sh:.2f}  DD {bh_dd*100:.0f}%\n")

trend = c > g["sma200"]
golden = g["e50"] > g["e200"]
print("baselines:")
stats(trend.astype(float), "trend (px>SMA200)")
stats(golden.astype(float), "golden (EMA50>EMA200)")
print("\n+ funding overlay (skip/lighten when longs over-crowded):")
stats((trend & (g["fund_pct"] < 0.90)).astype(float), "trend + skip funding>90pct")
stats((trend & (g["fund_pct"] < 0.75)).astype(float), "trend + skip funding>75pct")
stats((golden & (g["fund_pct"] < 0.90)).astype(float), "golden + skip funding>90pct")
# size down (half) instead of skip when crowded
half = trend.astype(float) * np.where(g["fund_pct"] >= 0.90, 0.5, 1.0)
stats(pd.Series(half, index=g.index), "trend, half-size when funding>90pct")
# funding contrarian boost: full long in trend, also long when funding very negative even if no trend
boost = ((trend) | (g["fund_pct"] < 0.10)).astype(float)
stats(boost, "trend OR funding<10pct (add cheap longs)")
print("\nfunding-only (no trend), for reference:")
stats((g["fund_pct"] < 0.50).astype(float), "long when funding below median")
