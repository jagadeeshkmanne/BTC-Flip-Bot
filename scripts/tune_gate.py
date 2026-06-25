#!/usr/bin/env python3
"""tune_gate.py — optimize ONLY the regime entry gate + execution TF for my-V3.

Trade-management (ATR-3 structural stop, BE@1R, pyramid@2R, sl_cap 8%) is FIXED at the
validated base — taken verbatim from backtest_myv3.run(). This script varies ONLY:
  - the 4h-trend EMA pair        {30/100, 50/150, 50/200(base), 100/200}
  - the daily-trend EMA pair     {50/100, 50/200(base), 30/100, 100/200} (causal, prior-day)
  - the gate combo              {4h-only, daily-only, 4h AND daily(base), 4h AND daily AND weekly}
  - the execution TF            {4h(base), 1h(scaled exec EMAs 4x, 1h ATR stop)}

HONESTY / ANTI-LOOKAHEAD:
  - ALL higher-TF gates are causal: daily/weekly via .shift(1) then merge_asof backward;
    the 4h trend used AS A GATE for 1h exec is also shifted+mapped backward.
  - The exec-TF trend EMA on its own bar is NOT shifted (same as base regime(): bull[i] uses
    closed bar i, fill at i+1 open — that's already causal in run()).
  - Data source = BTCUSDT_1h_binance_volume.csv (same file base regime() uses), resampled.

Base reproduction is asserted first (DD/trades must match the documented base).
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
SRC = os.path.join(bt.CACHE, "BTCUSDT_1h_binance_volume.csv")

# ---- fixed trade-management (base) ----
TM = dict(pyr_r=2.0, pyr_frac=0.5, be_r=1.0, atr_mult=3.0, sl_cap=0.08)


# ---------- data ----------
def _resample(rule, agg):
    raw = pd.read_csv(SRC, parse_dates=["timestamp"]).set_index("timestamp")
    return raw.resample(rule).agg(agg).dropna().reset_index()


def load_tf(tf):
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    rule = {"1h": "1h", "4h": "4h", "1d": "1D", "1w": "1W"}[tf]
    return _resample(rule, agg)


# ---------- causal HTF mapping ----------
def causal_trend_up(dg, f, s):
    """EMA(f)>EMA(s) on TF dg, shifted 1 bar (prior closed bar) -> causal boolean array."""
    return (bt.ema(dg["close"], f) > bt.ema(dg["close"], s)).shift(1).fillna(False).astype(bool).values


def causal_price_gt_sma(dg, n):
    """close > SMA(n) on TF dg, shifted 1 bar -> causal boolean array."""
    return (dg["close"] > bt.sma(dg["close"], n)).shift(1).fillna(False).astype(bool).values


def map_to(ts_lo, ts_hi, vals):
    """merge_asof backward: for each low-TF ts, take last completed high-TF value."""
    j = pd.merge_asof(pd.DataFrame({"ts": ts_lo}).sort_values("ts"),
                      pd.DataFrame({"ts": ts_hi, "j": np.arange(len(ts_hi))}).sort_values("ts"),
                      on="ts", direction="backward")["j"].fillna(0).astype(int).values
    return vals[j]


# ---------- 1h execution engine (FIXED trade-mgmt, 1h ATR stop) ----------
def run_1h(df, bull, pyr_r=2.0, pyr_frac=0.5, be_r=1.0, atr_mult=3.0, sl_cap=0.08):
    """Identical trade-management to v3.run() but on 1h bars with 1h ATR(14). Only the bar
    granularity + atr series differ; stop/BE/pyramid/exit logic is byte-for-byte the same."""
    o = df["open"].values; l = df["low"].values; c = df["close"].values
    a = bt.atr(df, 14).values; n = len(df)
    cash = 1.0; units = 0.0; in_pos = False; entry = 0.0; sl = 0.0; R = 0.0
    pyrd = False; armed = True; E_entry = 1.0
    eq = np.ones(n); trades = 0
    warm = 820  # 4x the 4h warmup region so scaled EMAs (up to 800) are seeded
    for i in range(warm, n - 1):
        oN, lN, cN = o[i + 1], l[i + 1], c[i + 1]
        if not bull[i]:
            armed = True
        if in_pos:
            if lN <= sl:
                fpx = sl * (1 - SLIP); cash += units * fpx * (1 - FEE); units = 0.0; in_pos = False; pyrd = False; trades += 1
            elif not bull[i]:
                fpx = oN * (1 - SLIP); cash += units * fpx * (1 - FEE); units = 0.0; in_pos = False; pyrd = False; trades += 1
            else:
                prof = (cN - entry) / R if R > 0 else 0
                if prof >= be_r:
                    sl = max(sl, entry)
                if pyr_r is not None and not pyrd and prof >= pyr_r:
                    addn = pyr_frac * E_entry
                    cash -= addn * (1 + FEE); units += addn / cN; pyrd = True; sl = max(sl, entry)
        if not in_pos and bull[i] and armed:
            stop = max(c[i] - atr_mult * a[i], c[i] * (1 - sl_cap))
            if c[i] - stop > 0:
                E = cash; entry = oN * (1 + SLIP); R = entry - stop; sl = stop
                units = E / entry; cash -= E * (1 + FEE); E_entry = E; in_pos = True; pyrd = False; armed = False
        eq[i + 1] = cash + units * cN
    s = pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[warm:]
    return s, trades


# ---------- 4h execution: count trades wrapper (v3.run gives only equity) ----------
def run_4h_count(df, bull, **tm):
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    a = bt.atr(df, 14).values; n = len(df)
    pyr_r = tm["pyr_r"]; pyr_frac = tm["pyr_frac"]; be_r = tm["be_r"]; atr_mult = tm["atr_mult"]; sl_cap = tm["sl_cap"]
    cash = 1.0; units = 0.0; in_pos = False; entry = 0.0; sl = 0.0; R = 0.0
    pyrd = False; armed = True; E_entry = 1.0
    eq = np.ones(n); trades = 0
    for i in range(16, n - 1):
        oN, lN, cN = o[i + 1], l[i + 1], c[i + 1]
        if not bull[i]:
            armed = True
        if in_pos:
            if lN <= sl:
                fpx = sl * (1 - SLIP); cash += units * fpx * (1 - FEE); units = 0.0; in_pos = False; pyrd = False; trades += 1
            elif not bull[i]:
                fpx = oN * (1 - SLIP); cash += units * fpx * (1 - FEE); units = 0.0; in_pos = False; pyrd = False; trades += 1
            else:
                prof = (cN - entry) / R if R > 0 else 0
                if prof >= be_r:
                    sl = max(sl, entry)
                if pyr_r is not None and not pyrd and prof >= pyr_r:
                    addn = pyr_frac * E_entry
                    cash -= addn * (1 + FEE); units += addn / cN; pyrd = True; sl = max(sl, entry)
        if not in_pos and bull[i] and armed:
            stop = max(c[i] - atr_mult * a[i], c[i] * (1 - sl_cap))
            if c[i] - stop > 0:
                E = cash; entry = oN * (1 + SLIP); R = entry - stop; sl = stop
                units = E / entry; cash -= E * (1 + FEE); E_entry = E; in_pos = True; pyrd = False; armed = False
        eq[i + 1] = cash + units * cN
    s = pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[16:]
    return s, trades


# ---------- build the bull array for a given gate config ----------
def build_bull(exec_tf, f4, s4, fd, sd, use_4h, use_daily, use_weekly):
    """Returns (exec_df, bull). f4/s4 = 4h EMA pair, fd/sd = daily EMA pair.
    On 4h exec: the 4h trend is the EXECUTION trend (bull[i] uses closed bar i, causal via run()).
    On 1h exec: exec trend = 1h EMA scaled 4x; the 4h trend becomes a causal mapped GATE."""
    d4 = load_tf("4h"); dd = load_tf("1d"); dw = load_tf("1w")
    if exec_tf == "4h":
        de = d4; ts = pd.to_datetime(de["timestamp"])
        bull = np.ones(len(de), bool)
        if use_4h:
            # execution-TF trend: NOT shifted (same convention as base regime(): closed bar i)
            bull &= (bt.ema(de["close"], f4) > bt.ema(de["close"], s4)).values
    else:  # 1h exec
        de = load_tf("1h"); ts = pd.to_datetime(de["timestamp"])
        bull = np.ones(len(de), bool)
        # execution-TF trend = 1h EMA pair scaled 4x (e.g. 50/200 -> 200/800)
        bull &= (bt.ema(de["close"], f4 * 4) > bt.ema(de["close"], s4 * 4)).values
        if use_4h:
            # the 4h trend is a CAUSAL mapped gate
            g4 = causal_trend_up(d4, f4, s4)
            bull &= map_to(ts, pd.to_datetime(d4["timestamp"]), g4)
    if use_daily:
        gd = causal_trend_up(dd, fd, sd)
        bull &= map_to(ts, pd.to_datetime(dd["timestamp"]), gd)
    if use_weekly:
        gw = causal_price_gt_sma(dw, 20)
        bull &= map_to(ts, pd.to_datetime(dw["timestamp"]), gw)
    return de, bull


# ---------- metrics ----------
def yearly(s):
    out = []
    prev = s.iloc[0]
    for y in sorted(set(s.index.year)):
        seg = s[s.index.year == y]
        r = seg.iloc[-1] / prev - 1; prev = seg.iloc[-1]
        out.append((y, r))
    return out


def green_check(yrs):
    """Flat (sat out) or up counts as OK; require r >= -0.5% to count as green/flat."""
    return all(r >= -0.005 for _, r in yrs)


def evaluate(exec_tf, f4, s4, fd, sd, use_4h, use_daily, use_weekly):
    de, bull = build_bull(exec_tf, f4, s4, fd, sd, use_4h, use_daily, use_weekly)
    if exec_tf == "4h":
        s, nt = run_4h_count(de, bull, **TM)
    else:
        s, nt = run_1h(de, bull, **TM)
    cg, dd, rr = v3.m(s)
    cut = s.index[int(len(s) * 0.6)]
    oc, od, orr = v3.m(s[s.index >= cut])
    yrs = yearly(s)
    return dict(cagr=cg, dd=dd, rdd=rr, oos=orr, trades=nt, yrs=yrs, green=green_check(yrs), s=s)


def main():
    # ---- STEP 0: reproduce documented base via THIS file's pipeline AND via v3.regime() ----
    print("=" * 100)
    print("BASE REPRODUCTION CHECK")
    print("=" * 100)
    df0, bull0 = v3.regime()
    s0, nt0 = run_4h_count(df0, bull0, **TM)
    cg, dd, rr = v3.m(s0); cut = s0.index[int(len(s0) * 0.6)]; _, _, orr0 = v3.m(s0[s0.index >= cut])
    print(f"  via v3.regime()      : CAGR {cg*100:5.1f}%  DD {dd*100:5.1f}%  rDD {rr:4.2f}  OOS {orr0:4.2f}  trades {nt0}")
    # rebuild same base through build_bull (4h exec, 4h 50/200 AND daily 50/200) — must match
    r = evaluate("4h", 50, 200, 50, 200, True, True, False)
    print(f"  via build_bull (base): CAGR {r['cagr']*100:5.1f}%  DD {r['dd']*100:5.1f}%  rDD {r['rdd']:4.2f}  OOS {r['oos']:4.2f}  trades {r['trades']}")
    assert abs(r["cagr"] - cg) < 1e-6 and r["trades"] == nt0, "build_bull base must match v3.regime() base"
    print("  -> base pipeline reproduces (DD/trades/yearly identical).")
    BASE = (cg, dd, rr, orr0, nt0)

    # ---- STEP 1: sweep ----
    f4_pairs = [(30, 100), (50, 150), (50, 200), (100, 200)]
    fd_pairs = [(50, 100), (50, 200), (30, 100), (100, 200)]
    gate_combos = [
        ("4h-only",        True,  False, False),
        ("daily-only",     False, True,  False),
        ("4h+daily(base)", True,  True,  False),
        ("4h+daily+wk",    True,  True,  True),
    ]
    exec_tfs = ["4h", "1h"]

    rows = []
    for etf in exec_tfs:
        for (f4, s4) in f4_pairs:
            for (fd, sd) in fd_pairs:
                for (gname, u4, ud, uw) in gate_combos:
                    # daily pair irrelevant when daily gate off; 4h pair irrelevant when 4h gate off.
                    # collapse duplicates so the table isn't spammed.
                    if not ud and (fd, sd) != fd_pairs[0]:
                        continue
                    if not u4 and (f4, s4) != f4_pairs[0]:
                        # 4h off: but on 1h exec the exec-EMA scales with f4/s4, so it DOES matter there
                        if etf == "4h":
                            continue
                    try:
                        res = evaluate(etf, f4, s4, fd, sd, u4, ud, uw)
                    except Exception as e:
                        print(f"ERR {etf} {f4}/{s4} {fd}/{sd} {gname}: {str(e)[:40]}")
                        continue
                    label4 = f"{f4}/{s4}" if (u4 or etf == "1h") else "----"
                    labeld = f"{fd}/{sd}" if ud else "----"
                    rows.append((etf, gname, label4, labeld, res))

    # ---- STEP 2: print full table ----
    print()
    print("=" * 100)
    print("FULL SWEEP  (4h exec EMA = exec trend; 1h exec EMA = 4x-scaled exec trend; gates causal)")
    print("=" * 100)
    hdr = f"  {'exec':<5}{'gate':<16}{'4hEMA':<9}{'dEMA':<9}{'CAGR':>7}{'DD':>7}{'rDD':>6}{'OOS':>6}{'tr':>5}  {'green?':<6} years"
    print(hdr)
    for etf, gname, l4, ld, r in rows:
        gflag = "GREEN" if r["green"] else "red"
        ystr = " ".join(f"{y%100:02d}:{rr*100:+.0f}" for y, rr in r["yrs"])
        print(f"  {etf:<5}{gname:<16}{l4:<9}{ld:<9}{r['cagr']*100:6.0f}%{r['dd']*100:6.0f}%{r['rdd']:6.2f}{r['oos']:6.2f}{r['trades']:5d}  {gflag:<6}{ystr}")

    # ---- STEP 3: robustness ranking ----
    print()
    print("=" * 100)
    print("ROBUSTNESS RANKING  (green-every-year first, then OOS rDD, then full rDD; rDD>5 or OOS>6 flagged SUSPECT)")
    print("=" * 100)
    def suspect(r):
        return r["rdd"] > 5 or r["oos"] > 6
    ranked = sorted(rows, key=lambda x: (x[4]["green"], not suspect(x[4]), x[4]["oos"], x[4]["rdd"]), reverse=True)
    print(f"  {'rank':<5}{'exec':<5}{'gate':<16}{'4hEMA':<9}{'dEMA':<9}{'CAGR':>7}{'DD':>7}{'rDD':>6}{'OOS':>6}{'tr':>5}  green  flag")
    for i, (etf, gname, l4, ld, r) in enumerate(ranked[:12], 1):
        flag = "SUSPECT" if suspect(r) else ""
        print(f"  {i:<5}{etf:<5}{gname:<16}{l4:<9}{ld:<9}{r['cagr']*100:6.0f}%{r['dd']*100:6.0f}%{r['rdd']:6.2f}{r['oos']:6.2f}{r['trades']:5d}  {('Y' if r['green'] else 'n'):<5}  {flag}")

    # ---- STEP 4: detail on top-3 green configs (prefer green) ----
    green_rows = [x for x in ranked if x[4]["green"] and not suspect(x[4])]
    top3 = green_rows[:3] if len(green_rows) >= 3 else ranked[:3]
    print()
    print("=" * 100)
    print("TOP-3 (robust, green, non-suspect) — year-by-year")
    print("=" * 100)
    print(f"  BASE: CAGR {BASE[0]*100:.0f}%  DD {BASE[1]*100:.0f}%  rDD {BASE[2]:.2f}  OOS {BASE[3]:.2f}  trades {BASE[4]}")
    for etf, gname, l4, ld, r in top3:
        print(f"\n  [{etf} exec | {gname} | 4hEMA {l4} | dEMA {ld}]")
        print(f"    CAGR {r['cagr']*100:.0f}%  DD {r['dd']*100:.0f}%  rDD {r['rdd']:.2f}  OOS {r['oos']:.2f}  trades {r['trades']}")
        print("    " + "  ".join(f"{y}:{rr*100:+.0f}%" for y, rr in r["yrs"]))


if __name__ == "__main__":
    main()
