"""trend_enhancements.py — step OUTSIDE the EMA-tuning box. (user 2026-06-12)
Enhancements tested vs baseline (EMA30/150 + exit px<EMA50, 4 pairs, perp,
funding paid, fees):
  A baseline        : EMA30/150 & px>EMA50
  B donchian        : 55-bar breakout entry, 20-bar low exit (turtle-style)
  C adx-filter      : baseline + ADX(14on4h)>20
  D ensemble        : 3 signals vote (EMA cross / Donchian / ROC90>0), size=votes/3
  E chandelier      : EMA trend gate + ATR(22)x3 trailing-high exit
  F rel-momentum    : baseline signals, weight coins by 30d ROC rank (strongest gets more)
Protocol: IS ..2022-12-31 / OOS 2023... Judge on OOS. Per-year for the winner.
"""
import numpy as np
import pandas as pd

COST = 0.00075
ANN = 2190.0
SPLIT = pd.Timestamp("2023-01-01")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

F = pd.read_csv("/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_funding.csv",
                parse_dates=["timestamp"]).set_index("timestamp")
fund4h = (F["funding_rate"].resample("4h").ffill() / 2.0)

D = {}
for s in COINS:
    df = pd.read_csv(f"/Users/jags/Desktop/BTC-Flip-Bot/data/cache/{s}_4h.csv",
                     parse_dates=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    D[s] = df[["open", "high", "low", "close"]]


def atr(df, p=22):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"]-df["low"], (df["high"]-pc).abs(), (df["low"]-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/p, adjust=False).mean()


def adx(df, p=14):
    up = df["high"].diff(); dn = -df["low"].diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = atr(df, p)
    pdi = 100*plus.ewm(alpha=1/p, adjust=False).mean()/a
    mdi = 100*minus.ewm(alpha=1/p, adjust=False).mean()/a
    dx = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/p, adjust=False).mean()


def pos_baseline(df):
    c = df["close"]
    return ((c.ewm(span=30, adjust=False, min_periods=30).mean() > c.ewm(span=150, adjust=False, min_periods=150).mean())
            & (c > c.ewm(span=50, adjust=False, min_periods=50).mean())).astype(float)


def pos_donchian(df):
    c = df["close"]
    hi = df["high"].rolling(55).max().shift(1)
    lo = df["low"].rolling(20).min().shift(1)
    pos = pd.Series(np.nan, index=c.index)
    pos[c > hi] = 1.0; pos[c < lo] = 0.0
    return pos.ffill().fillna(0.0)


def pos_adx(df):
    return pos_baseline(df) * (adx(df) > 20).astype(float)


def pos_ensemble(df):
    c = df["close"]
    v1 = pos_baseline(df)
    v2 = pos_donchian(df)
    v3 = (c / c.shift(540) - 1 > 0).astype(float)        # ROC ~90d on 4h
    return (v1 + v2 + v3) / 3.0


def pos_chandelier(df):
    c = df["close"]
    trend = c.ewm(span=30, adjust=False, min_periods=30).mean() > c.ewm(span=150, adjust=False, min_periods=150).mean()
    a = atr(df)
    hh = df["high"].rolling(22).max()
    stop = hh - 3*a
    pos = (trend & (c > stop)).astype(float)
    return pos


def port_returns(pos_fn, weight_mom=False):
    rs = {}
    for s in COINS:
        df = D[s]; c = df["close"]
        pos = pos_fn(df)
        held = pos.shift(1).fillna(0.0)
        flips = held.diff().abs().fillna(0.0)
        fr = fund4h.reindex(c.index).fillna(0.0)
        rs[s] = pd.DataFrame({"r": held*c.pct_change() - held*fr - COST*flips,
                              "mom": c/c.shift(180)-1, "held": held})
    idx = rs["BTCUSDT"].index
    if not weight_mom:
        return pd.DataFrame({s: rs[s]["r"] for s in COINS}).mean(axis=1, skipna=True).dropna()
    # relative momentum weights among active coins (no lookahead: momentum shifted)
    R = pd.DataFrame({s: rs[s]["r"] for s in COINS})
    M = pd.DataFrame({s: rs[s]["mom"].shift(1) for s in COINS})
    H = pd.DataFrame({s: rs[s]["held"] for s in COINS})
    rank = M.where(H > 0).rank(axis=1)               # rank only active coins
    w = rank.div(rank.sum(axis=1), axis=0).fillna(0.0)
    return (R*w).sum(axis=1).dropna()


def met(sr):
    eq = (1+sr).cumprod()
    yrs = len(sr)/ANN
    sh = sr.mean()/sr.std()*np.sqrt(ANN) if sr.std() > 0 else 0
    dd = (eq/eq.cummax()-1).min()
    return (eq.iloc[-1]-1)*100, sh, dd*100


variants = [("A baseline EMA", lambda: port_returns(pos_baseline)),
            ("B donchian 55/20", lambda: port_returns(pos_donchian)),
            ("C +ADX>20 filter", lambda: port_returns(pos_adx)),
            ("D ensemble vote", lambda: port_returns(pos_ensemble)),
            ("E chandelier exit", lambda: port_returns(pos_chandelier)),
            ("F rel-momentum wts", lambda: port_returns(pos_baseline, weight_mom=True))]
print(f"  {'variant':>20} | {'IS tot':>9} {'IS Sh':>6} {'IS DD':>6} | {'OOS tot':>8} {'OOS Sh':>7} {'OOS DD':>7}")
results = {}
for name, fn in variants:
    sr = fn()
    results[name] = sr
    i, o = sr[sr.index < SPLIT], sr[sr.index >= SPLIT]
    ti, si, di = met(i); to, so, do = met(o)
    print(f"  {name:>20} | {ti:>+8.0f}% {si:>6.2f} {di:>5.0f}% | {to:>+7.0f}% {so:>7.2f} {do:>6.0f}%")
# per-year for best OOS Sharpe
best = max(results, key=lambda k: met(results[k][results[k].index >= SPLIT])[1])
sr = results[best]
py = sr.groupby(sr.index.year).apply(lambda s: (1+s).prod()-1)
print(f"\nBEST by OOS Sharpe: {best}")
print("  per-year: " + "  ".join(f"{y}:{v*100:+.0f}%" for y, v in py.items()))
t, s, d = met(sr)
print(f"  full period: total {t:+.0f}%  Sharpe {s:.2f}  maxDD {d:.0f}%")
