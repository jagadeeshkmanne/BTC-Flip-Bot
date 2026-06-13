"""trend_round3_dd_vs_ret.py — round 3: raise return AND cut DD?
Base v3: 4h, EMA30/150 & px>EMA50 & ADX>20, BTC-leader gate, 4 perp pairs.
  G v3 baseline 1x / 2x
  H v3 + pyramid (half->full after +1 ATR)
  I conviction sizing: exposure = fraction of signals true (EMA cross, px>e50,
    ADX>20, BTC-up) -> 0/0.25/0.5/0.75/1.0 per coin
  J anti-martingale leverage: 2x at equity highs (DD<5%), 1x in drawdown
IS ..2022 / OOS 2023.. ; judge OOS.
"""
import numpy as np
import pandas as pd
import trend_improve_round2 as T

SPLIT = pd.Timestamp("2023-01-01")
COINS = T.COINS
COST = T.COST


def build_conviction():
    fund = (T.F["funding_rate"].resample("4h").ffill() / 2.0)
    P = {s: T.load(s, "4h") for s in COINS}
    btc_up = T.pos_c(P["BTCUSDT"])
    R = {}
    for s in COINS:
        df = P[s]; c = df["close"]
        e30 = c.ewm(span=30, adjust=False, min_periods=30).mean()
        e150 = c.ewm(span=150, adjust=False, min_periods=150).mean()
        e50 = c.ewm(span=50, adjust=False, min_periods=50).mean()
        v = ((e30 > e150).astype(float) + (c > e50).astype(float)
             + (T.adx(df) > 20).astype(float)
             + btc_up.reindex(c.index).ffill().fillna(0.0))
        pos = (v / 4.0).where(v >= 2, 0.0)        # need >=2 votes, size by votes
        held = pos.shift(1).fillna(0.0)
        flips = held.diff().abs().fillna(0.0)
        fr = fund.reindex(c.index).fillna(0.0)
        R[s] = held*c.pct_change() - held*fr - COST*flips
    return pd.DataFrame(R).mean(axis=1, skipna=True).dropna()


def antimartingale(sr, hi=2.0, lo=1.0, dd_thr=0.05):
    eq = 1.0; mx = 1.0; out = []
    lev = hi
    for x in sr.values:
        out.append(x*lev)
        eq *= (1 + x*lev); mx = max(mx, eq)
        lev = hi if (1 - eq/mx) < dd_thr else lo   # decided AFTER the bar (no lookahead)
    return pd.Series(out, index=sr.index)


def met(sr):
    eq = (1+sr).cumprod()
    sh = sr.mean()/sr.std()*np.sqrt(2190.0) if sr.std() > 0 else 0
    dd = (eq/eq.cummax()-1).min()
    return (eq.iloc[-1]-1)*100, sh, dd*100


v3 = T.build(tf="4h", leader=True)
v3pyr = T.build(tf="4h", leader=True, pyramid=True)
variants = [("G v3 1x", v3), ("G v3 2x", v3*2.0),
            ("H v3+pyramid 1x", v3pyr), ("H v3+pyramid 2x", v3pyr*2.0),
            ("I conviction-sized", build_conviction()),
            ("J anti-mart 1-2x", antimartingale(v3))]
print(f"  {'variant':>20} | {'IS tot':>9} {'IS Sh':>6} {'IS DD':>6} | {'OOS tot':>8} {'OOS Sh':>7} {'OOS DD':>7}")
for name, sr in variants:
    i, o = sr[sr.index < SPLIT], sr[sr.index >= SPLIT]
    ti, si, di = met(i); to, so, do = met(o)
    print(f"  {name:>20} | {ti:>+8.0f}% {si:>6.2f} {di:>5.0f}% | {to:>+7.0f}% {so:>7.2f} {do:>6.0f}%")
