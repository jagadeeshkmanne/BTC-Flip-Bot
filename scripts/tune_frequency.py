#!/usr/bin/env python3
"""tune_frequency.py — can we ADD trades to my-V3 LONG without hurting ret/DD?

my-V3 LONG (BTC 4h) makes only ~22 trades in 6.7yr because of the `armed` flag: after
entering it sets armed=False and only RE-ARMS when regime goes flat (not bull). One trade
per trend leg, ~34-day holds. Tuned-long base: CAGR ~91%, DD -31%, ret/DD ~2.9, OOS ~4.7.

This script copies/extends the tuned LONG run() and tests mechanisms that ADD trades, each
counting #trades, keeping ret/DD >= ~2.9, DD <= ~33%, green every year:
  1. RE-ENTRY within a trend (cooldown K bars OR pullback-and-resume on short EMA reclaim)
  2. FASTER regime EMAs (4h 30/100 + daily 30/100 vs base 50/200)
  3. PYRAMID as more entries (adds at 2R/4R/6R)
  4. ADD a faster parallel sleeve (4h EMA20/50 pullback, sized smaller)

GROUND TRUTH from the grid test: more trades != more profit (491 trades = 6% CAGR vs
22 trades = 89%). Goal is NOT to maximize trade count — it's to find whether ANY freq boost
adds trades WITHOUT degrading ret/DD. Report the tradeoff explicitly.

Honesty: signals on closed bars, fill next open, fee 0.055%+slip 0.05% per side, stops
ratchet up only, no lookahead. Base must reproduce (tuned long CAGR ~91%).
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT


# ------------------------------------------------------------------ regime build
def _daily_gate(dd, fast, slow):
    return (bt.ema(dd["close"], fast) > bt.ema(dd["close"], slow)).shift(1).fillna(False).astype(bool)


def regimes(f_fast=50, f_slow=200, d_fast=50, d_slow=200):
    """Returns (df_4h, bull_bool). Identical construction to my-V3 (merge_asof prior-day gate).
    Default 50/200 == base. Pass 30/100 for the faster-EMA test."""
    raw = pd.read_csv(os.path.join(bt.CACHE, "BTCUSDT_1h_binance_volume.csv"), parse_dates=["timestamp"])
    df = raw.set_index("timestamp").resample("4h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    dd = raw.set_index("timestamp").resample("1D").agg({"close": "last"}).dropna().reset_index()
    d_up = _daily_gate(dd, d_fast, d_slow)
    ts = pd.to_datetime(df["timestamp"])
    dmap = pd.merge_asof(pd.DataFrame({"ts": ts}).sort_values("ts"),
        pd.DataFrame({"ts": pd.to_datetime(dd["timestamp"]), "b": d_up.values}).sort_values("ts"),
        on="ts", direction="backward")["b"].fillna(False).astype(bool).values
    f_up = (bt.ema(df["close"], f_fast) > bt.ema(df["close"], f_slow)).values
    return df, (f_up & dmap)


# ------------------------------------------------------------------ core engine
def run(df, bull, pyr_r=2.0, pyr_frac=1.0, be_r=1.0, atr_mult=3.5, sl_cap=0.08, be_buf=0.01,
        # frequency mechanisms (all OFF by default -> reproduces tuned long base) ----
        reentry_cooldown=None,    # int K bars: after a stop-out, re-arm K bars later if still bull
        reentry_reclaim=None,     # int ema_n: re-arm when close reclaims EMA(n) (pullback-and-resume)
        pyr_levels=None,          # list of R multiples to add at, e.g. [2,4,6]; overrides pyr_r
        fast_size=0.0, fast_fast=20, fast_slow=50,  # parallel faster sleeve (EMA pullback), size frac
        return_trades=False):
    """Honest signed cash/units engine extending the my-V3 tuned LONG run().

    Base behaviour (all freq knobs default/off): enter when bull & armed; armed goes False on
    entry and only back True when regime !bull. Stop = max(c-atr*ATR, c*(1-cap)); BE at +be_r*R
    (entry*(1+be_buf)); pyramid (borrowed) at +pyr_r*R, stop ratchets to entry. Exit: stop hit
    intrabar OR regime flips. Stops only ever ratchet UP (max()).

    Frequency knobs add trades:
      reentry_cooldown=K : after a stop-out while still bull, re-arm K bars later.
      reentry_reclaim=n  : after a stop-out while still bull, re-arm when close>EMA(n).
      pyr_levels=[2,4,6] : multiple pyramid adds (each counted as a layer, same trend).
      fast_size>0        : second parallel sleeve on a 4h EMA(fast/slow) pullback-resume,
                           sized fast_size of equity, own stop, no regime gate beyond bull.
    """
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    a = bt.atr(df, 14).values; n = len(df)
    ema_re = bt.ema(df["close"], reentry_reclaim).values if reentry_reclaim else None
    # faster sleeve signal: pullback to/below EMA(fast) then reclaim, with EMA fast>slow
    if fast_size > 0:
        ef = bt.ema(df["close"], fast_fast).values
        es = bt.ema(df["close"], fast_slow).values
        below = c < ef
        fast_sig = np.zeros(n, bool)
        for i in range(1, n):
            if ef[i] > es[i] and below[i-1] and c[i] > ef[i]:
                fast_sig[i] = True
    else:
        fast_sig = None

    cash = 1.0; units = 0.0; in_pos = False; entry = 0.0; stop = 0.0; R = 0.0
    notional0 = 0.0; pyr_done = 0; armed = True; cooldown = 0; stopped_bull = False
    # fast sleeve state (separate position, separate units accounting via f_units)
    f_units = 0.0; f_in = False; f_entry = 0.0; f_stop = 0.0
    eq = np.ones(n); n_trades = 0; n_pyr = 0; n_fast = 0

    for i in range(16, n - 1):
        oN = o[i+1]; hN = h[i+1]; lN = l[i+1]; cN = c[i+1]

        # re-arm logic ---------------------------------------------------------
        if not bull[i]:
            armed = True; stopped_bull = False; cooldown = 0
        else:
            # still bull: handle re-entry re-arming after a stop-out
            if stopped_bull and not in_pos:
                if reentry_cooldown is not None:
                    cooldown -= 1
                    if cooldown <= 0:
                        armed = True; stopped_bull = False
                elif reentry_reclaim is not None:
                    # re-arm only on a fresh reclaim of the short EMA (pullback-and-resume)
                    if c[i-1] < ema_re[i-1] and c[i] > ema_re[i]:
                        armed = True; stopped_bull = False

        # ---- main position management ----
        if in_pos:
            if lN <= stop:                              # stop hit intrabar
                fpx = stop*(1-SLIP); cash += units*fpx*(1-FEE); units = 0.0
                in_pos = False
                if bull[i]:
                    stopped_bull = True
                    if reentry_cooldown is not None: cooldown = reentry_cooldown
                else:
                    armed = True; stopped_bull = False
            elif not bull[i]:                           # regime flip -> exit next open
                fpx = oN*(1-SLIP); cash += units*fpx*(1-FEE); units = 0.0
                in_pos = False; armed = True; stopped_bull = False
            else:
                prof = (cN-entry)/R if R > 0 else 0
                if prof >= be_r:
                    stop = max(stop, entry*(1+be_buf))  # break-even (ratchet up)
                if pyr_levels is not None:
                    while pyr_done < len(pyr_levels) and prof >= pyr_levels[pyr_done]:
                        addn = pyr_frac*notional0
                        cash -= addn*(1+FEE); units += addn/cN
                        stop = max(stop, entry); pyr_done += 1; n_pyr += 1
                elif pyr_r is not None and pyr_done == 0 and prof >= pyr_r:
                    addn = pyr_frac*notional0
                    cash -= addn*(1+FEE); units += addn/cN
                    stop = max(stop, entry); pyr_done = 1; n_pyr += 1

        # ---- main entry ----
        if not in_pos and bull[i] and armed:
            st = max(c[i]-atr_mult*a[i], c[i]*(1-sl_cap))
            if c[i]-st > 0:
                E = cash; entry = oN*(1+SLIP); R = entry-st; stop = st
                notional0 = E; units = E/entry; cash -= E*(1+FEE)
                in_pos = True; pyr_done = 0; armed = False; stopped_bull = False
                n_trades += 1

        # ---- parallel faster sleeve (independent small position) ----
        if fast_size > 0:
            if f_in:
                if lN <= f_stop:
                    fpx = f_stop*(1-SLIP); cash += f_units*fpx*(1-FEE); f_units = 0.0; f_in = False
                elif not bull[i]:
                    fpx = oN*(1-SLIP); cash += f_units*fpx*(1-FEE); f_units = 0.0; f_in = False
            if not f_in and bull[i] and fast_sig[i]:
                fE = cash*fast_size
                if fE > 0:
                    f_entry = oN*(1+SLIP)
                    fst = max(c[i]-atr_mult*a[i], c[i]*(1-sl_cap))
                    if c[i]-fst > 0:
                        f_stop = fst; f_units = fE/f_entry; cash -= fE*(1+FEE)
                        f_in = True; n_fast += 1

        eq[i+1] = cash + units*cN + f_units*cN

    s = pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]
    if return_trades:
        return s, n_trades, n_pyr, n_fast
    return s


# ------------------------------------------------------------------ metrics
def m(s):
    yrs = max((s.index[-1]-s.index[0]).days/365.25, 1e-9)
    cg = (s.iloc[-1]/s.iloc[0])**(1/yrs)-1 if s.iloc[-1] > 0 else -1
    dd = (s/s.cummax()-1).min()
    return cg, dd, (cg/abs(dd) if dd < -1e-9 else 0)


def yr(s, y):
    seg = s[s.index.year == y]
    return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg) > 20 else None


def green_check(s):
    """Returns (all_green_bool, list of (year, ret%))."""
    rows = []
    allg = True
    for y in sorted(set(s.index.year)):
        r = yr(s, y)
        if r is None:
            continue
        rows.append((y, r))
        if r < 0:
            allg = False
    return allg, rows


def report(df, bull, name, rows, **kw):
    s, nt, npy, nf = run(df, bull, return_trades=True, **kw)
    cg, dd, rr = m(s)
    o = m(s[s.index >= s.index[int(len(s)*0.6)]])
    allg, _ = green_check(s)
    total = nt + npy + nf
    rows.append((name, total, nt, npy, nf, cg*100, dd*100, rr, o[2], "Y" if allg else "N"))
    return s


def main():
    base_df, base_bull = regimes()                       # 50/200 (base)
    f_df, f_bull = regimes(30, 100, 30, 100)             # faster regime EMAs

    print("="*108)
    print("TUNE FREQUENCY — can my-V3 LONG take MORE trades without hurting ret/DD? (BTC 4h, 1x)")
    print("="*108)
    hdr = f"  {'mechanism':<42}{'#tot':>5}{'#ent':>5}{'#pyr':>5}{'#fst':>5}{'CAGR':>7}{'DD':>6}{'r/DD':>6}{'OOS':>7}{'grn':>5}"
    print(hdr); print("  "+"-"*104)

    rows = []
    # 0) BASE — must reproduce tuned long (CAGR ~91%, DD -31%, ret/DD ~2.9, OOS ~4.7)
    base_s = report(base_df, base_bull, "BASE tuned long (armed-flip only)", rows)

    # 1) RE-ENTRY: cooldown of K bars after stop-out while still bull
    for K in (6, 12, 24):
        report(base_df, base_bull, f"1. re-entry cooldown K={K} bars", rows, reentry_cooldown=K)
    # 1b) RE-ENTRY: pullback-and-resume (reclaim short EMA)
    for E in (10, 20):
        report(base_df, base_bull, f"1. re-entry reclaim EMA{E}", rows, reentry_reclaim=E)

    # 2) FASTER regime EMAs (4h 30/100 + daily 30/100)
    report(f_df, f_bull, "2. faster regime EMAs 30/100", rows)
    #    faster EMAs + cooldown re-entry stacked
    report(f_df, f_bull, "2. faster EMAs + cooldown K=12", rows, reentry_cooldown=12)

    # 3) PYRAMID as more entries: multi-layer adds at 2R/4R/6R
    report(base_df, base_bull, "3. pyramid layers [2R]", rows, pyr_levels=[2.0])
    report(base_df, base_bull, "3. pyramid layers [2R,4R]", rows, pyr_levels=[2.0, 4.0])
    report(base_df, base_bull, "3. pyramid layers [2R,4R,6R]", rows, pyr_levels=[2.0, 4.0, 6.0])
    report(base_df, base_bull, "3. pyramid layers [2,4,6] frac0.5", rows, pyr_levels=[2.0, 4.0, 6.0], pyr_frac=0.5)

    # 4) ADD faster parallel sleeve (4h EMA20/50 pullback), sized smaller
    for sz in (0.25, 0.5):
        report(base_df, base_bull, f"4. +fast sleeve EMA20/50 sz={sz}", rows, fast_size=sz)

    for r in rows:
        nm, tot, ne, npy, nf, cg, dd, rr, oos, g = r
        print(f"  {nm:<42}{tot:>5}{ne:>5}{npy:>5}{nf:>5}{cg:>6.0f}%{dd:>5.0f}%{rr:>6.2f}{oos:>7.2f}{g:>5}")

    # pass/fail vs gates: ret/DD >= 2.9, DD <= 33%, green every year, MORE trades than base
    base_tot = rows[0][1]
    print("\n  GATES: ret/DD >= 2.9 AND DD <= 33pct AND green every year AND #trades > base(%d)" % base_tot)
    print("  "+"-"*104)
    winners = []
    for r in rows[1:]:
        nm, tot, ne, npy, nf, cg, dd, rr, oos, g = r
        ok = (rr >= 2.9) and (dd >= -33.0) and (g == "Y") and (tot > base_tot)
        if ok: winners.append(r)
        print(f"  {'PASS' if ok else 'fail':<6}{nm:<42}#tr={tot:<4} r/DD={rr:.2f} DD={dd:.0f}% grn={g}")

    # year-by-year for BASE + best winner (highest ret/DD among passers, else 'none')
    print("\n  YEAR-BY-YEAR — BASE:")
    _, brows = green_check(base_s)
    print("    " + "  ".join(f"{y}:{v:+.0f}%" for y, v in brows))
    if winners:
        best = max(winners, key=lambda x: x[7])
        print(f"\n  BEST PASSING MECHANISM: {best[0]}  (#trades {best[1]} vs base {base_tot}, "
              f"CAGR {best[5]:.0f}%, DD {best[6]:.0f}%, r/DD {best[7]:.2f}, OOS {best[8]:.2f})")
    else:
        print("\n  NO mechanism passed all gates while adding trades. "
              "The low trade count appears intrinsic to the edge.")


if __name__ == "__main__":
    main()
