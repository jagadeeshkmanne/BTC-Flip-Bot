"""gap_entry.py — can the EMA gap (its sign AND slope) time entries? (user 2026-06-12)

"Yellow crossing up, gap going up then coming down" = use the EMA20-EMA50 gap as
the signal: enter when it crosses up & EXPANDS, exit when it peaks & CONTRACTS.

Tested on 15m (the gap's native TF in v2.2) and 5m, long/flat spot, net of fees,
vs buy & hold. Three readings of the idea:
  cross      : long while gap>0                (plain EMA cross)
  gap_rising : long while gap>0 AND gap rising  (cross up + expanding; exit when it contracts)
  slope_only : long while gap rising            (pure gap-momentum, ignore sign)
Position taken next bar (no lookahead). Fee 0.075%/side on position changes.
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
COST = 0.00075
raw = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").set_index("timestamp")


def frame(rule):
    c = raw["close"].resample(rule).last().dropna()
    e20 = c.ewm(span=20, adjust=False, min_periods=20).mean()
    e50 = c.ewm(span=50, adjust=False, min_periods=50).mean()
    gap = (e20 - e50) / e50 * 100          # signed gap, %
    df = pd.DataFrame({"close": c, "gap": gap}).dropna()
    df["dgap"] = df["gap"].diff()
    return df


def metrics(pos, ret, ann, mid):
    held = pos.shift(1).fillna(0.0)
    flips = held.diff().abs().fillna(0.0)
    sr = held * ret - COST * flips
    eq = (1 + sr).cumprod()
    yrs = len(sr) / ann
    cagr = eq.iloc[-1]**(1/yrs) - 1 if eq.iloc[-1] > 0 else -1
    sharpe = sr.mean()/sr.std()*np.sqrt(ann) if sr.std() > 0 else 0
    dd = (eq/eq.cummax() - 1).min()
    is_ = (1 + sr[sr.index < mid]).prod() - 1
    oos = (1 + sr[sr.index >= mid]).prod() - 1
    return cagr, sharpe, dd, int((flips > 0).sum()), is_, oos


for rule, ann in [("15min", 35040.0), ("5min", 105120.0)]:
    df = frame(rule)
    ret = df["close"].pct_change().fillna(0.0)
    mid = df.index[len(df)//2]
    bh = df["close"].iloc[-1]/df["close"].iloc[0] - 1
    bh_sh = ret.mean()/ret.std()*np.sqrt(ann)
    print(f"\n===== {rule}  {df.index[0].date()}->{df.index[-1].date()}  "
          f"BUY&HOLD {bh*100:+.0f}% (Sharpe {bh_sh:.2f})  IS/OOS@{mid.date()} =====")
    print(f"  {'strategy':14}{'CAGR':>8}{'Sharpe':>7}{'maxDD':>7}{'flips':>7}{'IS':>9}{'OOS':>9}")
    sigs = {"cross": df["gap"] > 0,
            "gap_rising": (df["gap"] > 0) & (df["dgap"] > 0),
            "slope_only": df["dgap"] > 0}
    for name, pos in sigs.items():
        cagr, sh, dd, fl, is_, oos = metrics(pos.astype(float), ret, ann, mid)
        print(f"  {name:14}{cagr*100:>7.1f}%{sh:>7.2f}{dd*100:>6.0f}%{fl:>7}{is_*100:>8.1f}%{oos*100:>8.1f}%")
