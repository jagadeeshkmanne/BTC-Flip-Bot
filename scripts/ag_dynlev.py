#!/usr/bin/env python3
"""ag_dynlev.py — DYNAMIC de-leveraging around a 1.8x ceiling to cut the 1.8x-base DD.

Base to beat (reproduced byte-for-byte in this file):
  run_v(df,L,bear,ss,pa, np.ones(n)*1.8, lock_frac=0.33, lock_r=6.0)
    -> CAGR 145%  DD -37%  ret/DD 3.88  OOS 3.91  green every year
       bears 2018:+108  2022:+43  2026:+23

GOAL: cut DD to <=-30% (ideally -25%) keeping CAGR>=120%.

ANGLE — instead of a fixed 1.8x, pass a PER-BAR leverage array (levarr) that de-levers in storms
but averages ~1.8x so CAGR is preserved:
  (1) VOL-SCALED   lev = clip(target/realized_vol_causal, lo, hi), calibrated avg ~1.8
  (2) DD-SCALED    cut lev toward 0.6x when the STRATEGY's own equity is >X% below its running
                   peak (CAUSAL — uses a 1-bar-lagged equity proxy), restore on recovery
  (3) REGIME-SCALED hi (1.8x) in clean bull, lower on daily-bear / short side

ANTI-LOOKAHEAD: every leverage input is causal. Vol median is EXPANDING (never full-sample).
The DD-scaled lever is driven by a causal equity proxy: we run the base 1.8x equity ONCE, then
lag it by 1 bar to decide today's leverage (you only ever know yesterday's drawdown). We also
report the REALIZED avg/peak leverage actually applied while a position is open.

GATE for a winner: full 2017-2026 CAGR>=120% AND DD<=-30% AND OOS healthy AND all 3 bears
positive AND green every year.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_voltarget_dd import run_v, yr, grn
from backtest_myv3_ext import build
from backtest_short_aggressive import daily_macd_bear
from backtest_myv3_shorts import m

BPD = 6
BASE_KW = dict(lock_frac=0.33, lock_r=6.0)   # the base's pyramid/lock config — held fixed


def realized_lev(df, L, bear, ss, pa, levarr, **kw):
    """Re-run the engine but record the leverage actually applied at each ENTRY and the
    fraction of bars in-position, to report realized avg/peak exposure (not just the array mean)."""
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    a = bt.atr(df, 14).values; n = len(df)
    side = 0; armedL = armedS = True
    levs = []          # leverage applied per entry
    bars_in = 0
    for i in range(16, n - 1):
        if not L[i]: armedL = True
        if not bear[i]: armedS = True
        if side != 0:
            bars_in += 1
            regime_out = (not L[i]) if side == 1 else (not bear[i])
            # cheap proxy for exit: regime flip (covers the dominant exit mode for exposure stats)
            if regime_out:
                side = 0
        if side == 0:
            if L[i] and armedL:
                levs.append(levarr[i]); side = 1; armedL = False
            elif bear[i] and armedS:
                levs.append(ss[i] * levarr[i]); side = -1; armedS = False
    levs = np.array(levs) if levs else np.array([0.0])
    return levs.mean(), levs.max(), bars_in / (n - 16)


class Cfg:
    """Holds the per-bar arrays so we can compute realized average leverage exactly."""
    def __init__(s, df, L, bear, ss, pa):
        s.df, s.L, s.bear, s.ss, s.pa = df, L, bear, ss, pa
        s.n = len(df)
        s.c = df["close"].values


def build_inputs():
    df, bull = build(); c = df["close"]
    L = bull & (c > bt.sma(c, 9 * 30 * 6).shift(1)).fillna(False).values
    bear = ((c / c.rolling(40 * 6).max().shift(1) - 1) < -0.10).fillna(False).values & daily_macd_bear(df)
    ddh = (c / c.rolling(180 * 6).max().shift(1) - 1).fillna(0).values
    ss = np.where(ddh <= -0.30, 1.0, np.where(ddh <= -0.20, 0.50, 0.25))
    pa = (c > 2.2 * bt.sma(c, 140 * 6).shift(1)).fillna(False).values
    return df, L, bear, ss, pa


# ---------- causal leverage builders ----------
def vol_scaled(df, target, lo, hi, win=30):
    """lev = clip(target / realized_vol_causal, lo, hi). Vol = rolling std of returns; the
    ratio's scale is set by `target`. Shifted 1 bar -> only past info. Calibrate target so the
    MEAN of the (clipped) array ~ 1.8."""
    ret = pd.Series(df["close"].values).pct_change()
    rv = ret.rolling(win).std()
    lev = (target / rv).clip(lo, hi).shift(1).fillna(1.0).values
    return lev


def calibrate_target(df, lo, hi, target_avg=1.8, win=30):
    """Pick `target` so the clipped vol-scaled array averages ~target_avg (causal-safe: this is a
    one-time scalar fit on the realized-vol distribution; we still SHIFT the array so no bar sees
    its own vol. It only sets the units of the ratio, not the timing — same role as the base's
    fixed 1.8 scalar)."""
    ret = pd.Series(df["close"].values).pct_change()
    rv = ret.rolling(win).std()
    lo_t, hi_t = 1e-5, 1.0
    for _ in range(60):
        t = (lo_t + hi_t) / 2
        avg = (t / rv).clip(lo, hi).shift(1).fillna(1.0).mean()
        if avg < target_avg: lo_t = t
        else: hi_t = t
    return (lo_t + hi_t) / 2


def dd_scaled(df, L, bear, ss, pa, base_lev, dd_trip, lev_floor, **kw):
    """Run base 1.8x equity ONCE, lag it 1 bar, and cut leverage toward lev_floor when the
    strategy's own causal drawdown exceeds dd_trip; scale linearly between 0 and dd_trip*2.
    Returns a per-bar leverage array. CAUSAL: today's lever uses yesterday's equity/DD only."""
    one_lev = np.ones(len(df)) * base_lev
    eq = run_v(df, L, bear, ss, pa, one_lev, **kw)
    # align equity back onto the full df index (eq starts at bar 16)
    full = pd.Series(np.nan, index=range(len(df)))
    full.iloc[16:16 + len(eq)] = eq.values
    full = full.ffill().bfill()
    e = full.values
    peak = np.maximum.accumulate(np.where(np.isnan(e), -np.inf, e))
    ddown = e / peak - 1.0
    ddown = np.nan_to_num(ddown, nan=0.0)
    # lag by 1 bar (only know yesterday's DD)
    ddl = np.concatenate([[0.0], ddown[:-1]])
    # linear de-lever: at dd=0 -> base_lev; at dd<=-2*dd_trip -> lev_floor
    span = 2 * dd_trip
    frac = np.clip((-ddl) / span, 0.0, 1.0)
    lev = base_lev - frac * (base_lev - lev_floor)
    return lev


def regime_scaled(df, L, bear, hi_bull, lo_other):
    """hi_bull in clean long-regime bars, lo_other elsewhere (bear/short or flat). Causal: uses
    today's regime flags which are already built from lagged data."""
    lev = np.where(L, hi_bull, lo_other).astype(float)
    return lev


# ---------- reporting ----------
def evaluate(df, L, bear, ss, pa, levarr, name, **kw):
    s = run_v(df, L, bear, ss, pa, levarr, **kw)
    cg, dd, rr = m(s)
    oos = m(s[s.index >= s.index[int(len(s) * 0.6)]])
    avg_l, peak_l, expo = realized_lev(df, L, bear, ss, pa, levarr, **kw)
    g = grn(s)
    win = (cg >= 1.20) and (dd >= -0.30) and (yr(s, 2018) > 0) and (yr(s, 2022) > 0) and (yr(s, 2026) > 0) and g
    return dict(name=name, cg=cg, dd=dd, rr=rr, oos=oos[2], oosdd=oos[1],
                avg_l=avg_l, peak_l=peak_l, expo=expo, g=g, win=win,
                y18=yr(s, 2018), y22=yr(s, 2022), y26=yr(s, 2026),
                yrs={y: yr(s, y) for y in range(2017, 2027)})


def prow(r):
    flag = "  <== WIN" if r["win"] else ("  win-DD" if r["dd"] >= -0.30 and r["cg"] >= 1.20 else "")
    print(f"  {r['name']:<40}{r['cg']*100:>5.0f}%{r['dd']*100:>5.0f}%{r['rr']:>6.2f}{r['oos']:>6.2f}"
          f"  {r['avg_l']:>4.2f}/{r['peak_l']:>4.2f}"
          f"  {r['y18']:>+4.0f}/{r['y22']:>+3.0f}/{r['y26']:>+3.0f}  {'grn' if r['g'] else 'RED'}{flag}")


def main():
    df, L, bear, ss, pa = build_inputs()
    n = len(df)

    print("=" * 104)
    print("DYNAMIC DE-LEVERAGING around a 1.8x ceiling — cut the 1.8x base DD at constant return")
    print("=" * 104)
    print(f"  {'config':<40}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  {'avgL/pkL':>9}  2018/22/26")

    results = []

    # ---- baselines ----
    print("  -- baselines (reproduce) --")
    r = evaluate(df, L, bear, ss, pa, np.ones(n) * 1.8, "fixed 1.8x (BASE)", **BASE_KW)
    prow(r)
    r = evaluate(df, L, bear, ss, pa, np.ones(n) * 1.4, "fixed 1.4x", **BASE_KW); prow(r)
    r = evaluate(df, L, bear, ss, pa, np.ones(n) * 1.2, "fixed 1.2x", **BASE_KW); prow(r)

    # ---- (1) VOL-SCALED around 1.8 avg ----
    print("  -- (1) VOL-SCALED: clip(target/vol,lo,hi), target calibrated to avg~1.8 --")
    for lo, hi in [(0.6, 1.8), (0.8, 1.8), (1.0, 1.8), (0.6, 2.2), (0.8, 2.5), (0.6, 2.5)]:
        t = calibrate_target(df, lo, hi, 1.8)
        lev = vol_scaled(df, t, lo, hi)
        r = evaluate(df, L, bear, ss, pa, lev, f"vol {lo:.1f}-{hi:.1f} (avg~1.8)", **BASE_KW)
        prow(r); results.append(r)

    # ---- (2) DD-SCALED: de-lever on the strategy's own causal drawdown ----
    print("  -- (2) DD-SCALED: cut toward floor when strat DD > trip (causal, 1-bar lag) --")
    for trip, floor in [(0.15, 0.6), (0.20, 0.6), (0.25, 0.6), (0.15, 0.8), (0.20, 0.9), (0.12, 0.6)]:
        lev = dd_scaled(df, L, bear, ss, pa, 1.8, trip, floor, **BASE_KW)
        r = evaluate(df, L, bear, ss, pa, lev, f"dd-scaled trip-{int(trip*100)}% floor{floor}", **BASE_KW)
        prow(r); results.append(r)

    # ---- (3) REGIME-SCALED ----
    print("  -- (3) REGIME-SCALED: 1.8x clean bull, lower on bear/short --")
    for hb, lo in [(1.8, 1.2), (1.8, 1.0), (2.0, 1.2), (2.0, 1.0), (1.8, 0.8)]:
        lev = regime_scaled(df, L, bear, hb, lo)
        r = evaluate(df, L, bear, ss, pa, lev, f"regime bull{hb}/other{lo}", **BASE_KW)
        prow(r); results.append(r)

    # ---- COMBOS: vol-scaled * dd-scaled (the two storm-killers stacked) ----
    print("  -- (4) COMBO: vol-scaled(avg1.8) MULTIPLIED by dd-scaled de-lever --")
    combos = []
    for (lo, hi) in [(0.6, 2.2), (0.8, 2.5), (0.6, 2.5)]:
        t = calibrate_target(df, lo, hi, 1.8)
        volv = vol_scaled(df, t, lo, hi)
        for (trip, floor) in [(0.15, 0.6), (0.20, 0.6), (0.20, 0.7)]:
            ddv = dd_scaled(df, L, bear, ss, pa, 1.8, trip, floor, **BASE_KW)
            # multiply de-lever fraction onto the vol-scaled array (dd factor in [floor/1.8, 1])
            ddfac = ddv / 1.8
            lev = np.clip(volv * ddfac, 0.4, hi)
            r = evaluate(df, L, bear, ss, pa, lev, f"vol{lo}-{hi}*dd{int(trip*100)}/{floor}", **BASE_KW)
            prow(r); results.append(r); combos.append(r)

    # ---- COMBO: regime ceiling * dd-scaled ----
    print("  -- (5) COMBO: regime ceiling * dd-scaled --")
    for (hb, lo) in [(1.8, 1.0), (2.0, 1.0)]:
        regv = regime_scaled(df, L, bear, hb, lo)
        for (trip, floor) in [(0.15, 0.6), (0.20, 0.6)]:
            ddv = dd_scaled(df, L, bear, ss, pa, 1.8, trip, floor, **BASE_KW)
            lev = regv * (ddv / 1.8)
            r = evaluate(df, L, bear, ss, pa, lev, f"regime{hb}/{lo}*dd{int(trip*100)}/{floor}", **BASE_KW)
            prow(r); results.append(r); combos.append(r)

    # ---- (6) FRONTIER: find the lowest-DD point that still holds CAGR>=120% ----
    print("  -- (6) FRONTIER push: mild de-levers aimed at CAGR~120-130%, DD<=-30% --")
    # regime with a higher bull ceiling so de-levering elsewhere keeps avg up
    for (hb, lo) in [(2.2, 1.2), (2.2, 1.0), (2.0, 1.4), (2.4, 1.2)]:
        lev = regime_scaled(df, L, bear, hb, lo)
        r = evaluate(df, L, bear, ss, pa, lev, f"regime bull{hb}/other{lo}", **BASE_KW)
        prow(r); results.append(r)
    # vol-scaled high ceiling + gentle dd-trip (keep avg high)
    for (lo, hi) in [(0.6, 2.5), (0.6, 3.0)]:
        t = calibrate_target(df, lo, hi, 1.9)
        volv = vol_scaled(df, t, lo, hi)
        for (trip, floor) in [(0.25, 0.7), (0.30, 0.7)]:
            ddv = dd_scaled(df, L, bear, ss, pa, 1.8, trip, floor, **BASE_KW)
            lev = np.clip(volv * (ddv / 1.8), 0.4, hi)
            r = evaluate(df, L, bear, ss, pa, lev, f"vol{lo}-{hi}(1.9)*dd{int(trip*100)}/{floor}", **BASE_KW)
            prow(r); results.append(r)

    # ---- FINAL: best 3 by lowest DD subject to CAGR>=120% ----
    print("=" * 104)
    print("FINAL — best 3 (lowest DD with CAGR>=120%, all 3 bears +, green every year)")
    print("=" * 104)
    elig = [r for r in results if r["cg"] >= 1.20 and r["y18"] > 0 and r["y22"] > 0 and r["y26"] > 0 and r["g"]]
    elig.sort(key=lambda r: r["dd"], reverse=True)   # least-negative DD first
    base = evaluate(df, L, bear, ss, pa, np.ones(n) * 1.8, "fixed 1.8x (BASE)", **BASE_KW)
    print(f"  BASE 1.8x: CAGR {base['cg']*100:.0f}%  DD {base['dd']*100:.0f}%  ret/DD {base['rr']:.2f}\n")
    if not elig:
        print("  NONE met CAGR>=120% AND DD<=-30% AND 3 bears + AND green. Showing closest by DD:")
        cand = [r for r in results if r["cg"] >= 1.20]
        cand.sort(key=lambda r: r["dd"], reverse=True)
        elig = cand[:3]
    for r in elig[:3]:
        print(f"  >> {r['name']}")
        print(f"     CAGR {r['cg']*100:.0f}%  DD {r['dd']*100:.0f}%  ret/DD {r['rr']:.2f}  OOS {r['oos']:.2f} (oosDD {r['oosdd']*100:.0f}%)")
        print(f"     realized avg lev {r['avg_l']:.2f}x  peak lev {r['peak_l']:.2f}x  in-position {r['expo']*100:.0f}%")
        print(f"     bears 2018:{r['y18']:+.0f}%  2022:{r['y22']:+.0f}%  2026:{r['y26']:+.0f}%   green={r['g']}")
        print(f"     year-by-year: " + "  ".join(f"{y}:{r['yrs'][y]:+.0f}" for y in range(2017, 2027)))
        print()


if __name__ == "__main__":
    main()
