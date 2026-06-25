#!/usr/bin/env python3
"""backtest_myv3_stop.py — STOP-MANAGEMENT variations on my-V3 (pyramid ON).

Base run() copied from backtest_myv3.py and extended with a `stop_mode` family:
  - 'be'        : plain break-even at +be_r*R (== base when be_buf=0).
  - 'be_buf'    : at +be_r*R move stop to entry*(1+be_buf). sweep be_buf.
  - 'ratchet'   : every +1R of NEW profit (above last lock), raise stop by 1R (lock R-by-R).
  - 'swinglow'  : trail stop up to lowest-low of last N CONFIRMED bars (bars <= i, fill i+1).
  - 'timestop'  : exit if in pos > T bars without making a new intrabar high.
  - 'tiered'    : structural -> BE at 1R -> +1R lock at 3R -> swing-low trail thereafter.

ANTI-BUG INVARIANTS (enforced):
  * Stop only ratchets UP for a long: sl=max(sl, new) ALWAYS, never lowered.
  * Stop fills intrabar at the stop price with slippage: fpx = sl*(1-SLIP).
  * No lookahead: swing low / new-high tracking uses bars up through i, fill at i+1.
  * mark = cash + units*price (honest borrowed-pyramid engine identical to base).
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


def run(df, bull, pyr_r=2.0, pyr_frac=1.0, be_r=1.0, atr_mult=3.0, sl_cap=0.08,
        stop_mode='be', be_buf=0.0, swing_n=10, time_t=0):
    """Honest cash/units engine with selectable stop-management. Returns (equity Series, n_trades).

    stop_mode controls how the stop is RAISED while in a position. All modes keep the structural
    initial stop and the pyramid-add (which still locks to BE on add). Stops never move down.
    """
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    a = bt.atr(df, 14).values; n = len(df)
    cash = 1.0; units = 0.0; in_pos = False; entry = 0.0; sl = 0.0; R = 0.0
    pyrd = False; armed = True; E_entry = 1.0
    lock_steps = 0          # how many +1R locks taken (ratchet/tiered)
    hi_since = 0.0          # highest intrabar high since entry (time stop / new-high)
    bars_since_hi = 0       # bars since last new high
    eq = np.ones(n); ntr = 0

    for i in range(16, n - 1):
        oN, hN, lN, cN = o[i + 1], h[i + 1], l[i + 1], c[i + 1]
        if not bull[i]:
            armed = True
        if in_pos:
            # --- decide stop raise FIRST (using info up to bar i, applied for the i+1 bar) ---
            prof = (c[i] - entry) / R if R > 0 else 0   # profit in R on closed bar i (no lookahead)

            if stop_mode in ('be', 'be_buf', 'tiered'):
                if prof >= be_r:
                    sl = max(sl, entry * (1 + (be_buf if stop_mode == 'be_buf' else 0.0)))

            if stop_mode in ('ratchet', 'tiered'):
                # tiered: only start locking at >=3R (lock the 1R, 2R... below current). ratchet: from 1R.
                start_r = 3 if stop_mode == 'tiered' else 1
                if prof >= start_r:
                    want = int(np.floor(prof))            # how many full R locked-in achievable
                    # lock stop at entry + (want-1)*R  (trail 1R behind current R-multiple)
                    target_steps = want - 1
                    if target_steps > lock_steps:
                        lock_steps = target_steps
                    if lock_steps >= 1:
                        sl = max(sl, entry + lock_steps * R)

            if stop_mode in ('swinglow',) or (stop_mode == 'tiered' and prof >= 3):
                # lowest low of last swing_n CONFIRMED bars (i-swing_n+1 .. i). fill applies at i+1.
                lo = l[max(16, i - swing_n + 1): i + 1]
                if len(lo):
                    swing = lo.min()
                    if swing < c[i]:                      # only trail if below price (a real stop)
                        sl = max(sl, swing)

            # --- time stop bookkeeping on closed bar i ---
            if h[i] > hi_since:
                hi_since = h[i]; bars_since_hi = 0
            else:
                bars_since_hi += 1

            # --- exits, evaluated on bar i+1 ---
            if lN <= sl:                                   # stop hit intrabar (stop-first)
                fpx = sl * (1 - SLIP); cash += units * fpx * (1 - FEE)
                units = 0.0; in_pos = False; pyrd = False; ntr += 1
            elif stop_mode == 'timestop' and time_t > 0 and bars_since_hi >= time_t:
                fpx = oN * (1 - SLIP); cash += units * fpx * (1 - FEE)
                units = 0.0; in_pos = False; pyrd = False; ntr += 1
            elif not bull[i]:                              # regime flip -> exit next open
                fpx = oN * (1 - SLIP); cash += units * fpx * (1 - FEE)
                units = 0.0; in_pos = False; pyrd = False; ntr += 1
            else:
                if pyr_r is not None and not pyrd and prof >= pyr_r:
                    addn = pyr_frac * E_entry               # add % of original notional (borrowed)
                    cash -= addn * (1 + FEE); units += addn / cN; pyrd = True
                    sl = max(sl, entry)                     # pyramid locks at least BE

        if not in_pos and bull[i] and armed:
            stop = max(c[i] - atr_mult * a[i], c[i] * (1 - sl_cap))
            if c[i] - stop > 0:
                E = cash; entry = oN * (1 + SLIP); R = entry - stop; sl = stop
                units = E / entry; cash -= E * (1 + FEE); E_entry = E
                in_pos = True; pyrd = False; armed = False
                lock_steps = 0; hi_since = entry; bars_since_hi = 0

        eq[i + 1] = cash + units * cN
    s = pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]
    return s, ntr


def metr(s):
    cg, dd, rr = m(s)
    cut = s.index[int(len(s) * 0.6)]
    oc, od, orr = m(s[s.index >= cut])
    return cg, dd, rr, orr


def main():
    df, bull = regime()
    # base for reference (use base engine to be exact)
    sb = v3.run(df, bull, pyr_r=2.0, pyr_frac=1.0, be_r=1.0, atr_mult=3.0, sl_cap=0.08)
    bcg, bdd, brr = m(sb)
    bcut = sb.index[int(len(sb) * 0.6)]; _, _, borr = m(sb[sb.index >= bcut])

    print("=" * 96)
    print("MY-V3 STOP-MANAGEMENT SWEEP (pyramid ON: pyr_r=2.0 frac=1.0)  BTC 4h")
    print("=" * 96)
    print(f"  {'config':<46}{'CAGR':>7}{'DD':>6}{'r/DD':>7}{'OOS':>7}{'#exit':>7}  flag")

    def row(name, **kw):
        s, ntr = run(df, bull, **kw)
        cg, dd, rr, orr = metr(s)
        flag = ''
        if rr > 5 or orr > 6:
            flag = 'SUSPECT(too-good, re-check)'
        print(f"  {name:<46}{cg*100:>6.0f}%{dd*100:>5.0f}%{rr:>7.2f}{orr:>7.2f}{ntr:>6}  {flag}")
        return name, cg, dd, rr, orr, ntr

    results = []
    # reference: my-V3 base via this engine in 'be' be_buf=0 (should match base)
    results.append(row("[base repro] be be_buf=0", stop_mode='be', be_buf=0.0))
    print(f"  {'[BASE official]':<46}{bcg*100:>6.0f}%{bdd*100:>5.0f}%{brr:>7.2f}{borr:>7.2f}{'-':>6}")
    print("  " + "-" * 90)

    # 1) BE + buffer
    for b in (0.0, 0.005, 0.01, 0.02):
        results.append(row(f"1) BE+buf {b*100:.1f}%", stop_mode='be_buf', be_buf=b))
    print("  " + "-" * 90)
    # 2) ratchet R-by-R
    results.append(row("2) ratchet (lock R-by-R from 1R)", stop_mode='ratchet'))
    print("  " + "-" * 90)
    # 3) swing-low ratchet
    for N in (5, 10, 20):
        results.append(row(f"3) swing-low trail N={N}", stop_mode='swinglow', swing_n=N))
    print("  " + "-" * 90)
    # 4) time stop
    for T in (30, 60, 120):
        results.append(row(f"4) time stop T={T} bars", stop_mode='timestop', time_t=T))
    print("  " + "-" * 90)
    # 5) tiered ladder (BE@1R -> +1R lock@3R -> swing trail), sweep swing N
    for N in (10, 20):
        results.append(row(f"5) tiered ladder (swingN={N})", stop_mode='tiered', swing_n=N))

    print("=" * 96)
    print(f"  BASE: CAGR {bcg*100:.0f}%  DD {bdd*100:.0f}%  ret/DD {brr:.2f}  OOS {borr:.2f}")

    # --- pick best by ret/DD among non-suspect, then show year-by-year ---
    cand = [r for r in results if not (r[3] > 5 or r[4] > 6)]
    best = max(cand, key=lambda r: r[3])
    print(f"\n  BEST by ret/DD: {best[0]}  ret/DD {best[3]:.2f}  OOS {best[4]:.2f}  "
          f"({'BEATS' if best[3] > brr else 'does NOT beat'} base ret/DD; "
          f"OOS {'beats' if best[4] > borr else 'below'} base)")

    # rebuild best series for year-by-year
    bestmap = {
        "1) BE+buf 0.0%": dict(stop_mode='be_buf', be_buf=0.0),
        "1) BE+buf 0.5%": dict(stop_mode='be_buf', be_buf=0.005),
        "1) BE+buf 1.0%": dict(stop_mode='be_buf', be_buf=0.01),
        "1) BE+buf 2.0%": dict(stop_mode='be_buf', be_buf=0.02),
        "2) ratchet (lock R-by-R from 1R)": dict(stop_mode='ratchet'),
        "3) swing-low trail N=5": dict(stop_mode='swinglow', swing_n=5),
        "3) swing-low trail N=10": dict(stop_mode='swinglow', swing_n=10),
        "3) swing-low trail N=20": dict(stop_mode='swinglow', swing_n=20),
        "4) time stop T=30 bars": dict(stop_mode='timestop', time_t=30),
        "4) time stop T=60 bars": dict(stop_mode='timestop', time_t=60),
        "4) time stop T=120 bars": dict(stop_mode='timestop', time_t=120),
        "5) tiered ladder (swingN=10)": dict(stop_mode='tiered', swing_n=10),
        "5) tiered ladder (swingN=20)": dict(stop_mode='tiered', swing_n=20),
        "[base repro] be be_buf=0": dict(stop_mode='be', be_buf=0.0),
    }
    sbest, _ = run(df, bull, **bestmap[best[0]])
    print(f"\n  YEAR-BY-YEAR for BEST ({best[0]}):")
    print(f"    {'year':<6}{'return':>9}{'maxDD':>9}")
    for yr, g in sbest.groupby(sbest.index.year):
        ret = g.iloc[-1] / g.iloc[0] - 1
        ddy = (g / g.cummax() - 1).min()
        print(f"    {yr:<6}{ret*100:>8.0f}%{ddy*100:>8.0f}%")


if __name__ == "__main__":
    main()
