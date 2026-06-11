"""ic_scan.py — model-free predictive-power scan on BTC 5m bars.

For ~20 features computable from closed bars, measure Spearman rank
correlation (information coefficient, IC) with forward returns at horizons
of 3, 12, 48 bars (15m, 1h, 4h) — per YEAR, so a transient correlation
can't masquerade as a stable edge.

Also: top-vs-bottom decile spread of forward returns per feature (the
practical money: trade top decile long, bottom decile short), gross and
net of the 0.15% round-trip cost.

If no feature shows |IC| stable across years AND a decile spread that
clears costs, then NO entry rule built from these features can sustain
profit — regardless of exit logic, leverage, or fee regime.
"""
import numpy as np
import pandas as pd
from scipy import stats as ss

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
COST_RT = 0.0015
HORIZONS = [3, 12, 48]

df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp")
df = df[df["timestamp"] >= "2019-10-01"].reset_index(drop=True)
c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

feats = {}
d = c.diff()
g = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
ls_ = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
feats["rsi14"] = 100 - 100 / (1 + g / ls_.replace(0, np.nan))
for w in (20, 60, 288):
    feats[f"z{w}"] = (c - c.rolling(w).mean()) / c.rolling(w).std()
for n in (1, 3, 12, 48, 288, 2016):
    feats[f"mom{n}"] = c.pct_change(n)
feats["vol_ratio"] = v / v.rolling(288).mean()
feats["vol_spike"] = v / v.rolling(20).mean()
rng = (h - l) / c
feats["range_now"] = rng
feats["range_ratio"] = rng / rng.rolling(288).mean()
hi288 = h.rolling(288).max(); lo288 = l.rolling(288).min()
feats["range_pos288"] = (c - lo288) / (hi288 - lo288)
feats["hour"] = df["timestamp"].dt.hour.astype(float)
feats["dow"] = df["timestamp"].dt.dayofweek.astype(float)
e20 = c.ewm(span=20, adjust=False).mean(); e100 = c.ewm(span=100, adjust=False).mean()
feats["ema_gap"] = (e20 - e100) / e100
tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
feats["atr_pct"] = tr.rolling(14).mean() / c
feats["close_loc"] = (c - l) / (h - l).replace(0, np.nan)   # close position in bar
feats["streak"] = (np.sign(d).groupby((np.sign(d) != np.sign(d).shift()).cumsum()).cumcount() + 1) * np.sign(d)

years = df["timestamp"].dt.year
F = pd.DataFrame(feats)

print(f"bars {len(df):,}  features {len(feats)}  horizons {HORIZONS} bars")
for hz in HORIZONS:
    fwd = c.shift(-hz) / c - 1
    print(f"\n──── horizon {hz} bars ({hz*5} min) ────")
    hdr = f"{'feature':<14}" + "".join(f"{y:>8}" for y in range(2020, 2027)) + f"{'allIC':>8}{'decile_gross%':>14}{'net%':>8}"
    print(hdr)
    for name in F.columns:
        x = F[name]
        ok = x.notna() & fwd.notna()
        ics = []
        for y in range(2020, 2027):
            m = ok & (years == y)
            if m.sum() < 5000:
                ics.append(np.nan); continue
            ics.append(ss.spearmanr(x[m], fwd[m]).statistic)
        m = ok
        ic_all = ss.spearmanr(x[m], fwd[m]).statistic
        # decile spread (sampled non-overlapping every hz bars to avoid autocorr)
        sub = pd.DataFrame({"x": x[m], "f": fwd[m]}).iloc[::hz]
        qq = sub["x"].rank(pct=True)
        top = sub["f"][qq >= 0.9].mean()
        bot = sub["f"][qq <= 0.1].mean()
        spread = (top - bot) * 100          # long top, short bottom, gross %
        net = spread - 2 * COST_RT * 100    # two positions' round trips
        row = f"{name:<14}" + "".join(f"{i:>8.3f}" if np.isfinite(i) else f"{'—':>8}" for i in ics)
        print(row + f"{ic_all:>8.3f}{spread:>14.4f}{net:>8.4f}")
