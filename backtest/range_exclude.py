"""range_exclude.py — does excluding entries by how 'in-range' price has been help?
(user 2026-06-12) For each v2.2 entry, measure the range width over the prior
1h/2h/4h/1d, then check forward outcome by range bucket. If any range regime is
a clearly worse (or better) subset, we can exclude it.
"""
import numpy as np
import pandas as pd
import fresh_honest as fh

bt = fh.prep()
O, H, L = bt["open"].values, bt["high"].values, bt["low"].values
RSI, ATR, GAP, TR = bt["rsi"].values, bt["atr_pct"].values, bt["gap"].values, bt["trend"].values
C = bt["close"].values
n = len(bt)
RT = 2*(0.00055+0.0002)
LOOKBACKS = {"1h": 12, "2h": 24, "4h": 48, "1d": 288}
# range width % over each lookback (rolling), known at signal bar
rng = {}
for k, lb in LOOKBACKS.items():
    hi = pd.Series(H).rolling(lb).max(); lo = pd.Series(L).rolling(lb).min()
    rng[k] = ((hi - lo) / pd.Series(C) * 100).values

TP, SL = 0.005, 0.006
recs = []
i = 1
while i < n-1:
    if (np.isnan(RSI[i]) or np.isnan(ATR[i]) or np.isnan(GAP[i]) or np.isnan(TR[i])
            or ATR[i] > 0.80 or GAP[i] < 0.0020):
        i += 1; continue
    side = 1 if RSI[i] <= 35 else (-1 if RSI[i] >= 65 else 0)
    if side == 0:
        i += 1; continue
    entry = O[i+1]; tp_px = entry*(1+TP*side); sl_px = entry*(1-SL*side)
    ret = None; j = i+1
    while j < n:
        if (side == 1 and L[j] <= sl_px) or (side == -1 and H[j] >= sl_px):
            ret = -SL; break
        if (side == 1 and H[j] >= tp_px) or (side == -1 and L[j] <= tp_px):
            ret = TP; break
        j += 1
    if ret is None:
        break
    rec = {"net": (ret - RT)*100}
    for k in LOOKBACKS:
        rec[k] = rng[k][i]
    recs.append(rec); i = j+1

df = pd.DataFrame(recs)
print(f"v2.2 entries: {len(df):,}  (TP {TP*100}% / SL {SL*100}%, honest fees)")
print(f"baseline net: {df['net'].mean():+.4f}%/trade  (negative = loses)\n")
print("Net expectancy by RANGE-WIDTH quintile over each lookback")
print("(tight range = small width = consolidation; wide = trending/volatile):\n")
for k in LOOKBACKS:
    q = pd.qcut(df[k], 5, labels=["tightest","tight","mid","wide","widest"])
    g = df.groupby(q, observed=True)["net"].agg(["count", "mean"])
    print(f"  {k} range:")
    for idx, row in g.iterrows():
        flag = "  <-- best" if row["mean"] == g["mean"].max() else ""
        print(f"     {idx:>9}  N={row['count']:>6.0f}  net {row['mean']:>+8.4f}%{flag}")
    best = g["mean"].max()
    print(f"     -> even the BEST {k} bucket: {best:+.4f}%/trade  "
          f"({'PROFITABLE' if best > 0 else 'still loses'})\n")
