#!/usr/bin/env python3
"""backtest_myv3_pyr.py — MULTI-LAYER pyramiding extension of my-V3.

Generalizes backtest_myv3.run()'s SINGLE pyramid add into N evenly/arbitrarily spaced adds,
with per-add size schedules, a gross-leverage cap, a stop that ratchets as the stack grows,
and an optional ATR-chandelier trail after the final add.

HONEST cash/units engine (identical fills/fees to backtest_myv3):
  - base: 1x notional long at next-open; structural stop = entry - atr_mult*ATR(14), capped sl_cap%.
  - each pyramid add BORROWS: cash -= addn*(1+FEE); units += addn/price.   (cash can go negative)
  - stops only RATCHET UP (sl = max(sl, new)).  intrabar stop fill at sl*(1-SLIP) w/ fee.
  - regime flip -> exit at next open.   mark = cash + units*price.   no lookahead (signal i, act i+1).
  - leverage cap: an add is SKIPPED if it would push gross exposure (units_after*price) / equity > cap.
  - after the LAST scheduled add, optional chandelier trail: sl = max(sl, highest_high_since_entry - tr_mult*ATR).

Peak leverage (max units*price / equity over the run) is tracked and reported so true risk is visible.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
import backtest_myv3 as v3

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT
regime = v3.regime
m = v3.m


def run(df, bull, adds=None, be_r=1.0, atr_mult=3.0, sl_cap=0.08,
        lock="prev_add", lev_cap=None, trail_mult=None):
    """Multi-layer pyramiding honest engine.

    adds: list of (r_level, frac) — at +r_level*R (vs ORIGINAL entry/R), add `frac` of the
          ORIGINAL notional (E_entry). Levels processed in order, each fires ONCE.
          adds=None or [] -> no pyramiding (tight-SL base).
    lock: stop-ratchet rule applied after each add:
          "prev_add"  -> sl = max(sl, fill price of the add just placed)  (locks the level you just crossed)
          "blended_be"-> sl = max(sl, blended break-even = total_cost_basis / total_units)
          "be"        -> sl = max(sl, original entry)
    lev_cap: max gross leverage (units*price/equity). If an add would exceed it, the add is skipped.
    trail_mult: after the LAST add fires, switch remaining stop to chandelier (hh - trail_mult*ATR), ratchet-up.
    """
    adds = adds or []
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    a = bt.atr(df, 14).values; n = len(df)
    cash = 1.0; units = 0.0; in_pos = False
    entry = 0.0; sl = 0.0; R = 0.0; E_entry = 1.0
    n_done = 0                 # how many scheduled adds have fired
    cost_basis = 0.0           # total notional paid for current units (for blended BE)
    hh = 0.0                   # highest high since entry (for chandelier)
    armed = True
    eq = np.ones(n)
    peak_lev = 0.0
    n_trades = 0
    for i in range(16, n - 1):
        oN, lN, cN = o[i + 1], l[i + 1], c[i + 1]
        if not bull[i]:
            armed = True
        if in_pos:
            hh = max(hh, h[i])                                  # track on closed bars (no lookahead)
            if lN <= sl:                                        # stop hit intrabar
                fpx = sl * (1 - SLIP); cash += units * fpx * (1 - FEE)
                units = 0.0; in_pos = False; n_done = 0
            elif not bull[i]:                                   # regime flip -> exit next open
                fpx = oN * (1 - SLIP); cash += units * fpx * (1 - FEE)
                units = 0.0; in_pos = False; n_done = 0
            else:
                prof = (cN - entry) / R if R > 0 else 0.0
                if prof >= be_r:
                    sl = max(sl, entry)                         # break-even on original
                # fire the next scheduled add if its level is reached
                if n_done < len(adds):
                    r_lvl, frac = adds[n_done]
                    if prof >= r_lvl:
                        addn = frac * E_entry
                        # leverage-cap check on the post-add gross exposure
                        eq_now = cash + units * cN
                        gross_after = (units + addn / cN) * cN
                        if lev_cap is None or (eq_now > 0 and gross_after / eq_now <= lev_cap + 1e-9):
                            addpx = cN                          # add executes at this bar's close-basis price
                            cash -= addn * (1 + FEE)
                            units += addn / addpx
                            cost_basis += addn
                            # ratchet the stop per the chosen lock rule
                            if lock == "prev_add":
                                sl = max(sl, addpx)
                            elif lock == "blended_be":
                                sl = max(sl, cost_basis / units if units > 0 else sl)
                            else:  # "be"
                                sl = max(sl, entry)
                        n_done += 1                             # mark fired even if skipped by cap (don't retry forever)
                # after the LAST add, switch to chandelier trail
                if trail_mult is not None and n_done >= len(adds) and len(adds) > 0:
                    sl = max(sl, hh - trail_mult * a[i])
            if in_pos:
                lev = units * cN / (cash + units * cN) if (cash + units * cN) > 0 else 0.0
                peak_lev = max(peak_lev, lev)
        if not in_pos and bull[i] and armed:
            stop = max(c[i] - atr_mult * a[i], c[i] * (1 - sl_cap))
            if c[i] - stop > 0:
                E = cash; entry = oN * (1 + SLIP); R = entry - stop; sl = stop
                units = E / entry; cash -= E * (1 + FEE)
                E_entry = E; cost_basis = E; in_pos = True
                n_done = 0; armed = False; hh = h[i]; n_trades += 1
        eq[i + 1] = cash + units * cN
    s = pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]
    return s, peak_lev, n_trades


def evaluate(df, bull, **kw):
    s, lev, nt = run(df, bull, **kw)
    cg, dd, rr = m(s)
    cut = s.index[int(len(s) * 0.6)]
    oc, od, orr = m(s[s.index >= cut])
    return dict(cagr=cg, dd=dd, rdd=rr, oos_rdd=orr, lev=lev, nt=nt, s=s)


def row(name, r):
    print(f"  {name:<46}{r['cagr']*100:>6.0f}%{r['dd']*100:>6.0f}%{r['rdd']:>7.2f}{r['oos_rdd']:>9.2f}"
          f"{r['nt']:>6}{r['lev']:>8.2f}x")


def yearly(s):
    out = {}
    for y, g in s.groupby(s.index.year):
        out[y] = ((g.iloc[-1] / g.iloc[0] - 1) * 100, (g / g.cummax() - 1).min() * 100)
    return out


def main():
    df, bull = regime()
    base = evaluate(df, bull, adds=[(2.0, 1.0)], lock="be")   # exact backtest_myv3 base
    print("=" * 100)
    print("MULTI-LAYER PYRAMIDING — my-V3 (BTC 4h, MTF entry, structural SL). BASE = single add @2R frac1.0")
    print("=" * 100)
    print(f"  {'config':<46}{'CAGR':>7}{'DD':>6}{'r/DD':>7}{'OOS rDD':>9}{'#tr':>6}{'pkLev':>9}")
    print("-" * 100)
    row("BASE single add 2R frac1.0 (be lock)", base)
    print("-" * 100)

    cfgs1 = [
        ("N1 {2R}",            [(2.0, 1.0)]),
        ("N2 {2R,4R}",         [(2.0, 1.0), (4.0, 1.0)]),
        ("N3 {2R,4R,6R}",      [(2.0, 1.0), (4.0, 1.0), (6.0, 1.0)]),
        ("N4 {1.5,3,4.5,6R}",  [(1.5, 1.0), (3.0, 1.0), (4.5, 1.0), (6.0, 1.0)]),
    ]

    # ---- 1) lock comparison: prev_add (locks stop to current price) vs blended_be ----
    print("  [1a] N adds frac1.0, lock=prev_add  (ratchet stop to the add's price)")
    for nm, ad in cfgs1:
        row(nm, evaluate(df, bull, adds=ad, lock="prev_add"))
    print("       -> prev_add is DESTRUCTIVE: stop jumps to +Nx price, any retrace nukes the stack.")
    print("  [1b] N adds frac1.0, lock=blended_be  (ratchet stop to blended cost basis) -- the WORKING lock")
    for nm, ad in cfgs1:
        row(nm, evaluate(df, bull, adds=ad, lock="blended_be"))
    print("-" * 100)

    # ---- 2) add-size schedules on N3 {2R,4R,6R}, blended_be ----
    print("  [2] size schedules on N3 {2R,4R,6R}, lock=blended_be")
    sched = [
        ("equal 0.5/0.5/0.5",   [0.5, 0.5, 0.5]),
        ("equal 1.0/1.0/1.0",   [1.0, 1.0, 1.0]),
        ("decreasing 1/.5/.25", [1.0, 0.5, 0.25]),
        ("increasing .25/.5/1", [0.25, 0.5, 1.0]),
    ]
    lvls = [2.0, 4.0, 6.0]
    for nm, fr in sched:
        row(nm, evaluate(df, bull, adds=list(zip(lvls, fr)), lock="blended_be"))
    print("-" * 100)

    # ---- 3) leverage cap (blended_be N2/N3) ----
    print("  [3] leverage cap, lock=blended_be")
    ad2 = [(2.0, 1.0), (4.0, 1.0)]; ad3 = [(2.0, 1.0), (4.0, 1.0), (6.0, 1.0)]
    for cap in [None, 3.0, 2.5, 2.0]:
        row(f"N2 {{2,4}} cap={cap}", evaluate(df, bull, adds=ad2, lock="blended_be", lev_cap=cap))
    for cap in [None, 3.0, 2.5, 2.0]:
        row(f"N3 {{2,4,6}} cap={cap}", evaluate(df, bull, adds=ad3, lock="blended_be", lev_cap=cap))
    print("-" * 100)

    # ---- 4) chandelier trail after last add (blended_be) ----
    print("  [4] chandelier trail (hh - k*ATR) AFTER last add, lock=blended_be")
    for k in [5.0, 4.0, 3.0, 2.0]:
        row(f"N2 {{2,4}} trail k={k}", evaluate(df, bull, adds=ad2, lock="blended_be", trail_mult=k))
    for k in [5.0, 4.0, 3.0, 2.0]:
        row(f"N3 {{2,4,6}} trail k={k}", evaluate(df, bull, adds=ad3, lock="blended_be", trail_mult=k))
    print("       -> chandelier HURTS here: it tightens the stop and clips the trend ride.")
    print("=" * 100)

    # ---- winner: year-by-year ----
    print("\nYEAR-BY-YEAR (ret%, maxDD%)  base vs best risk-adjusted (N2 {2,4} blended_be)")
    win = run(df, bull, adds=ad2, lock="blended_be")[0]
    bse = run(df, bull, adds=[(2.0, 1.0)], lock="be")[0]
    print(f"  {'year':<6}{'BASE ret/dd':>22}{'N2{2,4} blended ret/dd':>26}")
    yb, yw = yearly(bse), yearly(win)
    for y in sorted(set(yb) | set(yw)):
        b = yb.get(y, (0, 0)); w = yw.get(y, (0, 0))
        print(f"  {y:<6}{b[0]:>+12.0f}% dd{b[1]:>5.0f}%{w[0]:>+15.0f}% dd{w[1]:>5.0f}%")


if __name__ == "__main__":
    main()
