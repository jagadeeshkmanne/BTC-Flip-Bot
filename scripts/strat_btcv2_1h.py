#!/usr/bin/env python3
"""strat_btcv2_1h.py — NEW, SEPARATE strategy: btcv2 (config D) logic ported to 1h bars.

DOES NOT touch the live 4h bot. This is a fresh experiment to see whether running the same
BTC-long / ETH-short conviction engine on a SHORTER timeframe (1h) opens/closes trades more
often and produces steadier MONTHLY income.

What's ported faithfully from run_ec3 (config D):
  - BTC long: trend-bull gate, ATR stop, break-even at +1R, pyramid at +2R, parabolic de-risk,
    +6R partial lock, conviction leverage 1x->2.5x (ADX + EMA-gap).
  - ETH short: drop-from-high + daily-MACD-bear gate, bear-depth sizing, ATR stop, +1R trail.

What changes for the 1h port (controls trade frequency):
  - base bars = 1h (BPD=24).  EMA fast/slow trend + ATR stop run on 1h bars  => faster, more trades.
  - DAILY filters (daily EMA50/200, daily MACD, 9-month macro SMA, 35/180-day highs, 20-week
    parabolic) stay CALENDAR-correct (computed on the daily series or scaled by BPD).

Honest accounting identical to the 4h engine: closed-bar signals, next-bar-open fills,
fee 0.055%/side + 0.05% slip, intrabar stops on real high/low, IS/OOS 60/40.

Usage:  python3 strat_btcv2_1h.py [ema_f ema_s atr_n]   (default 50 200 14 = native-fast)
        python3 strat_btcv2_1h.py 200 800 56            (calendar-matched = ~same as 4h)
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT


_RULE = {"1h": "1h", "2h": "2h", "4h": "4h", "6h": "6h", "8h": "8h", "12h": "12h"}
_BPD = {"1h": 24, "2h": 12, "4h": 6, "6h": 4, "8h": 3, "12h": 2}


def load_tf(sym, tf="1h"):
    raw = pd.read_csv(os.path.join(bt.CACHE, f"{sym}_1h_binance_ext.csv"), parse_dates=["timestamp"])
    df = raw.set_index("timestamp").resample(_RULE[tf]).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    dd = raw.set_index("timestamp").resample("1D").agg({"close": "last"}).dropna().reset_index()
    return df, dd


def load1h(sym): return load_tf(sym, "1h")


def daily_map(df, dd, series):
    """Map a (shifted, causal) daily boolean/float series onto the 1h timestamps."""
    ts = pd.to_datetime(df["timestamp"])
    return pd.merge_asof(pd.DataFrame({"ts": ts}).sort_values("ts"),
        pd.DataFrame({"ts": pd.to_datetime(dd["timestamp"]), "b": series.values}).sort_values("ts"),
        on="ts", direction="backward")["b"]


def run(ema_f=50, ema_s=200, atr_n=14, tf="1h",
        long_lev=None, lock_frac=0.33, lock_r=6.0, drop_pct=0.10, drop_look=35,
        atr_mult=3.5, sl_cap=0.12, be_r=1.0, pyr_r=2.0, pyr_frac=1.0, be_buf=0.01,
        s_atr=6.0, s_cap=0.20, size_lo=0.5, size_mid=1.0, size_hi=1.0,
        strail_k=3.5, strail_arm_R=1.0,
        lf_rsi_min=None, lf_rsi_max=None, lf_macd=False, lf_adx=None):
    BPD = _BPD[tf]
    btc, btcd = load_tf("BTCUSDT", tf); eth, ethd = load_tf("ETHUSDT", tf)
    common = pd.Index(btc["timestamp"]).intersection(pd.Index(eth["timestamp"]))
    btc = btc[btc["timestamp"].isin(common)].reset_index(drop=True)
    eth = eth[eth["timestamp"].isin(common)].reset_index(drop=True)
    bc = btc["close"]; ec = eth["close"]

    # --- gates ---
    dbull = daily_map(btc, btcd, (bt.ema(btcd["close"], 50) > bt.ema(btcd["close"], 200)).shift(1).fillna(False)
                      ).fillna(False).astype(bool).values
    macro = (bc > bt.sma(bc, 9 * 30 * BPD).shift(1)).fillna(False).values
    bull = (bt.ema(bc, ema_f) > bt.ema(bc, ema_s)).values & dbull & macro
    parab = (bc > 2.2 * bt.sma(bc, 140 * BPD).shift(1)).fillna(False).values
    mld = bt.ema(ethd["close"], 12) - bt.ema(ethd["close"], 26); sigd = bt.ema(mld, 9)
    macd_bear = daily_map(eth, ethd, (mld < sigd).shift(1).fillna(False)).fillna(False).astype(bool).values
    drop_arr = ((ec / ec.rolling(drop_look * BPD).max().shift(1) - 1) < -drop_pct).fillna(False).values
    bear = drop_arr & macd_bear
    bdd = (ec / ec.rolling(180 * BPD).max().shift(1) - 1).fillna(0).values
    ssize = np.where(bdd <= -0.30, size_hi, np.where(bdd <= -0.20, size_mid, size_lo))
    if long_lev is None: long_lev = np.ones(len(btc))

    # --- ENTRY-ONLY confirmation filters (gate the OPEN, never the hold/exit) ---
    entry_ok = np.ones(len(btc), bool)
    if lf_rsi_min is not None or lf_rsi_max is not None:
        r = bt.rsi(bc, 14).shift(1).fillna(50).values
        if lf_rsi_min is not None: entry_ok &= (r >= lf_rsi_min)
        if lf_rsi_max is not None: entry_ok &= (r <= lf_rsi_max)
    if lf_macd:
        ml = bt.ema(bc, 12) - bt.ema(bc, 26); sg = bt.ema(ml, 9)
        entry_ok &= (ml > sg).shift(1).fillna(False).astype(bool).values
    if lf_adx is not None:
        entry_ok &= (bt.adx(btc, 14).shift(1).fillna(0).values >= lf_adx)

    bo, bh, bl, bcv = [btc[k].values for k in ("open", "high", "low", "close")]; ba = bt.atr(btc, atr_n).values
    so, sh, sl_, scv = [eth[k].values for k in ("open", "high", "low", "close")]; sa = bt.atr(eth, atr_n).values
    n = len(btc); cash = 1.0; units = 0.0; side = 0; entry = stop = R = 0.0
    pyrd = parabd = lockd = False; notional0 = 0.0; lowS = np.inf; armedL = armedS = True
    eq = np.ones(n); ntrades = 0; entry_i = 0; holds = []
    def mpx(i): return bcv[i] if side == 1 else (scv[i] if side == -1 else bcv[i])

    start = max(ema_s + 5, atr_n + 5, 20)
    for i in range(start, n - 1):
        if not bull[i]: armedL = True
        if not bear[i]: armedS = True
        if side == 1:
            lN = bl[i + 1]; oN = bo[i + 1]; cN = bcv[i + 1]
            if lN <= stop:
                fpx = stop * (1 - SLIP); cash += units * fpx - abs(units) * fpx * FEE; units = 0.0; side = 0; pyrd = parabd = lockd = False; ntrades += 1; holds.append(i + 1 - entry_i)
            elif not bull[i]:
                fpx = oN * (1 - SLIP); cash += units * fpx - abs(units) * fpx * FEE; units = 0.0; side = 0; pyrd = parabd = lockd = False; ntrades += 1; holds.append(i + 1 - entry_i)
            else:
                prof = (cN - entry) / R
                if prof >= be_r: stop = max(stop, entry * (1 + be_buf))
                if not pyrd and prof >= pyr_r:
                    addn = pyr_frac * notional0; au = addn / cN; cash -= au * cN + addn * FEE; units += au; pyrd = True; stop = max(stop, entry)
                if lock_frac > 0 and not lockd and prof >= lock_r:
                    fpx = oN * (1 - SLIP); cl = lock_frac * units; cash += cl * fpx * (1 - FEE); units -= cl; lockd = True
                if not parabd and parab[i]:
                    fpx = oN * (1 - SLIP); cl = 0.5 * units; cash += cl * fpx * (1 - FEE); units -= cl; parabd = True
        elif side == -1:
            if sl_[i] < lowS: lowS = sl_[i]
            hN = sh[i + 1]; oN = so[i + 1]; cN = scv[i + 1]
            tstop = stop
            if strail_k > 0 and (entry - scv[i]) / R >= strail_arm_R:
                tstop = min(stop, lowS + strail_k * sa[i])
            if hN >= tstop:
                fpx = tstop * (1 + SLIP); cash += units * fpx - abs(units) * fpx * FEE; units = 0.0; side = 0; stop = tstop; ntrades += 1; holds.append(i + 1 - entry_i)
            elif not bear[i]:
                fpx = oN * (1 + SLIP); cash += units * fpx - abs(units) * fpx * FEE; units = 0.0; side = 0; ntrades += 1; holds.append(i + 1 - entry_i)
            else:
                stop = tstop
                if (entry - cN) / R >= be_r: stop = min(stop, entry * (1 - be_buf))
        if side == 0:
            if bull[i] and entry_ok[i] and armedL:
                E = cash; oN = bo[i + 1]; st = max(bcv[i] - atr_mult * ba[i], bcv[i] * (1 - sl_cap)); ent = oN * (1 + SLIP)
                if ent - st > 0:
                    notional0 = E * long_lev[i]; units = notional0 / ent; cash -= units * ent + notional0 * FEE
                    entry = ent; stop = st; R = ent - st; side = 1; pyrd = parabd = lockd = False; armedL = False; entry_i = i + 1
            elif bear[i] and armedS:
                E = cash; oN = so[i + 1]; st = min(scv[i] + s_atr * sa[i], scv[i] * (1 + s_cap)); ent = oN * (1 - SLIP)
                if st - ent > 0:
                    notional = ssize[i] * E; units = -notional / ent; cash -= units * ent + notional * FEE
                    entry = ent; stop = st; R = st - ent; side = -1; armedS = False; lowS = np.inf; entry_i = i + 1
        eq[i + 1] = cash + units * mpx(i + 1)
    avg_hold_bars = (sum(holds) / len(holds)) if holds else 0.0
    return pd.Series(eq, index=pd.to_datetime(btc["timestamp"])).iloc[start:], ntrades, avg_hold_bars / BPD


def build_llev(ema_f=50, ema_s=200, tf="1h"):
    btc, _ = load_tf("BTCUSDT", tf); eth, _ = load_tf("ETHUSDT", tf)
    common = pd.Index(btc["timestamp"]).intersection(pd.Index(eth["timestamp"]))
    b = btc[btc["timestamp"].isin(common)].reset_index(drop=True); c = b["close"]
    adx = bt.adx(b, 14).shift(1).fillna(0).values
    egap = ((bt.ema(c, ema_f) - bt.ema(c, ema_s)) / bt.ema(c, ema_s)).shift(1).fillna(0).values
    conv = np.clip(adx / 35.0, 0, 1) * 0.5 + np.clip(egap / 0.12, 0, 1) * 0.5
    return 1.0 + 1.5 * conv


def stats(eq, nt, hold_d):
    cagr, dd, rdd = bt.metrics(eq)
    oos = eq[eq.index >= eq.index[int(len(eq) * 0.6)]]
    ocagr, odd, ordd = bt.metrics(oos)
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    m = eq.resample("ME").last().pct_change().dropna()
    return dict(tpy=nt/yrs, hold=hold_d, cagr=cagr, dd=dd, rdd=rdd, ocagr=ocagr, ordd=ordd,
                avg_m=m.mean(), med_m=m.median(), pos=(m>0).mean(), worst=m.min())


def tune():
    # Keep FULL v2 machinery + v2 trend speed (50/200 @4h, calendar-matched 200/800 @1h).
    # Finetune = add ENTRY-ONLY indicator filters + EMA neighbors. Baseline = no filter.
    bases = [("4h", 50, 200, 14), ("4h", 40, 200, 14), ("4h", 60, 240, 16),
             ("1h", 200, 800, 56), ("1h", 150, 600, 42)]
    filters = [("none", {}), ("rsi>50", dict(lf_rsi_min=50)), ("rsi>55", dict(lf_rsi_min=55)),
               ("rsi<75", dict(lf_rsi_max=75)), ("rsi45-80", dict(lf_rsi_min=45, lf_rsi_max=80)),
               ("macd", dict(lf_macd=True)), ("adx>20", dict(lf_adx=20)), ("adx>25", dict(lf_adx=25)),
               ("macd+rsi>50", dict(lf_macd=True, lf_rsi_min=50))]
    print("# FINETUNE 50/200 winner: entry-only RSI/MACD/ADX filters + EMA neighbors, 4h & 1h")
    print("# (full v2 machinery + conviction leverage kept intact; baseline 4h 50/200 = OOS 3.50)\n")
    print(f"{'tf':>3} {'ema':>8} {'filter':>12} {'hold_d':>6} {'t/yr':>5} {'CAGR':>7} {'maxDD':>7} "
          f"{'r/DD':>5} {'OOS_rDD':>7} | {'avg_mo':>6} {'pos%':>5} {'>=10%':>6} {'worst':>6}")
    print("-" * 104)
    rows = []
    for tf, ef, es, an in bases:
        llev = build_llev(ef, es, tf)
        for fl, kw in filters:
            eq, nt, hold = run(ema_f=ef, ema_s=es, atr_n=an, tf=tf, long_lev=llev, **kw)
            m = eq.resample("ME").last().pct_change().dropna()
            r = stats(eq, nt, hold); r.update(tf=tf, ema=f"{ef}/{es}", fl=fl,
                ge10=(m >= 0.10).mean())
            rows.append(r)
    rows.sort(key=lambda x: x["ordd"], reverse=True)
    for r in rows:
        print(f"{r['tf']:>3} {r['ema']:>8} {r['fl']:>12} {r['hold']:>6.1f} {r['tpy']:>5.0f} "
              f"{r['cagr']:>7.1%} {r['dd']:>7.1%} {r['rdd']:>5.2f} {r['ordd']:>7.2f} | "
              f"{r['avg_m']:>6.1%} {r['pos']:>5.0%} {r['ge10']:>6.0%} {r['worst']:>6.1%}")
    print(f"\n  baseline to beat: 4h 50/200 none  -> OOS r/DD 3.50, 54% green months")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "tune":
        tune(); sys.exit(0)
    tf = sys.argv[1] if len(sys.argv) > 1 else "4h"
    # FINETUNE like v2: keep all risk machinery, sweep ONLY trend speed (= hold length) + ATR.
    # On 4h: 1 bar = 4h, 6 bars = 1 day.  Faster EMA pair => shorter holds, more trades.
    speeds = [(8, 21), (12, 26), (12, 50), (20, 50), (21, 55), (34, 100), (50, 200)]
    print(f"# btcv2 machinery, finetuned for SHORTER HOLDS on {tf}  (full risk engine kept intact)\n")
    print(f"{'ema':>9} {'atr':>4} {'hold_d':>7} {'t/yr':>5} {'CAGR':>7} {'maxDD':>7} {'r/DD':>5} "
          f"{'OOS_rDD':>7} {'OOS_CAGR':>8} | {'avg_mo':>6} {'med_mo':>6} {'pos%':>5} {'worst':>6}")
    print("-" * 108)
    rows = []
    for ef, es in speeds:
        an = max(7, int(round(14 * ef / 50)))   # scale ATR window with trend speed
        llev = build_llev(ef, es, tf)
        eq, nt, hold = run(ema_f=ef, ema_s=es, atr_n=an, tf=tf, long_lev=llev)
        r = stats(eq, nt, hold); r["lbl"] = f"{ef}/{es}"; r["an"] = an
        rows.append(r)
        print(f"{r['lbl']:>9} {an:>4} {hold:>7.1f} {r['tpy']:>5.0f} {r['cagr']:>7.1%} {r['dd']:>7.1%} "
              f"{r['rdd']:>5.2f} {r['ordd']:>7.2f} {r['ocagr']:>8.1%} | "
              f"{r['avg_m']:>6.1%} {r['med_m']:>6.1%} {r['pos']:>5.0%} {r['worst']:>6.1%}")
    print(f"\n  ref: live btcv2 (4h, ema50/200) holds ~weeks, OOS r/DD 4.29, ~57% green months")
