"""funding_analysis.py — does funding rate predict forward returns / pick the side?
(user 2026-06-12) Funding reflects positioning (crowded longs vs shorts) — info NOT
in price. Test: 1) forward returns bucketed by funding rate, 2) a funding-timed
long/flat & long/short strategy net of fees+funding vs buy & hold.
"""
import numpy as np
import pandas as pd

F = pd.read_csv("/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_funding.csv", parse_dates=["timestamp"])
P = pd.read_csv("/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_1h.csv", parse_dates=["timestamp"]).set_index("timestamp")
close = P["close"].sort_index()

# price at each funding time and forward (8h / 24h / 72h later)
def px_at(ts):
    idx = close.index.searchsorted(ts)
    return close.iloc[min(idx, len(close)-1)]

F = F.sort_values("timestamp").reset_index(drop=True)
F["p0"] = F["timestamp"].map(px_at)
F["p8"] = (F["timestamp"] + pd.Timedelta(hours=8)).map(px_at)
F["p24"] = (F["timestamp"] + pd.Timedelta(hours=24)).map(px_at)
F["p72"] = (F["timestamp"] + pd.Timedelta(hours=72)).map(px_at)
F["fwd8"] = (F["p8"]/F["p0"] - 1) * 100
F["fwd24"] = (F["p24"]/F["p0"] - 1) * 100
F["fwd72"] = (F["p72"]/F["p0"] - 1) * 100
F["fr_bp"] = F["funding_rate"] * 10000   # basis points per 8h
F = F.dropna()

print(f"Funding points {F['timestamp'].iloc[0].date()}->{F['timestamp'].iloc[-1].date()}  N={len(F)}")
print(f"funding mean {F['fr_bp'].mean():.2f} bp/8h\n")

print("1) FORWARD RETURN by funding-rate decile (contrarian if high funding -> negative fwd):")
print(f"   {'funding bucket (bp/8h)':>26} {'N':>5} {'fwd+8h':>8} {'fwd+24h':>9} {'fwd+72h':>9}")
F["q"] = pd.qcut(F["fr_bp"], 10, duplicates="drop")
for idx, s in F.groupby("q", observed=True):
    print(f"   {str(idx):>26} {len(s):>5} {s['fwd8'].mean():>+7.3f}% {s['fwd24'].mean():>+8.3f}% {s['fwd72'].mean():>+8.3f}%")

# correlation
print(f"\n   corr(funding, fwd24h) = {F['fr_bp'].corr(F['fwd24']):+.3f}   "
      f"corr(funding, fwd72h) = {F['fr_bp'].corr(F['fwd72']):+.3f}")

print("\n2) STRATEGY: position decided by funding, held to next funding (8h), vs buy&hold")
FEE = 0.00055
# returns of holding from each funding stamp to the next
F["ret8_frac"] = F["p8"]/F["p0"] - 1
mid = F["timestamp"].iloc[len(F)//2]
for name, pos in [
    ("momentum: long if funding>0 else flat", (F["fr_bp"] > 0).astype(float)),
    ("contrarian: long if funding<0 else flat", (F["fr_bp"] < 0).astype(float)),
    ("contrarian LS: long fund<0 / short fund>0", np.sign(-F["fr_bp"])),
    ("fade extremes: short top-decile, long bottom", np.where(F["fr_bp"] > F["fr_bp"].quantile(.9), -1.0, np.where(F["fr_bp"] < F["fr_bp"].quantile(.1), 1.0, 0.0))),
]:
    pos = pd.Series(pos, index=F.index)
    held = pos.shift(1).fillna(0.0)
    # pay funding: a long pays funding_rate (if positive), short receives; plus trade fee on changes
    funding_pay = held.clip(lower=0) * F["funding_rate"] - held.clip(upper=0).abs() * (-F["funding_rate"])
    flips = held.diff().abs().fillna(0.0)
    sr = held * F["ret8_frac"] - funding_pay - FEE * flips
    eq = (1 + sr).cumprod()
    is_ = (1+sr[F["timestamp"] < mid]).prod()-1
    oos = (1+sr[F["timestamp"] >= mid]).prod()-1
    print(f"   {name:42} total {(eq.iloc[-1]-1)*100:>+8.1f}%  IS {is_*100:>+7.1f}%  OOS {oos*100:>+7.1f}%  flips {int((flips>0).sum())}")
bh = (close.iloc[-1]/F['p0'].iloc[0]-1)*100
print(f"   {'BUY & HOLD':42} total {bh:>+8.1f}%")
