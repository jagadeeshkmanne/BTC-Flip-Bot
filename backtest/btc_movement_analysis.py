"""btc_movement_analysis.py — data-science view: is there a predictable pattern
in BTC 5m movement? (user 2026-06-12)

Not a strategy test. Direct statistics on the price process:
  1) Autocorrelation of 5m returns (momentum vs mean-reversion, and at what lag).
  2) Conditional forward-1h return given the trailing-1h move ("if BTC is down X%
     in the last hour, what does the next hour do?") — the core question.
  3) Same, conditioned on RSI extremes (where v2.2 enters).
  4) How much of next-hour return is *predictable* (linear R^2, IS vs OOS) and
     how the best directional edge compares to the 0.15% round-trip cost.
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
c = df["close"]
r1 = c.pct_change()
COST = 0.15  # % round trip

# Wilder RSI9
d = c.diff(); ag = d.clip(lower=0).ewm(alpha=1/9, adjust=False).mean()
al = (-d.clip(upper=0)).ewm(alpha=1/9, adjust=False).mean()
rsi = 100 - 100/(1 + ag/al.replace(0, np.nan))

trail1h = (c / c.shift(12) - 1) * 100      # trailing 1h move, %
fwd1h = (c.shift(-12) / c - 1) * 100       # forward 1h move, %
fwd2h = (c.shift(-24) / c - 1) * 100

print(f"BTC 5m, {df['timestamp'].iloc[0].date()}->{df['timestamp'].iloc[-1].date()}, {len(df):,} bars")
print(f"per-bar 5m return: mean {r1.mean()*100:+.4f}%  std {r1.std()*100:.3f}%  "
      f"(this is the NOISE every prediction fights)\n")

print("1) AUTOCORRELATION of 5m returns (≈0 => random walk, no momentum/reversion):")
for lag in (1, 2, 3, 6, 12, 24):
    ac = r1.autocorr(lag)
    print(f"   lag {lag:>2} ({lag*5:>3}min): {ac:+.4f}")

print("\n2) FORWARD 1h return, bucketed by TRAILING 1h move:")
print("   'if BTC moved X in the last hour, what's the mean next-hour return?'")
q = pd.qcut(trail1h, 10, duplicates="drop")
g = pd.DataFrame({"trail": trail1h, "fwd": fwd1h}).dropna().groupby(q, observed=True)
print(f"   {'trailing 1h bucket':>22} {'N':>7} {'mean fwd1h':>11} {'std fwd1h':>10}")
means = []
for idx, sub in g:
    m = sub["fwd"].mean(); s = sub["fwd"].std()
    means.append(m)
    print(f"   {str(idx):>22} {len(sub):>7} {m:>+10.3f}% {s:>9.2f}%")
spread = max(means) - min(means)
print(f"   --> spread (best minus worst bucket): {spread:.3f}%  vs cost {COST}%")

print("\n3) FORWARD 1h return conditioned on RSI9 extremes (where v2.2 enters):")
for label, mask in [("RSI<=30 (deep oversold)", rsi <= 30), ("RSI<=35 (v2.2 long)", rsi <= 35),
                    ("35<RSI<65 (neutral)", (rsi > 35) & (rsi < 65)),
                    ("RSI>=65 (v2.2 short)", rsi >= 65), ("RSI>=70 (deep overbought)", rsi >= 70)]:
    f = fwd1h[mask].dropna()
    print(f"   {label:>26}: N={len(f):>7}  mean fwd1h {f.mean():+.4f}%  "
          f"(±{f.std()/np.sqrt(len(f)):.4f}% SE)  std {f.std():.2f}%")

print("\n4) PREDICTABILITY — linear model of fwd1h from trailing moves at 5 horizons:")
feats = pd.DataFrame({f"r{h}": c / c.shift(h) - 1 for h in (1, 3, 6, 12, 24)})
data = pd.concat([feats, fwd1h.rename("y")], axis=1).dropna()
mid = len(data) // 2
tr, te = data.iloc[:mid], data.iloc[mid:]
X_tr = np.c_[np.ones(len(tr)), tr[feats.columns].values]; y_tr = tr["y"].values
X_te = np.c_[np.ones(len(te)), te[feats.columns].values]; y_te = te["y"].values
beta, *_ = np.linalg.lstsq(X_tr, y_tr, rcond=None)
def r2(X, y):
    pred = X @ beta
    return 1 - ((y - pred)**2).sum() / ((y - y.mean())**2).sum()
print(f"   train R^2 {r2(X_tr,y_tr):.5f}   TEST R^2 {r2(X_te,y_te):.5f}  (1.0=perfect, 0=useless)")
print(f"   => predictable fraction of next-hour movement is essentially {max(0,r2(X_te,y_te))*100:.2f}%")
