#!/usr/bin/env python3
"""ag_shortconfirm.py — SWAP/ABLATE the SHORT momentum confirm in V2.

V2 SHORT gate = (price down >10% from 40d high) AND (daily MACD line < signal).
This script keeps the drop>10%/40d trigger AND the bear-depth size + parabolic-long
fixed (built identically to backtest_v2_combined.py main()), and varies ONLY the
momentum confirm that ANDs with the drop:

  (A) ABLATE:  drop-only, no confirm.
  (B) SWAP :   daily RSI<{40,45,50}, ADX(14)>20 & -DI>+DI, daily EMA50<EMA200,
               Bollinger(20,2) lower-band break, ROC<0, OBV falling, price<VWAP.
  (C) COMBINE: drop% AND two confirms.

ANTI-BUG: base (drop10 & dailyMACD<sig) is reproduced byte-for-byte FIRST and asserted
against the known V2 numbers (CAGR 103 / DD -31 / r/DD 3.34 / OOS 3.24 / bears +56/+26/+14).
All daily-derived confirms use .shift(1) on the DAILY series + merge_asof(direction=backward)
onto the 4h bars — exactly how daily_macd_bear() avoids lookahead. Confirms computed on the
4h close (BB/ROC/OBV/VWAP/RSI/ADX-4h) are shifted by 1 4h-bar.

GATE for a "winner": positive over full 2017-2026 AND OOS positive AND ALL 3 bears
(2018/2022/2026) positive AND green every calendar year. Distrust r/DD>5 or OOS>6.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_ext import build
from backtest_short_aggressive import daily_macd_bear
from backtest_myv3_final import m
from backtest_v2_combined import run2, yr

EXT = os.path.join(bt.CACHE, "BTCUSDT_1h_binance_ext.csv")
BPD = 6  # 4h bars per day


# ----- daily helper: compute a boolean daily condition, shift1, map backward to 4h -----
def _daily_close():
    raw = pd.read_csv(EXT, parse_dates=["timestamp"])
    dd = raw.set_index("timestamp").resample("1D").agg({"close": "last"}).dropna().reset_index()
    return dd


def _map_daily_bool(df, dd, cond_bool):
    """cond_bool: bool Series aligned to dd (daily). Shift by 1 day, map backward onto df 4h bars."""
    b = pd.Series(np.asarray(cond_bool, bool)).shift(1).fillna(False)
    ts = pd.to_datetime(df["timestamp"])
    return pd.merge_asof(
        pd.DataFrame({"ts": ts}).sort_values("ts"),
        pd.DataFrame({"ts": pd.to_datetime(dd["timestamp"]), "b": b.values}).sort_values("ts"),
        on="ts", direction="backward")["b"].fillna(False).astype(bool).values


# ----- the confirms -----
def confirm_macd(df, dd):
    ml = bt.ema(dd["close"], 12) - bt.ema(dd["close"], 26); sig = bt.ema(ml, 9)
    return _map_daily_bool(df, dd, (ml < sig).values)


def confirm_rsi(df, dd, thr):
    r = bt.rsi(dd["close"], 14)
    return _map_daily_bool(df, dd, (r < thr).values)


def confirm_adx(df, dd):
    """ADX(14)>20 with -DI>+DI on DAILY bars. Needs daily OHLC -> rebuild daily OHLC."""
    raw = pd.read_csv(EXT, parse_dates=["timestamp"])
    dohlc = raw.set_index("timestamp").resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    up = dohlc["high"].diff(); dn = -dohlc["low"].diff()
    pdm = ((up > dn) & (up > 0)) * up.clip(lower=0)
    ndm = ((dn > up) & (dn > 0)) * dn.clip(lower=0)
    a = bt.atr(dohlc, 14)
    pdi = 100 * bt.ema(pdm, 14) / a; ndi = 100 * bt.ema(ndm, 14) / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, 1e-9)
    adxv = bt.ema(dx, 14)
    cond = (adxv > 20) & (ndi > pdi)
    return _map_daily_bool(df, dohlc, cond.values)


def confirm_ema(df, dd):
    cond = bt.ema(dd["close"], 50) < bt.ema(dd["close"], 200)
    return _map_daily_bool(df, dd, cond.values)


def confirm_bbreak(df, dd):
    """Daily close below lower Bollinger(20,2)."""
    lo, _, _ = bt.bbands(dd["close"], 20, 2.0)
    cond = dd["close"] < lo
    return _map_daily_bool(df, dd, cond.values)


def confirm_roc(df, dd, n=10):
    """Daily rate-of-change (n=10) < 0."""
    roc = dd["close"] / dd["close"].shift(n) - 1
    return _map_daily_bool(df, dd, (roc < 0).values)


def confirm_obv(df, dd):
    """OBV falling on DAILY bars (uses daily volume). OBV slope over 10d < 0."""
    raw = pd.read_csv(EXT, parse_dates=["timestamp"])
    cols = {"close": "last"}
    has_vol = "volume" in raw.columns
    if has_vol:
        dv = raw.set_index("timestamp").resample("1D").agg({"close": "last", "volume": "sum"}).dropna().reset_index()
        sign = np.sign(dv["close"].diff().fillna(0))
        obv = (sign * dv["volume"]).cumsum()
    else:
        # no volume in cache -> degrade to price-proxy OBV (sign*1) so the test still runs
        dv = dd.copy()
        sign = np.sign(dv["close"].diff().fillna(0))
        obv = sign.cumsum()
    cond = obv < obv.shift(10)
    return _map_daily_bool(df, dv, cond.values), has_vol


def confirm_vwap(df, dd):
    """Price < rolling VWAP(20d). Needs volume; degrade to SMA20 if absent."""
    raw = pd.read_csv(EXT, parse_dates=["timestamp"])
    if "volume" in raw.columns:
        dv = raw.set_index("timestamp").resample("1D").agg({"close": "last", "volume": "sum"}).dropna().reset_index()
        pv = (dv["close"] * dv["volume"]).rolling(20).sum()
        vv = dv["volume"].rolling(20).sum().replace(0, 1e-9)
        vwap = pv / vv
        cond = dv["close"] < vwap
        return _map_daily_bool(df, dv, cond.values), True
    else:
        cond = dd["close"] < bt.sma(dd["close"], 20)
        return _map_daily_bool(df, dd, cond.values), False


# ----- evaluation harness -----
def evaluate(df, LONG, ssize, parab_trig, drop, confirm, name, results):
    bear = drop & confirm if confirm is not None else drop
    s = run2(df, LONG, bear, ssize, parab_trig, use_parab=True, use_bd=True)
    cg, dd, rr = m(s); oos = m(s[s.index >= s.index[int(len(s) * 0.6)]])[2]
    b18, b22, b26 = yr(s, 2018), yr(s, 2022), yr(s, 2026)
    years = {y: yr(s, y) for y in range(2017, 2027)}
    all_green = all(v >= 0 for v in years.values())
    bears_pos = (b18 >= 0) and (b22 >= 0) and (b26 >= 0)
    passes = (cg > 0) and (oos > 0) and bears_pos and all_green
    results[name] = dict(cg=cg, dd=dd, rr=rr, oos=oos, b18=b18, b22=b22, b26=b26,
                         years=years, passes=passes, s=s)
    return results[name]


def main():
    df, bull = build(); c = df["close"]
    macro = (c > bt.sma(c, 9 * 30 * BPD).shift(1)).fillna(False).values
    LONG = bull & macro
    hh40 = c.rolling(40 * BPD).max().shift(1)
    drop = ((c / hh40 - 1) < -0.10).fillna(False).values
    hh180 = c.rolling(180 * BPD).max().shift(1); ddh = (c / hh180 - 1).fillna(0).values
    ssize = np.where(ddh <= -0.30, 1.0, np.where(ddh <= -0.20, 0.50, 0.25))
    parab_trig = (c > 2.2 * bt.sma(c, 140 * BPD).shift(1)).fillna(False).values

    dd = _daily_close()

    # ---- ANTI-BUG: reproduce base byte-for-byte first ----
    macd = confirm_macd(df, dd)
    base = run2(df, LONG, drop & macd, ssize, parab_trig, use_parab=True, use_bd=True)
    cg, ddd, rr = m(base); oos = m(base[base.index >= base.index[int(len(base) * 0.6)]])[2]
    b18, b22, b26 = yr(base, 2018), yr(base, 2022), yr(base, 2026)
    print("=" * 96)
    print("BASE REPRODUCTION CHECK (must equal V2: CAGR 103 / DD -31 / r/DD 3.34 / OOS 3.24 / +56/+26/+14)")
    print(f"  drop10 & dailyMACD<sig : CAGR {cg*100:.0f}%  DD {ddd*100:.0f}%  r/DD {rr:.2f}  "
          f"OOS {oos:.2f}  bears {b18:+.0f}/{b22:+.0f}/{b26:+.0f}")
    ok = (round(cg*100) == 103 and round(ddd*100) == -31 and abs(rr-3.34) < 0.03
          and abs(oos-3.24) < 0.03 and round(b18) == 56 and round(b22) == 26 and round(b26) == 14)
    print("  >> BASE REPRODUCED EXACTLY" if ok else "  >> !!! BASE MISMATCH — ABORT, fix before trusting anything")
    if not ok:
        sys.exit(1)
    print("=" * 96)

    # ---- build all confirms ----
    rsi40 = confirm_rsi(df, dd, 40); rsi45 = confirm_rsi(df, dd, 45); rsi50 = confirm_rsi(df, dd, 50)
    adxc = confirm_adx(df, dd)
    emac = confirm_ema(df, dd)
    bbc = confirm_bbreak(df, dd)
    rocc = confirm_roc(df, dd, 10)
    obvc, obv_has = confirm_obv(df, dd)
    vwapc, vwap_has = confirm_vwap(df, dd)

    confirms = [
        ("(base) daily MACD<sig", macd),
        ("(A) ABLATE: drop-only, no confirm", None),
        ("(B) daily RSI<40", rsi40),
        ("(B) daily RSI<45", rsi45),
        ("(B) daily RSI<50", rsi50),
        ("(B) daily ADX>20 & -DI>+DI", adxc),
        ("(B) daily EMA50<EMA200", emac),
        ("(B) daily close<lowerBB(20,2)", bbc),
        ("(B) daily ROC(10)<0", rocc),
        ("(B) OBV falling(10d)" + ("" if obv_has else " [no-vol proxy]"), obvc),
        ("(B) price<VWAP(20d)" + ("" if vwap_has else " [no-vol->SMA]"), vwapc),
        # (C) COMBINE: drop% AND two confirms
        ("(C) MACD<sig & RSI<50", macd & rsi50),
        ("(C) MACD<sig & EMA50<200", macd & emac),
        ("(C) MACD<sig & ROC<0", macd & rocc),
        ("(C) RSI<50 & EMA50<200", rsi50 & emac),
        ("(C) EMA50<200 & ROC<0", emac & rocc),
        ("(C) MACD<sig & ADX>20&-DI>+DI", macd & adxc),
    ]

    results = {}
    print(f"  {'short confirm (ANDed with drop>10%/40d)':<40}{'CAGR':>6}{'DD':>6}{'r/DD':>6}{'OOS':>6}"
          f"  {'2018/22/26':>16}  {'all-yrs-green':>13}")
    print("  " + "-" * 92)
    for name, cf in confirms:
        r = evaluate(df, LONG, ssize, parab_trig, drop, cf, name, results)
        pb = f"{r['b18']:+.0f}/{r['b22']:+.0f}/{r['b26']:+.0f}"
        green = "PASS" if r['passes'] else ("green" if all(v >= 0 for v in r['years'].values()) else "RED yr")
        print(f"  {name:<40}{r['cg']*100:>5.0f}%{r['dd']*100:>5.0f}%{r['rr']:>6.2f}{r['oos']:>6.2f}"
              f"  {pb:>16}  {green:>13}")

    # ---- rank winners by r/DD among those that PASS the gate ----
    print("\n" + "=" * 96)
    passers = {k: v for k, v in results.items() if v['passes']}
    ranked = sorted(passers.items(), key=lambda kv: kv[1]['rr'], reverse=True)
    print(f"  GATE-PASSING configs (full+OOS+all 3 bears+every year green): {len(passers)} of {len(results)}")
    print("  TOP 3 by ret/DD (gate-passers only):")
    for i, (name, v) in enumerate(ranked[:3], 1):
        print(f"   {i}. {name:<42} CAGR {v['cg']*100:.0f}%  DD {v['dd']*100:.0f}%  r/DD {v['rr']:.2f}  OOS {v['oos']:.2f}")

    # ---- per-bear + year-by-year for the winner ----
    if ranked:
        wname, wv = ranked[0]
        print("\n  WINNER (best gate-passing ret/DD): " + wname)
        print(f"    full: CAGR {wv['cg']*100:.0f}%  DD {wv['dd']*100:.0f}%  r/DD {wv['rr']:.2f}  OOS {wv['oos']:.2f}")
        print(f"    per-bear: 2018 {wv['b18']:+.0f}%   2022 {wv['b22']:+.0f}%   2026 {wv['b26']:+.0f}%")
        print("    year-by-year: " + "  ".join(f"{y}:{wv['years'][y]:+.0f}%" for y in range(2017, 2027)))

    # ---- MACD verdict ----
    print("\n" + "=" * 96)
    macd_rr = results["(base) daily MACD<sig"]['rr']
    better = {k: v for k, v in passers.items() if v['rr'] > macd_rr and not k.startswith("(base)")}
    print(f"  MACD base r/DD = {macd_rr:.2f}.  Gate-passing confirms that BEAT MACD on r/DD: {len(better)}")
    if better:
        for k, v in sorted(better.items(), key=lambda kv: kv[1]['rr'], reverse=True):
            print(f"    BEATS MACD: {k:<42} r/DD {v['rr']:.2f} (vs {macd_rr:.2f})")
        print("  => MACD is NOT the single best confirm (see above).")
    else:
        print("  => No gate-passing confirm beats daily-MACD on ret/DD. MACD is already the best confirm.")


if __name__ == "__main__":
    main()
