"""round2_fade.py — round-2 honest sweep, informed by ic_scan.py.

ic_scan found ONE stable effect: short-horizon mean reversion (IC -0.03..-0.08
for rsi14/z20/z60/mom3/mom12/mom48, negative in ALL 7 years). Round 2 tests
whether ANY tradable rule harvests it net of costs, plus families round 1
didn't cover.

Anti-lookahead: extreme thresholds are trailing 2880-bar rolling quantiles,
shifted 1 bar. Engine = validated scalp_1pct_search.run_trades (random PF
0.996 @ 0 fee; oracle PF 3.87). IS < 2024-01-01 <= OOS, selection on IS only.

Cost scenarios:
  TAKER  0.055%/side + 0.02% slip (real retail market orders)
  MAKER* 0.02%/side, zero slip — FANTASY UPPER BOUND (assumes every limit
         fills with zero adverse selection; reality is strictly worse)
  0fee   diagnostic

Families:
  FADE-mom3/12/48, FADE-z60: enter opposite to extreme (trailing q10/q05),
      pure time exit at H bars (harvests the measured effect directly).
  MTF: 1h EMA20>50 trend + 5m z20 pullback < -1 -> long with trend (and
      mirror short). TP/SL 0.6/0.6, and time-exit variant.
  VSPIKE: volume > 5x 20-bar mean; fade and follow variants, time exit.
  STREAK: >=5 consecutive same-direction closes -> fade, time exit.
  TOD: long 13-21 UTC (US session), flat otherwise (hour IC was weakly +).
"""
import numpy as np
import pandas as pd
import scalp_1pct_search as eng

eng.MAX_HOLD = 48                      # default time exit: 4h
IS_END = pd.Timestamp("2024-01-01")
GOAL_MONTH = 0.348
SCEN = {"TAKER": 2 * (0.00055 + 0.0002), "MAKER*": 2 * 0.0002, "0fee": 0.0}

df = eng.load()
ts = df["timestamp"]
c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
n = len(df)

def trailing_extreme(x, q_lo, q_hi, win=2880):
    lo = x.rolling(win).quantile(q_lo).shift(1)
    hi = x.rolling(win).quantile(q_hi).shift(1)
    return (x < lo).values, (x > hi).values


def variants():
    out = []   # (name, long_sig, short_sig, hold_bars, sl, tp)
    feats = {"mom3": c.pct_change(3), "mom12": c.pct_change(12),
             "mom48": c.pct_change(48),
             "z60": (c - c.rolling(60).mean()) / c.rolling(60).std()}
    for fname, x in feats.items():
        for q, qtag in ((0.10, "q10"), (0.05, "q05")):
            lo_ext, hi_ext = trailing_extreme(x, q, 1 - q)
            for hold in (12, 48):
                # fade: long when feature is extremely LOW, short when HIGH
                out.append((f"FADE-{fname}-{qtag}-h{hold}", lo_ext, hi_ext,
                            hold, 0.50, 0.50))
    # MTF pullback
    c1h = c.copy(); c1h.index = ts
    h1 = c1h.resample("1h", label="left", closed="left").last().dropna()
    e20 = h1.ewm(span=20, adjust=False, min_periods=20).mean()
    e50 = h1.ewm(span=50, adjust=False, min_periods=50).mean()
    tr1h = pd.Series(np.where(e20 > e50, 1, -1), index=h1.index + pd.Timedelta(hours=1))
    trend = pd.merge_asof(pd.DataFrame({"ts": ts}),
                          pd.DataFrame({"ts": tr1h.index, "tr": tr1h.values}).sort_values("ts"),
                          on="ts", direction="backward")["tr"].values
    z20 = ((c - c.rolling(20).mean()) / c.rolling(20).std()).values
    mtf_long = (trend == 1) & (z20 < -1.0)
    mtf_short = (trend == -1) & (z20 > 1.0)
    out.append(("MTF-pullback-tp/sl0.6", mtf_long, mtf_short, 288, 0.006, 0.006))
    out.append(("MTF-pullback-h48", mtf_long, mtf_short, 48, 0.50, 0.50))
    # volume spike
    vs = (v / v.rolling(20).mean()).values
    up_bar = (c.diff() > 0).values
    spike = vs > 5.0
    out.append(("VSPIKE-follow-h12", spike & up_bar, spike & ~up_bar, 12, 0.50, 0.50))
    out.append(("VSPIKE-fade-h12", spike & ~up_bar, spike & up_bar, 12, 0.50, 0.50))
    # streak fade
    d = np.sign(c.diff())
    grp = (d != d.shift()).cumsum()
    streak = (d.groupby(grp).cumcount() + 1) * d
    out.append(("STREAK5-fade-h12", (streak <= -5).values, (streak >= 5).values,
                12, 0.50, 0.50))
    # time of day
    hr = ts.dt.hour.values
    tod_long = (hr == 13)
    out.append(("TOD-US-long-h96", tod_long, np.zeros(n, bool), 96, 0.50, 0.50))
    return out


def main():
    print(f"bars {n:,}   IS<{IS_END.date()}<=OOS   goal: every month >= +34.8%")
    hdr = (f"{'variant':<26}{'cost':>7}{'seg':>5}{'trades':>7}{'WR%':>6}{'PF':>6}"
           f"{'final_eq':>10}{'pos_mo%':>8}{'goal_mo%':>9}{'med_mo%':>8}")
    print(hdr); print("-" * len(hdr))
    for name, ls, ss, hold, sl, tp in variants():
        eng.MAX_HOLD = hold
        trades = eng.run_trades(df, ls, ss, sl, tp)
        if len(trades) < 30:
            print(f"{name:<26}  (only {len(trades)} trades)"); continue
        t_is = [t for t in trades if pd.Timestamp(t[0]) < IS_END]
        t_oos = [t for t in trades if pd.Timestamp(t[0]) >= IS_END]
        for ctag, cost in SCEN.items():
            for seg, tt in (("IS", t_is), ("OOS", t_oos)):
                r = eng.evaluate(tt, 3, cost)        # 3x; report lev-free PF anyway
                if r is None: continue
                fe = f"{r['final']:.3f}" if r["final"] < 1e4 else f"{r['final']:.2e}"
                print(f"{name:<26}{ctag:>7}{seg:>5}{r['n']:>7}{r['wr']*100:>6.1f}"
                      f"{min(r['pf'],99.9):>6.2f}{fe:>10}{r['pos_months']*100:>8.1f}"
                      f"{r['goal_months']*100:>9.1f}{r['med_month']*100:>8.2f}"
                      + ("  BLOWN" if r["blown"] else ""))
        print()


if __name__ == "__main__":
    main()
