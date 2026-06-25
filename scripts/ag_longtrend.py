#!/usr/bin/env python3
"""ag_longtrend.py — SWAP THE LONG TREND INDICATOR in the V2 bull gate.

V2 base bull gate = f_up(4h EMA50>200) AND dmap(prior-day EMA50>200). The macro filter
(price>SMA(9mo)) and ALL trade management (ATR3.5 stop cap12%, BE@1R, pyramid@2R, parabolic
de-risk, bear-depth short) stay byte-for-byte identical to backtest_v2_combined.py.

ONLY the 4h-trend component (f_up) is replaced with alternative CAUSAL trend booleans:
  Supertrend(10,3), Donchian(20/55), Ichimoku(tenkan/kijun + cloud), HMA(50/200),
  Vortex(14), Keltner trend, EMA pairs {21/55, 34/89, 50/200(=base), 89/233}.

Each alt trend is built on the SAME 4h df, shifted so signal uses only closed bars (no
lookahead), then ANDed with dmap & macro. Short side unchanged. Reuses run2() from
backtest_v2_combined verbatim so the engine is identical.

GATE: a winner must hold full 2017-2026 + OOS(last 40%) + positive ALL 3 bears + green every year.
BASE to beat: ret/DD 3.34, OOS 3.24.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_ext import build, EXT, BPD
from backtest_short_aggressive import daily_macd_bear
from backtest_v2_combined import run2
from backtest_myv3_final import m


# ---------------------------------------------------------------------------
# Rebuild the SAME 4h df + the DAILY trend (dmap) exactly as backtest_myv3_ext.build(),
# but return df, dmap, f_up separately so we can swap f_up. We re-derive dmap here from the
# same source to keep it pinned identical, and assert df matches build()'s df.
# ---------------------------------------------------------------------------
def build_parts():
    raw = pd.read_csv(EXT, parse_dates=["timestamp"])
    df = raw.set_index("timestamp").resample("4h").agg(
        {"open":"first","high":"max","low":"min","close":"last"}).dropna().reset_index()
    dd = raw.set_index("timestamp").resample("1D").agg({"close":"last"}).dropna().reset_index()
    d_up = (bt.ema(dd["close"],50) > bt.ema(dd["close"],200)).shift(1).fillna(False).astype(bool)
    ts = pd.to_datetime(df["timestamp"])
    dmap = pd.merge_asof(pd.DataFrame({"ts":ts}).sort_values("ts"),
        pd.DataFrame({"ts":pd.to_datetime(dd["timestamp"]),"b":d_up.values}).sort_values("ts"),
        on="ts", direction="backward")["b"].fillna(False).astype(bool).values
    f_up = (bt.ema(df["close"],50) > bt.ema(df["close"],200)).values  # base 4h trend
    return df, dmap, f_up


# ---------------------------------------------------------------------------
# Alternative CAUSAL 4h trend booleans. Each returns a bool array length len(df).
# Rule: every indicator is computed on closed bars then .shift(1) so the value used at
# decision bar i reflects only data up to bar i-1 close — matches the base f_up convention?
#   NOTE: base f_up is NOT shifted (it uses bar i's close, decided at close of i, filled at
#   i+1 open — that IS causal because run2 acts at i for fill at i+1). To be apples-to-apples
#   we therefore do NOT shift either (use bar i close), keeping the same causality as f_up.
# ---------------------------------------------------------------------------
def t_ema(df, fast, slow):
    c = df["close"]
    return (bt.ema(c,fast) > bt.ema(c,slow)).values


def t_supertrend(df, period=10, mult=3.0):
    h,l,c = df["high"].values, df["low"].values, df["close"].values
    a = bt.atr(df, period).values
    hl2 = (h+l)/2.0
    upper = hl2 + mult*a; lower = hl2 - mult*a
    n=len(df); fu=np.zeros(n); fl=np.zeros(n); dirn=np.ones(n)  # 1=up,-1=down
    for i in range(1,n):
        fu[i] = upper[i] if (upper[i]<fu[i-1] or c[i-1]>fu[i-1]) else fu[i-1]
        fl[i] = lower[i] if (lower[i]>fl[i-1] or c[i-1]<fl[i-1]) else fl[i-1]
        if dirn[i-1]==1:
            dirn[i] = -1 if c[i] < fl[i] else 1
        else:
            dirn[i] = 1 if c[i] > fu[i] else -1
    return dirn==1


def t_donchian(df, n):
    h,l,c = df["high"], df["low"], df["close"]
    upper = h.rolling(n).max().shift(1)   # breakout of prior n-bar high (shift1 = no lookahead)
    lower = l.rolling(n).min().shift(1)
    # state machine: long after close breaks upper, flat/down after close breaks lower
    cu = (c > upper).values; cl = (c < lower).values
    N=len(df); up=np.zeros(N,bool); state=False
    for i in range(N):
        if cu[i]: state=True
        elif cl[i]: state=False
        up[i]=state
    return up


def t_ichimoku(df, tenkan=9, kijun=26, senkou=52):
    h,l,c = df["high"], df["low"], df["close"].values
    def mid(p): return (h.rolling(p).max()+l.rolling(p).min())/2.0
    tk = mid(tenkan).values; kj = mid(kijun).values
    # cloud (senkou A/B) projected forward kijun bars => at bar i the cloud in effect was
    # computed kijun bars ago; shift forward = use values from i-kijun (causal, no lookahead)
    sa = ((mid(tenkan)+mid(kijun))/2.0).shift(kijun).values
    sb = mid(senkou).shift(kijun).values
    cloud_top = np.fmax(sa, sb)
    bull = (tk > kj) & (c > cloud_top)
    return np.nan_to_num(bull, nan=0.0).astype(bool)


def _wma(s, n):
    w = np.arange(1, n+1, dtype=float)
    return s.rolling(n).apply(lambda x: np.dot(x, w)/w.sum(), raw=True)


def t_hma(df, fast, slow):
    c = df["close"]
    def hma(n):
        return _wma(2*_wma(c, n//2) - _wma(c, n), int(np.sqrt(n)))
    return (hma(fast) > hma(slow)).values


def t_vortex(df, n=14):
    h,l,c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)
    vmp = (h - l.shift(1)).abs(); vmm = (l - h.shift(1)).abs()
    str_ = tr.rolling(n).sum()
    vip = vmp.rolling(n).sum()/str_; vim = vmm.rolling(n).sum()/str_
    return (vip > vim).fillna(False).values


def t_keltner(df, ema_n=20, atr_n=10, mult=1.0):
    c = df["close"]
    mid = bt.ema(c, ema_n); a = bt.atr(df, atr_n)
    upper = mid + mult*a
    # trend = close above upper Keltner band (breakout trend), stay until back below mid
    cu = (c > upper).values; cm = (c < mid).values
    N=len(df); up=np.zeros(N,bool); state=False
    for i in range(N):
        if cu[i]: state=True
        elif cm[i]: state=False
        up[i]=state
    return up


def yr(s,y):
    seg=s[s.index.year==y]; return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg)>20 else 0.0


def main():
    df, dmap, f_up = build_parts()
    # assert our df matches build()'s df byte-for-byte
    df0, bull0 = build()
    assert len(df0)==len(df) and np.array_equal(df0["close"].values, df["close"].values), "df mismatch!"
    assert np.array_equal(bull0, (f_up & dmap)), "bull reconstruction mismatch!"
    c = df["close"]

    # ---- identical macro + short side + parabolic (copied from backtest_v2_combined.main) ----
    macro = (c > bt.sma(c, 9*30*BPD).shift(1)).fillna(False).values
    hh40 = c.rolling(40*BPD).max().shift(1); drop = ((c/hh40-1) < -0.10).fillna(False).values
    bear = drop & daily_macd_bear(df)
    hh180 = c.rolling(180*BPD).max().shift(1); ddh = (c/hh180-1).fillna(0).values
    ssize = np.where(ddh<=-0.30,1.0,np.where(ddh<=-0.20,0.50,0.25))
    parab_trig = (c > 2.2*bt.sma(c,140*BPD).shift(1)).fillna(False).values

    # ---- the trend candidates (4h trend boolean to AND with dmap & macro) ----
    cands = [
        ("EMA50/200 (BASE)", f_up),
        ("Supertrend(10,3)",  t_supertrend(df,10,3.0)),
        ("Donchian 20",       t_donchian(df,20)),
        ("Donchian 55",       t_donchian(df,55)),
        ("Ichimoku tk/kj+cloud", t_ichimoku(df,9,26,52)),
        ("HMA 50/200",        t_hma(df,50,200)),
        ("Vortex(14)",        t_vortex(df,14)),
        ("Keltner(20,10,1)",  t_keltner(df,20,10,1.0)),
        ("EMA21/55",          t_ema(df,21,55)),
        ("EMA34/89",          t_ema(df,34,89)),
        ("EMA89/233",         t_ema(df,89,233)),
    ]

    print("="*100)
    print("SWAP THE LONG TREND INDICATOR — V2 engine, daily-trend + 9mo-macro + mgmt all IDENTICAL")
    print("BASE to beat: ret/DD 3.34, OOS 3.24, bears +56/+26/+14, green every year")
    print("="*100)
    print(f"  {'4h trend':<24}{'CAGR':>6}{'DD':>6}{'r/DD':>6}{'OOS':>6}  {'2018/22/26':>14}  {'green-all':>9}")
    print("  "+"-"*96)

    results=[]
    for name, trend in cands:
        LONG = np.asarray(trend, bool) & dmap & macro
        s = run2(df, LONG, bear, ssize, parab_trig, use_parab=True, use_bd=True)
        cg,dd,rr = m(s); oos = m(s[s.index>=s.index[int(len(s)*0.6)]])[2]
        years = {y: yr(s,y) for y in range(2017,2027)}
        b18,b22,b26 = years[2018], years[2022], years[2026]
        green = all(v >= 0 for v in years.values())
        bears_ok = (b18>0 and b22>0 and b26>0)
        passes = (green and bears_ok)
        pb = f"{b18:+.0f}/{b22:+.0f}/{b26:+.0f}"
        flag = ""
        if name != "EMA50/200 (BASE)":
            if rr > 3.34 and passes: flag=" <== BEATS BASE"
            elif not passes: flag=" (FAILS gate)"
        print(f"  {name:<24}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{oos:>6.2f}  {pb:>14}  {'YES' if green else 'no':>9}{flag}")
        results.append((name, cg, dd, rr, oos, b18, b22, b26, green, bears_ok, passes, years, s))

    # ---- best 3 by ret/DD among GATE-PASSERS ----
    passers = [r for r in results if r[10]]
    passers.sort(key=lambda r: r[3], reverse=True)
    print("\n"+"="*100)
    print("BEST 3 (gate-passers, ranked by ret/DD):")
    print("="*100)
    for name, cg, dd, rr, oos, b18, b22, b26, green, bears_ok, passes, years, s in passers[:3]:
        print(f"  {name:<24} CAGR {cg*100:.0f}%  DD {dd*100:.0f}%  ret/DD {rr:.2f}  OOS {oos:.2f}  bears {b18:+.0f}/{b22:+.0f}/{b26:+.0f}")

    # ---- winner = best gate-passer; full per-year ----
    if passers:
        w = passers[0]
        print("\n  WINNER per-bear: 2018 %+.0f%%  2022 %+.0f%%  2026 %+.0f%%" % (w[5],w[6],w[7]))
        print("  WINNER year-by-year ("+w[0]+"):")
        print("   "+" ".join(f"{y}:{w[11][y]:+.0f}%" for y in range(2017,2027)))

    # ---- verdict ----
    base = next(r for r in results if r[0]=="EMA50/200 (BASE)")
    beaters = [r for r in passers if r[0]!="EMA50/200 (BASE)" and r[3] > base[3]]
    print("\n"+"="*100)
    if beaters:
        print(f"VERDICT: {len(beaters)} trend indicator(s) BEAT base ret/DD {base[3]:.2f} while passing the full gate:")
        for r in beaters:
            print(f"   {r[0]}: ret/DD {r[3]:.2f} (vs {base[3]:.2f}), OOS {r[4]:.2f}")
    else:
        print(f"VERDICT: NOTHING beats base ret/DD {base[3]:.2f} on a full-gate basis. EMA50/200 stays.")
        # report the closest gate-passing alternative
        alt = [r for r in passers if r[0]!="EMA50/200 (BASE)"]
        if alt:
            best_alt = alt[0]
            print(f"   Closest gate-passing alt: {best_alt[0]} ret/DD {best_alt[3]:.2f}, OOS {best_alt[4]:.2f}")
    print("="*100)


if __name__=="__main__":
    main()
