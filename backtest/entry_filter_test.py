#!/usr/bin/env python3
"""entry_filter_test.py — do the proposed entry filters extract an informative
subset from the rsiscalp signals? (external review 2026-06-12)

FINDINGS #2 measured the FULL entry population as zero-information. Filters
select subsets, which could in principle differ. Tested here, population-style
(every qualifying signal bar, no position/cooldown interactions):

  with-trend        long only in 15m uptrend, short only in downtrend
  counter-only      the complement (what the deployed bots actually trade)
  bb-touch          signal bar touches the 20/2σ Bollinger band (review #1)
  candle-confirm    signal bar closes in the trade direction (review #1)
  vol>avg20         signal bar volume above its 20-bar average (review #6)
  adx>25 / adx<20   15m ADX14 regime split (review #4)
  REVIEW-COMPOSITE  with-trend AND bb AND candle AND vol (review's full spec)

Outcome per signal: enter next bar open, first-touch of TP/SL barriers
(intra-bar, pessimistic SL-first), else mark at 144-bar horizon close.
Geometries: deployed-ish TP0.5/SL0.6 and the review's TP1.0/SL0.6.
Random-walk baseline: P(TP first) = SL/(TP+SL). Costs: 0.15% round trip
(2×0.055% taker + 2×0.02% slip).

If a subset's NET expectancy is positive and holds on both halves → real
candidate, promote to full honest sim. Otherwise the entry door closes too.
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
HORIZON = 144
COSTS = 0.0015 * 100          # % round trip
GEOMETRIES = [(0.5, 0.6), (1.0, 0.6)]
SPLIT = np.datetime64("2023-01-01")


def wilder_rsi(c, n):
    d = c.diff()
    ag = d.clip(lower=0).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    return 100 - 100/(1 + ag/al)


def adx14(df15):
    h, l, c = df15["high"], df15["low"], df15["close"]
    up, dn = h.diff(), -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    pdi = 100 * pd.Series(plus_dm, index=df15.index).ewm(alpha=1/14, adjust=False).mean() / atr
    mdi = 100 * pd.Series(minus_dm, index=df15.index).ewm(alpha=1/14, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return dx.ewm(alpha=1/14, adjust=False, min_periods=14).mean()


def prep():
    df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    df["rsi"] = wilder_rsi(df["close"], 9)
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    df["atr_pct"] = tr.rolling(14).mean() / df["close"] * 100
    df["bb_mid"] = df["close"].rolling(20).mean()
    sd = df["close"].rolling(20).std()
    df["bb_lo"], df["bb_hi"] = df["bb_mid"] - 2 * sd, df["bb_mid"] + 2 * sd
    df["vol_ok"] = df["volume"] > df["volume"].rolling(20).mean()
    df["bull"] = df["close"] > df["open"]

    dfx = df.set_index("timestamp")
    f15 = dfx[["open", "high", "low", "close"]].resample("15min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    e20 = f15["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    e50 = f15["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    t15 = pd.DataFrame({"closed_at": f15.index + pd.Timedelta(minutes=15),
                        "trend": np.where(e20 > e50, 1.0, -1.0),
                        "gap": (e20 - e50).abs() / e50,
                        "adx": adx14(f15).values})
    t15.loc[e20.isna() | e50.isna(), ["trend", "gap"]] = np.nan
    return pd.merge_asof(df, t15.sort_values("closed_at"),
                         left_on="timestamp", right_on="closed_at",
                         direction="backward", allow_exact_matches=True)


def main():
    bt = prep()
    O, H, L, C = (bt[c].values for c in ("open", "high", "low", "close"))
    TS = bt["timestamp"].values
    n = len(bt)

    sig_long = ((bt["rsi"] <= 35) & (bt["atr_pct"] <= 0.8) & (bt["gap"] >= 0.002)
                & bt["trend"].notna()).values
    sig_short = ((bt["rsi"] >= 65) & (bt["atr_pct"] <= 0.8) & (bt["gap"] >= 0.002)
                 & bt["trend"].notna()).values
    trend = bt["trend"].values
    bb_lo_t = (bt["low"] <= bt["bb_lo"]).values
    bb_hi_t = (bt["high"] >= bt["bb_hi"]).values
    vol_ok = bt["vol_ok"].values
    bull, bear = bt["bull"].values, (~bt["bull"]).values
    adx = bt["adx"].values

    FILTERS = {
        "all (deployed population)": (sig_long, sig_short),
        "with-trend (no counter)":   (sig_long & (trend == 1), sig_short & (trend == -1)),
        "counter-only (deployed)":   (sig_long & (trend == -1), sig_short & (trend == 1)),
        "bb-touch":                  (sig_long & bb_lo_t, sig_short & bb_hi_t),
        "candle-confirm":            (sig_long & bull, sig_short & bear),
        "vol>avg20":                 (sig_long & vol_ok, sig_short & vol_ok),
        "adx>25 (trending)":         (sig_long & (adx > 25), sig_short & (adx > 25)),
        "adx<20 (ranging)":          (sig_long & (adx < 20), sig_short & (adx < 20)),
        "REVIEW-COMPOSITE":          (sig_long & (trend == 1) & bb_lo_t & bull & vol_ok,
                                      sig_short & (trend == -1) & bb_hi_t & bear & vol_ok),
    }

    def outcomes(mask_long, mask_short, tp, sl):
        res, when = [], []
        idx = np.flatnonzero(mask_long | mask_short)
        for i in idx:
            if i + 1 >= n:
                continue
            side = 1 if mask_long[i] else -1
            e = O[i + 1]
            tp_px = e * (1 + tp / 100 * side)
            sl_px = e * (1 - sl / 100 * side)
            r = None
            for j in range(i + 1, min(i + 1 + HORIZON, n)):
                sl_hit = (L[j] <= sl_px) if side == 1 else (H[j] >= sl_px)
                tp_hit = (H[j] >= tp_px) if side == 1 else (L[j] <= tp_px)
                if sl_hit:               # pessimistic: SL first
                    r = -sl; break
                if tp_hit:
                    r = tp; break
            if r is None:
                j = min(i + HORIZON, n - 1)
                r = (C[j] / e - 1) * 100 * side
            res.append(r); when.append(TS[i])
        return np.array(res), np.array(when)

    print(f"Signal bars: long {sig_long.sum():,}, short {sig_short.sum():,} "
          f"({bt['timestamp'].iloc[0].date()} -> {bt['timestamp'].iloc[-1].date()})\n")
    for tp, sl in GEOMETRIES:
        p_rand = sl / (tp + sl) * 100
        print(f"━━ TP {tp}% / SL {sl}%  (random-walk TP-first = {p_rand:.1f}%) ━━")
        print(f"{'filter':<27}{'N':>8}{'TP-first%':>10}{'gross%/tr':>11}{'net%/tr':>9}"
              f"{'h1 net%':>9}{'h2 net%':>9}")
        for name, (ml, ms) in FILTERS.items():
            r, w = outcomes(ml, ms, tp, sl)
            if len(r) == 0:
                print(f"{name:<27}{0:>8}"); continue
            tp_first = (r >= tp - 1e-9).mean() * 100
            gross = r.mean()
            net = gross - COSTS
            h1 = r[w < SPLIT].mean() - COSTS if (w < SPLIT).any() else float("nan")
            h2 = r[w >= SPLIT].mean() - COSTS if (w >= SPLIT).any() else float("nan")
            print(f"{name:<27}{len(r):>8,}{tp_first:>10.1f}{gross:>11.3f}{net:>9.3f}"
                  f"{h1:>9.3f}{h2:>9.3f}")
        print()


if __name__ == "__main__":
    main()
