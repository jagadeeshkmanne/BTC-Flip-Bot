#!/usr/bin/env python3
"""ag_shortentry.py — SHORT-ENTRY-TRIGGER search. LONG is pinned (validated base); we hunt
for the best SHORT entry CONDITION to replace/augment the base "drop10 & daily-MACD<signal".

Pinned LONG = bull & (price>SMA(9mo)); run(atr_mult=3.5,sl_cap=0.12,be_r=1.0,pyr_r=2.0,pyr_frac=1.0).
Pinned SHORT sizing  = short_size=0.40,s_atr=5.0,s_cap=0.15,s_pyr_r=None  (same as base — we only
change WHICH bars are allowed to short, not how the short is sized/stopped, so the comparison is
apples-to-apples on the ENTRY trigger).

BASE to beat (full 2017-2026, reproduced byte-for-byte):
    CAGR 96%  DD -43%  ret/DD 2.24  OOS 3.05  bears 2018+26%/2022+11%/2026+8%  green every year.

Triggers tested (all on CLOSED bars, shift(1) on any daily merge => no lookahead):
  drop%  : price down >X% from N-day high          X in {8,10,12,15}, N=40d
  RSI    : RSI(14) < {35,40,45}
  ADX    : ADX(14) > {20,25}  (trend strength)
  BBlow  : close < lower Bollinger(20,2)
  EMAslp : daily EMA50 slope < 0  (falling)
  EMAxo  : daily EMA20 < daily EMA50
  VOLsrg : down-bar AND volume > 1.5x its 20-bar avg
  MACD   : daily MACD < signal (the base confirm, kept for combos)
and combos (drop% AND each confirm), sweeping drop threshold.

ROBUSTNESS GATE for a "winner": beat base ret/DD 2.24 on full history AND OOS>=3.05-ish AND
positive in ALL THREE bears (2018/2022/2026) AND green every calendar year.
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

EXT = os.path.join(bt.CACHE, "BTCUSDT_1h_binance_ext.csv")
BPD = 6  # 4h bars per day

LP = dict(atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0)
SP = dict(short_size=0.40, s_atr=5.0, s_cap=0.15, s_pyr_r=None)


# ---------- daily-derived signals, merged backward (no lookahead) ----------
def _daily_raw():
    raw = pd.read_csv(EXT, parse_dates=["timestamp"])
    return raw.set_index("timestamp").resample("1D").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()


def _merge_to_4h(df, dts, vals):
    """Map a daily boolean series (already shift(1)'d) onto the 4h index, backward."""
    ts = pd.to_datetime(df["timestamp"])
    out = pd.merge_asof(
        pd.DataFrame({"ts": ts}).sort_values("ts"),
        pd.DataFrame({"ts": pd.to_datetime(dts), "b": np.asarray(vals)}).sort_values("ts"),
        on="ts", direction="backward")["b"]
    return out.fillna(False).to_numpy().astype(bool)


def build_signals(df):
    """Return a dict of boolean trigger arrays aligned to the 4h df (all lookahead-safe)."""
    raw = pd.read_csv(EXT, parse_dates=["timestamp"])
    c = df["close"]
    sig = {}

    # --- drop-from-high (4h native) ---
    hh40 = c.rolling(40 * BPD).max().shift(1)
    for X in (0.08, 0.10, 0.12, 0.15):
        sig[f"drop{int(X*100)}"] = ((c / hh40 - 1) < -X).fillna(False).to_numpy()

    # --- RSI(14) on 4h (oversold-ish momentum down) ---
    r = bt.rsi(c, 14).shift(1)
    for t in (35, 40, 45):
        sig[f"rsi<{t}"] = (r < t).fillna(False).to_numpy()

    # --- ADX(14) trend strength on 4h ---
    ax = bt.adx(df, 14).shift(1)
    for t in (20, 25):
        sig[f"adx>{t}"] = (ax > t).fillna(False).to_numpy()

    # --- lower Bollinger(20,2) on 4h ---
    lo, mid, up = bt.bbands(c, 20, 2.0)
    sig["bblow"] = (c.shift(1) < lo.shift(1)).fillna(False).to_numpy()

    # --- volume surge on a down bar (4h) ---
    vol = raw.set_index("timestamp").resample("4h").agg({"volume": "sum"}).reindex(
        pd.to_datetime(df["timestamp"]).values)["volume"].reset_index(drop=True)
    vavg = vol.rolling(20).mean()
    downbar = (c < c.shift(1))
    sig["volsrg"] = ((vol.shift(1) > 1.5 * vavg.shift(1)) & downbar.shift(1)).fillna(False).to_numpy()

    # --- daily-derived (merge backward) ---
    dd = _daily_raw()
    dc = dd["close"]
    # MACD<signal (base confirm)
    ml = bt.ema(dc, 12) - bt.ema(dc, 26); ms = bt.ema(ml, 9)
    sig["macd"] = _merge_to_4h(df, dd["timestamp"], (ml < ms).shift(1).fillna(False).to_numpy())
    # daily EMA50 slope < 0
    e50 = bt.ema(dc, 50)
    slp = (e50 < e50.shift(5))  # falling over 5 days
    sig["ema50dn"] = _merge_to_4h(df, dd["timestamp"], slp.shift(1).fillna(False).to_numpy())
    # daily EMA20 < EMA50
    e20 = bt.ema(dc, 20)
    sig["ema20<50"] = _merge_to_4h(df, dd["timestamp"], (e20 < e50).shift(1).fillna(False).to_numpy())

    return sig


# ---------- metrics ----------
def yr(s, y):
    seg = s[s.index.year == y]
    return (seg.iloc[-1] / seg.iloc[0] - 1) * 100 if len(seg) > 20 else 0.0


def evaluate(df, LONG, bear):
    sC = run(df, LONG, bear, allow_long=True, allow_short=True, **LP, **SP)
    cg, dd, rr = m(sC)
    o = m(sC[sC.index >= sC.index[int(len(sC) * 0.6)]])
    sS = run(df, LONG, bear, allow_long=False, allow_short=True, **LP, **SP)
    sdd = m(sS)[1]
    years = {y: yr(sC, y) for y in range(2017, 2027)}
    bears = (years[2018], years[2022], years[2026])
    allgreen = all(v >= 0 for v in years.values())
    ntrig = int(bear.sum())
    return dict(cg=cg, dd=dd, rr=rr, oos=o[2], sdd=sdd, bears=bears,
                allgreen=allgreen, years=years, sC=sC, ntrig=ntrig)


def gate(res, base_rr=2.24):
    """Robustness gate: beats base ret/DD, all 3 bears positive, green every year, OOS solid."""
    return (res["rr"] > base_rr and all(b > 0 for b in res["bears"])
            and res["allgreen"] and res["oos"] >= 2.8)


def main():
    df, bull = build()
    c = df["close"]
    macro = (c > bt.sma(c, 9 * 30 * 6).shift(1)).fillna(False).to_numpy()
    LONG = bull & macro
    sig = build_signals(df)

    print("=" * 104)
    print("SHORT-ENTRY-TRIGGER SEARCH — LONG pinned (bull & price>SMA9mo), short sizing pinned. BTC 4h 2017-2026")
    print("=" * 104)

    # reference rows
    zero = np.zeros(len(df), bool)
    sL = run(df, LONG, zero, allow_long=True, allow_short=False, **LP)
    cgl, ddl, rrl = m(sL); oosl = m(sL[sL.index >= sL.index[int(len(sL)*0.6)]])[2]
    base = evaluate(df, LONG, sig["drop10"] & sig["macd"])

    hdr = f"  {'short trigger':<46}{'CAGR':>5}{'DD':>6}{'r/DD':>6}{'OOS':>6}  {'2018/22/26':>16}  {'sDD':>5}{'#':>6}  green"
    print(hdr); print("  " + "-" * 100)
    print(f"  {'LONG-ONLY (no short)':<46}{cgl*100:>4.0f}%{ddl*100:>5.0f}%{rrl:>6.2f}{oosl:>6.2f}  {'(long only)':>16}")
    pb = "%+.0f/%+.0f/%+.0f" % base["bears"]
    print(f"  {'>>> BASE: drop10 & MACD (the bar to beat)':<46}{base['cg']*100:>4.0f}%{base['dd']*100:>5.0f}%"
          f"{base['rr']:>6.2f}{base['oos']:>6.2f}  {pb:>16}  {base['sdd']*100:>4.0f}%{base['ntrig']:>6}  {'Y' if base['allgreen'] else 'n'}")
    print("  " + "-" * 100)

    # ---- candidate trigger definitions ----
    candidates = []
    # SINGLE confirms (no drop gate) — generally too trigger-happy, but test honestly
    for k in ("rsi<35", "rsi<40", "rsi<45", "adx>20", "adx>25", "bblow", "volsrg",
              "macd", "ema50dn", "ema20<50"):
        candidates.append((f"[single] {k}", sig[k]))

    # DROP x CONFIRM combos, sweeping drop threshold
    drops = ["drop8", "drop10", "drop12", "drop15"]
    confirms = ["macd", "rsi<40", "rsi<45", "adx>20", "adx>25", "bblow",
                "ema50dn", "ema20<50", "volsrg"]
    for d in drops:
        for cf in confirms:
            candidates.append((f"{d} & {cf}", sig[d] & sig[cf]))

    # a few TRIPLE combos (drop + trend + momentum) — the "high-conviction" shorts
    triples = [
        ("drop10 & macd & ema20<50", sig["drop10"] & sig["macd"] & sig["ema20<50"]),
        ("drop10 & macd & adx>20", sig["drop10"] & sig["macd"] & sig["adx>20"]),
        ("drop10 & ema20<50 & adx>20", sig["drop10"] & sig["ema20<50"] & sig["adx>20"]),
        ("drop8 & macd & ema20<50", sig["drop8"] & sig["macd"] & sig["ema20<50"]),
        ("drop12 & macd & ema20<50", sig["drop12"] & sig["macd"] & sig["ema20<50"]),
        ("drop10 & ema20<50 & rsi<45", sig["drop10"] & sig["ema20<50"] & sig["rsi<45"]),
        ("drop8 & ema20<50 & adx>20", sig["drop8"] & sig["ema20<50"] & sig["adx>20"]),
    ]
    candidates += triples

    rows = []
    for name, bear in candidates:
        r = evaluate(df, LONG, bear)
        rows.append((name, r))
        pb = "%+.0f/%+.0f/%+.0f" % r["bears"]
        win = gate(r) and r["rr"] > base["rr"]
        flag = " <== WINNER" if win else (" <beats r/DD" if r["rr"] > base["rr"] else "")
        print(f"  {name:<46}{r['cg']*100:>4.0f}%{r['dd']*100:>5.0f}%{r['rr']:>6.2f}{r['oos']:>6.2f}  "
              f"{pb:>16}  {r['sdd']*100:>4.0f}%{r['ntrig']:>6}  {'Y' if r['allgreen'] else 'n'}{flag}")

    # ---- rank: passes gate first (by ret/DD), else by ret/DD ----
    def keyf(nr):
        _, r = nr
        return (1 if gate(r) else 0, r["rr"])
    ranked = sorted(rows, key=keyf, reverse=True)

    print("\n" + "=" * 104)
    print("TOP 3 SHORT-TRIGGER CONFIGS (ranked: passes robustness gate, then ret/DD)")
    print("=" * 104)
    top3 = ranked[:3]
    for rank, (name, r) in enumerate(top3, 1):
        pb = "%+.1f / %+.1f / %+.1f" % r["bears"]
        print(f"\n  #{rank}  {name}")
        print(f"      CAGR {r['cg']*100:.1f}%   maxDD {r['dd']*100:.1f}%   ret/DD {r['rr']:.2f}   "
              f"OOS ret/DD {r['oos']:.2f}   short-sleeve DD {r['sdd']*100:.1f}%   triggers {r['ntrig']}")
        print(f"      bears 2018/2022/2026: {pb}      green-every-year: {'YES' if r['allgreen'] else 'NO'}")
        print(f"      passes robustness gate: {'YES' if gate(r) else 'NO'}     "
              f"beats BASE ret/DD 2.24: {'YES' if r['rr'] > base['rr'] else 'NO'}")

    # ---- year-by-year for the winner ----
    wname, wr = top3[0]
    print("\n" + "=" * 104)
    print(f"YEAR-BY-YEAR — winner: {wname}")
    print("=" * 104)
    for y in range(2017, 2027):
        bh = ""
        print(f"    {y}: {wr['years'][y]:+7.1f}%")
    print("\n  BASE year-by-year (drop10 & MACD) for reference:")
    for y in range(2017, 2027):
        print(f"    {y}: {base['years'][y]:+7.1f}%")

    # ---- verdict ----
    print("\n" + "=" * 104)
    print("VERDICT")
    print("=" * 104)
    beats = [nr for nr in rows if gate(nr[1]) and nr[1]["rr"] > base["rr"]]
    if beats:
        print(f"  {len(beats)} trigger(s) pass the FULL robustness gate AND beat base ret/DD 2.24.")
        print(f"  Best: {top3[0][0]}  ret/DD {top3[0][1]['rr']:.2f} vs base 2.24.")
    else:
        print("  NO trigger passes the full robustness gate AND beats base ret/DD 2.24.")
        # best by raw ret/DD regardless of gate
        br = sorted(rows, key=lambda nr: nr[1]["rr"], reverse=True)[0]
        print(f"  Best raw ret/DD: {br[0]} = {br[1]['rr']:.2f} "
              f"(gate={'pass' if gate(br[1]) else 'FAIL'}, "
              f"bears {'+'.join('%.0f'%b for b in br[1]['bears'])}).")
        print("  => Base 'drop10 & MACD' remains the validated short trigger.")


if __name__ == "__main__":
    main()
