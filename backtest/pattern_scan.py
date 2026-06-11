"""pattern_scan.py — broad conditional forward-return scan on BTC history.

For each signal (dummy condition on a bar), measure the mean forward return
over horizon H bars, vs the unconditional mean, with a t-stat. IS = <2024,
OOS = >=2024. A pattern is REAL only if |t_IS| >= 2.5 AND same sign in OOS
with |t_OOS| >= 1.5 AND the magnitude clears round-trip costs (0.13%) at a
plausible holding period.

Multiple-testing note: ~150 tests at |t|>=2.5 expect ~2 false positives by
chance; OOS same-sign filter cuts that roughly in half. Treat survivors as
candidates, not certainties.
"""
import pandas as pd
import numpy as np

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv"
IS_END = pd.Timestamp("2024-01-01")
COST = 0.0013  # round trip taker+slip


def frames():
    df = pd.read_csv(CSV, parse_dates=["timestamp"]).set_index("timestamp")
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return {"1h": df.resample("1h").agg(agg).dropna(),
            "1d": df.resample("1D").agg(agg).dropna()}


def rsi(close, n):
    d = close.diff()
    ag = d.clip(lower=0).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + ag / al)


def build_signals(f, tf):
    c, h, l, v = f["close"], f["high"], f["low"], f["volume"]
    sig = {}
    # momentum / trend
    for n in ([6, 24, 72] if tf == "1h" else [3, 10, 30, 90]):
        r = c.pct_change(n)
        sig[f"mom{n}>0"] = r > 0
        sig[f"mom{n}<0"] = r < 0
    sma50 = c.rolling(50).mean(); sma200 = c.rolling(200).mean()
    sig["px>sma200"] = c > sma200
    sig["px<sma200"] = c < sma200
    sig["sma50>sma200"] = sma50 > sma200
    # mean reversion
    for n in [9, 14]:
        rs = rsi(c, n)
        sig[f"rsi{n}<30"] = rs < 30
        sig[f"rsi{n}>70"] = rs > 70
    z = (c - c.rolling(20).mean()) / c.rolling(20).std()
    sig["z<-2"] = z < -2
    sig["z>+2"] = z > 2
    # volatility regime
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    vol20 = c.pct_change().rolling(20).std()
    q_hi = vol20.rolling(500, min_periods=100).quantile(0.8)
    q_lo = vol20.rolling(500, min_periods=100).quantile(0.2)
    sig["vol-high"] = vol20 > q_hi
    sig["vol-low"] = vol20 < q_lo
    sig["range>2atr"] = tr > 2 * atr
    # volume
    vs = v.rolling(20).mean()
    sig["volspike2x"] = v > 2 * vs
    sig["volspike2x&up"] = (v > 2 * vs) & (c > c.shift())
    sig["volspike2x&dn"] = (v > 2 * vs) & (c < c.shift())
    # structure / streaks
    up = c > c.shift()
    sig["3up"] = up & up.shift(1) & up.shift(2)
    sig["3dn"] = ~up & ~up.shift(1).astype(bool) & ~up.shift(2).astype(bool)
    body = (c - f["open"]).abs()
    wick_lo = (np.minimum(f["open"], c) - l)
    wick_hi = (h - np.maximum(f["open"], c))
    sig["hammer"] = (wick_lo > 2 * body) & (c < c.rolling(20).mean())
    sig["shoot-star"] = (wick_hi > 2 * body) & (c > c.rolling(20).mean())
    # proximity to highs/lows
    n_hi = 168 if tf == "1h" else 30
    sig[f"near{n_hi}hi"] = c >= c.rolling(n_hi).max() * 0.995
    sig[f"near{n_hi}lo"] = c <= c.rolling(n_hi).min() * 1.005
    # time effects
    if tf == "1h":
        for hr in [0, 8, 13, 14, 16, 20]:
            sig[f"hour={hr:02d}"] = pd.Series(f.index.hour == hr, index=f.index)
        sig["weekend"] = pd.Series(f.index.dayofweek >= 5, index=f.index)
    else:
        for d in range(7):
            sig[f"dow={d}"] = pd.Series(f.index.dayofweek == d, index=f.index)
    return sig


def scan():
    rows = []
    for tf, f in frames().items():
        horizons = [1, 6, 24] if tf == "1h" else [1, 5]
        fwd = {H: f["close"].shift(-H - 1) / f["open"].shift(-1) - 1 for H in horizons}
        sigs = build_signals(f, tf)
        is_mask = f.index < IS_END
        for name, s in sigs.items():
            s = s.fillna(False)
            for H in horizons:
                r = fwd[H]
                for seg, m in [("IS", is_mask), ("OOS", ~is_mask)]:
                    sel = r[s & m].dropna()
                    base = r[m].dropna()
                    if len(sel) < 30:
                        rows.append((tf, name, H, seg, len(sel), np.nan, np.nan)); continue
                    edge = sel.mean() - base.mean()
                    t = edge / (sel.std() / np.sqrt(len(sel))) if sel.std() > 0 else 0
                    rows.append((tf, name, H, seg, len(sel), edge * 100, t))
    df = pd.DataFrame(rows, columns=["tf", "signal", "H", "seg", "n", "edge_pct", "t"])
    return df


def main():
    df = scan()
    p = df.pivot_table(index=["tf", "signal", "H"], columns="seg",
                       values=["n", "edge_pct", "t"])
    p.columns = [f"{a}_{b}" for a, b in p.columns]
    p = p.reset_index()
    total = len(p)
    cand = p[(p["t_IS"].abs() >= 2.5)].copy()
    surv = cand[(np.sign(cand["edge_pct_IS"]) == np.sign(cand["edge_pct_OOS"]))
                & (cand["t_OOS"].abs() >= 1.5)].copy()
    print(f"tests run: {total}  |  IS-significant (|t|>=2.5): {len(cand)}  |  "
          f"OOS survivors (same sign, |t|>=1.5): {len(surv)}")
    print(f"expected false positives at this threshold: ~{total*0.0124:.1f} IS, "
          f"~{len(cand)*0.13/2:.1f} after OOS filter\n")
    cols = ["tf", "signal", "H", "n_IS", "edge_pct_IS", "t_IS", "n_OOS", "edge_pct_OOS", "t_OOS"]
    print("=== IS-significant candidates (all of them, survivors marked *) ===")
    cand["surv"] = cand.index.isin(surv.index)
    cand = cand.sort_values("t_IS", key=lambda s: s.abs(), ascending=False)
    for _, r in cand.iterrows():
        mark = "*" if r["surv"] else " "
        per_yr = ""
        print(f" {mark} {r['tf']:<3} {r['signal']:<16} H={int(r['H']):<3} "
              f"IS: {r['edge_pct_IS']:+.3f}%/trade (t={r['t_IS']:+.1f}, n={int(r['n_IS'])})   "
              f"OOS: {r['edge_pct_OOS']:+.3f}% (t={r['t_OOS']:+.1f}, n={int(r['n_OOS'])})")
    print(f"\nround-trip cost line: {COST*100:.2f}% — an edge must exceed this per trade to be tradable")


if __name__ == "__main__":
    main()
