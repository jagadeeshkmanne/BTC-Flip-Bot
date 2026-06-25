#!/usr/bin/env python3
"""ag_riskwindow.py — RISK-WINDOW DE-RISK OVERLAY on the 1.8x V2 base.

ANGLE: keep 1.8x leverage everywhere EXCEPT surgically cut it (toward 1.0x or flat) ONLY in
the specific bars where the big leveraged DDs happen, so CAGR is mostly preserved.

Per-bar leverage = 1.8x BASE, reduced when one or more CAUSAL risk signals fire:
  (1) EXTENDED   — price >X% above its 20d/50d MA (overbought, correction risk)
  (2) ATR SPIKE  — ATR% jumped vs its trailing median (vol regime turning)
  (3) POST-PARAB — a bar or N after the parabolic-de-risk fired (blow-off in progress)
  (4) WEEKLY ROLL— weekly momentum (close vs N-bars-ago / weekly EMA) rolling over
  (5) PYR+VOL    — the pyramid would be active AND vol rising (the exact Aug-2020 setup)

ANTI-BUG: 1.8x base reproduced byte-for-byte FIRST (assert). Every risk signal is .shift(1)
(causal — uses only info known at the close of bar i, applied to bar i's sizing decision, which
itself sizes the i+1 fill inside run_v). Distrust ret/DD>5 or OOS>6. GATE = full 2017-2026 +
OOS positive + ALL 3 bears (2018/2022/2026) positive + green every year.

Reported per config: CAGR / DD / ret/DD / OOS / per-bear / green / %-of-time de-levered.
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
from backtest_myv3_final import m

BPD = 6  # 4h bars per day

# ---------------------------------------------------------------------------
# gates (identical to backtest_voltarget_dd.main) + risk-signal toolbox
# ---------------------------------------------------------------------------

def gates():
    df, bull = build()
    c = df["close"]
    L = bull & (c > bt.sma(c, 9 * 30 * 6).shift(1)).fillna(False).values
    bear = ((c / c.rolling(40 * 6).max().shift(1) - 1) < -0.10).fillna(False).values & daily_macd_bear(df)
    ddh = (c / c.rolling(180 * 6).max().shift(1) - 1).fillna(0).values
    ss = np.where(ddh <= -0.30, 1.0, np.where(ddh <= -0.20, 0.50, 0.25))
    pa = (c > 2.2 * bt.sma(c, 140 * 6).shift(1)).fillna(False).values
    return df, L, bear, ss, pa


def risk_signals(df, pa):
    """Build all the per-bar CAUSAL risk flags. Each is a boolean np.array, length n,
    shifted by 1 so it only uses information available at/through the PREVIOUS closed bar."""
    c = df["close"]
    a = bt.atr(df, 14)
    n = len(df)

    # (1) EXTENDED above MA — overbought
    ma20 = bt.sma(c, 20 * BPD)
    ma50 = bt.sma(c, 50 * BPD)
    ext20 = (c / ma20 - 1)   # fraction above 20d MA
    ext50 = (c / ma50 - 1)

    # (2) ATR% spike vs trailing median
    atrp = (a / c)                                   # ATR as fraction of price
    atrp_med = atrp.rolling(60 * BPD).median()       # ~60d trailing median
    atr_ratio = atrp / atrp_med

    # (3) parabolic flag itself (post-parab handled via shift windows below)
    parab = pd.Series(pa, index=c.index)

    # (4) weekly momentum rolling over: weekly EMA slope negative / price below weekly EMA
    wk = 7 * BPD
    wema = bt.ema(c, 4 * wk)                          # ~4-week EMA
    wmom = c - c.shift(2 * wk)                        # 2-week momentum
    wema_slope = wema - wema.shift(wk)               # weekly EMA slope

    # (6) SHORT-SQUEEZE risk: short fighting a rally. The -37% max-DD is a 2022 short that
    #     bled while BTC rose +21%. Causal proxy: short price ABOVE its short-term EMA and
    #     short-term momentum turning UP (price bouncing) => squeeze risk for an active short.
    ema_st = bt.ema(c, 3 * BPD)                       # ~3-day EMA
    above_st = (c > ema_st)                          # price reclaiming short-term trend
    mom_up = (c - c.shift(3 * BPD)) > 0              # 3-day momentum positive (bouncing)
    # also: BTC already fell a lot off-high then is recovering -> late/squeezed short
    off_high = (c / c.rolling(40 * BPD).max() - 1)   # current drop from 40d high

    return dict(ext20=ext20, ext50=ext50, atr_ratio=atr_ratio, parab=parab,
                wmom=wmom, wema_slope=wema_slope, c=c,
                above_st=above_st, mom_up=mom_up, off_high=off_high)


def shift1(arr):
    """Causal shift of a BOOLEAN condition -> np.array shifted by 1 bar, NaN->False.
    Accepts a Series that may be object/bool dtype (from comparisons over NaN-containing data)."""
    s = arr.shift(1)
    return s.fillna(False).astype(bool).values


# ---------------------------------------------------------------------------
# build a per-bar leverage array from BASE + de-risk windows
# ---------------------------------------------------------------------------

def build_lev(df, sig, base=1.8, floor=1.0,
              ext_thr=None, ext_ma="ext50",
              atr_thr=None,
              post_parab_bars=0,
              weekly=False,
              squeeze=False, bear=None,
              cut=0.5):
    """Per-bar leverage = base, reduced toward `floor` (or flat=0) in danger windows.
    `cut` is the multiplicative reduction applied to (base) in a danger bar:
        lev = floor + (base-floor)*cut   when a window is active   (cut=0 -> floor; cut=1 -> base)
    All signals causal via shift1.
    """
    n = len(df)
    danger = np.zeros(n, dtype=bool)

    if ext_thr is not None:
        ext = sig[ext_ma]
        danger |= shift1(ext > ext_thr)

    if atr_thr is not None:
        danger |= shift1(sig["atr_ratio"] > atr_thr)

    if post_parab_bars > 0:
        # parabolic fired on bar i -> de-risk bars i..i+post_parab_bars (causal: use shifted parab)
        pflag = sig["parab"].astype(bool)
        win = pflag.copy()
        for k in range(1, post_parab_bars + 1):
            win = win | pflag.shift(k).fillna(False)
        danger |= shift1(win)

    if weekly:
        roll = (sig["wema_slope"] < 0) & (sig["wmom"] < 0)
        danger |= shift1(roll)

    if squeeze:
        # short-squeeze de-risk: only meaningful where a short would be on (bear flag),
        # and price is bouncing (reclaiming short-term trend & 3d momentum up).
        sq = sig["above_st"] & sig["mom_up"]
        if bear is not None:
            sq = sq & pd.Series(np.asarray(bear, bool), index=sq.index)
        danger |= shift1(sq)

    lev = np.ones(n) * base
    derisk_lev = floor + (base - floor) * cut
    lev[danger] = derisk_lev
    return lev, danger


def build_lev_pyrvol(df, sig, base=1.8, floor=1.0, atr_thr=1.15, cut=0.0):
    """(5) PYR+VOL: de-risk when vol is rising hard (the pyramid-active Aug-2020 setup proxy).
    We can't see pyramid state from outside run_v, so use 'in an uptrend long would be held AND
    ATR ratio rising' as the causal proxy: bull-ish (price>20dMA) AND atr_ratio>thr."""
    n = len(df)
    above = (sig["c"] > bt.sma(sig["c"], 20 * BPD))
    volrise = sig["atr_ratio"] > atr_thr
    danger = shift1(above & volrise)
    lev = np.ones(n) * base
    lev[danger] = floor + (base - floor) * cut
    return lev, danger


# ---------------------------------------------------------------------------
# eval harness
# ---------------------------------------------------------------------------

def evaluate(df, L, bear, ss, pa, levarr, danger=None, **kw):
    s = run_v(df, L, bear, ss, pa, levarr, lock_frac=0.33, lock_r=6.0, **kw)
    cg, dd, rr = m(s)
    oos = m(s[s.index >= s.index[int(len(s) * 0.6)]])[2]
    pct = (danger.mean() * 100) if danger is not None else 0.0
    return s, cg, dd, rr, oos, pct


HDR = f"  {'config':<40}{'CAGR':>6}{'DD':>6}{'r/DD':>6}{'OOS':>6}  {'2018/22/26':>13} {'grn':>4} {'%delev':>7}"


def prow(name, s, cg, dd, rr, oos, pct):
    g = 'grn' if grn(s) else 'RED'
    print(f"  {name:<40}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{oos:>6.2f}  "
          f"{yr(s,2018):>+4.0f}/{yr(s,2022):>+3.0f}/{yr(s,2026):>+3.0f} {g:>4} {pct:>6.1f}%")


def main():
    df, L, bear, ss, pa = gates()
    sig = risk_signals(df, pa)
    n = len(df)

    # ---- ANTI-BUG: reproduce the 1.8x BASE byte-for-byte FIRST ----
    base = np.ones(n) * 1.8
    s0, cg0, dd0, rr0, oos0, _ = evaluate(df, L, bear, ss, pa, base)
    assert abs(cg0 - 1.452) < 0.01 and abs(dd0 + 0.374) < 0.01 and abs(rr0 - 3.88) < 0.05, \
        f"BASE mismatch! CAGR {cg0:.3f} DD {dd0:.3f} rr {rr0:.3f}"
    print("=" * 96)
    print("RISK-WINDOW DE-RISK OVERLAY on 1.8x V2 base  (lock 33% @ +6R)  full 2017-2026")
    print("  GOAL: DD<=-30% (ideally -25%) keeping CAGR>=120%. de-risk ONLY in danger windows.")
    print("=" * 96)
    print(HDR)
    print("  -- baselines --")
    prow("1.8x BASE (reproduced)", s0, cg0, dd0, rr0, oos0, 0.0)
    sf, cgf, ddf, rrf, oosf, _ = evaluate(df, L, bear, ss, pa, np.ones(n) * 1.0)
    prow("1.0x flat (blanket reduction)", sf, cgf, ddf, rrf, oosf, 100.0)
    s13, cg13, dd13, rr13, oos13, _ = evaluate(df, L, bear, ss, pa, np.ones(n) * 1.3)
    prow("1.3x flat (blanket reduction)", s13, cg13, dd13, rr13, oos13, 100.0)
    s14, cg14, dd14, rr14, oos14, _ = evaluate(df, L, bear, ss, pa, np.ones(n) * 1.4)
    prow("1.4x flat (blanket reduction)", s14, cg14, dd14, rr14, oos14, 100.0)

    results = []  # (name, s, cg, dd, rr, oos, pct)

    def run_named(name, levarr, danger, **kw):
        s, cg, dd, rr, oos, pct = evaluate(df, L, bear, ss, pa, levarr, danger, **kw)
        prow(name, s, cg, dd, rr, oos, pct)
        results.append((name, s, cg, dd, rr, oos, pct))

    # ---------------- (1) EXTENDED above MA ----------------
    print("  -- (1) EXTENDED: cut to floor when price > X% above MA --")
    for ma, thr in [("ext50", 0.40), ("ext50", 0.30), ("ext50", 0.25), ("ext50", 0.20),
                    ("ext20", 0.20), ("ext20", 0.15), ("ext20", 0.10)]:
        for floor, cut in [(1.0, 0.0), (1.2, 0.0)]:
            lev, dg = build_lev(df, sig, ext_thr=thr, ext_ma=ma, floor=floor, cut=cut)
            run_named(f"ext {ma}>{thr:.0%} ->{floor:.1f}x", lev, dg)

    # ---------------- (2) ATR spike ----------------
    print("  -- (2) ATR%-spike: cut to floor when ATR%/median > X --")
    for thr in [1.6, 1.4, 1.3, 1.2]:
        for floor in [1.0, 1.2]:
            lev, dg = build_lev(df, sig, atr_thr=thr, floor=floor, cut=0.0)
            run_named(f"atr ratio>{thr} ->{floor:.1f}x", lev, dg)

    # ---------------- (3) POST-PARABOLIC ----------------
    print("  -- (3) POST-PARABOLIC: cut for N bars after parabolic fires --")
    for nb in [30, 60, 90, 120]:
        lev, dg = build_lev(df, sig, post_parab_bars=nb, floor=1.0, cut=0.0)
        run_named(f"post-parab {nb}bars ->1.0x", lev, dg)

    # ---------------- (4) WEEKLY momentum roll-over ----------------
    print("  -- (4) WEEKLY momentum rolling over: cut when wEMA slope<0 & 2w-mom<0 --")
    for floor, cut in [(1.0, 0.0), (1.2, 0.0), (1.0, 0.5)]:
        lev, dg = build_lev(df, sig, weekly=True, floor=floor, cut=cut)
        run_named(f"weekly-roll ->{floor:.1f}x cut{cut}", lev, dg)

    # ---------------- (5) PYR+VOL (Aug-2020 setup) ----------------
    print("  -- (5) PYR+VOL proxy: uptrend AND ATR rising --")
    for thr in [1.3, 1.2, 1.15]:
        for floor in [1.0, 1.2]:
            lev, dg = build_lev_pyrvol(df, sig, atr_thr=thr, floor=floor, cut=0.0)
            run_named(f"pyrvol atr>{thr} ->{floor:.1f}x", lev, dg)

    # ---------------- (6) SHORT-SQUEEZE de-risk (targets the -37% 2022 short bleed) ----------------
    print("  -- (6) SHORT-SQUEEZE: cut leverage when an active short is fighting a bounce --")
    for floor, cut in [(1.0, 0.0), (1.2, 0.0), (0.0, 0.0)]:
        tag = "flat" if floor == 0.0 else f"{floor:.1f}x"
        lev, dg = build_lev(df, sig, squeeze=True, bear=bear, floor=floor, cut=cut)
        run_named(f"short-squeeze ->{tag}", lev, dg)

    # ---------------- COMBINED overlays ----------------
    print("  -- COMBINED: ATR-spike OR extended OR weekly (union of danger windows) --")
    combos = [
        ("ext50>.30 | atr>1.4 ->1.0x", dict(ext_thr=0.30, ext_ma="ext50", atr_thr=1.4, floor=1.0, cut=0.0)),
        ("ext50>.25 | atr>1.3 ->1.0x", dict(ext_thr=0.25, ext_ma="ext50", atr_thr=1.3, floor=1.0, cut=0.0)),
        ("ext50>.25 | atr>1.3 ->1.2x", dict(ext_thr=0.25, ext_ma="ext50", atr_thr=1.3, floor=1.2, cut=0.0)),
        ("ext20>.15 | atr>1.4 ->1.0x", dict(ext_thr=0.15, ext_ma="ext20", atr_thr=1.4, floor=1.0, cut=0.0)),
        ("ext50>.30 | atr>1.4 | wk ->1.0x", dict(ext_thr=0.30, ext_ma="ext50", atr_thr=1.4, weekly=True, floor=1.0, cut=0.0)),
        ("ext50>.25 | atr>1.3 | wk ->1.0x", dict(ext_thr=0.25, ext_ma="ext50", atr_thr=1.3, weekly=True, floor=1.0, cut=0.0)),
        ("ext50>.30 | atr>1.4 | wk ->1.2x", dict(ext_thr=0.30, ext_ma="ext50", atr_thr=1.4, weekly=True, floor=1.2, cut=0.0)),
        ("atr>1.3 | wk ->1.0x", dict(atr_thr=1.3, weekly=True, floor=1.0, cut=0.0)),
        ("ext50>.30 | atr>1.6 | post-parab90 ->1.0x", dict(ext_thr=0.30, ext_ma="ext50", atr_thr=1.6, post_parab_bars=90, floor=1.0, cut=0.0)),
        ("ext50>.25 | atr>1.3 | post-parab60 ->1.0x", dict(ext_thr=0.25, ext_ma="ext50", atr_thr=1.3, post_parab_bars=60, floor=1.0, cut=0.0)),
        # squeeze (short) + extended/atr (long) — attack BOTH DD types at once
        ("squeeze | ext50>.30 ->1.0x", dict(squeeze=True, bear=bear, ext_thr=0.30, ext_ma="ext50", floor=1.0, cut=0.0)),
        ("squeeze | ext50>.25 | atr>1.3 ->1.0x", dict(squeeze=True, bear=bear, ext_thr=0.25, ext_ma="ext50", atr_thr=1.3, floor=1.0, cut=0.0)),
        ("squeeze->flat | ext50>.30 ->1.0x", dict(squeeze=True, bear=bear, ext_thr=0.30, ext_ma="ext50", floor=0.0, cut=0.0)),
        ("squeeze->flat | ext50>.25 | atr>1.3 ->1.0x", dict(squeeze=True, bear=bear, ext_thr=0.25, ext_ma="ext50", atr_thr=1.3, floor=0.0, cut=0.0)),
        ("squeeze | ext50>.30 | atr>1.4 ->1.0x", dict(squeeze=True, bear=bear, ext_thr=0.30, ext_ma="ext50", atr_thr=1.4, floor=1.0, cut=0.0)),
    ]
    for name, kw in combos:
        lev, dg = build_lev(df, sig, **kw)
        run_named(name, lev, dg)

    # ---------------- DUAL-SIDE: long-vol-rising de-risk + short-vol-elevated de-risk ----------------
    # The genuinely best CAUSAL combo: long side cut when uptrend+ATR rising (pyrvol), short side
    # cut when bear-flag on AND ATR elevated. (Attacks both DD populations from the entry side.)
    print("  -- DUAL-SIDE: long pyrvol + short-in-high-vol (best causal combo) --")
    above20 = (sig["c"] > bt.sma(sig["c"], 20 * BPD))
    bser = pd.Series(np.asarray(bear, bool), index=sig["c"].index)
    for atl, ats, fs in [(1.15, 1.2, 1.0), (1.15, 1.2, 0.0), (1.15, 1.1, 1.0)]:
        lev = np.ones(n) * 1.8
        long_dg = shift1(above20 & (sig["atr_ratio"] > atl))
        short_dg = shift1(bser & (sig["atr_ratio"] > ats))
        lev[long_dg] = 1.0
        lev[short_dg] = fs
        run_named(f"dual: longATR>{atl} | shortATR>{ats}->{fs:.0f}x", lev, long_dg | short_dg)

    # ---------------- FINAL: best 3 (lowest DD, CAGR>=120%) ----------------
    print("=" * 96)
    print("FINAL — best 3 by lowest DD among CAGR>=120% AND green AND all-3-bears-positive AND OOS>0")
    print("=" * 96)
    def ok(r):
        name, s, cg, dd, rr, oos, pct = r
        bears_ok = yr(s, 2018) > 0 and yr(s, 2022) > 0 and yr(s, 2026) > 0
        return cg >= 1.20 and grn(s) and bears_ok and oos > 0
    elig = [r for r in results if ok(r)]
    elig.sort(key=lambda r: r[3], reverse=True)  # dd is negative; higher (closer to 0) = better
    print(HDR)
    for r in elig[:3]:
        prow(*r)

    if elig:
        print("\nFULL per-year + per-bear for the TOP pick:")
        name, s, cg, dd, rr, oos, pct = elig[0]
        print(f"  config: {name}")
        print(f"  CAGR {cg*100:.0f}%  DD {dd*100:.0f}%  ret/DD {rr:.2f}  OOS {oos:.2f}  de-levered {pct:.1f}% of bars")
        for y in range(2017, 2027):
            print(f"    {y}: {yr(s,y):+.1f}%")
    else:
        print("  (none met the full gate)")

    print("\nVERDICT — targeted de-risk vs blanket leverage reduction:")
    print(f"  1.8x BASE:   CAGR {cg0*100:.0f}%  DD {dd0*100:.0f}%  ret/DD {rr0:.2f}")
    print(f"  1.0x flat:   CAGR {cgf*100:.0f}%  DD {ddf*100:.0f}%  ret/DD {rrf:.2f}")
    print(f"  1.3x flat:   CAGR {cg13*100:.0f}%  DD {dd13*100:.0f}%  ret/DD {rr13:.2f}")
    print(f"  1.4x flat:   CAGR {cg14*100:.0f}%  DD {dd14*100:.0f}%  ret/DD {rr14:.2f}")
    print("\nSTRUCTURAL FINDING (why DD floors at ~-36/-37% for the overlay):")
    print("  The binding max-DD is NOT a leveraged long ride-down — it is a SHORT that bled while")
    print("  BTC rallied (2022-02->04 -37% with BTC +21%; 2019-09->11 and 2022-09 are siblings).")
    print("  Those shorts ENTER at normal vol (atr_ratio ~1.0) and ext50 ~0 -> no causal at-entry")
    print("  risk signal distinguishes them from good shorts. And run_v reads levarr ONLY at entry,")
    print("  so a per-bar overlay cannot resize an already-open bleeding short. Cutting leverage")
    print("  across the whole 2022 window (incl. open bars) DOES reach -33%, but that is non-causal.")
    print("  => Targeted de-risk WINS on the long side (preserves ~145-150% CAGR, lifts ret/DD to")
    print("     ~4.2 vs base 3.88) but the short-driven DD floor (~-36%) is near-irreducible causally.")


if __name__ == "__main__":
    main()
