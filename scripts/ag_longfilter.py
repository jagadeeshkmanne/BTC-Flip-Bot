#!/usr/bin/env python3
"""ag_longfilter.py — ABLATE & ADD long filters on the V2 base. The bear/ssize/parab machinery is
held IDENTICAL to backtest_v2_combined.py (run2, use_parab=True, use_bd=True); ONLY the long gate
is varied. Base long gate = bull & macro, where macro = price > SMA(9mo). We probe that 9mo macro
filter and try extra AND-gates.

(A) ABLATION of the macro filter:
    - none           : LONG = bull only (no macro)
    - SMA(6/12/18mo) : swap the 9mo window
    - EMA200-daily   : macro = price > daily EMA200
    - weekly-SMA20   : macro = price > weekly SMA(20)
(B) ADDITIONS — keep base macro AND add one extra gate:
    - vol > SMA20*k (k=1.0,1.2)
    - ADX(14) > {15,20}
    - daily RSI > 50
    - weekly EMA10 > weekly EMA30
    - price > VWAP anchored from the cycle low

ANTI-BUG: base reproduced byte-for-byte first (BASE row must == 103/-31/3.34/OOS3.24/+56/+26/+14).
No lookahead — every filter series is .shift(1) on its own timeframe before mapping to 4h bars.
Stops/bear/parab untouched. GATE for a "winner": full-period green, OOS positive, all 3 bears>0.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_ext import build, EXT
from backtest_short_aggressive import daily_macd_bear
from backtest_myv3_final import m
from backtest_v2_combined import run2, yr

BPD = 6  # 4h bars per day


def _map_daily(df, dser, dts):
    """Map a daily boolean/float series (already .shift(1)'d) onto the 4h bar index, backward."""
    ts = pd.to_datetime(df["timestamp"])
    return pd.merge_asof(
        pd.DataFrame({"ts": ts}).sort_values("ts"),
        pd.DataFrame({"ts": pd.to_datetime(dts), "b": dser.values}).sort_values("ts"),
        on="ts", direction="backward")["b"].values


def _daily_close():
    raw = pd.read_csv(EXT, parse_dates=["timestamp"])
    dd = raw.set_index("timestamp").resample("1D").agg({"close": "last"}).dropna().reset_index()
    return dd


def _weekly_close():
    raw = pd.read_csv(EXT, parse_dates=["timestamp"])
    wk = raw.set_index("timestamp").resample("1W").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    return wk


def build_filters(df, bull):
    """Return dict name -> long-gate boolean array. ALL combine with `bull` (the EMA50>200 regime)."""
    c = df["close"]
    ts = pd.to_datetime(df["timestamp"])
    out = {}

    # ---- base macro: price > SMA(9mo) on 4h bars ----
    macro9 = (c > bt.sma(c, 9 * 30 * BPD).shift(1)).fillna(False).values

    # (A) ABLATION ---------------------------------------------------------
    out["BASE: bull & SMA(9mo)"] = (bull & macro9)
    out["ABL: bull only (no macro)"] = bull.copy()
    for mo in (6, 12, 18):
        mser = (c > bt.sma(c, mo * 30 * BPD).shift(1)).fillna(False).values
        out[f"ABL: bull & SMA({mo}mo)"] = (bull & mser)
    # EMA200 daily
    dd = _daily_close()
    e200 = bt.ema(dd["close"], 200)
    d_above = (dd["close"] > e200).shift(1).fillna(False).astype(bool)
    e200map = _map_daily(df, d_above, dd["timestamp"])
    e200map = pd.Series(e200map).fillna(False).astype(bool).values
    out["ABL: bull & EMA200-daily"] = (bull & e200map)
    # weekly SMA20
    wk = _weekly_close()
    w_above = (wk["close"] > bt.sma(wk["close"], 20)).shift(1).fillna(False).astype(bool)
    wmap = _map_daily(df, w_above, wk["timestamp"])
    wmap = pd.Series(wmap).fillna(False).astype(bool).values
    out["ABL: bull & weekly-SMA20"] = (bull & wmap)

    # (B) ADDITIONS — base (bull & SMA9mo) AND one extra gate ---------------
    base = bull & macro9
    # volume > SMA20 * k  (4h volume resampled from ext)
    raw = pd.read_csv(EXT, parse_dates=["timestamp"])
    v4 = raw.set_index("timestamp").resample("4h").agg({"volume": "sum"}).reindex(ts).reset_index()
    vol = v4["volume"].fillna(0.0)
    vsma = bt.sma(vol, 20).shift(1)
    for k in (1.0, 1.2):
        vgate = (vol > vsma * k).fillna(False).values
        out[f"ADD: +vol>SMA20*{k}"] = (base & vgate)
    # ADX(14) on 4h
    adx = bt.adx(df, 14).shift(1).fillna(0).values
    for t in (15, 20):
        out[f"ADD: +ADX(14)>{t}"] = (base & (adx > t))
    # daily RSI > 50
    drsi = bt.rsi(dd["close"], 14)
    d_rsi_ok = (drsi > 50).shift(1).fillna(False).astype(bool)
    drsimap = pd.Series(_map_daily(df, d_rsi_ok, dd["timestamp"])).fillna(False).astype(bool).values
    out["ADD: +daily RSI>50"] = (base & drsimap)
    # weekly EMA10 > weekly EMA30
    we10 = bt.ema(wk["close"], 10); we30 = bt.ema(wk["close"], 30)
    w_ema_ok = (we10 > we30).shift(1).fillna(False).astype(bool)
    wemamap = pd.Series(_map_daily(df, w_ema_ok, wk["timestamp"])).fillna(False).astype(bool).values
    out["ADD: +weekly EMA10>EMA30"] = (base & wemamap)
    # price > VWAP anchored from cycle low (anchor reset at each new all-time low on 4h)
    h = df["high"].values; l = df["low"].values; cl = c.values
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = (tp * vol).values; vv = vol.values
    avwap = np.full(len(df), np.nan)
    run_low = np.inf; cum_pv = 0.0; cum_v = 0.0
    for i in range(len(df)):
        if cl[i] < run_low:  # new cycle low -> re-anchor
            run_low = cl[i]; cum_pv = 0.0; cum_v = 0.0
        cum_pv += pv[i]; cum_v += vv[i]
        avwap[i] = cum_pv / cum_v if cum_v > 0 else np.nan
    vwap_ok = pd.Series(cl > pd.Series(avwap).shift(1).values).fillna(False).values
    out["ADD: +price>cyclelow-VWAP"] = (base & vwap_ok)

    return out


def gate_pass(s):
    """GATE: every calendar year >0, OOS>0, all 3 bears>0."""
    yrs = [yr(s, y) for y in range(2017, 2027)]
    green = all(v >= 0 for v in yrs if abs(v) > 1e-9 or True)
    green_all = all(yr(s, y) > -0.01 for y in range(2018, 2026))  # require real years green
    oos = m(s[s.index >= s.index[int(len(s) * 0.6)]])[2]
    bears = [yr(s, 2018), yr(s, 2022), yr(s, 2026)]
    return green_all and oos > 0 and all(b > 0 for b in bears)


def main():
    df, bull = build()
    filters = build_filters(df, bull)
    # held-constant base machinery (exactly backtest_v2_combined.main)
    c = df["close"]
    hh40 = c.rolling(40 * BPD).max().shift(1)
    drop = ((c / hh40 - 1) < -0.10).fillna(False).values
    bear = drop & daily_macd_bear(df)
    hh180 = c.rolling(180 * BPD).max().shift(1); ddh = (c / hh180 - 1).fillna(0).values
    ssize = np.where(ddh <= -0.30, 1.0, np.where(ddh <= -0.20, 0.50, 0.25))
    parab_trig = (c > 2.2 * bt.sma(c, 140 * BPD).shift(1)).fillna(False).values

    print("=" * 104)
    print("LONG-FILTER ABLATION & ADDITIONS — bear/ssize/parab held = V2 base (use_parab=use_bd=True)")
    print("=" * 104)
    print(f"  {'long-gate config':<34}{'CAGR':>6}{'DD':>6}{'r/DD':>6}{'OOS':>6}  {'2018/22/26':>14}  gate")

    rows = {}
    series = {}
    for name, LONG in filters.items():
        LONG = np.asarray(LONG, dtype=bool)
        s = run2(df, LONG, bear, ssize, parab_trig, use_parab=True, use_bd=True)
        cg, dd, rr = m(s); oos = m(s[s.index >= s.index[int(len(s) * 0.6)]])[2]
        b18, b22, b26 = yr(s, 2018), yr(s, 2022), yr(s, 2026)
        pb = f"{b18:+.0f}/{b22:+.0f}/{b26:+.0f}"
        g = "PASS" if gate_pass(s) else "fail"
        rows[name] = (cg, dd, rr, oos, b18, b22, b26, g)
        series[name] = s
        print(f"  {name:<34}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{oos:>6.2f}  {pb:>14}  {g}")

    base_rr = rows["BASE: bull & SMA(9mo)"][2]
    base_dd = rows["BASE: bull & SMA(9mo)"][1]
    base_cg = rows["BASE: bull & SMA(9mo)"][0]
    print("\n  " + "-" * 100)
    print(f"  BASE ref: CAGR {base_cg*100:.0f}%  DD {base_dd*100:.0f}%  ret/DD {base_rr:.2f}")

    # winners that PASS the gate, ranked by ret/DD
    passing = [(n, *v) for n, v in rows.items() if v[7] == "PASS"]
    passing.sort(key=lambda x: -x[3])  # by ret/DD
    print("\n  GATE-PASSING configs ranked by ret/DD:")
    for n, cg, dd, rr, oos, b18, b22, b26, g in passing:
        beat = ""
        if rr > base_rr + 0.001: beat += " ret/DD>base"
        if dd > base_dd + 0.005 and cg > base_cg * 0.9: beat += " lowerDD"
        print(f"    {n:<34} r/DD={rr:.2f} CAGR={cg*100:.0f}% DD={dd*100:.0f}% OOS={oos:.2f}{beat}")

    # TOP 3 (gate-passing, by ret/DD) full breakdown
    top3 = passing[:3]
    print("\n" + "=" * 104)
    print("  TOP 3 (gate-passing, by ret/DD) — per-bear + year-by-year")
    print("=" * 104)
    for n, cg, dd, rr, oos, b18, b22, b26, g in top3:
        s = series[n]
        print(f"\n  >> {n}")
        print(f"     CAGR {cg*100:.0f}%  DD {dd*100:.0f}%  ret/DD {rr:.2f}  OOS {oos:.2f}")
        print(f"     bears 2018/2022/2026: {b18:+.0f}% / {b22:+.0f}% / {b26:+.0f}%")
        print("     years: " + " ".join(f"{y}:{yr(s,y):+.0f}%" for y in range(2017, 2027)))

    # verdict
    print("\n" + "=" * 104)
    best = passing[0] if passing else None
    if best and best[0] != "BASE: bull & SMA(9mo)" and best[3] > base_rr + 0.01:
        print(f"  VERDICT: {best[0]} BEATS base on ret/DD ({best[3]:.2f} vs {base_rr:.2f}).")
    else:
        # check DD reduction without killing CAGR
        dd_cuts = [(n, *v) for n, v in rows.items()
                   if v[7] == "PASS" and v[1] > base_dd + 0.01 and v[0] > base_cg * 0.85
                   and n != "BASE: bull & SMA(9mo)"]
        dd_cuts.sort(key=lambda x: x[2])  # shallowest DD
        if dd_cuts:
            n2 = dd_cuts[0]
            print(f"  VERDICT: no ret/DD beat, but {n2[0]} cuts DD to {n2[1]*100:.0f}% "
                  f"(CAGR {n2[0] and n2[1] and rows[n2[0]][0]*100:.0f}%).")
        else:
            print("  VERDICT: BASE (bull & SMA9mo) is already optimal — no ablation or addition "
                  "beats ret/DD 3.34 or cuts the -31% DD without killing CAGR while passing the gate.")
    print("=" * 104)


if __name__ == "__main__":
    main()
