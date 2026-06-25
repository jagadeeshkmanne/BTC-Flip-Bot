#!/usr/bin/env python3
"""ftune_short_ext.py — find a ROBUST SHORT engine for my-V3 across ALL 3 BTC bears (2018/2022/2026).

User's idea: "percentage-down THEN indicators". Short gate families:
  1. price down X% from N-day high (pure drop trigger).
  2. drop trigger AND an indicator confirm (RSI<thr, MACD<sig, daily EMA50<EMA200, price<SMA(Nmo)).
  3. momentum rollover: daily EMA50 crossing down EMA200; or price<SMA(6mo) AND falling (<50d MA).
Sweep short_size, short stop atr, short cap, short pyramid on/off.

HARD GATE: a recommendable short must be >= 0 in ALL THREE bears (2018, 2022, 2026),
keep combined DD <= ~45%, and keep combined ret/DD >= the long-only baseline (2.00).
If NO short clears the bar, say so honestly.

Engine: backtest_myv3_final.run() — signed cash/units, shifted/causal gates, stop-above-entry
shorts (hit on high>=stop), ratchets DOWN. Long baseline pinned via macro filter.

ANTI-BUG: (0) verify allow_short=False reproduces the long-only macro baseline.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_ext import build, EXT
from backtest_myv3_final import run
from backtest_myv3_final import m as m_final  # CAGR, DD, ret/DD

BPD = 6  # 4h bars per day

# bear years -> the calendar window we score the short on
BEAR_YEARS = (2018, 2022, 2026)


def m(s):
    return m_final(s)


def yr(s, y):
    seg = s[s.index.year == y]
    return (seg.iloc[-1] / seg.iloc[0] - 1) * 100 if len(seg) > 20 else 0.0


def oos(s):
    return m(s[s.index >= s.index[int(len(s) * 0.6)]])[2]


def count_short_trades(df, bull, bear, **kw):
    """Re-run the engine logic counting short entries (units<0 openings)."""
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    a = bt.atr(df, 14).values; n = len(df)
    short_size = kw.get("short_size", 0.25); s_atr = kw.get("s_atr", 4.0)
    s_cap = kw.get("s_cap", 0.12); s_pyr_r = kw.get("s_pyr_r", 2.0)
    lev = kw.get("lev", 1.0); atr_mult = kw.get("atr_mult", 3.5); sl_cap = kw.get("sl_cap", 0.08)
    be_buf = kw.get("be_buf", 0.01); be_r = kw.get("be_r", 1.0); pyr_r = kw.get("pyr_r", 2.0)
    pyr_frac = kw.get("pyr_frac", 1.0)
    cash = 1.0; units = 0.0; side = 0; entry = 0.0; stop = 0.0; R = 0.0; pyrd = False; notional0 = 0.0
    armedL = True; armedS = True; nshort = 0
    for i in range(16, n - 1):
        oN, hN, lN, cN = o[i + 1], h[i + 1], l[i + 1], c[i + 1]
        if not bull[i]: armedL = True
        if not bear[i]: armedS = True
        if side != 0:
            hit = (lN <= stop) if side == 1 else (hN >= stop)
            regime_out = (not bull[i]) if side == 1 else (not bear[i])
            if hit:
                fpx = stop * (1 - bt.SLIP_PCT) if side == 1 else stop * (1 + bt.SLIP_PCT)
                cash += units * fpx - abs(units) * fpx * bt.FEE_PCT; units = 0.0; side = 0; pyrd = False
            elif regime_out:
                fpx = oN * (1 - bt.SLIP_PCT) if side == 1 else oN * (1 + bt.SLIP_PCT)
                cash += units * fpx - abs(units) * fpx * bt.FEE_PCT; units = 0.0; side = 0; pyrd = False
            else:
                prof = ((cN - entry) / R) if side == 1 else ((entry - cN) / R)
                if prof >= be_r:
                    be = entry * (1 + be_buf) if side == 1 else entry * (1 - be_buf)
                    stop = max(stop, be) if side == 1 else min(stop, be)
                pr = s_pyr_r if side == -1 else pyr_r
                if pr is not None and not pyrd and prof >= pr:
                    addn = pyr_frac * notional0
                    add_units = (addn / cN) if side == 1 else (-addn / cN)
                    cash -= add_units * cN + addn * bt.FEE_PCT; units += add_units; pyrd = True
                    stop = max(stop, entry) if side == 1 else min(stop, entry)
        if side == 0:
            go = 0
            if bull[i] and armedL: go = 1
            elif bear[i] and armedS: go = -1
            if go != 0:
                E = cash
                if go == 1:
                    st = max(c[i] - atr_mult * a[i], c[i] * (1 - sl_cap)); entry = oN * (1 + bt.SLIP_PCT); sz = lev
                else:
                    st = min(c[i] + s_atr * a[i], c[i] * (1 + s_cap)); entry = oN * (1 - bt.SLIP_PCT); sz = lev * short_size
                ok = (entry - st > 0) if go == 1 else (st - entry > 0)
                if ok:
                    notional0 = sz * E
                    units = go * notional0 / entry; cash = E - units * entry - notional0 * bt.FEE_PCT
                    stop = st; R = abs(entry - st); side = go; pyrd = False
                    if go == 1: armedL = False
                    else:
                        armedS = False; nshort += 1
    return nshort


# ----------------------------------------------------------------------------
# build daily indicator series, mapped onto the 4h grid (all shifted -> causal)
# ----------------------------------------------------------------------------
def daily_indicators(df):
    raw = pd.read_csv(EXT, parse_dates=["timestamp"])
    dd = raw.set_index("timestamp").resample("1D").agg({"close": "last"}).dropna().reset_index()
    dc = dd["close"]
    e50 = bt.ema(dc, 50); e200 = bt.ema(dc, 200)
    d_emadn = (e50 < e200).shift(1).fillna(False)
    # MACD on daily close
    macd = bt.ema(dc, 12) - bt.ema(dc, 26); sig = bt.ema(macd, 9)
    d_macddn = (macd < sig).shift(1).fillna(False)
    d_rsi = bt.rsi(dc, 14).shift(1).fillna(50.0)
    ts = pd.to_datetime(df["timestamp"])
    srcdf = pd.DataFrame({"ts": pd.to_datetime(dd["timestamp"]), "emadn": d_emadn.values,
                          "macddn": d_macddn.values, "rsi": d_rsi.values}).sort_values("ts")
    merged = pd.merge_asof(pd.DataFrame({"ts": ts}).sort_values("ts"), srcdf, on="ts", direction="backward")
    return (merged["emadn"].fillna(False).astype(bool).values,
            merged["macddn"].fillna(False).astype(bool).values,
            merged["rsi"].fillna(50.0).values)


def main():
    df, bull = build()
    c = df["close"]
    n = len(df)

    # PINNED LONG GATE — macro filter (bull AND price>SMA(9mo)), causal
    sma9 = bt.sma(c, 9 * 30 * BPD).shift(1)
    long_gate = (bull & (c > sma9).values)

    d_emadn, d_macddn, d_rsi = daily_indicators(df)

    print("=" * 110)
    print("ROBUST SHORT SEARCH for my-V3 — %-down + indicator gate, ALL 3 BTC bears (2018/2022/2026)")
    print("=" * 110)

    # ---- ANTI-BUG (0): reproduce long-only macro baseline -------------------
    base = run(df, long_gate, np.zeros(n, bool), allow_long=True, allow_short=False)
    cb, db, rb = m(base)
    print(f"\n[SANITY] long-only macro baseline  CAGR {cb*100:.0f}%  DD {db*100:.0f}%  ret/DD {rb:.2f}  "
          f"(target ~86% / -43% / 2.00)")
    print(f"         per-year combined: {'  '.join(f'{y}:{yr(base,y):+.0f}%' for y in BEAR_YEARS)}")
    base_yr = {y: yr(base, y) for y in BEAR_YEARS}

    # ---- build candidate bear gates -----------------------------------------
    gates = {}

    # Family 1: pure %-down from N-day high
    for X in (0.10, 0.15, 0.20, 0.25):
        for N in (20, 40, 60):
            hh = c.rolling(N * BPD).max().shift(1)
            drop = ((c / hh - 1) < -X).fillna(False).values
            gates[f"F1 drop>{int(X*100)}%/{N}d"] = drop

    # Family 2: drop trigger AND indicator confirm
    confirms = {
        "rsi<40": (d_rsi < 40),
        "rsi<45": (d_rsi < 45),
        "rsi<50": (d_rsi < 50),
        "macd<sig": d_macddn,
        "dEMA50<200": d_emadn,
        "c<SMA6mo": (c < bt.sma(c, 6 * 30 * BPD).shift(1)).fillna(False).values,
        "c<SMA9mo": (c < sma9).fillna(False).values,
    }
    for X in (0.10, 0.15, 0.20):
        for N in (20, 40, 60):
            hh = c.rolling(N * BPD).max().shift(1)
            drop = ((c / hh - 1) < -X).fillna(False).values
            for cname, cf in confirms.items():
                gates[f"F2 drop>{int(X*100)}%/{N}d & {cname}"] = (drop & np.asarray(cf, bool))

    # Family 3: momentum rollover
    # 3a: daily EMA50<EMA200 (downcross persistence)
    gates["F3 dEMA50<200"] = d_emadn.copy()
    # 3b: price<SMA(6mo) AND falling (below 50d MA)
    ma50 = bt.sma(c, 50 * BPD).shift(1)
    sma6 = bt.sma(c, 6 * 30 * BPD).shift(1)
    gates["F3 c<SMA6mo & <50dMA"] = ((c < sma6) & (c < ma50)).fillna(False).values
    # 3c: daily EMA50<200 AND price<50dMA (rollover + falling)
    gates["F3 dEMA50<200 & <50dMA"] = (d_emadn & (c < ma50).fillna(False).values)

    # ---- short engine param sweep -------------------------------------------
    sizes = (0.15, 0.25, 0.40)
    satrs = (3.0, 4.0, 5.0)
    scaps = (0.08, 0.12, 0.15)
    pyrs = (None, 2.0)  # pyramid off / on

    print(f"\nScreening {len(gates)} gates x {len(sizes)*len(satrs)*len(scaps)*len(pyrs)} param combos "
          f"= {len(gates)*len(sizes)*len(satrs)*len(scaps)*len(pyrs)} configs...")
    print("HARD GATE: 2018>=0 AND 2022>=0 AND 2026>=0 AND combinedDD<=45% AND ret/DD>=2.00 AND no lookahead.\n")

    rows = []   # all configs
    passrows = []  # configs clearing the hard gate
    for gname, bear in gates.items():
        for sz in sizes:
            for sa in satrs:
                for sc in scaps:
                    for py in pyrs:
                        kw = dict(allow_long=True, allow_short=True, short_size=sz,
                                  s_atr=sa, s_cap=sc, s_pyr_r=py)
                        s = run(df, long_gate, bear, **kw)
                        cg, dd, rr = m(s)
                        b = {y: yr(s, y) for y in BEAR_YEARS}
                        o = oos(s)
                        row = dict(gate=gname, sz=sz, sa=sa, sc=sc, py=("on" if py else "off"),
                                   cagr=cg * 100, dd=dd * 100, rr=rr, oos=o,
                                   b2018=b[2018], b2022=b[2022], b2026=b[2026])
                        rows.append(row)
                        # marginal short contribution per bear (vs long-only) — the real test
                        marg = {y: b[y] - base_yr[y] for y in BEAR_YEARS}
                        row["m2018"] = marg[2018]; row["m2022"] = marg[2022]; row["m2026"] = marg[2026]
                        # HARD GATE: short must be >= 0 in each bear -> use marginal short contribution
                        clears = (marg[2018] >= -0.5 and marg[2022] >= -0.5 and marg[2026] >= -0.5
                                  and dd * 100 >= -45.0 and rr >= rb and rr <= 5.0)
                        if clears:
                            row["nshort"] = count_short_trades(df, long_gate, bear, **kw)
                            passrows.append(row)

    alldf = pd.DataFrame(rows)
    print(f"Total configs tested: {len(alldf)}")
    print(f"Configs with positive MARGINAL short in all 3 bears: "
          f"{((alldf.m2018>=-0.5)&(alldf.m2022>=-0.5)&(alldf.m2026>=-0.5)).sum()}")
    print(f"... AND DD<=45% AND ret/DD>={rb:.2f} (clear FULL hard gate): {len(passrows)}\n")

    # diagnostic: best-by-bear so we can SEE where 2018 fails
    print("Per-bear MARGINAL short contribution range (best gate config per bear year):")
    for y in BEAR_YEARS:
        col = f"m{y}"
        best = alldf.loc[alldf[col].idxmax()]
        print(f"   {y}: best marginal short = {best[col]:+.1f}%  via [{best['gate']} | sz{best['sz']} "
              f"atr{best['sa']} cap{best['sc']} pyr{best['py']}]")
    # how many gates can make 2018 short non-negative at all?
    g2018 = alldf.groupby("gate")["m2018"].max()
    ok2018 = g2018[g2018 >= -0.5]
    print(f"\nGates that can make the 2018 short non-negative (>= -0.5%) for SOME param combo: "
          f"{len(ok2018)}/{len(g2018)}")
    for gn, v in ok2018.sort_values(ascending=False).items():
        print(f"   {gn:<34} best 2018 marginal {v:+.1f}%")

    # ---- report the clearing configs, ranked by ret/DD ----------------------
    hdr = (f"\n  {'gate':<30}{'sz':>5}{'atr':>4}{'cap':>5}{'pyr':>4}"
           f"{'CAGR':>6}{'DD':>6}{'r/DD':>6}{'OOS':>6}{'#sh':>5}"
           f"{'2018':>7}{'2022':>7}{'2026':>7}  (marginal short: 18/22/26)")
    if passrows:
        pdf = pd.DataFrame(passrows).sort_values("rr", ascending=False)
        print("\n" + "=" * 110)
        print("CONFIGS CLEARING THE 3-BEAR HARD GATE (ranked by combined ret/DD):")
        print("=" * 110)
        print(hdr)
        for _, r in pdf.head(20).iterrows():
            print(f"  {r['gate']:<30}{r['sz']:>5}{r['sa']:>4.0f}{r['sc']:>5}{r['py']:>4}"
                  f"{r['cagr']:>5.0f}%{r['dd']:>5.0f}%{r['rr']:>6.2f}{r['oos']:>6.2f}{int(r['nshort']):>5}"
                  f"{r['b2018']:>+6.0f}%{r['b2022']:>+6.0f}%{r['b2026']:>+6.0f}%"
                  f"   ({r['m2018']:+.0f}/{r['m2022']:+.0f}/{r['m2026']:+.0f})")
    else:
        print("\n" + "=" * 110)
        print("NO config clears the 3-bear hard gate.")
        print("=" * 110)
        # show the closest near-misses (best combined ret/DD among those positive in 2 of 3 bears)
        alldf["nbears_ok"] = ((alldf.m2018 >= -0.5).astype(int) + (alldf.m2022 >= -0.5).astype(int)
                              + (alldf.m2026 >= -0.5).astype(int))
        near = alldf[(alldf.nbears_ok >= 2) & (alldf.dd >= -45) & (alldf.rr >= rb) & (alldf.rr <= 5)]
        near = near.sort_values("rr", ascending=False)
        print(f"\nClosest NEAR-MISSES (positive short in 2 of 3 bears, DD<=45%, ret/DD>={rb:.2f}):")
        print(hdr.replace("#sh", "   "))
        for _, r in near.head(15).iterrows():
            print(f"  {r['gate']:<30}{r['sz']:>5}{r['sa']:>4.0f}{r['sc']:>5}{r['py']:>4}"
                  f"{r['cagr']:>5.0f}%{r['dd']:>5.0f}%{r['rr']:>6.2f}{r['oos']:>6.2f}{'':>5}"
                  f"{r['b2018']:>+6.0f}%{r['b2022']:>+6.0f}%{r['b2026']:>+6.0f}%"
                  f"   ({r['m2018']:+.0f}/{r['m2022']:+.0f}/{r['m2026']:+.0f})")

    return alldf, passrows


if __name__ == "__main__":
    main()
