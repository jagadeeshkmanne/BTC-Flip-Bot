#!/usr/bin/env python3
"""ag_ddreduce.py — can we cut V2's -31% DD without killing CAGR?

V2 BASE (parabolic de-risk long + bear-depth short, pyramid ON): CAGR 103%, DD -31%, ret/DD 3.34, OOS 3.24.
The -31% is RIDE-THROUGH of sharp BTC corrections (e.g. the fast -19% Aug-2020 drop while holding a
pyramided long), NOT a bad entry. So we attack it with CAUSAL vol-aware long-side modifiers:

  (1) PYR_VOL   — skip/halve the pyramid when causal ATR%>rolling-median (don't double up in vol).
  (2) ENTRY_VOL — skip NEW long entries when causal ATR% is extreme (top quantile).
  (3) STOP_FAST — tighten the long stop when ATR expands fast (ATR rising vs its own MA).
  (4) PROFIT_CAP— when open long profit is large, take partial off (lock more), independent of parabolic.
  (5) NOPYR_N   — no pyramid in the first N days of a new long trend (entry was recent/unproven).

ANTI-BUG: run2_ext() with ALL filters OFF == backtest_v2_combined.run2 byte-for-byte (asserted).
All vol/ATR signals use ONLY past data (shift(1)). Stops only ratchet (never loosen). Honest fees/slip.
GATE: full 2017-2026 + OOS + all 3 bears (2018/2022/2026) positive + every year green.
Goal: DD better than -28% while keeping ret/DD >= 3.0.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_ext import build
from backtest_short_aggressive import daily_macd_bear
from backtest_v2_combined import run2 as run2_base
from backtest_myv3_final import m

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT; BPD = 6


def run2_ext(df, bull, bear, ssize, parab_trig, use_parab=False, use_bd=False,
             short_size=0.40, atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0,
             be_buf=0.01, s_atr=5.0, s_cap=0.15,
             # ---- causal vol-aware long-side modifiers (all default OFF => == run2_base) ----
             atrp_hi=None,          # array<bool>, causal: ATR% above rolling median (high vol)
             atrp_extreme=None,     # array<bool>, causal: ATR% in extreme top quantile
             atr_fast=None,         # array<bool>, causal: ATR expanding fast (rising vs its MA)
             pyr_vol_mode=None,     # None | "skip" | "halve" : modify pyramid when atrp_hi[i]
             entry_vol_skip=False,  # skip new LONG entries when atrp_extreme[i]
             stop_fast_mult=None,   # when atr_fast[i] & long: ratchet stop to entry-mult*ATR (tighten)
             profit_cap_r=None,     # when long open-profit >= this many R: take profit_cap_frac off
             profit_cap_frac=0.5,
             nopyr_days=None,       # no pyramid in first N days of a new long trend
             ):
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    a = bt.atr(df, 14).values; n = len(df)
    cash = 1.0; units = 0.0; side = 0; entry = 0.0; stop = 0.0; R = 0.0
    pyrd = False; notional0 = 0.0; parab = False; pcapped = False; entry_i = 0
    armedL = True; armedS = True; eq = np.ones(n)
    for i in range(16, n - 1):
        oN, hN, lN, cN = o[i + 1], h[i + 1], l[i + 1], c[i + 1]
        if not bull[i]: armedL = True
        if not bear[i]: armedS = True
        if side != 0:
            hit = (lN <= stop) if side == 1 else (hN >= stop)
            regime_out = (not bull[i]) if side == 1 else (not bear[i])
            if hit:
                fpx = stop * (1 - SLIP) if side == 1 else stop * (1 + SLIP)
                cash += units * fpx - abs(units) * fpx * FEE
                units = 0.0; side = 0; pyrd = False; parab = False; pcapped = False
            elif regime_out:
                fpx = oN * (1 - SLIP) if side == 1 else oN * (1 + SLIP)
                cash += units * fpx - abs(units) * fpx * FEE
                units = 0.0; side = 0; pyrd = False; parab = False; pcapped = False
            else:
                prof = ((cN - entry) / R) if side == 1 else ((entry - cN) / R)
                if prof >= be_r:
                    be = entry * (1 + be_buf) if side == 1 else entry * (1 - be_buf)
                    stop = max(stop, be) if side == 1 else min(stop, be)
                # ---- (3) STOP_FAST: tighten long stop when ATR expands fast (ratchet only) ----
                if (side == 1 and stop_fast_mult is not None and atr_fast is not None
                        and atr_fast[i]):
                    tight = cN - stop_fast_mult * a[i]
                    stop = max(stop, tight)        # only ratchet up, never loosen
                # ---- pyramid (long only) with (1) vol mod + (5) no-pyr-first-N-days ----
                if side == 1 and not pyrd and prof >= pyr_r:
                    allow_pyr = True; frac = pyr_frac
                    if nopyr_days is not None and (i - entry_i) < nopyr_days * BPD:
                        allow_pyr = False
                    if pyr_vol_mode is not None and atrp_hi is not None and atrp_hi[i]:
                        if pyr_vol_mode == "skip":
                            allow_pyr = False
                        elif pyr_vol_mode == "halve":
                            frac = pyr_frac * 0.5
                    if allow_pyr and frac > 0:
                        addn = frac * notional0; au = addn / cN
                        cash -= au * cN + addn * FEE; units += au
                        stop = max(stop, entry)
                    pyrd = True   # consume the pyramid event regardless (matches base 'one-shot')
                # ---- (4) PROFIT_CAP: lock partial when open profit large (long only) ----
                if (use_parab is not None and side == 1 and not pcapped
                        and profit_cap_r is not None and prof >= profit_cap_r):
                    fpx = oN * (1 - SLIP); cl = profit_cap_frac * units
                    cash += cl * fpx * (1 - FEE); units -= cl; pcapped = True
                # ---- PARABOLIC DE-RISK (base feature, long only) ----
                if use_parab and side == 1 and not parab and parab_trig[i]:
                    fpx = oN * (1 - SLIP); cl = 0.5 * units
                    cash += cl * fpx * (1 - FEE); units -= cl; parab = True
        if side == 0:
            go = 0
            if bull[i] and armedL: go = 1
            elif bear[i] and armedS: go = -1
            # ---- (2) ENTRY_VOL: skip new LONG entry when ATR% extreme ----
            if go == 1 and entry_vol_skip and atrp_extreme is not None and atrp_extreme[i]:
                go = 0; armedL = False   # consume the arm so we don't re-fire same trend
            if go != 0:
                E = cash
                if go == 1:
                    st = max(c[i] - atr_mult * a[i], c[i] * (1 - sl_cap)); entry = oN * (1 + SLIP); sz = 1.0
                else:
                    st = min(c[i] + s_atr * a[i], c[i] * (1 + s_cap)); entry = oN * (1 - SLIP)
                    sz = (ssize[i] if use_bd else short_size)
                ok = (entry - st > 0) if go == 1 else (st - entry > 0)
                if ok:
                    notional0 = sz * E; units = go * notional0 / entry; cash = E - units * entry - notional0 * FEE
                    stop = st; R = abs(entry - st); side = go
                    pyrd = False; parab = False; pcapped = False; entry_i = i
                    if go == 1: armedL = False
                    else: armedS = False
        eq[i + 1] = cash + units * cN
    return pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]


def yr(s, y):
    seg = s[s.index.year == y]
    return (seg.iloc[-1] / seg.iloc[0] - 1) * 100 if len(seg) > 20 else 0.0


def per_bear(s):
    return yr(s, 2018), yr(s, 2022), yr(s, 2026)


def all_years_green(s):
    return all(yr(s, y) >= 0 for y in range(2017, 2027) if len(s[s.index.year == y]) > 20)


def oos(s):
    return m(s[s.index >= s.index[int(len(s) * 0.6)]])[2]


def main():
    df, bull = build(); c = df["close"]
    macro = (c > bt.sma(c, 9 * 30 * BPD).shift(1)).fillna(False).values
    LONG = bull & macro
    hh40 = c.rolling(40 * BPD).max().shift(1); drop = ((c / hh40 - 1) < -0.10).fillna(False).values
    bear = drop & daily_macd_bear(df)
    hh180 = c.rolling(180 * BPD).max().shift(1); ddh = (c / hh180 - 1).fillna(0).values
    ssize = np.where(ddh <= -0.30, 1.0, np.where(ddh <= -0.20, 0.50, 0.25))
    parab_trig = (c > 2.2 * bt.sma(c, 140 * BPD).shift(1)).fillna(False).values

    # ---- CAUSAL vol signals (ONLY past data: ATR is EMA of past TR; shift(1) everywhere) ----
    atr14 = bt.atr(df, 14)
    atrp = (atr14 / c)                       # ATR as % of price
    atrp_s = atrp.shift(1)                    # causal: yesterday's ATR%
    med = atrp_s.rolling(120 * BPD, min_periods=30 * BPD).median()   # ~120d rolling median
    atrp_hi = (atrp_s > med).fillna(False).values
    q80 = atrp_s.rolling(120 * BPD, min_periods=30 * BPD).quantile(0.80)
    q90 = atrp_s.rolling(120 * BPD, min_periods=30 * BPD).quantile(0.90)
    atrp_x80 = (atrp_s > q80).fillna(False).values
    atrp_x90 = (atrp_s > q90).fillna(False).values
    # ATR expanding fast: current ATR > 1.3 * its own 20-bar MA (causal shift1)
    atr_ma = atr14.rolling(20).mean()
    atr_fast = ((atr14.shift(1) > 1.3 * atr_ma.shift(1))).fillna(False).values

    print("=" * 100)
    print("ag_ddreduce — cut V2's -31% DD without killing CAGR?  (causal vol-aware long modifiers)")
    print("=" * 100)

    # ---------- ANTI-BUG: reproduce V2 base byte-for-byte ----------
    s_base_ref = run2_base(df, LONG, bear, ssize, parab_trig, use_parab=True, use_bd=True)
    s_base_ext = run2_ext(df, LONG, bear, ssize, parab_trig, use_parab=True, use_bd=True)
    maxdiff = float(np.abs(s_base_ref.values - s_base_ext.values).max())
    assert maxdiff < 1e-12, f"BASE NOT REPRODUCED byte-for-byte! maxdiff={maxdiff:.2e}"
    cg0, dd0, rr0 = m(s_base_ext)
    print(f"  [anti-bug] run2_ext(all filters OFF) == run2_base   maxdiff={maxdiff:.1e}  OK")
    b18, b22, b26 = per_bear(s_base_ext)
    print(f"  V2 BASE: CAGR {cg0*100:.0f}%  DD {dd0*100:.0f}%  ret/DD {rr0:.2f}  OOS {oos(s_base_ext):.2f}  "
          f"bears {b18:+.0f}/{b22:+.0f}/{b26:+.0f}  green={all_years_green(s_base_ext)}")
    print("-" * 100)

    common = dict(use_parab=True, use_bd=True)
    sig = dict(atrp_hi=atrp_hi, atrp_extreme=atrp_x90, atr_fast=atr_fast)

    configs = [
        ("V2 BASE (no modifier)", {}),
        # (1) pyramid vol-mod
        ("(1) PYR skip when ATR%>median", dict(pyr_vol_mode="skip", atrp_hi=atrp_hi)),
        ("(1) PYR halve when ATR%>median", dict(pyr_vol_mode="halve", atrp_hi=atrp_hi)),
        # (2) entry vol-skip
        ("(2) skip LONG entry when ATR%>q90", dict(entry_vol_skip=True, atrp_extreme=atrp_x90)),
        ("(2) skip LONG entry when ATR%>q80", dict(entry_vol_skip=True, atrp_extreme=atrp_x80)),
        # (3) stop-fast tighten
        ("(3) tighten stop 2.5*ATR when fast", dict(stop_fast_mult=2.5, atr_fast=atr_fast)),
        ("(3) tighten stop 2.0*ATR when fast", dict(stop_fast_mult=2.0, atr_fast=atr_fast)),
        ("(3) tighten stop 1.5*ATR when fast", dict(stop_fast_mult=1.5, atr_fast=atr_fast)),
        # (4) profit-cap (extra lock beyond parabolic)
        ("(4) lock 50% at +4R open profit", dict(profit_cap_r=4.0, profit_cap_frac=0.5)),
        ("(4) lock 50% at +6R open profit", dict(profit_cap_r=6.0, profit_cap_frac=0.5)),
        ("(4) lock 33% at +5R open profit", dict(profit_cap_r=5.0, profit_cap_frac=0.33)),
        # (5) no-pyr first N days
        ("(5) no pyramid first 10d of trend", dict(nopyr_days=10)),
        ("(5) no pyramid first 20d of trend", dict(nopyr_days=20)),
        ("(5) no pyramid first 30d of trend", dict(nopyr_days=30)),
        # ---- combos of the most promising single modifiers ----
        ("C: PYR-skip + stop-fast2.0", dict(pyr_vol_mode="skip", atrp_hi=atrp_hi,
                                            stop_fast_mult=2.0, atr_fast=atr_fast)),
        ("C: PYR-halve + stop-fast2.0", dict(pyr_vol_mode="halve", atrp_hi=atrp_hi,
                                             stop_fast_mult=2.0, atr_fast=atr_fast)),
        ("C: PYR-skip + lock50@5R", dict(pyr_vol_mode="skip", atrp_hi=atrp_hi,
                                         profit_cap_r=5.0, profit_cap_frac=0.5)),
        ("C: stop-fast2.0 + lock50@5R", dict(stop_fast_mult=2.0, atr_fast=atr_fast,
                                             profit_cap_r=5.0, profit_cap_frac=0.5)),
        ("C: PYR-skip + stop-fast2.0 + lock50@5R", dict(pyr_vol_mode="skip", atrp_hi=atrp_hi,
                                                        stop_fast_mult=2.0, atr_fast=atr_fast,
                                                        profit_cap_r=5.0, profit_cap_frac=0.5)),
    ]

    print(f"  {'config':<42}{'CAGR':>6}{'DD':>6}{'r/DD':>6}{'OOS':>6}  {'18/22/26':>14}  {'grn':>4}")
    rows = []
    for name, kw in configs:
        s = run2_ext(df, LONG, bear, ssize, parab_trig, **common, **kw)
        cg, dd, rr = m(s); ob = oos(s); b1, b2, b3 = per_bear(s); g = all_years_green(s)
        rows.append((name, cg, dd, rr, ob, (b1, b2, b3), g, s))
        pb = f"{b1:+.0f}/{b2:+.0f}/{b3:+.0f}"
        flag = ""
        if name != "V2 BASE (no modifier)":
            if dd > dd0 and rr >= 3.0: flag = " <-- DD better, r/DD ok"
            elif dd > dd0: flag = " (DD better, r/DD<3)"
        print(f"  {name:<42}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{ob:>6.2f}  {pb:>14}  {str(g):>4}{flag}")

    # ---------- FINAL: best 3 by (lowest DD with ret/DD>=3.0), gate-passing ----------
    print("\n" + "=" * 100)
    print("FINAL — best 3 by LOWEST DD subject to ret/DD>=3.0 AND all-3-bears>0 AND every-year-green")
    print("=" * 100)
    cand = [r for r in rows if r[0] != "V2 BASE (no modifier)"
            and r[3] >= 3.0 and all(b > 0 for b in r[5]) and r[6]]
    # rank by DD closest to 0 (least negative = smallest drawdown)
    cand.sort(key=lambda r: -r[2])
    print(f"  V2 BASE for reference: CAGR {cg0*100:.0f}%  DD {dd0*100:.0f}%  ret/DD {rr0:.2f}\n")
    if not cand:
        print("  No gate-passing config beats BASE DD with ret/DD>=3.0.")
    for k, (name, cg, dd, rr, ob, bears, g, s) in enumerate(cand[:3], 1):
        print(f"  #{k}  {name}")
        print(f"      CAGR {cg*100:.0f}%   DD {dd*100:.1f}%  (vs BASE {dd0*100:.1f}%, "
              f"{(dd-dd0)*100:+.1f}pt)   ret/DD {rr:.2f}   OOS {ob:.2f}")
        print(f"      bears 2018 {bears[0]:+.0f}%  2022 {bears[1]:+.0f}%  2026 {bears[2]:+.0f}%")
        print("      year-by-year: " + " ".join(f"{y}:{yr(s,y):+.0f}%" for y in range(2017, 2027)))
        print()

    # verdict
    print("-" * 100)
    if cand and cand[0][2] > -0.28 and cand[0][3] >= 3.0:
        best = cand[0]
        print(f"  VERDICT: DD CAN be pushed below -28% at ret/DD>=3.0  -> '{best[0]}' "
              f"DD {best[2]*100:.1f}% ret/DD {best[3]:.2f} (CAGR {best[1]*100:.0f}% vs {cg0*100:.0f}%).")
    else:
        bestdd = max((r[2] for r in cand), default=dd0)
        print(f"  VERDICT: best gate-passing DD = {bestdd*100:.1f}% (target was < -28%). "
              f"The sharp-correction ride-through DD is largely STRUCTURAL — see raw rows above.")


if __name__ == "__main__":
    main()
