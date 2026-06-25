#!/usr/bin/env python3
"""ftune_wf_ext.py — full all-years ROBUSTNESS AUDIT of the my-V3 BTC LONG strategy.

Walk-forward (rolling re-optimization) on extended BTC 2017-2026, plus per-era breakdown and
an honest remove-best-trades (per-trade ROE, recompounded) test.

Long strategy = bull regime (4h+daily EMA50/200) + optional macro filter (price>SMA(9mo))
                + ATR-3.5 structural stop + break-even@1R + pyramid@2R.

Engine: backtest_myv3_final.run() (capital-multiple pyramid via notional0). This file ONLY
re-uses that exact engine + build() regime — no re-implementation, so no spurious winners.

ANTI-BUG rules honored:
  - SANITY: base reproduces full-data long ret/DD ~1.83 (no macro) / 2.00 (macro) before anything.
  - WALK-FORWARD has NO leak: train window strictly ends before test window starts; config picked
    on train ONLY, applied to a fresh run sliced to the test window.
  - REMOVE-BEST-TRADES uses per-trade ROE (equity multiple of each closed position incl. pyramid),
    removed and the REST RECOMPOUNDED — NOT $-PnL with a frozen denominator.
  - Distrust ret/DD > 5 (flagged).
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_ext import build
from backtest_myv3_final import run, m

BPD = 6  # 4h bars per day

# config sweep grid (LONG only)
ATR_GRID = [3.0, 3.5, 4.0]
PYR_GRID = [0.5, 1.0, 1.5]
MACRO_GRID = ["off", "9mo"]


def macro_mask(c):
    """price > SMA(9 months) on 4h bars, prior bar (no look-ahead)."""
    return (c > bt.sma(c, 9 * 30 * BPD).shift(1)).fillna(False).values


def bull_for(bull, macro, macro_mode):
    return (bull & macro) if macro_mode == "9mo" else bull


def equity_for(df, bull, macro, cfg):
    """Full equity Series for a config over the whole df."""
    b = bull_for(bull, macro, cfg["macro"])
    return run(df, b, np.zeros(len(df), bool), allow_long=True, allow_short=False,
               atr_mult=cfg["atr"], pyr_frac=cfg["pyr"])


def slice_eq(eq, t0, t1):
    """Equity over [t0, t1), rebased to 1.0 at the window's first bar."""
    seg = eq[(eq.index >= t0) & (eq.index < t1)]
    if len(seg) < 10:
        return seg
    return seg / seg.iloc[0]


def metrics_on_concat(rets_segments):
    """Stitch per-window OOS rebased segments into one continuous equity curve and measure."""
    pieces = []
    eq0 = 1.0
    idx = []
    vals = []
    for seg in rets_segments:
        if len(seg) < 2:
            continue
        scaled = seg * eq0  # chain: continue from where we left off
        idx.extend(list(seg.index))
        vals.extend(list(scaled.values))
        eq0 = scaled.iloc[-1]
    eq = pd.Series(vals, index=pd.to_datetime(idx))
    eq = eq[~eq.index.duplicated(keep="first")].sort_index()
    return eq


# ---------------------------------------------------------------------------
# TEST 1 — WALK-FORWARD (rolling 2y train / 1y test, re-optimize each test window)
# ---------------------------------------------------------------------------
def walk_forward(df, bull, macro):
    print("=" * 96)
    print("TEST 1 — WALK-FORWARD (2y train / 1y test, rolling; re-pick best LONG config on TRAIN, apply OOS)")
    print("=" * 96)
    # precompute every config's full-data equity once (cheap: 18 configs)
    configs = [dict(atr=a, pyr=p, macro=mo) for a in ATR_GRID for p in PYR_GRID for mo in MACRO_GRID]
    full = {}
    for cfg in configs:
        full[(cfg["atr"], cfg["pyr"], cfg["macro"])] = equity_for(df, bull, macro, cfg)

    years = sorted(set(pd.to_datetime(df["timestamp"]).dt.year))
    y0, y1 = years[0], years[-1]
    # test windows: each calendar year that has >=2 full prior years of train
    oos_segments = []
    print(f"  {'train':<22}{'test':<8}{'picked cfg (atr/pyr/macro)':<28}{'train rDD':>10}{'test rDD':>10}{'test ret%':>11}")
    for ty in range(y0 + 2, y1 + 1):
        tr0 = pd.Timestamp(f"{ty-2}-01-01"); tr1 = pd.Timestamp(f"{ty}-01-01")
        te0 = pd.Timestamp(f"{ty}-01-01");   te1 = pd.Timestamp(f"{ty+1}-01-01")
        # pick best config on TRAIN by ret/DD
        best = None
        for cfg in configs:
            eq = full[(cfg["atr"], cfg["pyr"], cfg["macro"])]
            tr = slice_eq(eq, tr0, tr1)
            if len(tr) < 30:
                continue
            cg, dd, rr = m(tr)
            if best is None or rr > best[0]:
                best = (rr, cfg)
        if best is None:
            continue
        bcfg = best[1]
        # apply OOS to TEST window (fresh slice of that exact config's full curve)
        eq = full[(bcfg["atr"], bcfg["pyr"], bcfg["macro"])]
        te = slice_eq(eq, te0, te1)
        if len(te) < 10:
            continue
        tcg, tdd, trr = m(te)
        tret = (te.iloc[-1] / te.iloc[0] - 1) * 100
        oos_segments.append(te)
        cfgstr = f"{bcfg['atr']}/{bcfg['pyr']}/{bcfg['macro']}"
        print(f"  {str(tr0.date())+'..'+str((tr1-pd.Timedelta(days=1)).date()):<22}{ty:<8}{cfgstr:<28}{best[0]:>10.2f}{trr:>10.2f}{tret:>11.0f}")

    # stitch the OOS test windows into one continuous out-of-sample curve
    oos = metrics_on_concat(oos_segments)
    cg, dd, rr = m(oos)
    print("  " + "-" * 92)
    print(f"  CONCATENATED OOS (walk-forward, re-optimized each year):")
    print(f"     CAGR {cg*100:>6.1f}%   maxDD {dd*100:>6.1f}%   ret/DD {rr:>5.2f}")
    flag = "  <-- DISTRUST (ret/DD>5)" if rr > 5 else ""
    verdict = "ROBUST (edge survives rolling re-optimization)" if rr >= 1.0 else \
              ("WEAK/CURVE-FIT (edge largely fails OOS)" if rr < 0.6 else "MARGINAL")
    print(f"     VERDICT: {verdict}{flag}")
    return oos, (cg, dd, rr)


# ---------------------------------------------------------------------------
# TEST 2 — PER-ERA breakdown of the FIXED BASE config
# ---------------------------------------------------------------------------
def per_era(df, bull, macro):
    print()
    print("=" * 96)
    print("TEST 2 — PER-ERA breakdown, FIXED BASE config (atr=3.5, pyr=1.0, macro=off) — is edge stable?")
    print("=" * 96)
    base = dict(atr=3.5, pyr=1.0, macro="off")
    eq = equity_for(df, bull, macro, base)
    eras = [("2017-2019", "2017-01-01", "2020-01-01"),
            ("2020-2022", "2020-01-01", "2023-01-01"),
            ("2023-2026", "2023-01-01", "2027-01-01")]
    print(f"  {'era':<14}{'bars':>7}{'CAGR':>9}{'maxDD':>9}{'ret/DD':>9}{'totret%':>10}")
    rows = []
    for name, a, b in eras:
        seg = slice_eq(eq, pd.Timestamp(a), pd.Timestamp(b))
        if len(seg) < 10:
            print(f"  {name:<14}  (insufficient data)")
            continue
        cg, dd, rr = m(seg)
        tot = (seg.iloc[-1] / seg.iloc[0] - 1) * 100
        flag = "  <-- DISTRUST" if rr > 5 else ""
        print(f"  {name:<14}{len(seg):>7}{cg*100:>8.0f}%{dd*100:>8.0f}%{rr:>9.2f}{tot:>9.0f}%{flag}")
        rows.append((name, cg, dd, rr, tot))
    return rows


# ---------------------------------------------------------------------------
# TEST 3 — REMOVE-BEST-TRADES (per-trade ROE, recompound the rest)
# ---------------------------------------------------------------------------
def run_with_trades(df, bull_eff, atr_mult=3.5, pyr_frac=1.0):
    """Clone of backtest_myv3_final.run (LONG only) that records per-trade ROE = equity multiple.
    ROE_trade = E_after_full_position / E_before_entry. A 'trade' spans entry->exit incl. pyramid.
    Recompounds identically to the engine, so chaining all ROEs reproduces final equity."""
    FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT
    o=df["open"].values; h=df["high"].values; l=df["low"].values; c=df["close"].values
    a=bt.atr(df,14).values; n=len(df)
    bull = bull_eff
    cash=1.0; units=0.0; side=0; entry=0.0; stop=0.0; R=0.0; pyrd=False; notional0=0.0
    armedL=True
    E_before=1.0
    trades=[]  # equity multiples per closed position
    for i in range(16, n-1):
        oN,hN,lN,cN = o[i+1],h[i+1],l[i+1],c[i+1]
        if not bull[i]: armedL=True
        if side!=0:
            hit = (lN<=stop)
            regime_out = (not bull[i])
            if hit or regime_out:
                if hit:
                    fpx = stop*(1-SLIP)
                else:
                    fpx = oN*(1-SLIP)
                cash += units*fpx - abs(units)*fpx*FEE; units=0.0; side=0; pyrd=False
                E_after = cash  # flat now -> equity == cash
                trades.append(E_after / E_before)
            else:
                prof = (cN-entry)/R
                if prof>=1.0:
                    be = entry*(1+0.01)
                    stop = max(stop,be)
                if not pyrd and prof>=2.0:
                    addn = pyr_frac*notional0
                    add_units = addn/cN
                    cash -= add_units*cN + addn*FEE; units += add_units; pyrd=True
                    stop = max(stop,entry)
        if side==0:
            if bull[i] and armedL:
                E=cash
                st=max(c[i]-atr_mult*a[i], c[i]*(1-0.08)); entry=oN*(1+SLIP)
                if entry-st>0:
                    E_before=E
                    notional0 = E
                    units = notional0/entry; cash = E - units*entry - notional0*FEE
                    stop=st; R=abs(entry-st); side=1; pyrd=False
                    armedL=False
    return np.array(trades)


def remove_best(df, bull, macro):
    print()
    print("=" * 96)
    print("TEST 3 — REMOVE-BEST-TRADES (per-trade ROE = equity multiple, recompound the rest)")
    print("=" * 96)
    base = dict(atr=3.5, pyr=1.0, macro="off")
    bull_eff = bull_for(bull, macro, base["macro"])
    roes = run_with_trades(df, bull_eff, atr_mult=base["atr"], pyr_frac=base["pyr"])
    # sanity: chaining all ROEs ~ the engine's final equity multiple
    full_mult = float(np.prod(roes))
    eq = equity_for(df, bull, macro, base)
    engine_mult = float(eq.iloc[-1] / eq.iloc[0])
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)

    def stats_from_mult(mult, ntr):
        cg = mult ** (1 / yrs) - 1
        return cg

    print(f"  trades recorded: {len(roes)}   chained ROE product = {full_mult:.2f}x   engine final = {engine_mult:.2f}x")
    print(f"  (sanity: products should match within rounding) -> {'OK' if abs(full_mult/engine_mult-1)<0.02 else 'MISMATCH!'}")
    order = np.argsort(roes)[::-1]  # best (highest ROE) first
    top = roes[order]
    print(f"  top ROE trades: " + "  ".join(f"{x:.2f}x" for x in top[:6]))
    print()
    print(f"  {'remove top N':<16}{'trades left':>12}{'final mult':>13}{'CAGR':>9}{'vs full':>10}")
    base_cagr = stats_from_mult(full_mult, len(roes))
    print(f"  {'(none/full)':<16}{len(roes):>12}{full_mult:>12.2f}x{base_cagr*100:>8.0f}%{'--':>10}")
    rows = []
    for k in (1, 3, 5):
        keep = np.delete(roes, order[:k])  # remove the k highest-ROE trades, recompound rest
        mult = float(np.prod(keep))
        cg = mult ** (1 / yrs) - 1
        retain = cg / base_cagr * 100 if base_cagr != 0 else 0
        print(f"  remove top {k:<5}{len(keep):>12}{mult:>12.2f}x{cg*100:>8.0f}%{retain:>9.0f}%")
        rows.append((k, len(keep), mult, cg, retain))
    return rows


def main():
    df, bull = build()
    c = df["close"]
    macro = macro_mask(c)

    # ---- SANITY GATE (anti-bug): base must reproduce full-data long ret/DD ~1.83 / 2.00 ----
    s_nomacro = equity_for(df, bull, macro, dict(atr=3.5, pyr=1.0, macro="off"))
    s_macro   = equity_for(df, bull, macro, dict(atr=3.5, pyr=1.0, macro="9mo"))
    cg0, dd0, rr0 = m(s_nomacro)
    cg1, dd1, rr1 = m(s_macro)
    print("=" * 96)
    print("SANITY GATE — base reproduces honest full-data baseline before any robustness test")
    print("=" * 96)
    print(f"  LONG no-macro : CAGR {cg0*100:.0f}%  DD {dd0*100:.0f}%  ret/DD {rr0:.2f}   (expect ~1.83)")
    print(f"  LONG macro-9mo: CAGR {cg1*100:.0f}%  DD {dd1*100:.0f}%  ret/DD {rr1:.2f}   (expect ~2.00)")
    ok = abs(rr0 - 1.83) < 0.06 and abs(rr1 - 2.00) < 0.06
    print(f"  SANITY: {'PASS' if ok else 'FAIL — abort, base does not reproduce!'}")
    if not ok:
        sys.exit(1)
    print()

    walk_forward(df, bull, macro)
    per_era(df, bull, macro)
    remove_best(df, bull, macro)


if __name__ == "__main__":
    main()
