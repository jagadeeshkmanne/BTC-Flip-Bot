"""v3_param_plateau.py — fine-tune sweep around v3 = plateau or peak?
Grid: fast x slow x exitEMA x ADX, 4 pairs, leader gate, perp+funding+fees.
Protocol: rank by IS Sharpe (..2022); show OOS (2023..) for the top-12 and for
v3 itself; then single-axis sensitivity around v3. If neighbors are all close,
v3 is robust; if v3 or any cell stands alone, it's curve-fit.
"""
import numpy as np
import pandas as pd
import trend_improve_round2 as T

SPLIT = pd.Timestamp("2023-01-01")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
fund = (T.F["funding_rate"].resample("4h").ffill() / 2.0)
P = {s: T.load(s, "4h") for s in COINS}
ADX_CACHE = {s: T.adx(P[s]) for s in COINS}


def pos_param(sym, fast, slow, ex, athr):
    df = P[sym]; c = df["close"]
    p = ((c.ewm(span=fast, adjust=False, min_periods=fast).mean()
          > c.ewm(span=slow, adjust=False, min_periods=slow).mean())
         & (c > c.ewm(span=ex, adjust=False, min_periods=ex).mean())
         & (ADX_CACHE[sym] > athr))
    return p.astype(float)


def port(fast, slow, ex, athr):
    btc_up = pos_param("BTCUSDT", fast, slow, ex, athr)
    R = {}
    for s in COINS:
        c = P[s]["close"]
        pos = pos_param(s, fast, slow, ex, athr)
        if s != "BTCUSDT":
            pos = pos * btc_up.reindex(pos.index).ffill().fillna(0.0)
        held = pos.shift(1).fillna(0.0)
        flips = held.diff().abs().fillna(0.0)
        fr = fund.reindex(c.index).fillna(0.0)
        R[s] = held*c.pct_change() - held*fr - T.COST*flips
    return pd.DataFrame(R).mean(axis=1, skipna=True).dropna()


def met(sr):
    eq = (1+sr).cumprod()
    sh = sr.mean()/sr.std()*np.sqrt(2190.0) if sr.std() > 0 else 0
    dd = (eq/eq.cummax()-1).min()
    return (eq.iloc[-1]-1)*100, sh, dd*100


rows = []
for fast in (20, 30, 40):
    for slow in (100, 150, 200):
        for ex in (40, 50, 60):
            for athr in (18, 22):
                sr = port(fast, slow, ex, athr)
                i, o = sr[sr.index < SPLIT], sr[sr.index >= SPLIT]
                rows.append(((fast, slow, ex, athr), met(i), met(o)))
rows.sort(key=lambda r: r[1][1], reverse=True)
print("grid (fast,slow,exit,ADX) ranked by IS Sharpe — OOS shown (judge plateau, don't cherry-pick):")
print(f"  {'params':>18} | {'IS Sh':>6} {'IS DD':>6} | {'OOS tot':>8} {'OOS Sh':>7} {'OOS DD':>7}")
for p, mi, mo in rows[:12]:
    print(f"  {str(p):>18} | {mi[1]:>6.2f} {mi[2]:>5.0f}% | {mo[0]:>+7.0f}% {mo[1]:>7.2f} {mo[2]:>6.0f}%")
oos_sh = [r[2][1] for r in rows]
print(f"\n  across ALL {len(rows)} cells: OOS Sharpe median {np.median(oos_sh):.2f}, "
      f"min {min(oos_sh):.2f}, max {max(oos_sh):.2f}")
# v3 exact
sr = port(30, 150, 50, 20)
i, o = sr[sr.index < SPLIT], sr[sr.index >= SPLIT]
print(f"  v3 (30,150,50,20):  IS Sh {met(i)[1]:.2f} | OOS {met(o)[0]:+.0f}% Sh {met(o)[1]:.2f} DD {met(o)[2]:.0f}%")
