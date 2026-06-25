#!/usr/bin/env python3
"""ag_trendstrength.py — TREND-STRENGTH GATING to cut my-V3's -43% DD.

ANGLE: the -43% DD comes from "fake bull" whipsaws — the regime flips bull in weak/choppy
trends, the long enters, and stops out. Idea: only take longs (HARD GATE) or size them up
(SIZE SCALER) when the trend is STRONG; sit out / half-size weak-or-choppy bulls.

LONG base gate is pinned to the VALIDATED base: bull & (price>SMA(9mo)).  We AND a STRENGTH
filter onto it (hard gate) or scale long size by strength (scaler). SHORT sleeve is left
exactly as the validated base so the comparison is apples-to-apples on the long strength idea:
    SHORT = (drop>10% off 40d-high) & (daily MACD<signal), sz0.40, s_atr5, s_cap0.15, no pyr.

VALIDATED BASE (full 2017-2026, reproduced byte-for-byte): CAGR 96% DD -43% ret/DD 2.24 OOS 3.05,
bears 2018+26%/2022+11%/2026+8%, green every year.  GOAL: REDUCE -43% DD while keeping CAGR;
beat ret/DD 2.24, ideally DD better than -43%.

STRENGTH SIGNALS (all on CLOSED bars; daily merges & slopes shift(1) => no lookahead):
  ADX(14)>{20,25,30}            (4h directional-strength)
  |EMA50 slope|>thr            (EMA50 rising over 10 bars by >thr*price; longs are up-trends)
  ribbon EMA20>EMA50>EMA100    (stacked up, aligned trend)
  price>EMA50 for N bars       (sustained, not a one-bar poke) N in {5,10,20}

TEST MODES:
  (A) HARD GATE  : LONG = base & strength            (sit out weak bulls entirely)
  (B) SIZE SCALER: long size = full if strong else half (stay in, smaller in chop)

ANTI-BUG: base reproduced byte-for-byte first (sanity row); ADX/slope/ribbon/streak all
shift(1) or built on closed bars (no lookahead); the scaler engine is a byte-for-byte copy of
backtest_myv3_final.run with the ONLY change being a per-bar long-size array, and size=full
everywhere must reproduce the base exactly (sanity row). Stops still ratchet. Distrust ret/DD>5
or OOS>6. GATE for a WINNER: full data + OOS + all 3 bears>=0 + green every year + beats 2.24.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_ext import build, EXT
from backtest_myv3_final import run, m

BPD = 6  # 4h bars per day
FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT

# pinned long structural params (validated base)
LP = dict(atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0)
# pinned short sleeve (validated base)
SP = dict(short_size=0.40, s_atr=5.0, s_cap=0.15, s_pyr_r=None)


# ----------------------------------------------------------------------------
# build the validated base gates
# ----------------------------------------------------------------------------
def base_gates(df):
    c = df["close"]
    macro = (c > bt.sma(c, 9 * 30 * BPD).shift(1)).fillna(False).to_numpy()
    bull = df["_bull"].to_numpy()
    LONG = bull & macro
    # SHORT = drop>10% off 40d-high  &  daily MACD<signal
    hh40 = c.rolling(40 * BPD).max().shift(1)
    drop10 = ((c / hh40 - 1) < -0.10).fillna(False).to_numpy()
    dd = pd.read_csv(EXT, parse_dates=["timestamp"]).set_index("timestamp").resample("1D").agg(
        {"close": "last"}).dropna().reset_index()
    dc = dd["close"]
    ml = bt.ema(dc, 12) - bt.ema(dc, 26); ms = bt.ema(ml, 9)
    macd = _merge_to_4h(df, dd["timestamp"], (ml < ms).shift(1).fillna(False).to_numpy())
    BEAR = drop10 & macd
    return LONG, BEAR


def _merge_to_4h(df, dts, vals):
    ts = pd.to_datetime(df["timestamp"])
    out = pd.merge_asof(
        pd.DataFrame({"ts": ts}).sort_values("ts"),
        pd.DataFrame({"ts": pd.to_datetime(dts), "b": np.asarray(vals)}).sort_values("ts"),
        on="ts", direction="backward")["b"]
    return out.fillna(False).to_numpy().astype(bool)


# ----------------------------------------------------------------------------
# trend-strength signals (all causal)
# ----------------------------------------------------------------------------
def strength_signals(df):
    c = df["close"]
    sig = {}

    ax = bt.adx(df, 14).shift(1)
    for t in (20, 25, 30):
        sig[f"adx>{t}"] = (ax > t).fillna(False).to_numpy()

    # EMA50 slope: rise over 10 bars normalized by price; longs are up-trends so we want UP slope
    e50 = bt.ema(c, 50)
    slope = ((e50 - e50.shift(10)) / e50.shift(10)).shift(1)  # fractional rise over 10 bars
    for t in (0.02, 0.04, 0.06):
        sig[f"slope>{t}"] = (slope > t).fillna(False).to_numpy()

    # ribbon stacked up: EMA20>EMA50>EMA100 (use closed-bar values, shift(1) to be safe)
    e20 = bt.ema(c, 20); e100 = bt.ema(c, 100)
    ribbon = ((e20 > e50) & (e50 > e100)).shift(1).fillna(False).to_numpy()
    sig["ribbon"] = ribbon

    # price>EMA50 for N consecutive closed bars
    above = (c > e50)
    streak = above.groupby((~above).cumsum()).cumcount() + 1  # consecutive True count
    streak = (streak * above)  # 0 when below
    for N in (5, 10, 20):
        sig[f"above50x{N}"] = (streak.shift(1) >= N).fillna(False).to_numpy()

    return sig


# ----------------------------------------------------------------------------
# size-scaler engine: byte-for-byte copy of backtest_myv3_final.run, the ONLY
# change is `sz=lev` for longs becomes `sz=lev*long_size[i]` (per-bar long size).
# long_size all-ones must reproduce the base exactly (verified by sanity row).
# ----------------------------------------------------------------------------
def run_scaled(df, bull, bear, long_size, allow_long=True, allow_short=True, lev=1.0,
               short_size=0.25, pyr_r=2.0, pyr_frac=1.0, be_r=1.0, atr_mult=3.5, sl_cap=0.08,
               be_buf=0.01, s_atr=4.0, s_cap=0.12, s_pyr_r=2.0, trail_k=None):
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    a = bt.atr(df, 14).values; n = len(df)
    ls = np.asarray(long_size, float)
    cash = 1.0; units = 0.0; side = 0; entry = 0.0; stop = 0.0; R = 0.0; pyrd = False; notional0 = 0.0
    peakH = 0.0; armedL = True; armedS = True
    eq = np.ones(n)
    for i in range(16, n - 1):
        oN, hN, lN, cN = o[i + 1], h[i + 1], l[i + 1], c[i + 1]
        if not bull[i]: armedL = True
        if not bear[i]: armedS = True
        if side != 0:
            if side == 1 and trail_k is not None:
                peakH = max(peakH, h[i]); stop = max(stop, peakH - trail_k * a[i])
            hit = (lN <= stop) if side == 1 else (hN >= stop)
            regime_out = (not bull[i]) if side == 1 else (not bear[i])
            if hit:
                fpx = stop * (1 - SLIP) if side == 1 else stop * (1 + SLIP)
                cash += units * fpx - abs(units) * fpx * FEE; units = 0.0; side = 0; pyrd = False
            elif regime_out:
                fpx = oN * (1 - SLIP) if side == 1 else oN * (1 + SLIP)
                cash += units * fpx - abs(units) * fpx * FEE; units = 0.0; side = 0; pyrd = False
            else:
                prof = ((cN - entry) / R) if side == 1 else ((entry - cN) / R)
                if prof >= be_r:
                    be = entry * (1 + be_buf) if side == 1 else entry * (1 - be_buf)
                    stop = max(stop, be) if side == 1 else min(stop, be)
                pr = s_pyr_r if side == -1 else pyr_r
                if pr is not None and not pyrd and prof >= pr:
                    addn = pyr_frac * notional0
                    add_units = (addn / cN) if side == 1 else (-addn / cN)
                    cash -= add_units * cN + addn * FEE; units += add_units; pyrd = True
                    stop = max(stop, entry) if side == 1 else min(stop, entry)
        if side == 0:
            go = 0
            if allow_long and bull[i] and armedL: go = 1
            elif allow_short and bear[i] and armedS: go = -1
            if go != 0:
                E = cash
                if go == 1:
                    st = max(c[i] - atr_mult * a[i], c[i] * (1 - sl_cap)); entry = oN * (1 + SLIP); sz = lev * ls[i]
                else:
                    st = min(c[i] + s_atr * a[i], c[i] * (1 + s_cap)); entry = oN * (1 - SLIP); sz = lev * short_size
                ok = (entry - st > 0) if go == 1 else (st - entry > 0)
                if ok and sz > 0:
                    notional0 = sz * E
                    units = go * notional0 / entry; cash = E - units * entry - notional0 * FEE
                    stop = st; R = abs(entry - st); side = go; pyrd = False; peakH = entry
                    if go == 1: armedL = False
                    else: armedS = False
        eq[i + 1] = cash + units * cN
    return pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]


# ----------------------------------------------------------------------------
# metrics / evaluation
# ----------------------------------------------------------------------------
def yr(s, y):
    seg = s[s.index.year == y]
    return (seg.iloc[-1] / seg.iloc[0] - 1) * 100 if len(seg) > 20 else 0.0


def evaluate(s):
    cg, dd, rr = m(s)
    o = m(s[s.index >= s.index[int(len(s) * 0.6)]])
    years = {y: yr(s, y) for y in range(2017, 2027)}
    bears = (years[2018], years[2022], years[2026])
    allgreen = all(v >= 0 for v in years.values())
    return dict(cg=cg, dd=dd, rr=rr, oos=o[2], bears=bears, allgreen=allgreen, years=years)


def gate(res, base_rr=2.24):
    return (res["rr"] > base_rr and all(b >= 0 for b in res["bears"])
            and res["allgreen"] and 0 < res["oos"] <= 6 and res["rr"] <= 5)


def main():
    df, bull = build()
    df = df.copy(); df["_bull"] = bull
    LONG, BEAR = base_gates(df)
    sig = strength_signals(df)
    nfull = np.ones(len(df))

    print("=" * 112)
    print("TREND-STRENGTH GATING — cut my-V3's -43% DD by skipping/half-sizing weak 'fake bull' trends. BTC 4h 2017-2026")
    print("=" * 112)

    # ---- ANTI-BUG sanity: base reproduced, and scaler==base at size=1 ----
    sbase = run(df, LONG, BEAR, allow_long=True, allow_short=True, **LP, **SP)
    rb = evaluate(sbase)
    s_scaler_full = run_scaled(df, LONG, BEAR, nfull, allow_long=True, allow_short=True, **LP, **SP)
    diff = float(np.max(np.abs(sbase.values - s_scaler_full.values)))
    print(f"\n[SANITY] base reproduced:  CAGR {rb['cg']*100:.0f}%  DD {rb['dd']*100:.0f}%  ret/DD {rb['rr']:.2f}  "
          f"OOS {rb['oos']:.2f}   (expect 96/-43/2.24/3.05)")
    print(f"[SANITY] scaler@size1 vs base max equity diff = {diff:.2e}  ({'OK ident' if diff < 1e-9 else 'MISMATCH!!'})")
    print(f"[SANITY] base bears 2018/22/26: {rb['bears'][0]:+.0f}/{rb['bears'][1]:+.0f}/{rb['bears'][2]:+.0f}  "
          f"green-every-year {'Y' if rb['allgreen'] else 'n'}")

    strengths = ["adx>20", "adx>25", "adx>30", "slope>0.02", "slope>0.04", "slope>0.06",
                 "ribbon", "above50x5", "above50x10", "above50x20"]

    hdr = (f"  {'config':<30}{'CAGR':>6}{'DD':>6}{'r/DD':>6}{'OOS':>6}  {'2018/22/26':>14}  "
           f"{'%bars':>6}  green  gate")
    print("\n" + "=" * 112)
    print("(A) HARD GATE — LONG = base & strength  (sit out weak bulls)")
    print("=" * 112)
    print(hdr); print("  " + "-" * 108)
    pb = "%+.0f/%+.0f/%+.0f" % rb["bears"]
    print(f"  {'BASE (no strength filter)':<30}{rb['cg']*100:>5.0f}%{rb['dd']*100:>5.0f}%{rb['rr']:>6.2f}"
          f"{rb['oos']:>6.2f}  {pb:>14}  {'100':>6}  {'Y' if rb['allgreen'] else 'n'}    --")
    print("  " + "-" * 108)

    rows = []
    base_long_bars = int(LONG.sum())
    for k in strengths:
        g = LONG & sig[k]
        s = run(df, g, BEAR, allow_long=True, allow_short=True, **LP, **SP)
        r = evaluate(s); r["mode"] = "hard"; r["name"] = k
        pct = 100.0 * int(g.sum()) / max(base_long_bars, 1)
        r["pct"] = pct
        rows.append(r)
        pb = "%+.0f/%+.0f/%+.0f" % r["bears"]
        ddflag = " DD<base" if r["dd"] > rb["dd"] else ""
        gflag = "WIN" if (gate(r) and r["rr"] > rb["rr"]) else ("rr+" if r["rr"] > rb["rr"] else "")
        print(f"  {'hard '+k:<30}{r['cg']*100:>5.0f}%{r['dd']*100:>5.0f}%{r['rr']:>6.2f}{r['oos']:>6.2f}  "
              f"{pb:>14}  {pct:>5.0f}%  {'Y' if r['allgreen'] else 'n'}    {gflag}{ddflag}")

    print("\n" + "=" * 112)
    print("(B) SIZE SCALER — long size = FULL if strength else HALF (half in {0.5}); stay in, smaller in chop")
    print("=" * 112)
    print(hdr); print("  " + "-" * 108)
    print(f"  {'BASE (always full size)':<30}{rb['cg']*100:>5.0f}%{rb['dd']*100:>5.0f}%{rb['rr']:>6.2f}"
          f"{rb['oos']:>6.2f}  {pb if False else '%+.0f/%+.0f/%+.0f'%rb['bears']:>14}  {'100':>6}  "
          f"{'Y' if rb['allgreen'] else 'n'}    --")
    print("  " + "-" * 108)
    for half in (0.5,):
        for k in strengths:
            ls = np.where(sig[k], 1.0, half)
            s = run_scaled(df, LONG, BEAR, ls, allow_long=True, allow_short=True, **LP, **SP)
            r = evaluate(s); r["mode"] = f"scale{half}"; r["name"] = k
            r["pct"] = 100.0 * float(np.mean(np.where(LONG, ls, np.nan)[LONG])) if LONG.any() else 0.0
            rows.append(r)
            pb = "%+.0f/%+.0f/%+.0f" % r["bears"]
            ddflag = " DD<base" if r["dd"] > rb["dd"] else ""
            gflag = "WIN" if (gate(r) and r["rr"] > rb["rr"]) else ("rr+" if r["rr"] > rb["rr"] else "")
            avgsz = float(np.mean(np.where(sig[k][LONG], 1.0, half))) if LONG.any() else 1.0
            print(f"  {'scale½ '+k:<30}{r['cg']*100:>5.0f}%{r['dd']*100:>5.0f}%{r['rr']:>6.2f}{r['oos']:>6.2f}  "
                  f"{pb:>14}  {avgsz*100:>5.0f}%  {'Y' if r['allgreen'] else 'n'}    {gflag}{ddflag}")

    # ---- rank all: better DD-improvement & ret/DD, gate first ----
    def keyf(r):
        return (1 if (gate(r) and r["rr"] > rb["rr"]) else 0, r["rr"])
    ranked = sorted(rows, key=keyf, reverse=True)

    print("\n" + "=" * 112)
    print("TOP 3 CONFIGS (ranked: passes robustness gate & beats base ret/DD first, then ret/DD)")
    print("=" * 112)
    top3 = ranked[:3]
    for rank, r in enumerate(top3, 1):
        pb = "%+.1f / %+.1f / %+.1f" % r["bears"]
        ddimp = (r["dd"] - rb["dd"]) * 100  # positive = shallower (better) DD
        print(f"\n  #{rank}  [{r['mode']}] {r['name']}")
        print(f"      CAGR {r['cg']*100:.1f}%   maxDD {r['dd']*100:.1f}%   ret/DD {r['rr']:.2f}   OOS ret/DD {r['oos']:.2f}")
        print(f"      DD vs base -43.x%: {ddimp:+.1f}pp ({'SHALLOWER/better' if ddimp > 0 else 'deeper/worse'})   "
              f"CAGR vs base 96%: {(r['cg']-rb['cg'])*100:+.0f}pp")
        print(f"      bears 2018/2022/2026: {pb}      green-every-year: {'YES' if r['allgreen'] else 'NO'}")
        print(f"      passes robustness gate: {'YES' if gate(r) else 'NO'}     "
              f"beats BASE ret/DD 2.24: {'YES' if r['rr'] > rb['rr'] else 'NO'}")

    # ---- year-by-year: base vs best ----
    best = top3[0]
    if best["mode"] == "hard":
        gbest = LONG & sig[best["name"]]
        sbest = run(df, gbest, BEAR, allow_long=True, allow_short=True, **LP, **SP)
    else:
        half = float(best["mode"].replace("scale", ""))
        ls = np.where(sig[best["name"]], 1.0, half)
        sbest = run_scaled(df, LONG, BEAR, ls, allow_long=True, allow_short=True, **LP, **SP)
    rbest = evaluate(sbest)

    print("\n" + "=" * 112)
    print(f"YEAR-BY-YEAR — base vs best [{best['mode']}] {best['name']}")
    print("=" * 112)
    print(f"    {'year':<6}{'BASE':>10}{'BEST':>10}")
    for y in range(2017, 2027):
        print(f"    {y:<6}{rb['years'][y]:>+9.1f}%{rbest['years'][y]:>+9.1f}%")

    # ---- verdict ----
    print("\n" + "=" * 112)
    print("VERDICT")
    print("=" * 112)
    winners = [r for r in rows if gate(r) and r["rr"] > rb["rr"]]
    dd_improvers = [r for r in rows if r["dd"] > rb["dd"] and r["allgreen"]
                    and all(b >= 0 for b in r["bears"])]
    if winners:
        w = max(winners, key=lambda r: r["rr"])
        print(f"  {len(winners)} config(s) pass the FULL gate AND beat base ret/DD 2.24.")
        print(f"  Best: [{w['mode']}] {w['name']}  ret/DD {w['rr']:.2f} vs 2.24,  DD {w['dd']*100:.1f}% vs -43%,  "
              f"CAGR {w['cg']*100:.0f}% vs 96%.")
        print("  => TREND-STRENGTH GATING BEATS the base on ret/DD.")
    else:
        print("  NO config passes the full gate AND beats base ret/DD 2.24.")
        if dd_improvers:
            di = max(dd_improvers, key=lambda r: r["dd"])
            print(f"  But {len(dd_improvers)} config(s) DO reduce the DD while staying green every year & all bears>=0.")
            print(f"  Shallowest-DD such config: [{di['mode']}] {di['name']}  "
                  f"DD {di['dd']*100:.1f}% (vs -43%),  ret/DD {di['rr']:.2f} (vs 2.24),  CAGR {di['cg']*100:.0f}% (vs 96%).")
            print("  => DD improved but the CAGR give-up means ret/DD did not beat base. Strength gating helps DD, "
                  "not the ret/DD ratio.")
        else:
            print("  No config both reduced DD and stayed green/all-bears-positive.")
        print("  => Base (no strength filter) remains the validated config on ret/DD.")


if __name__ == "__main__":
    main()
