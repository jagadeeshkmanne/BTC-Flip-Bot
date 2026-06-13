"""trend_rr.py — R:R / trade stats for the recommended strategy.
4h, golden cross (EMA50>EMA200) + exit when price<EMA50, long/flat spot.
Pairs: BTC/ETH/SOL/BNB USDT. A 'trade' = one long episode (entry->exit).
"""
import numpy as np
import pandas as pd

FEE = 0.00075
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]


def trades(sym):
    df = pd.read_csv(f"/Users/jags/Desktop/BTC-Flip-Bot/data/cache/{sym}_4h.csv",
                     parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    c = df["close"].values
    e50 = pd.Series(c).ewm(span=50, adjust=False, min_periods=50).mean().values
    e200 = pd.Series(c).ewm(span=200, adjust=False, min_periods=200).mean().values
    pos = (e50 > e200) & (c > e50)
    rets = []
    i = 1; n = len(c)
    while i < n - 1:
        if not pos[i] or np.isnan(e200[i]):
            i += 1; continue
        entry = c[i]; j = i + 1
        while j < n - 1 and pos[j]:
            j += 1
        rets.append(c[j] / entry - 1 - 2 * FEE)
        i = j + 1
    return np.array(rets)


allr = []
print(f"  {'pair':>8} {'trades':>7} {'win%':>6} {'avgWin':>8} {'avgLoss':>8} {'R:R':>6} {'PF':>6} {'bigWin':>8} {'bigLoss':>8}")
for sym in COINS:
    r = trades(sym); allr.append(r)
    w = r[r > 0]; l = r[r <= 0]
    rr = (w.mean() / abs(l.mean())) if len(l) else float("inf")
    pf = (w.sum() / abs(l.sum())) if len(l) else float("inf")
    print(f"  {sym:>8} {len(r):>7} {len(w)/len(r)*100:>5.0f}% {w.mean()*100:>+7.1f}% "
          f"{l.mean()*100:>+7.1f}% {rr:>6.2f} {pf:>6.2f} {r.max()*100:>+7.0f}% {r.min()*100:>+7.1f}%")
r = np.concatenate(allr)
w = r[r > 0]; l = r[r <= 0]
print(f"  {'ALL':>8} {len(r):>7} {len(w)/len(r)*100:>5.0f}% {w.mean()*100:>+7.1f}% "
      f"{l.mean()*100:>+7.1f}% {w.mean()/abs(l.mean()):>6.2f} {w.sum()/abs(l.sum()):>6.2f} "
      f"{r.max()*100:>+7.0f}% {r.min()*100:>+7.1f}%")
print(f"\n  avg trade {r.mean()*100:+.2f}%   median hold not shown   win:loss size ratio = the R:R column")
