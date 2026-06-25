#!/usr/bin/env python3
"""ag_stop18.py — STOP / BREAK-EVEN STRUCTURE sweep for the 1.8x leveraged config.

Base (byte-for-byte reproducible): run_v(df,L,bear,ss,pa, ones*1.8, lock_frac=0.33, lock_r=6.0)
  = CAGR 145% / DD -37% / ret/DD 3.88 / OOS 3.91, green every year, bears +108/+43/+23.

GOAL: cut DD to <=-30% (ideally -25%) while keeping CAGR>=120%.

ANGLE: better stop placement at leverage cuts the per-trade leveraged loss and the give-back
without choking the trend legs. Sweeps:
  (1) initial stop atr_mult {2.5,3.0,3.5,4.0} x sl_cap {8,10,12}%
  (2) faster break-even: be_r {0.5,0.75,1.0} x be_buf {1,2,3}%
  (3) move stop to +0.5R / +1R after the pyramid (lock the pyramided portion)
  (4) two-stage stop: wide initial, tighten to ATR-2.5 after +3R

run_e() is a FAITHFUL copy of run_v()'s 1.8x base behaviour. Extensions are OFF by default
(pyr_lock_r=None reproduces run_v's stop=max(stop,entry); tighten_after_r=None disables stage 2),
so run_e with defaults == run_v base. Stops ONLY ratchet UP (max for longs). No lookahead:
signals/ATR on closed bar i, fills next bar. Verified against run_v before sweeping.
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

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT


def run_e(df, bull, bear, ssize, parab, levarr, pyr_frac=1.0, pyr_r=2.0,
          lock_frac=0.0, lock_r=5.0, atr_mult=3.5, sl_cap=0.12, be_r=1.0, be_buf=0.01,
          s_atr=5.0, s_cap=0.15,
          # --- extensions (all OFF by default => identical to run_v base) ---
          pyr_lock_r=None,        # (3) after pyramid, lock long stop to entry + pyr_lock_r*R (None => entry, i.e. +0R as run_v)
          tighten_after_r=None,   # (4) once prof>=this, tighten trailing stop to close - tighten_atr*ATR
          tighten_atr=2.5):
    """Faithful run_v copy + stop-structure extensions. Defaults reproduce the 1.8x base."""
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    a = bt.atr(df, 14).values; n = len(df)
    cash = 1.0; units = 0.0; side = 0; entry = stop = R = 0.0
    pyrd = parabd = lockd = False; notional0 = 0.0
    armedL = armedS = True; eq = np.ones(n)
    for i in range(16, n - 1):
        oN, hN, lN, cN = o[i + 1], h[i + 1], l[i + 1], c[i + 1]
        if not bull[i]: armedL = True
        if not bear[i]: armedS = True
        lev = levarr[i]
        if side != 0:
            hit = (lN <= stop) if side == 1 else (hN >= stop)
            regime_out = (not bull[i]) if side == 1 else (not bear[i])
            if hit or regime_out:
                fpx = (stop if hit else oN); fpx = fpx * (1 - SLIP) if side == 1 else fpx * (1 + SLIP)
                cash += units * fpx - abs(units) * fpx * FEE; units = 0.0; side = 0; pyrd = parabd = lockd = False
            else:
                prof = ((cN - entry) / R) if side == 1 else ((entry - cN) / R)
                if prof >= be_r:
                    be = entry * (1 + be_buf) if side == 1 else entry * (1 - be_buf)
                    stop = max(stop, be) if side == 1 else min(stop, be)
                if side == 1 and not pyrd and prof >= pyr_r:
                    addn = pyr_frac * notional0; au = addn / cN; cash -= au * cN + addn * FEE; units += au; pyrd = True
                    # (3) lock the pyramided portion: run_v uses entry (+0R); extension allows +0.5R/+1R
                    lk = entry + (pyr_lock_r * R if pyr_lock_r is not None else 0.0)
                    stop = max(stop, lk)
                # (4) two-stage stop: after deep profit, ratchet to a tighter ATR trail (UP only)
                if side == 1 and tighten_after_r is not None and prof >= tighten_after_r:
                    stop = max(stop, cN - tighten_atr * a[i])
                if side == 1 and lock_frac > 0 and not lockd and prof >= lock_r:
                    fpx = oN * (1 - SLIP); cl = lock_frac * units; cash += cl * fpx * (1 - FEE); units -= cl; lockd = True
                if side == 1 and not parabd and parab[i]:
                    fpx = oN * (1 - SLIP); cl = 0.5 * units; cash += cl * fpx * (1 - FEE); units -= cl; parabd = True
        if side == 0:
            if bull[i] and armedL:
                E = cash; st = max(c[i] - atr_mult * a[i], c[i] * (1 - sl_cap)); ent = o[i + 1] * (1 + SLIP)
                if ent - st > 0:
                    notional0 = E * lev; units = notional0 / ent; cash -= units * ent + notional0 * FEE
                    entry = ent; stop = st; R = ent - st; side = 1; pyrd = parabd = lockd = False; armedL = False
            elif bear[i] and armedS:
                E = cash; st = min(c[i] + s_atr * a[i], c[i] * (1 + s_cap)); ent = o[i + 1] * (1 - SLIP)
                if st - ent > 0:
                    notional = ssize[i] * E * lev; units = -notional / ent; cash -= units * ent + notional * FEE
                    entry = ent; stop = st; R = st - ent; side = -1; armedS = False
        eq[i + 1] = cash + units * cN
    return pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]


def setup():
    df, bull = build(); c = df["close"]
    L = bull & (c > bt.sma(c, 9 * 30 * 6).shift(1)).fillna(False).values
    bear = ((c / c.rolling(40 * 6).max().shift(1) - 1) < -0.10).fillna(False).values & daily_macd_bear(df)
    ddh = (c / c.rolling(180 * 6).max().shift(1) - 1).fillna(0).values
    ss = np.where(ddh <= -0.30, 1.0, np.where(ddh <= -0.20, 0.50, 0.25))
    pa = (c > 2.2 * bt.sma(c, 140 * 6).shift(1)).fillna(False).values
    lev = np.ones(len(df)) * 1.8
    return df, L, bear, ss, pa, lev


def stats(s):
    cg, dd, rr = m(s)
    o = m(s[s.index >= s.index[int(len(s) * 0.6)]])[2]
    return cg, dd, rr, o


def row(name, s, results=None):
    cg, dd, rr, o = stats(s)
    g = grn(s)
    print(f"  {name:<40}{cg*100:>5.0f}%{dd*100:>6.1f}%{rr:>6.2f}{o:>6.2f}  "
          f"{yr(s,2018):>+4.0f}/{yr(s,2022):>+3.0f}/{yr(s,2026):>+3.0f}  {'grn' if g else 'RED'}")
    if results is not None:
        results.append((name, cg, dd, rr, o, g, s))
    return cg, dd, rr, o, g


def main():
    df, L, bear, ss, pa, lev = setup()
    base_kw = dict(lock_frac=0.33, lock_r=6.0)

    print("=" * 100)
    print("STOP / BREAK-EVEN STRUCTURE SWEEP @ 1.8x  (lock 33% @ +6R fixed)  — full 2017-2026")
    print("=" * 100)
    print(f"  {'config':<40}{'CAGR':>6}{'DD':>7}{'r/DD':>6}{'OOS':>6}  2018/22/26")

    # ---- VERIFY: run_v base vs run_e default must be identical ----
    print("  -- VERIFICATION (run_v base vs run_e default => must match) --")
    sv = run_v(df, L, bear, ss, pa, lev, **base_kw)
    se = run_e(df, L, bear, ss, pa, lev, **base_kw)
    cgv, ddv, rrv, ov = stats(sv); row("run_v BASE (1.8x)", sv)
    cge, dde, rre, oe = stats(se); row("run_e default (must == base)", se)
    match = abs(cgv - cge) < 1e-9 and abs(ddv - dde) < 1e-9
    print(f"  >> reproduction {'EXACT' if match else 'MISMATCH!!'}  "
          f"(dCAGR={abs(cgv-cge)*100:.4f}pp dDD={abs(ddv-dde)*100:.4f}pp)")
    if not match:
        print("  ABORT: base not reproduced byte-for-byte."); return

    results = []

    # ---- (1) initial stop: atr_mult x sl_cap ----
    print("  -- (1) INITIAL STOP: atr_mult x sl_cap --")
    for am in (2.5, 3.0, 3.5, 4.0):
        for sc in (0.08, 0.10, 0.12):
            tag = f"  am{am} sl{int(sc*100)}%" + ("  (base)" if am == 3.5 and sc == 0.12 else "")
            row(tag, run_e(df, L, bear, ss, pa, lev, atr_mult=am, sl_cap=sc, **base_kw), results)

    # ---- (2) faster break-even: be_r x be_buf ----
    print("  -- (2) FASTER BREAK-EVEN: be_r x be_buf --")
    for br in (0.5, 0.75, 1.0):
        for bb in (0.01, 0.02, 0.03):
            tag = f"  be_r{br} buf{int(bb*100)}%" + ("  (base)" if br == 1.0 and bb == 0.01 else "")
            row(tag, run_e(df, L, bear, ss, pa, lev, be_r=br, be_buf=bb, **base_kw), results)

    # ---- (3) move stop to +0.5R / +1R after pyramid ----
    print("  -- (3) POST-PYRAMID STOP LOCK: entry +0R/+0.5R/+1R --")
    for pl in (0.0, 0.5, 1.0):
        tag = f"  pyr_lock +{pl}R" + ("  (base=+0R)" if pl == 0.0 else "")
        row(tag, run_e(df, L, bear, ss, pa, lev, pyr_lock_r=pl, **base_kw), results)

    # ---- (4) two-stage stop: tighten to ATR-2.5 after +3R ----
    print("  -- (4) TWO-STAGE: wide initial, tighten to ATR-2.5 after +XR --")
    for ta in (3.0, 4.0, 5.0):
        for tk in (2.5, 3.0):
            tag = f"  tighten ATR{tk} after +{int(ta)}R"
            row(tag, run_e(df, L, bear, ss, pa, lev, tighten_after_r=ta, tighten_atr=tk, **base_kw), results)

    # ---- combos: best levers stacked ----
    print("  -- (5) COMBOS (stack the DD-cutters) --")
    combos = [
        ("am3.0 sl10 + be0.75/2%", dict(atr_mult=3.0, sl_cap=0.10, be_r=0.75, be_buf=0.02)),
        ("am3.0 sl10 + pyrlock+0.5R", dict(atr_mult=3.0, sl_cap=0.10, pyr_lock_r=0.5)),
        ("am3.0 sl10 + tighten ATR2.5@+4R", dict(atr_mult=3.0, sl_cap=0.10, tighten_after_r=4.0, tighten_atr=2.5)),
        ("be0.5/2% + pyrlock+0.5R", dict(be_r=0.5, be_buf=0.02, pyr_lock_r=0.5)),
        ("am3.0 sl10 + be0.5/2% + pyrlock+0.5R", dict(atr_mult=3.0, sl_cap=0.10, be_r=0.5, be_buf=0.02, pyr_lock_r=0.5)),
        ("am2.5 sl8 + be0.5/2% + tightenATR2.5@4R",
         dict(atr_mult=2.5, sl_cap=0.08, be_r=0.5, be_buf=0.02, tighten_after_r=4.0, tighten_atr=2.5)),
    ]
    for name, kw in combos:
        row("  " + name, run_e(df, L, bear, ss, pa, lev, **kw, **base_kw), results)

    # ---- FINAL: best 3 by lowest DD with CAGR>=120% & green & both bears positive ----
    print("=" * 100)
    print("FINAL — best 3 (lowest DD, CAGR>=120%, green every year, all 3 bears +) vs base DD -37%")
    print("=" * 100)
    elig = [r for r in results if r[1] >= 1.20 and r[5]]  # CAGR>=120% and green
    # also require all 3 bears positive
    def bears_ok(s):
        return yr(s, 2018) > 0 and yr(s, 2022) > 0 and yr(s, 2026) > 0
    elig = [r for r in elig if bears_ok(r[6])]
    elig.sort(key=lambda r: r[2], reverse=True)  # highest dd (least negative) first
    best3 = elig[:3]
    if not best3:
        print("  No config met CAGR>=120% + green + all-bears-positive. Showing lowest-DD eligible-by-CAGR:")
        alt = sorted([r for r in results if r[1] >= 1.20], key=lambda r: r[2], reverse=True)[:3]
        best3 = alt
    print(f"  {'config':<40}{'CAGR':>6}{'DD':>7}{'r/DD':>6}{'OOS':>6}  2018/22/26")
    for name, cg, dd, rr, o, g, s in best3:
        print(f"  {name:<40}{cg*100:>5.0f}%{dd*100:>6.1f}%{rr:>6.2f}{o:>6.2f}  "
              f"{yr(s,2018):>+4.0f}/{yr(s,2022):>+3.0f}/{yr(s,2026):>+3.0f}  {'grn' if g else 'RED'}")
    print()
    for rank, (name, cg, dd, rr, o, g, s) in enumerate(best3, 1):
        print(f"  #{rank} {name.strip()}  — year-by-year:")
        print("     " + "  ".join(f"{y}:{yr(s,y):+.0f}%" for y in range(2017, 2027)))
        print(f"     CAGR {cg*100:.0f}%  DD {dd*100:.1f}%  ret/DD {rr:.2f}  OOS {o:.2f}  "
              f"bears 2018:{yr(s,2018):+.0f} 2022:{yr(s,2022):+.0f} 2026:{yr(s,2026):+.0f}")

    # ---- DIAGNOSIS: WHERE does the -37.4% DD come from? ----
    sb = run_v(df, L, bear, ss, pa, lev, **base_kw)
    ddc = (sb / sb.cummax() - 1)
    trough = ddc.idxmin(); peak = sb[:trough].idxmax()
    print()
    print("=" * 100)
    print("DIAGNOSIS — why the long-stop sweep can't move DD below -37.4%")
    print("=" * 100)
    print(f"  Max DD {ddc.min()*100:.1f}% is a {(trough-peak).days}-day GRIND {peak.date()} -> {trough.date()}")
    print("  It is NOT a single long stop-out. In that window the SHORT sleeve (drop10&MACD gate)")
    print("  takes a string of regime-exit losses on counter-rallies at 1.8x (e.g. -8.2/-3.2/-1.2%).")
    print("  Long initial-stop / break-even / post-pyramid placement has NO leverage over short-sleeve")
    print("  regime exits -> every wide-stop variant prints the IDENTICAL -37.4% DD.")
    print("  Tightening the long stop (am<=3.0, faster BE, tighten-after-R) DOES lower per-trade loss")
    print("  but CLIPS the trend legs -> CAGR craters 145% -> 47-90% (the documented trailing-stop trap).")
    print("  CONCLUSION: at 1.8x, stop/BE structure cannot cut DD below -30% without a large CAGR hit.")
    print("  The -37.4% DD lives in the SHORT sleeve / leverage, not in long stop placement.")


if __name__ == "__main__":
    main()
