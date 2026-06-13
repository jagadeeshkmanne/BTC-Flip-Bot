"""trend_improve_round2.py — improvement round 2 on the ADX-enhanced 4h trend.
Base C: EMA30/150 & px>EMA50 & ADX14>20, 4 pairs, perp, funding paid, fees.
Tests:
  1) timeframe gradient: same rules on 1h / 4h (close the gap properly)
  2) BTC-leader filter : alts only long when BTC is also in uptrend
  3) inverse-vol weights: risk parity across active coins
  4) pyramid           : half size on entry, full after +1 ATR in favor
  5) ADX threshold robustness: 15 / 20 / 25 (plateau or knife-edge?)
IS ..2022-12-31, OOS 2023.. — judge on OOS.
"""
import numpy as np
import pandas as pd

COST = 0.00075
SPLIT = pd.Timestamp("2023-01-01")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

F = pd.read_csv("/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_funding.csv",
                parse_dates=["timestamp"]).set_index("timestamp")


def load(sym, tf):
    df = pd.read_csv(f"/Users/jags/Desktop/BTC-Flip-Bot/data/cache/{sym}_{tf}.csv",
                     parse_dates=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    return df[["open", "high", "low", "close"]]


def atr(df, p=14):
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


def pos_c(df, adx_thr=20):
    c = df["close"]
    return ((c.ewm(span=30, adjust=False, min_periods=30).mean() > c.ewm(span=150, adjust=False, min_periods=150).mean())
            & (c > c.ewm(span=50, adjust=False, min_periods=50).mean())
            & (adx(df) > adx_thr)).astype(float)


def build(tf="4h", adx_thr=20, leader=False, invvol=False, pyramid=False):
    bars_per_8h = {"1h": 8, "4h": 2}[tf]
    fund = (F["funding_rate"].resample(tf).ffill() / bars_per_8h)
    P = {s: load(s, tf) for s in COINS}
    btc_up = pos_c(P["BTCUSDT"], adx_thr)
    R, W = {}, {}
    for s in COINS:
        df = P[s]; c = df["close"]
        pos = pos_c(df, adx_thr)
        if leader and s != "BTCUSDT":
            pos = pos * btc_up.reindex(pos.index).ffill().fillna(0.0)
        if pyramid:
            a = atr(df)
            entry_px = c.where(pos.diff() == 1).ffill()
            full = (c > entry_px + a).astype(float)
            pos = pos * (0.5 + 0.5*full)
        held = pos.shift(1).fillna(0.0)
        flips = held.diff().abs().fillna(0.0)
        fr = fund.reindex(c.index).fillna(0.0)
        R[s] = held*c.pct_change() - held*fr - COST*flips
        W[s] = (1.0 / c.pct_change().rolling(180).std()) if invvol else pd.Series(1.0, index=c.index)
    Rdf = pd.DataFrame(R); Wdf = pd.DataFrame(W).shift(1)
    Wn = Wdf.div(Wdf.sum(axis=1), axis=0)
    return (Rdf*Wn).sum(axis=1).dropna() if invvol else Rdf.mean(axis=1, skipna=True).dropna()


def met(sr, tf):
    ann = {"1h": 8760.0, "4h": 2190.0}[tf]
    eq = (1+sr).cumprod()
    sh = sr.mean()/sr.std()*np.sqrt(ann) if sr.std() > 0 else 0
    dd = (eq/eq.cummax()-1).min()
    return (eq.iloc[-1]-1)*100, sh, dd*100


tests = [("C base 4h (ADX>20)", dict(tf="4h")),
         ("1h same rules", dict(tf="1h")),
         ("BTC-leader gate", dict(tf="4h", leader=True)),
         ("inverse-vol wts", dict(tf="4h", invvol=True)),
         ("pyramid 2-stage", dict(tf="4h", pyramid=True)),
         ("ADX>15", dict(tf="4h", adx_thr=15)),
         ("ADX>25", dict(tf="4h", adx_thr=25))]
print(f"  {'variant':>20} | {'IS tot':>9} {'IS Sh':>6} {'IS DD':>6} | {'OOS tot':>8} {'OOS Sh':>7} {'OOS DD':>7}")
for name, kw in tests:
    sr = build(**kw)
    tf = kw.get("tf", "4h")
    i, o = sr[sr.index < SPLIT], sr[sr.index >= SPLIT]
    ti, si, di = met(i, tf); to, so, do = met(o, tf)
    print(f"  {name:>20} | {ti:>+8.0f}% {si:>6.2f} {di:>5.0f}% | {to:>+7.0f}% {so:>7.2f} {do:>6.0f}%")
