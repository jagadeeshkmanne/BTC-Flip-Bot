#!/usr/bin/env python3
"""tune_shorts.py — tune a SLOW-GATED, SIZED SHORT engine to combine with the my-V3 long side.

Long side stays the my-V3 base (bull regime, atr_mult=3, sl_cap=0.08, pyr_r=2, be_r=1).
Short side is independent: a slow gate (price<SMA(N) and/or drawdown off N-mo high), its own
short_size (notional = short_size*equity), its own atr_mult / sl_cap / pyramid on|off.

The unified signed cash/units engine from backtest_myv3_shorts.run() is COPIED here and extended
with short-specific params + short_size. Long-side params are pinned to the base, so with
allow_short=False this MUST reproduce the long-only base byte-for-byte (verified in main()).

GOAL: a combined config with CAGR > base, maxDD < ~38%, ret/DD >= 2.38 (base), OOS ret/DD >= 2.38,
2022 & 2026 both positive.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_shorts import regimes, run as base_run, m

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT
BPD = 6  # 4h bars per day

# my-V3 LONG base params (pinned)
L_ATR = 3.0; L_SLCAP = 0.08; L_PYR = 2.0; L_PYRFRAC = 1.0; L_BER = 1.0; L_BEBUF = 0.005


def run2(df, bull, bear, allow_long=True, allow_short=False,
         # long side pinned to base by default
         pyr_r=L_PYR, pyr_frac=L_PYRFRAC, be_r=L_BER, atr_mult=L_ATR, sl_cap=L_SLCAP, be_buf=L_BEBUF,
         # short side independent
         short_size=1.0, s_atr_mult=3.0, s_sl_cap=0.08, s_pyr_r=2.0, s_pyr_frac=1.0):
    """COPY of backtest_myv3_shorts.run() with: (1) short-specific atr_mult/sl_cap/pyr_r/pyr_frac,
    (2) short_size scaling of short notional, (3) notional-based entry fee (identical to base for
    longs since long notional == E). Counts short trades. Returns (equity Series, n_short_trades).
    """
    o=df["open"].values; h=df["high"].values; l=df["low"].values; c=df["close"].values
    a=bt.atr(df,14).values; n=len(df)
    cash=1.0; units=0.0; side=0; entry=0.0; stop=0.0; R=0.0; pyrd=False; E_entry=1.0
    armedL=True; armedS=True
    eq=np.ones(n)
    n_short=0
    for i in range(16, n-1):
        oN,hN,lN,cN = o[i+1],h[i+1],l[i+1],c[i+1]
        if not bull[i]: armedL=True
        if not bear[i]: armedS=True
        # per-side active params
        if side==1:
            cur_pyr_r=pyr_r; cur_pyr_frac=pyr_frac
        else:
            cur_pyr_r=s_pyr_r; cur_pyr_frac=s_pyr_frac
        if side!=0:
            hit = (lN<=stop) if side==1 else (hN>=stop)
            regime_out = (not bull[i]) if side==1 else (not bear[i])
            if hit:
                fpx = stop*(1-SLIP) if side==1 else stop*(1+SLIP)
                cash += units*fpx - abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False
            elif regime_out:
                fpx = oN*(1-SLIP) if side==1 else oN*(1+SLIP)
                cash += units*fpx - abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False
            else:
                prof = ((cN-entry)/R) if side==1 else ((entry-cN)/R)
                if prof>=be_r:
                    be = entry*(1+be_buf) if side==1 else entry*(1-be_buf)
                    stop = max(stop,be) if side==1 else min(stop,be)
                if cur_pyr_r is not None and not pyrd and prof>=cur_pyr_r:
                    # scale short pyramid notional by short_size (long uses full E_entry)
                    sizef = 1.0 if side==1 else short_size
                    addn = cur_pyr_frac*sizef*E_entry
                    add_units = (addn/cN) if side==1 else (-addn/cN)
                    cash -= add_units*cN + addn*FEE; units += add_units; pyrd=True
                    stop = max(stop,entry) if side==1 else min(stop,entry)
        if side==0:
            go=0
            if allow_long and bull[i] and armedL: go=1
            elif allow_short and bear[i] and armedS: go=-1
            if go!=0:
                E=cash
                if go==1:
                    st=max(c[i]-atr_mult*a[i], c[i]*(1-sl_cap)); entry=oN*(1+SLIP)
                    if entry-st<=0: st=None
                    sizef=1.0
                else:
                    st=min(c[i]+s_atr_mult*a[i], c[i]*(1+s_sl_cap)); entry=oN*(1-SLIP)
                    if st-entry<=0: st=None
                    sizef=short_size
                if st is not None:
                    units = go*sizef*E/entry
                    notional = abs(units)*entry
                    cash = E - units*entry - notional*FEE
                    stop=st; R=abs(entry-st); side=go; E_entry=E; pyrd=False
                    if go==1: armedL=False
                    else: armedS=False; n_short+=1
        eq[i+1] = cash + units*cN
    return pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:], n_short


def yr_ret(s, y):
    seg = s[s.index.year == y]
    return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg) > 20 else 0.0


def oos_rr(s, n):
    return m(s[s.index >= s.index[int(n*0.6)]])[2]


def make_gate(c, kind, N, D=None):
    """Causal slow short gate (shift 1). kind 'sma': price<SMA(N mo). kind 'dd': >D off N-mo high."""
    if kind == "sma":
        sma = bt.sma(c, N*30*BPD).shift(1)
        return (c < sma).fillna(False).values, f"SMA{N}mo"
    else:  # drawdown off N-mo high
        hh = c.rolling(N*30*BPD).max().shift(1)
        dd = (c/hh - 1)
        return (dd < -D).fillna(False).values, f"DD{int(D*100)}%/{N}mo"


def main():
    df, bull, bear_daily = regimes()
    c = df["close"]; n = len(df)

    # ---------------------------------------------------------------
    # ANTI-BUG: allow_short=False must reproduce long-only base byte-for-byte
    # ---------------------------------------------------------------
    base_long = base_run(df, bull, bear_daily, allow_long=True, allow_short=False)
    test_long, _ = run2(df, bull, bear_daily, allow_long=True, allow_short=False)
    maxdiff = float(np.abs(base_long.values - test_long.values).max())
    cb, db, rb = m(base_long)
    ob = oos_rr(base_long, n)
    print("="*108)
    print("VERIFY: run2(allow_short=False) reproduces long-only base byte-for-byte")
    print(f"  max |equity diff| = {maxdiff:.3e}   ->  {'PASS (identical)' if maxdiff < 1e-12 else 'FAIL'}")
    print(f"  LONG-ONLY BASE:  CAGR {cb*100:.1f}%  maxDD {db*100:.1f}%  ret/DD {rb:.2f}  "
          f"OOS r/DD {ob:.2f}  2022 {yr_ret(base_long,2022):+.0f}%  2026 {yr_ret(base_long,2026):+.0f}%")
    if maxdiff >= 1e-12:
        print("  ABORTING: base mismatch, fix engine before tuning."); return

    # ---------------------------------------------------------------
    # Sweep
    # ---------------------------------------------------------------
    gates = []
    for N in (6, 9, 12, 18):
        gates.append(make_gate(c, "sma", N))
    for N, D in ((12, 0.20), (9, 0.15)):
        gates.append(make_gate(c, "dd", N, D))

    sizes = (0.25, 0.5, 0.75, 1.0)
    atrs = (3, 4, 5)
    caps = (0.08, 0.12)
    pyrs = (2.0, None)  # ON / OFF

    rows = []
    for gate, gname in gates:
        for size in sizes:
            for am in atrs:
                for cap in caps:
                    for pr in pyrs:
                        s, nsh = run2(df, bull, gate, allow_long=True, allow_short=True,
                                      short_size=size, s_atr_mult=am, s_sl_cap=cap, s_pyr_r=pr)
                        cg, dd, rr = m(s)
                        orr = oos_rr(s, n)
                        rows.append(dict(gate=gname, ssize=size, atr=am, cap=cap,
                                         pyr=("on" if pr else "off"), cagr=cg, dd=dd, rr=rr,
                                         orr=orr, y22=yr_ret(s,2022), y26=yr_ret(s,2026), nsh=nsh))

    R = pd.DataFrame(rows)

    # ---- full sweep table ----
    print("\n" + "="*108)
    print(f"SWEEP — {len(R)} combos (long pinned to base; short = slow gate + sized).  base ret/DD={rb:.2f}, OOS={ob:.2f}, DD={db*100:.0f}%")
    print("="*108)
    hdr = f"  {'gate':<11}{'sz':>5}{'atr':>4}{'cap':>6}{'pyr':>4}{'CAGR':>7}{'DD':>6}{'r/DD':>6}{'OOS':>6}{'2022':>7}{'2026':>7}{'#sh':>5}"
    print(hdr); print("  "+"-"*104)
    for _, x in R.sort_values("rr", ascending=False).iterrows():
        flag = ""
        if x.rr >= rb and x.orr >= ob and x.dd > -0.38 and x.y22 > 0 and x.y26 > 0 and x.cagr > cb:
            flag = "  <== BEATS BASE (all criteria)"
        elif x.rr >= rb and x.orr >= ob and x.dd > -0.38:
            flag = "  <- r/DD+OOS+DD ok"
        print(f"  {x['gate']:<11}{x['ssize']:>5.2f}{x['atr']:>4.0f}{x['cap']:>6.2f}{x['pyr']:>4}"
              f"{x['cagr']*100:>6.0f}%{x['dd']*100:>5.0f}%{x['rr']:>6.2f}{x['orr']:>6.2f}"
              f"{x['y22']:>6.0f}%{x['y26']:>6.0f}%{x['nsh']:>5.0f}{flag}")

    # ---- ranked finalists: must beat base on ret/DD AND OOS, DD<38%, 2022&2026>0, CAGR>base ----
    strict = R[(R.rr >= rb) & (R.orr >= ob) & (R.dd > -0.38) &
               (R.y22 > 0) & (R.y26 > 0) & (R.cagr > cb)].copy()
    print("\n" + "="*108)
    print(f"FINALISTS — beat base on ret/DD (>={rb:.2f}) AND OOS r/DD (>={ob:.2f}), DD<38%, 2022>0, 2026>0, CAGR>base")
    print("="*108)
    if len(strict) == 0:
        print("  NONE meet ALL criteria. Relaxed set (ret/DD>=base & DD<38%, 2022&2026>0):")
        strict = R[(R.rr >= rb) & (R.dd > -0.38) &
                   (R.y22 > 0) & (R.y26 > 0)].copy()
    # rank by ret/DD then CAGR
    strict = strict.sort_values(["rr", "cagr"], ascending=False)
    print(hdr)
    for _, x in strict.head(10).iterrows():
        print(f"  {x['gate']:<11}{x['ssize']:>5.2f}{x['atr']:>4.0f}{x['cap']:>6.2f}{x['pyr']:>4}"
              f"{x['cagr']*100:>6.0f}%{x['dd']*100:>5.0f}%{x['rr']:>6.2f}{x['orr']:>6.2f}"
              f"{x['y22']:>6.0f}%{x['y26']:>6.0f}%{x['nsh']:>5.0f}")

    # ---- year-by-year for the winner ----
    if len(strict):
        w = strict.iloc[0]
        prw = 2.0 if w["pyr"] == "on" else None
        gate = [g for g, gn in gates if gn == w["gate"]][0]
        sw, nshw = run2(df, bull, gate, allow_long=True, allow_short=True,
                        short_size=w["ssize"], s_atr_mult=w["atr"], s_sl_cap=w["cap"], s_pyr_r=prw)
        print("\n" + "="*108)
        print(f"WINNER year-by-year:  gate={w['gate']} size={w['ssize']} atr={w['atr']} cap={w['cap']} pyr={w['pyr']}  ({nshw} short trades)")
        print("="*108)
        cgw, ddw, rrw = m(sw)
        print(f"  COMBINED:   CAGR {cgw*100:.1f}%  maxDD {ddw*100:.1f}%  ret/DD {rrw:.2f}  OOS r/DD {oos_rr(sw,n):.2f}")
        print(f"  BASE(long): CAGR {cb*100:.1f}%  maxDD {db*100:.1f}%  ret/DD {rb:.2f}  OOS r/DD {ob:.2f}")
        print(f"  {'year':<7}{'long-only':>12}{'long+short':>13}")
        for y in sorted(set(sw.index.year)):
            ly = yr_ret(base_long, y); cy = yr_ret(sw, y)
            ls = f"{ly:+.0f}%" if abs(ly) > 1e-9 or len(base_long[base_long.index.year==y])>20 else "  --"
            cs = f"{cy:+.0f}%" if abs(cy) > 1e-9 or len(sw[sw.index.year==y])>20 else "  --"
            print(f"  {y:<7}{ls:>12}{cs:>13}")
    else:
        print("  NO config survives even the relaxed criteria.")


if __name__ == "__main__":
    main()
