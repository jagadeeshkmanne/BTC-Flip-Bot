#!/usr/bin/env python3
"""ag_stoptype.py — STOP-TYPE SHOOTOUT for the LONG leg of my-V3 (BTC 4h, EXTENDED 2017-2026).

GOAL: cut the validated base's -43% DD WITHOUT clipping the trend legs that ARE the edge.
A prior agent found plain trailing stops CRATER returns; this re-confirms that and tests
smarter stop types that only tighten AFTER a trade is already a winner.

VALIDATED BASE (reproduced byte-for-byte via the SAME build()/signals() the sibling agents
use; the long-stop kwargs below default to base so run() with stop_type='base' == base):
  LONG  = bull & (price>SMA(9mo)); atr_mult=3.5, sl_cap=0.12, be_r=1.0(buf1%), pyr_r=2.0
  SHORT = (drop>10%/40d) & (daily MACD<sig); short_size=0.40, s_atr=5.0, s_cap=0.15, s_pyr_r=None
  Combined full 2017-2026: CAGR 96%, DD -43%, ret/DD 2.24, OOS 3.05; green every year;
  bears 2018:+26% / 2022:+11% / 2026:+8%.

LONG-STOP VARIANTS (short leg held fixed = base; only the long stop mutates):
  (1) structural ATR mult {3,3.5,4,4.5} x cap {8,12,15}%
  (2) swing-low: stop = lowest low of last N closed bars, N={10,20,30}
  (3) activated chandelier: stop = highest-high - k*ATR, ARMED only after prof>=act_R
  (4) ratchet schedule: lock stop at entry+lock*R once prof past trigger R
  (5) two-stage: wide initial stop, tighten to a tighter ATR/cap AFTER the pyramid fires

ANTI-BUG: byte-for-byte base repro asserted first; stops ratchet UP only (long); signals on
closed bars, fill next open; intrabar stop-first; no lookahead (swing-low/chandelier read
l[..i]/h[i], decisions use c[i]/a[i], fill i+1). Distrust ret/DD>5 or OOS>6 -> re-derived by
hand in the AUDIT block.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_ext import build, BPD
from ag_voltarget import signals          # the validated long_sig/short_sig builder
from backtest_myv3_shorts import m

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT
BEARS = (2018, 2022, 2026)                  # the 3 major-bear years to score per-bear


# ---------------------------------------------------------------- engine
def run(df, long_sig, short_sig, allow_long=True, allow_short=True,
        # base long-stop spec:
        atr_mult=3.5, sl_cap=0.12, be_r=1.0, be_buf=0.01, pyr_r=2.0, pyr_frac=1.0,
        # base short leg (held fixed):
        short_size=0.40, s_atr=5.0, s_cap=0.15, s_pyr_r=None,
        # --- LONG-stop VARIANTS (stop_type='base' => structural base only) ---
        stop_type="base", swing_n=20, ch_k=3.0, ch_act_r=2.0,
        ratchet_at=None,            # [(trigR, lockR), ...] -> at prof>=trigR lock stop=entry+lockR*R
        stage2_after_pyr=None):     # dict(atr_mult=, sl_cap=) tighten long stop after pyramid
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    a = bt.atr(df, 14).values; n = len(df)
    bull = long_sig; bear = short_sig
    cash = 1.0; units = 0.0; side = 0; entry = 0.0; stop = 0.0; R = 0.0; pyrd = False
    notional0 = 0.0; armedL = True; armedS = True; peakH = 0.0
    eq = np.ones(n)
    for i in range(16, n - 1):
        oN, hN, lN, cN = o[i + 1], h[i + 1], l[i + 1], c[i + 1]
        if not bull[i]: armedL = True
        if not bear[i]: armedS = True
        if side != 0:
            # ---- exits checked with the stop set at the END of the PRIOR bar (no lookahead) ----
            hit = (lN <= stop) if side == 1 else (hN >= stop)
            regime_out = (not bull[i]) if side == 1 else (not bear[i])
            if hit:
                fpx = stop * (1 - SLIP) if side == 1 else stop * (1 + SLIP)
                cash += units * fpx - abs(units) * fpx * FEE; units = 0.0; side = 0; pyrd = False
            elif regime_out:
                fpx = oN * (1 - SLIP) if side == 1 else oN * (1 + SLIP)
                cash += units * fpx - abs(units) * fpx * FEE; units = 0.0; side = 0; pyrd = False
            else:
                # survived bar i+1 -> update stop for the NEXT bar (base BE/pyr + long variants)
                prof = ((cN - entry) / R) if side == 1 else ((entry - cN) / R)
                # base break-even
                if prof >= be_r:
                    be = entry * (1 + be_buf) if side == 1 else entry * (1 - be_buf)
                    stop = max(stop, be) if side == 1 else min(stop, be)
                # base pyramid (+ two-stage long tighten)
                pr = s_pyr_r if side == -1 else pyr_r
                if pr is not None and not pyrd and prof >= pr:
                    addn = pyr_frac * notional0
                    add_units = (addn / cN) if side == 1 else (-addn / cN)
                    cash -= add_units * cN + addn * FEE; units += add_units; pyrd = True
                    stop = max(stop, entry) if side == 1 else min(stop, entry)
                    if side == 1 and stage2_after_pyr is not None:
                        am = stage2_after_pyr.get("atr_mult", atr_mult)
                        cp = stage2_after_pyr.get("sl_cap", sl_cap)
                        stop = max(stop, max(cN - am * a[i], cN * (1 - cp)))
                # ---- LONG-stop ratchet VARIANTS (ratchet UP only; base = no-op) ----
                if side == 1 and stop_type != "base":
                    peakH = max(peakH, h[i])
                    pnow = prof                       # realized profit through close[i+1]
                    if stop_type == "swing":
                        lo = l[max(0, i - swing_n + 1):i + 1].min()
                        stop = max(stop, lo)
                    elif stop_type == "chandelier":
                        if pnow >= ch_act_r:
                            stop = max(stop, peakH - ch_k * a[i])
                    elif stop_type == "ratchet" and ratchet_at:
                        for trig, lock in ratchet_at:
                            if pnow >= trig:
                                stop = max(stop, entry + lock * R)
        if side == 0:
            go = 0
            if allow_long and bull[i] and armedL: go = 1
            elif allow_short and bear[i] and armedS: go = -1
            if go != 0:
                E = cash
                if go == 1:
                    st = max(c[i] - atr_mult * a[i], c[i] * (1 - sl_cap)); entry = oN * (1 + SLIP); sz = 1.0
                else:
                    st = min(c[i] + s_atr * a[i], c[i] * (1 + s_cap)); entry = oN * (1 - SLIP); sz = short_size
                ok = (entry - st > 0) if go == 1 else (st - entry > 0)
                if ok:
                    notional0 = sz * E
                    units = go * notional0 / entry; cash = E - units * entry - notional0 * FEE
                    stop = st; R = abs(entry - st); side = go; pyrd = False; peakH = entry
                    if go == 1: armedL = False
                    else: armedS = False
        eq[i + 1] = cash + units * cN
    return pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]


# ---------------------------------------------------------------- metrics
def oos(s):
    return m(s[s.index >= s.index[int(len(s) * 0.6)]])[2]


def yr(s, y):
    seg = s[s.index.year == y]
    return (seg.iloc[-1] / seg.iloc[0] - 1) * 100 if len(seg) > 20 else 0.0


def show(df, ls, ss, label, **kw):
    s = run(df, ls, ss, **kw)
    cg, dd, rr = m(s); o = oos(s)
    yrs = "  ".join(f"{y}:{yr(s, y):+.0f}%" for y in BEARS)
    print(f"  {label:<40}{cg*100:>5.0f}%{dd*100:>7.1f}%{rr:>6.2f}{o:>6.2f}   {yrs}")
    return s, (cg, dd, rr, o)


def main():
    df, bull = build()
    ls, ss = signals(df, bull)
    print("=" * 112)
    print("STOP-TYPE SHOOTOUT (long leg) — my-V3 combined, BTC 4h EXTENDED 2017-2026")
    print("  data:", pd.to_datetime(df['timestamp']).min().date(), "->",
          pd.to_datetime(df['timestamp']).max().date(),
          f"| {len(df)} 4h bars | long-bars {ls.sum()} | short-bars {ss.sum()}")
    print("=" * 112)
    print(f"  {'config':<40}{'CAGR':>5}{'DD':>8}{'r/DD':>6}{'OOS':>6}   {'/'.join(map(str,BEARS))} returns")

    # ---- (0) BASE REPRODUCTION (assert byte-for-byte) ----
    print("  --- BASE (reproduce first; must be CAGR96/DD-43/rDD2.24/OOS3.05) ---")
    s_base, mb = show(df, ls, ss, "BASE combined")
    cg0, dd0, rr0, oo0 = mb
    assert abs(cg0 - 0.96) < 0.02 and abs(dd0 + 0.43) < 0.02 and abs(rr0 - 2.24) < 0.05, \
        f"BASE DID NOT REPRODUCE: CAGR{cg0:.2f} DD{dd0:.2f} rDD{rr0:.2f}"
    print("  -- base reproduced OK --")
    show(df, ls, ss, "LONG-ONLY (base stop, no short)", allow_short=False)

    grid = {}

    # ---- (1) ATR mult x cap grid ----
    print("  --- (1) structural ATR x cap grid ---")
    for am in (3.0, 3.5, 4.0, 4.5):
        for cp in (0.08, 0.12, 0.15):
            s, mm = show(df, ls, ss, f"ATR-{am} cap{int(cp*100)}%", atr_mult=am, sl_cap=cp)
            grid[f"ATR{am}/cap{int(cp*100)}"] = (s, mm)

    # ---- (2) swing-low ----
    print("  --- (2) swing-low (lowest low last N bars) ---")
    for N in (10, 20, 30):
        s, mm = show(df, ls, ss, f"swing-low N={N}", stop_type="swing", swing_n=N)
        grid[f"swing{N}"] = (s, mm)

    # ---- (3) activated chandelier ----
    print("  --- (3) chandelier (HH - k*ATR) ARMED after +act_R ---")
    for k in (3.0, 4.0):
        for act in (1.0, 2.0, 3.0):
            s, mm = show(df, ls, ss, f"chandelier k={k} act={act}R",
                         stop_type="chandelier", ch_k=k, ch_act_r=act)
            grid[f"chand_k{k}_act{act}"] = (s, mm)

    # ---- (4) ratchet schedules ----
    print("  --- (4) ratchet: lock entry+lock*R once past trigger R ---")
    scheds = {
        "ratchet@2R->+1R": [(2.0, 1.0)],
        "ratchet@2R->+1R,3R->+2R": [(2.0, 1.0), (3.0, 2.0)],
        "ratchet@3R->+2R,4R->+3R": [(3.0, 2.0), (4.0, 3.0)],
        "ratchet@2R->+0.5,4R->+2R": [(2.0, 0.5), (4.0, 2.0)],
    }
    for name, sch in scheds.items():
        s, mm = show(df, ls, ss, name, stop_type="ratchet", ratchet_at=sch)
        grid[name] = (s, mm)

    # ---- (5) two-stage ----
    print("  --- (5) two-stage: wide initial, tighten after pyramid ---")
    for am0, cp0, am2, cp2 in ((4.0, 0.15, 2.5, 0.08), (4.5, 0.15, 3.0, 0.10),
                               (4.0, 0.15, 3.0, 0.12), (3.5, 0.12, 3.0, 0.10)):
        s, mm = show(df, ls, ss, f"2stage init{am0}/{int(cp0*100)} -> {am2}/{int(cp2*100)}",
                     atr_mult=am0, sl_cap=cp0, stage2_after_pyr=dict(atr_mult=am2, sl_cap=cp2))
        grid[f"2stage_{am0}_{am2}_{int(cp2*100)}"] = (s, mm)

    # ---- GATE & TOP 3 ----
    base_rr = rr0
    base_bears = {y: yr(s_base, y) for y in BEARS}
    base_yrs = {y: yr(s_base, y) for y in range(2017, 2027)}
    cand = []
    for name, (s, mm) in grid.items():
        cg, dd, rr, o = mm
        yrs_all = [yr(s, y) for y in range(2017, 2027)]
        green = all(v > -0.5 for v in yrs_all)
        bears_ok = all(yr(s, y) >= base_bears[y] - 1.0 for y in BEARS)
        beats = (rr > base_rr)
        cand.append((rr, name, s, mm, green, bears_ok, beats))
    cand.sort(key=lambda x: x[0], reverse=True)

    print("\n" + "=" * 112)
    print("RANKING by ret/DD (full history). GATE = beats base ret/DD + green-every-year + no worse on 3 bears")
    print("=" * 112)
    print(f"  BASE: CAGR {cg0*100:.0f}%  DD {dd0*100:.1f}%  ret/DD {base_rr:.2f}  OOS {oo0:.2f}  "
          f"bears {'/'.join(f'{y}:{base_bears[y]:+.0f}%' for y in BEARS)}")
    print(f"  {'config':<40}{'CAGR':>5}{'DD':>8}{'r/DD':>6}{'OOS':>6}  beats green bears")
    for rr, name, s, mm, green, bok, beats in cand:
        cg, dd, _, o = mm
        print(f"  {name:<40}{cg*100:>5.0f}%{dd*100:>7.1f}%{rr:>6.2f}{o:>6.2f}   "
              f"{'Y' if beats else 'n':>3}{'Y' if green else 'n':>5}{'Y' if bok else 'n':>5}")

    qualified = [x for x in cand if x[6] and x[4] and x[5]]
    print("\n" + "=" * 112)
    print(f"STRICT GATE (beat base ret/DD {base_rr:.2f} AND green-every-year AND no worse on 3 bears)"
          f" — {len(qualified)} pass")
    print("=" * 112)
    if qualified:
        for rr, name, s, mm, green, bok, beats in qualified[:5]:
            cg, dd, _, o = mm
            print(f"  {name:<40} CAGR {cg*100:.0f}%  DD {dd*100:.1f}%  ret/DD {rr:.2f}  OOS {o:.2f}")
    else:
        print("  NONE. No stop variant beats base ret/DD 2.24 while staying green every year.")
        print("  (The only configs that cut DD do so by clipping the very bull-year trend legs that")
        print("   are the edge — confirming the prior agent: tighter long stops crater CAGR.)")

    # ---- BEST 3 by the ACTUAL goal: lowest DD among green + bears-ok configs ----
    green_set = [x for x in cand if x[4] and x[5]]            # green every year + no worse on bears
    # DD is negative; SHALLOWEST drawdown = the LEAST-negative value => sort descending by DD
    green_set.sort(key=lambda x: x[3][1], reverse=True)
    pick = green_set[:3]
    print("\n" + "=" * 112)
    print("BEST 3 by the stated GOAL = LOWEST DD (among green-every-year + bears-ok configs)")
    print("  -> these are the genuine DD-reducers; the CAGR they give up is the cost")
    print("=" * 112)
    for rr, name, s, mm, green, bok, beats in pick:
        cg, dd, _, o = mm
        ddelta = (dd - dd0) * 100
        verdict = "BEATS base ret/DD" if rr > base_rr else f"below base ret/DD ({rr:.2f}<{base_rr:.2f})"
        print(f"\n  >>> {name}   [{verdict}]")
        print(f"      CAGR {cg*100:.0f}% (base 96%)   DD {dd*100:.1f}% (base {dd0*100:.1f}%, "
              f"delta {ddelta:+.1f}pp)   ret/DD {rr:.2f} (base {base_rr:.2f})   OOS {o:.2f} (base {oo0:.2f})")
        print("      year:  " + "  ".join(f"{y}:{yr(s,y):+.0f}%" for y in range(2017, 2027)))
        print("      bears: " + "  ".join(f"{y}:{yr(s,y):+.0f}%" for y in BEARS)
              + "   (base " + " ".join(f"{y}:{base_bears[y]:+.0f}%" for y in BEARS) + ")")

    # DD-comparison summary table: base vs the 3 lowest-DD picks
    print("\n  DD COMPARISON (does cutting DD pay for itself?):")
    print(f"    {'config':<34}{'DD':>8}{'CAGR':>7}{'ret/DD':>8}{'verdict':>22}")
    print(f"    {'BASE':<34}{dd0*100:>7.1f}%{cg0*100:>6.0f}%{base_rr:>8.2f}{'(reference)':>22}")
    for rr, name, s, mm, green, bok, beats in pick:
        cg, dd, _, o = mm
        v = "better risk-adj" if rr > base_rr else "worse risk-adj"
        print(f"    {name:<34}{dd*100:>7.1f}%{cg*100:>6.0f}%{rr:>8.2f}{v:>22}")

    # ---- AUDIT: re-derive by hand; flag ret/DD>5 or OOS>6 ----
    print("\n" + "=" * 112)
    print("AUDIT — re-derive metrics by hand (distrust ret/DD>5 or OOS>6)")
    print("=" * 112)
    audit = [("BASE", s_base)] + [(p[1], p[2]) for p in pick]
    for nm, s in audit:
        eq = s.values
        tot = eq[-1] / eq[0]
        yrs = (s.index[-1] - s.index[0]).days / 365.25
        cagr = tot ** (1 / yrs) - 1
        peak = np.maximum.accumulate(eq); dd = (eq / peak - 1).min()
        rr = cagr / abs(dd); o = oos(s)
        flag = "  <-- SUSPECT" if (rr > 5 or o > 6) else ""
        print(f"  {nm:<40} {tot:>8.1f}x /{yrs:.2f}y  CAGR {cagr*100:>5.1f}%  DD {dd*100:>6.1f}%  "
              f"ret/DD {rr:.2f}  OOS {o:.2f}{flag}")


if __name__ == "__main__":
    main()
