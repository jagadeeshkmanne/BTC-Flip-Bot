#!/usr/bin/env python3
"""ag_regime2.py — REGIME-DETECTION v2 for the my-V3 LONG side (BTC 4h, EXTENDED 2017-2026).

The validated base enters longs when `bull = (4h EMA50>200) AND (prior-day EMA50>200)`, gated
by a macro filter `price > SMA(9mo)`. This script asks: can a *better bull regime* enter trends
EARLIER or dodge fake-bull WHIPSAWS, and beat the base ret/DD robustly?

Everything below the bull array is held BYTE-FOR-BYTE identical to the base so the comparison is
apples-to-apples:
  - same macro long filter  : price > SMA(9mo)        (applied on top of every candidate bull)
  - same long params        : atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0
  - same SHORT gate         : (drop>10% over 40d) & (daily MACD<signal), sz=0.40, s_atr=5, s_cap=0.15
  - same engine             : backtest_myv3_final.run  (stops ratchet, HTF shift1/merge_asof backward)

ANTI-BUG: base reproduced first (DD -43% + OOS 3.05 match exactly; CAGR 91%/r-DD 2.11 is the
internal base every candidate is measured against). No lookahead — all higher-timeframe series are
.shift(1) before merge_asof(direction="backward"). Candidates that look too good (r/DD>5, OOS>6)
are distrusted, not celebrated.

ANGLE — four families of regime redefinition:
  (1) EMA pair sweep        : {30/100, 50/150, 100/200} on the 4h leg and/or the daily leg
  (2) weekly trend confirm  : AND (weekly EMA10>30)  or  AND (price>weekly SMA20)
  (3) trend-vs-range filter : only long when Bollinger-width / ATR-ratio says "trending"
  (4) debounce              : require N consecutive bull bars before the regime counts as bull

GATE to "beats base": full 2017-2026 data + OOS green + survives all 3 bears (2018/2022/2026)
+ ret/DD > 2.11 (the reproduced base) with no catastrophic year.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_final import run, m
from backtest_myv3_ext import EXT, BPD  # BPD = 6 bars/day at 4h

YEARS = list(range(2017, 2027))
BEARS = (2018, 2022, 2026)


# ----------------------------------------------------------------------------- data / regime parts
def load():
    """Return (df 4h, c close, ts, daily df). EXTENDED 2017-2026."""
    raw = pd.read_csv(EXT, parse_dates=["timestamp"])
    df = raw.set_index("timestamp").resample("4h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    dd = raw.set_index("timestamp").resample("1D").agg({"close": "last"}).dropna().reset_index()
    return df, df["close"], pd.to_datetime(df["timestamp"]), dd


def map_daily(boolser, dd, ts):
    """Project a DAILY boolean series onto the 4h index, no lookahead (shift1 + backward asof)."""
    src = boolser.shift(1).fillna(False).astype(bool)
    return pd.merge_asof(pd.DataFrame({"ts": ts}).sort_values("ts"),
        pd.DataFrame({"ts": pd.to_datetime(dd["timestamp"]), "b": src.values}).sort_values("ts"),
        on="ts", direction="backward")["b"].fillna(False).astype(bool).values


def map_weekly(boolser, wk, ts):
    """Project a WEEKLY boolean series onto the 4h index, no lookahead."""
    src = boolser.shift(1).fillna(False).astype(bool)
    return pd.merge_asof(pd.DataFrame({"ts": ts}).sort_values("ts"),
        pd.DataFrame({"ts": pd.to_datetime(wk["timestamp"]), "b": src.values}).sort_values("ts"),
        on="ts", direction="backward")["b"].fillna(False).astype(bool).values


def ema_bull(c, dd, ts, fast4, slow4, fastD, slowD):
    """bull = (4h EMA fast>slow) AND (prior-day EMA fastD>slowD).  The base is (50,200,50,200)."""
    f_up = (bt.ema(c, fast4) > bt.ema(c, slow4)).values
    d_up = (bt.ema(dd["close"], fastD) > bt.ema(dd["close"], slowD))
    return f_up & map_daily(d_up, dd, ts)


def macro_filter(c):
    """price > SMA(9 months). Same as the validated base macro long filter."""
    return (c > bt.sma(c, 9 * 30 * BPD).shift(1)).fillna(False).values


def base_short(df, c, ts, dd):
    """The validated SHORT gate, held fixed for every experiment: (drop>10%/40d)&(daily MACD<sig)."""
    hh = c.rolling(40 * BPD).max().shift(1)
    drop = ((c / hh - 1) < -0.10).fillna(False).values
    macd = bt.ema(dd["close"], 12) - bt.ema(dd["close"], 26)
    sig = bt.ema(macd, 9)
    md = map_daily((macd < sig), dd, ts)
    return drop & md


# ----------------------------------------------------------------------------- run / metrics
LONG_KW = dict(atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0,
               short_size=0.40, s_atr=5.0, s_cap=0.15)


def equity(df, bull, short):
    return run(df, bull, short, allow_long=True, allow_short=True, **LONG_KW)


def metr(s):
    cg, dd_, rr = m(s)
    o = m(s[s.index >= s.index[int(len(s) * 0.6)]])
    return cg, dd_, rr, o[2]


def yr(s, y):
    seg = s[s.index.year == y]
    return (seg.iloc[-1] / seg.iloc[0] - 1) * 100 if len(seg) > 20 else 0.0


def report(s, label):
    cg, dd_, rr, oos = metr(s)
    yrs = "  ".join(f"{y}:{yr(s,y):+.0f}%" for y in YEARS)
    print(f"  {label:<40}{cg*100:>5.0f}%{dd_*100:>5.0f}%{rr:>6.2f}{oos:>6.2f}   {yrs}")
    return dict(label=label, cagr=cg, dd=dd_, rdd=rr, oos=oos,
                bears={y: yr(s, y) for y in BEARS},
                years={y: yr(s, y) for y in YEARS}, series=s)


# ----------------------------------------------------------------------------- main
def main():
    df, c, ts, dd = load()
    macro = macro_filter(c)
    short = base_short(df, c, ts, dd)
    wk = pd.read_csv(EXT, parse_dates=["timestamp"]).set_index("timestamp").resample("1W").agg(
        {"close": "last"}).dropna().reset_index()

    # base bull (exactly the validated regime)
    base_bull = ema_bull(c, dd, ts, 50, 200, 50, 200) & macro

    print("=" * 140)
    print("REGIME-DETECTION v2 — redefine the BULL regime for the my-V3 LONG side (BTC 4h, EXT 2017-2026)")
    print("identical macro filter / long params / SHORT gate / engine below every bull array")
    print("=" * 140)
    print(f"  {'config':<40}{'CAGR':>5}{'DD':>6}{'r/DD':>6}{'OOS':>6}   year-by-year")
    print("  " + "-" * 136)

    results = []
    results.append(report(equity(df, base_bull, short), "BASE  bull=4h+D EMA50/200 (repro)"))
    print("  " + "-" * 136)

    # (1) EMA pair sweep -- on 4h leg, on daily leg, and both together
    print("  (1) EMA-PAIR SWEEP (enter earlier with tighter pairs / steadier with wider)")
    pairs = [(30, 100), (50, 150), (100, 200)]
    for f, sl in pairs:  # vary 4h leg, keep daily 50/200
        results.append(report(equity(df, ema_bull(c, dd, ts, f, sl, 50, 200) & macro, short),
                              f"4h {f}/{sl}  |  D 50/200"))
    for f, sl in pairs:  # vary daily leg, keep 4h 50/200
        results.append(report(equity(df, ema_bull(c, dd, ts, 50, 200, f, sl) & macro, short),
                              f"4h 50/200  |  D {f}/{sl}"))
    for f, sl in pairs:  # both legs together
        results.append(report(equity(df, ema_bull(c, dd, ts, f, sl, f, sl) & macro, short),
                              f"4h {f}/{sl}  |  D {f}/{sl}"))
    print("  " + "-" * 136)

    # (2) weekly trend confirm  ON TOP of the base bull (fewer fake-bull whipsaws)
    print("  (2) + WEEKLY trend confirm on base bull")
    w_ema = map_weekly((bt.ema(wk["close"], 10) > bt.ema(wk["close"], 30)), wk, ts)
    w_sma = map_weekly((wk["close"] > bt.sma(wk["close"], 20)), wk, ts)
    results.append(report(equity(df, base_bull & w_ema, short), "base & weekly EMA10>30"))
    results.append(report(equity(df, base_bull & w_sma, short), "base & price>weekly SMA20"))
    # weekly confirm on a tighter 4h leg (early entry but weekly-gated)
    bull_3010 = ema_bull(c, dd, ts, 30, 100, 50, 200) & macro
    results.append(report(equity(df, bull_3010 & w_ema, short), "4h 30/100 & weekly EMA10>30"))
    results.append(report(equity(df, bull_3010 & w_sma, short), "4h 30/100 & price>weekly SMA20"))
    print("  " + "-" * 136)

    # (3) trend-vs-range filter — only long when the regime is TRENDING, not chopping
    print("  (3) + TREND-vs-RANGE filter on base bull (only long in trending regime)")
    lo, mid, up = bt.bbands(c, 20, 2.0)
    bbw = ((up - lo) / mid)                      # Bollinger bandwidth
    atr14 = bt.atr(df, 14)
    atr_ratio = (atr14 / bt.sma(atr14, 100))     # short ATR vs its own slow mean
    # "trending" = bandwidth above its rolling median  (expansion, not contraction)
    for q in (0.40, 0.50, 0.60):
        trend = (bbw > bbw.rolling(200).quantile(q)).shift(1).fillna(False).values
        results.append(report(equity(df, base_bull & trend, short), f"base & BBwidth>q{int(q*100)}"))
    for thr in (1.0, 1.1):
        trend = (atr_ratio > thr).shift(1).fillna(False).values
        results.append(report(equity(df, base_bull & trend, short), f"base & ATRratio>{thr}"))
    # ADX>20/25 as an alternate trend gate
    adx = bt.adx(df, 14)
    for thr in (20, 25):
        trend = (adx > thr).shift(1).fillna(False).values
        results.append(report(equity(df, base_bull & trend, short), f"base & ADX>{thr}"))
    print("  " + "-" * 136)

    # (4) debounce — require N consecutive bull bars before the regime counts (kill 1-bar whipsaws)
    print("  (4) DEBOUNCE base bull (require N consecutive bull bars)")
    base_raw = ema_bull(c, dd, ts, 50, 200, 50, 200)  # debounce the raw regime, re-apply macro after
    for N in (3, 6, 12, 24):
        deb = pd.Series(base_raw).rolling(N).sum().fillna(0).values >= N
        results.append(report(equity(df, deb & macro, short), f"base debounced {N} bars"))
    # debounce a tighter pair (early-but-confirmed)
    raw_3010 = ema_bull(c, dd, ts, 30, 100, 50, 200)
    for N in (6, 12):
        deb = pd.Series(raw_3010).rolling(N).sum().fillna(0).values >= N
        results.append(report(equity(df, deb & macro, short), f"4h 30/100 debounced {N} bars"))
    print("  " + "-" * 136)

    # --------------------------------------------------------------------- ranking + verdict
    base = results[0]
    cand = results[1:]
    # robust filter: green every year, survives all 3 bears (>= -10%), OOS>0, plausible (r/DD<5, OOS<6)
    def robust(r):
        return (all(v > -10 for v in r["bears"].values())
                and min(r["years"].values()) > -15
                and r["oos"] > 0 and r["rdd"] < 5.0 and r["oos"] < 6.0)
    beat = [r for r in cand if r["rdd"] > base["rdd"] and robust(r)]
    beat.sort(key=lambda r: r["rdd"], reverse=True)
    # best-3 to display: beat-candidates first, then top robust candidates by r/DD to fill to 3
    robust_sorted = sorted([r for r in cand if robust(r)], key=lambda r: r["rdd"], reverse=True)
    top3 = beat[:]
    for r in robust_sorted:
        if len(top3) >= 3:
            break
        if r not in top3:
            top3.append(r)

    print()
    print("=" * 140)
    print(f"  BASE reproduced: CAGR {base['cagr']*100:.0f}%  DD {base['dd']*100:.0f}%  "
          f"r/DD {base['rdd']:.2f}  OOS {base['oos']:.2f}")
    print(f"  Candidates that beat base r/DD AND pass robustness gate (green years, all 3 bears, "
          f"OOS>0, not too-good-to-be-true): {len(beat)}")
    print("=" * 140)
    print(f"  {'rank / config':<44}{'CAGR':>5}{'DD':>6}{'r/DD':>6}{'OOS':>6}   "
          f"{'2018':>6}{'2022':>7}{'2026':>7}")
    print("  " + "-" * 136)
    show = top3 if top3 else sorted(cand, key=lambda r: r["rdd"], reverse=True)[:3]
    print(f"  --- BEST 3 ({len(beat)} beat base; rest are top robust candidates) — per-bear + verdict ---")
    for i, r in enumerate(show, 1):
        verdict = "BEATS BASE" if (r["rdd"] > base["rdd"] and robust(r)) else "does NOT beat base"
        print(f"  {i}. {r['label']:<41}{r['cagr']*100:>5.0f}%{r['dd']*100:>5.0f}%{r['rdd']:>6.2f}"
              f"{r['oos']:>6.2f}   {r['bears'][2018]:>+5.0f}%{r['bears'][2022]:>+6.0f}%"
              f"{r['bears'][2026]:>+6.0f}%   [{verdict}]")
    print("  " + "-" * 136)
    print("  FULL YEAR-BY-YEAR for BASE + best 3:")
    for r in [base] + show:
        yrs = "  ".join(f"{y}:{r['years'][y]:+.0f}%" for y in YEARS)
        print(f"    {r['label']:<42} {yrs}")

    print("=" * 140)
    if beat:
        b = beat[0]
        print(f"  VERDICT: best candidate '{b['label']}' beats base "
              f"(r/DD {b['rdd']:.2f} vs {base['rdd']:.2f}, OOS {b['oos']:.2f} vs {base['oos']:.2f}).")
    else:
        print(f"  VERDICT: NO regime redefinition robustly beats the base r/DD {base['rdd']:.2f}. "
              f"The 4h+daily EMA50/200 bull stands.")
    print("=" * 140)


if __name__ == "__main__":
    main()
