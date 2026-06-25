#!/usr/bin/env python3
"""ag_pullback.py — LONG PULLBACK-ENTRY search. SHORT is pinned (validated base); we change only
HOW the long ENTERS the bull regime: instead of entering immediately on the first bull bar, wait
for a PULLBACK within the uptrend for a better fill + tighter stop (=> tighter R => bigger pyramid).

VALIDATED BASE (full 2017-2026, reproduced byte-for-byte from ag_shortentry / backtest_myv3_final):
    LONG = bull & (price > SMA(9mo)), entered at next-open when regime first turns bull.
      run(atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0)
    SHORT = drop10 & daily-MACD<signal, short_size=0.40, s_atr=5.0, s_cap=0.15, s_pyr_r=None.
    => CAGR 96%  DD -43%  ret/DD 2.24  OOS 3.05  bears 2018+26/2022+11/2026+8  green every year.

PULLBACK TRIGGERS (entry only fires when, INSIDE a bull regime episode, the price has pulled back
and is resuming up — all on CLOSED bars, daily merges shift(1) => no lookahead):
  emaTouch{20,50} : low dipped to/under EMA{20,50} on a recent bar, then close back above it
                    (touch within last `look` bars AND now close>EMA AND bar is up).
  rsiDip{40,50}   : RSI(14) was < t within last `look` bars, now turned up (rsi>prev & rsi>t-ish).
  higherLow       : first confirmed higher-low after regime turns bull (swing low > prior swing low,
                    then a close above the swing-high pivot => structure resumes up).

The engine SEPARATES entry-trigger from regime-hold: a position is opened only on a pullback bar,
but is HELD/exited by the SAME bull-regime rule as the base (regime-off at next open, or stop).
armedL is consumed on entry and re-armed only when the regime turns off => still ONE position per
bull episode (no re-entry churn). When entry_ok == regime everywhere, it reproduces the base.

MISSED-TREND ACCOUNTING: a "trend" = one contiguous bull-regime episode. If the pullback trigger
never fires before the regime turns back off, that trend is MISSED (we report the miss rate and the
buy&hold-ish return we forfeited on those episodes).

GATE: beat base ret/DD 2.24 on full history AND OOS solid AND positive in all 3 bears (2018/22/26)
AND green every calendar year. Distrust ret/DD>5 / OOS>6. Stops ratchet, no lookahead.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_ext import build
from backtest_myv3_final import run as base_run, m

EXT = os.path.join(bt.CACHE, "BTCUSDT_1h_binance_ext.csv")
BPD = 6  # 4h bars per day

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT
LP = dict(atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0)
SP = dict(short_size=0.40, s_atr=5.0, s_cap=0.15, s_pyr_r=None)


# ============================================================================
# Engine: identical to backtest_myv3_final.run() EXCEPT the long entry trigger
# is gated by a separate `lentry` array (pullback condition). `lbull` is the
# regime used for arming + exit (exactly the base bull). short side untouched.
# When lentry is None (=> treated as all-True within regime) this is the base.
# ============================================================================
def run_pb(df, lbull, lentry, bear, allow_long=True, allow_short=True, lev=1.0,
           short_size=0.40, pyr_r=2.0, pyr_frac=1.0, be_r=1.0, atr_mult=3.5,
           sl_cap=0.12, be_buf=0.01, s_atr=5.0, s_cap=0.15, s_pyr_r=None, trail_k=None):
    o=df["open"].values; h=df["high"].values; l=df["low"].values; c=df["close"].values
    a=bt.atr(df,14).values; n=len(df)
    if lentry is None:
        lentry = lbull
    cash=1.0; units=0.0; side=0; entry=0.0; stop=0.0; R=0.0; pyrd=False; notional0=0.0
    peakH=0.0; armedL=True; armedS=True
    eq=np.ones(n)
    for i in range(16, n-1):
        oN,hN,lN,cN = o[i+1],h[i+1],l[i+1],c[i+1]
        if not lbull[i]: armedL=True
        if not bear[i]: armedS=True
        if side!=0:
            if side==1 and trail_k is not None:
                peakH=max(peakH,h[i]); stop=max(stop, peakH - trail_k*a[i])
            hit = (lN<=stop) if side==1 else (hN>=stop)
            regime_out = (not lbull[i]) if side==1 else (not bear[i])
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
                pr = s_pyr_r if side==-1 else pyr_r
                if pr is not None and not pyrd and prof>=pr:
                    addn = pyr_frac*notional0
                    add_units = (addn/cN) if side==1 else (-addn/cN)
                    cash -= add_units*cN + addn*FEE; units += add_units; pyrd=True
                    stop = max(stop,entry) if side==1 else min(stop,entry)
        if side==0:
            go=0
            # LONG enters only on a pullback bar (lentry) inside the bull regime (lbull)
            if allow_long and lbull[i] and armedL and lentry[i]: go=1
            elif allow_short and bear[i] and armedS: go=-1
            if go!=0:
                E=cash
                if go==1:
                    st=max(c[i]-atr_mult*a[i], c[i]*(1-sl_cap)); entry=oN*(1+SLIP); sz=lev
                else:
                    st=min(c[i]+s_atr*a[i], c[i]*(1+s_cap)); entry=oN*(1-SLIP); sz=lev*short_size
                ok = (entry-st>0) if go==1 else (st-entry>0)
                if ok:
                    notional0 = sz*E
                    units = go*notional0/entry; cash = E - units*entry - notional0*FEE
                    stop=st; R=abs(entry-st); side=go; pyrd=False; peakH=entry
                    if go==1: armedL=False
                    else: armedS=False
        eq[i+1] = cash + units*cN
    return pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]


# ---------- daily merge (no lookahead) ----------
def _merge_to_4h(df, dts, vals):
    ts = pd.to_datetime(df["timestamp"])
    out = pd.merge_asof(
        pd.DataFrame({"ts": ts}).sort_values("ts"),
        pd.DataFrame({"ts": pd.to_datetime(dts), "b": np.asarray(vals)}).sort_values("ts"),
        on="ts", direction="backward")["b"]
    return out.fillna(False).to_numpy().astype(bool)


# ---------- pullback entry triggers ----------
def pullback_triggers(df, look=8):
    """Dict of boolean 4h entry-trigger arrays (pullback then resume). All lookahead-safe:
    every component uses values known by the close of bar i (shift where needed)."""
    c = df["close"]; lo = df["low"]; hi = df["high"]
    sig = {}

    for span in (20, 50):
        e = bt.ema(c, span)
        # 'touched' = low dipped at/under EMA on this closed bar
        touched = (lo <= e)
        # touched within the last `look` bars (inclusive of bar i)
        touched_recent = touched.rolling(look).max().fillna(0).astype(bool)
        # now (bar i, closed) reclaiming: close above EMA AND bar is up
        reclaim = (c > e) & (c > c.shift(1))
        sig[f"emaTouch{span}"] = (touched_recent & reclaim).fillna(False).to_numpy()

    r = bt.rsi(c, 14)
    for t in (40, 50):
        was_low = (r < t).rolling(look).max().fillna(0).astype(bool)   # dipped below t recently
        turning = (r > r.shift(1)) & (r > t - 5)                       # momentum turning back up
        sig[f"rsiDip{t}"] = (was_low & turning).fillna(False).to_numpy()

    # first confirmed higher-low: swing pivots on a small window, then a close above the
    # most recent swing-high pivot (structure resumes up) with the prior swing-low higher.
    w = 3  # pivot half-window
    n = len(c)
    cl = c.to_numpy(); lov = lo.to_numpy(); hiv = hi.to_numpy()
    swing_lo = np.full(n, np.nan); swing_hi = np.full(n, np.nan)
    for i in range(w, n - w):
        if lov[i] == lov[i-w:i+w+1].min(): swing_lo[i] = lov[i]
        if hiv[i] == hiv[i-w:i+w+1].max(): swing_hi[i] = hiv[i]
    # forward-fill last confirmed pivot, but only AFTER it is confirmed (i+w bars later) => no peek
    last_lo = np.nan; prev_lo = np.nan; last_hi = np.nan
    last_lo_arr = np.full(n, np.nan); prev_lo_arr = np.full(n, np.nan); last_hi_arr = np.full(n, np.nan)
    for i in range(n):
        j = i - w  # pivot at j is confirmable now (needs w bars to its right)
        if j >= 0 and not np.isnan(swing_lo[j]):
            prev_lo = last_lo; last_lo = swing_lo[j]
        if j >= 0 and not np.isnan(swing_hi[j]):
            last_hi = swing_hi[j]
        last_lo_arr[i] = last_lo; prev_lo_arr[i] = prev_lo; last_hi_arr[i] = last_hi
    higher_low = (last_lo_arr > prev_lo_arr)
    breakout = cl > last_hi_arr  # close reclaims the last swing high
    sig["higherLow"] = (np.nan_to_num(higher_low, nan=0).astype(bool)
                        & np.nan_to_num(breakout, nan=0).astype(bool))
    return sig


# ---------- short gate (pinned base) ----------
def base_short(df):
    raw = pd.read_csv(EXT, parse_dates=["timestamp"])
    c = df["close"]
    hh40 = c.rolling(40 * BPD).max().shift(1)
    drop10 = ((c / hh40 - 1) < -0.10).fillna(False).to_numpy()
    dd = raw.set_index("timestamp").resample("1D").agg({"close": "last"}).dropna().reset_index()
    dc = dd["close"]; ml = bt.ema(dc, 12) - bt.ema(dc, 26); ms = bt.ema(ml, 9)
    macd = _merge_to_4h(df, dd["timestamp"], (ml < ms).shift(1).fillna(False).to_numpy())
    return drop10 & macd


# ---------- metrics ----------
def yr(s, y):
    seg = s[s.index.year == y]
    return (seg.iloc[-1] / seg.iloc[0] - 1) * 100 if len(seg) > 20 else 0.0


def trend_episodes(lbull):
    """List of (start_idx, end_idx_exclusive) for each contiguous bull regime episode."""
    eps = []; i = 0; n = len(lbull)
    while i < n:
        if lbull[i]:
            s = i
            while i < n and lbull[i]: i += 1
            eps.append((s, i))
        else:
            i += 1
    return eps


def missed_trends(df, lbull, lentry):
    """Count bull episodes where the pullback trigger never fired (within engine range 16..n-2),
    and the simple close-to-close return of those missed episodes (return forfeited)."""
    c = df["close"].to_numpy(); n = len(df)
    eps = trend_episodes(np.asarray(lbull))
    total = 0; missed = 0; missed_rets = []; taken_rets = []
    for s, e in eps:
        s2 = max(s, 16); e2 = min(e, n - 1)
        if s2 >= e2: continue
        total += 1
        fired = bool(np.asarray(lentry)[s2:e2].any())
        ret = c[e2 - 1] / c[s2] - 1.0  # close at regime entry -> close at regime exit
        if fired: taken_rets.append(ret)
        else:
            missed += 1; missed_rets.append(ret)
    return dict(total=total, missed=missed,
                miss_rate=(missed / total if total else 0.0),
                missed_avg_ret=(float(np.mean(missed_rets)) if missed_rets else 0.0),
                missed_pos=(sum(1 for r in missed_rets if r > 0)),
                taken_avg_ret=(float(np.mean(taken_rets)) if taken_rets else 0.0))


def evaluate(df, lbull, lentry, bear):
    sC = run_pb(df, lbull, lentry, bear, allow_long=True, allow_short=True, **LP, **SP)
    cg, dd, rr = m(sC)
    o = m(sC[sC.index >= sC.index[int(len(sC) * 0.6)]])
    years = {y: yr(sC, y) for y in range(2017, 2027)}
    bears = (years[2018], years[2022], years[2026])
    allgreen = all(v >= 0 for v in years.values())
    mt = missed_trends(df, lbull, lentry if lentry is not None else lbull)
    return dict(cg=cg, dd=dd, rr=rr, oos=o[2], bears=bears, allgreen=allgreen,
                years=years, sC=sC, mt=mt)


def gate(res, base_rr=2.24):
    return (res["rr"] > base_rr and all(b > 0 for b in res["bears"])
            and res["allgreen"] and res["oos"] >= 2.8)


def prow(name, r, base_rr):
    pb = "%+.0f/%+.0f/%+.0f" % r["bears"]
    mt = r["mt"]
    flag = ""
    if gate(r) and r["rr"] > base_rr: flag = " <== WINNER"
    elif r["rr"] > base_rr: flag = " <beats r/DD"
    miss = f"{mt['missed']}/{mt['total']}"
    print(f"  {name:<24}{r['cg']*100:>4.0f}%{r['dd']*100:>5.0f}%{r['rr']:>6.2f}{r['oos']:>6.2f}  "
          f"{pb:>14}  miss {miss:>7} ({mt['miss_rate']*100:>3.0f}%)  {'Y' if r['allgreen'] else 'n'}{flag}")


def main():
    df, bull = build()
    c = df["close"]
    macro = (c > bt.sma(c, 9 * 30 * 6).shift(1)).fillna(False).to_numpy()
    lbull = bull & macro
    bear = base_short(df)

    print("=" * 100)
    print("LONG PULLBACK-ENTRY SEARCH — short pinned (drop10 & MACD), long enters on a pullback. BTC 4h 2017-2026")
    print("=" * 100)

    # ---- SANITY: lentry == lbull must reproduce the base byte-for-byte ----
    s_base = base_run(df, lbull, bear, allow_long=True, allow_short=True, **LP, **SP)
    s_repro = run_pb(df, lbull, None, bear, allow_long=True, allow_short=True, **LP, **SP)
    same = np.allclose(s_base.values, s_repro.values, rtol=1e-9, atol=1e-12)
    base = evaluate(df, lbull, None, bear)
    print(f"  [sanity] run_pb(lentry=regime) reproduces base byte-for-byte: {'YES' if same else 'NO !!!'}")
    print(f"  {'config':<24}{'CAGR':>5}{'DD':>6}{'r/DD':>6}{'OOS':>6}  {'2018/22/26':>14}  {'missed trends':>16}  green")
    print("  " + "-" * 96)
    prow(">>> BASE (immediate)", base, base["rr"])
    print("  " + "-" * 96)

    # ---- pullback triggers ----
    results = []
    for look in (6, 8, 12):
        sig = pullback_triggers(df, look=look)
        for name, arr in sig.items():
            r = evaluate(df, lbull, arr, bear)
            results.append((f"{name} L{look}", r))
            prow(f"{name} L{look}", r, base["rr"])

    # ---- rank: passes gate first, then ret/DD (distrust absurd ratios) ----
    def keyf(nr):
        _, r = nr
        return (1 if gate(r) else 0, r["rr"])
    ranked = sorted(results, key=keyf, reverse=True)

    print("\n" + "=" * 100)
    print("TOP 3 PULLBACK-ENTRY CONFIGS (ranked: passes robustness gate, then ret/DD)")
    print("=" * 100)
    for rank, (name, r) in enumerate(ranked[:3], 1):
        mt = r["mt"]
        pb = "%+.1f / %+.1f / %+.1f" % r["bears"]
        print(f"\n  #{rank}  {name}")
        print(f"      CAGR {r['cg']*100:.1f}%   maxDD {r['dd']*100:.1f}%   ret/DD {r['rr']:.2f}   OOS ret/DD {r['oos']:.2f}")
        print(f"      bears 2018/2022/2026: {pb}      green-every-year: {'YES' if r['allgreen'] else 'NO'}")
        print(f"      missed trends: {mt['missed']}/{mt['total']} ({mt['miss_rate']*100:.0f}%)  "
              f"| avg ret of MISSED episodes {mt['missed_avg_ret']*100:+.0f}% "
              f"({mt['missed_pos']} were winners)  | avg ret of TAKEN {mt['taken_avg_ret']*100:+.0f}%")
        print(f"      passes gate: {'YES' if gate(r) else 'NO'}     beats BASE ret/DD 2.24: {'YES' if r['rr'] > base['rr'] else 'NO'}")

    # ---- year-by-year: base + top config ----
    wname, wr = ranked[0]
    print("\n" + "=" * 100)
    print(f"YEAR-BY-YEAR (return %) — BASE vs best pullback ({wname})")
    print("=" * 100)
    print(f"    {'year':<6}{'BASE':>10}{'  '+wname:>16}")
    for y in range(2017, 2027):
        print(f"    {y:<6}{base['years'][y]:>9.1f}%{wr['years'][y]:>14.1f}%")

    # ---- verdict ----
    print("\n" + "=" * 100)
    print("VERDICT")
    print("=" * 100)
    beats = [nr for nr in results if gate(nr[1]) and nr[1]["rr"] > base["rr"]]
    if beats:
        print(f"  {len(beats)} pullback trigger(s) pass the FULL gate AND beat base ret/DD 2.24:")
        for nm, r in sorted(beats, key=lambda x: x[1]["rr"], reverse=True):
            print(f"    {nm:<16} ret/DD {r['rr']:.2f}  OOS {r['oos']:.2f}  miss {r['mt']['miss_rate']*100:.0f}%")
        print("  => PULLBACK ENTRY BEATS THE BASE.")
    else:
        br = sorted(results, key=lambda nr: nr[1]["rr"], reverse=True)[0]
        print("  NO pullback trigger both passes the full robustness gate AND beats base ret/DD 2.24.")
        print(f"  Best raw ret/DD: {br[0]} = {br[1]['rr']:.2f} (base 2.24); "
              f"gate={'pass' if gate(br[1]) else 'FAIL'}; miss {br[1]['mt']['miss_rate']*100:.0f}%.")
        print("  => Immediate entry remains best; waiting for a pullback misses too much trend.")


if __name__ == "__main__":
    main()
