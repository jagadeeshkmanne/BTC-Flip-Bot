#!/usr/bin/env python3
"""tune_multicoin.py — does the my-V3 COMBINED edge generalize beyond BTC?

Runs the my-V3 COMBINED config (tuned LONG: MTF gate + ATR-3.5 structural stop cap 8% +
break-even@1R(+1%) + pyramid@2R frac1.0;  slow-gated SHORT: only price<12mo-SMA, short_size
0.25, ATR-4 stop cap 12%, pyramid@2R) on BTC / ETH / BNB / SOL at 4h, 1x.

Two regime modes:
  (A) each coin gated by its OWN 4h+daily EMA50/200 regime;
  (B) ALTS gated by BTC's regime (BTC stays on its own).

Then builds an EQUAL-WEIGHT, periodically-REBALANCED basket from the per-coin equity curves
(each coin = 1/N of capital each rebalance step) over the window where ALL coins have data.

Generalized regime builder uses bt.load(coin,'4h') + bt.load(coin,'1d') (HTF causal: daily
EMA shift(1), merge_asof backward onto 4h) + 12mo-SMA short gate on the coin's own 4h close.

ANTI-BUG: BTC's canonical base (98%/-31%/3.15 OOS5.06 from backtest_myv3_final via the volume
CSV) is reprinted for reference; the generalized bt.load BTC path is slightly different (other
source CSV, ~3.5mo less warmup) so we sanity-check it is in the same ballpark, not identical.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_final import run          # unified long+short engine (COMBINED defaults)
from backtest_myv3_shorts import regimes as btc_regimes, m  # canonical BTC + metrics

COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
SMA_12MO_4H = 12 * 30 * 6   # 12 months of 4h bars (same constant as backtest_myv3_final.main)


# ----------------------------------------------------------------------------------------
def build_regime(coin):
    """GENERALIZED per-coin regime via bt.load. Returns (df_4h, bull, bear_own).
    bull      = (4h EMA50>200) AND (prior-day EMA50>200)            [merge_asof backward, shift1]
    bear_own  = (4h EMA50<200) AND (prior-day EMA50<200)            [for completeness/symmetry]
    The SHORT gate actually used is the slow 12mo-SMA gate, built per-coin in run_coin().
    """
    df = bt.load(coin, "4h")
    dd = bt.load(coin, "1d")
    # daily trend, causal: compute on closed daily bars then shift 1 day forward
    d_up = (bt.ema(dd["close"], 50) > bt.ema(dd["close"], 200)).shift(1).fillna(False).astype(bool)
    d_dn = (bt.ema(dd["close"], 50) < bt.ema(dd["close"], 200)).shift(1).fillna(False).astype(bool)
    ts = pd.to_datetime(df["timestamp"])

    def mp(src):
        return pd.merge_asof(
            pd.DataFrame({"ts": ts}).sort_values("ts"),
            pd.DataFrame({"ts": pd.to_datetime(dd["timestamp"]), "b": src.values}).sort_values("ts"),
            on="ts", direction="backward")["b"].fillna(False).astype(bool).values

    f_up = (bt.ema(df["close"], 50) > bt.ema(df["close"], 200)).values
    f_dn = (bt.ema(df["close"], 50) < bt.ema(df["close"], 200)).values
    bull = f_up & mp(d_up)
    bear_own = f_dn & mp(d_dn)
    return df, bull, bear_own


def slow_short_gate(df):
    """price < 12-month SMA(close) shift1  — the COMBINED short gate, on this coin's 4h close."""
    c = df["close"]
    return (c < bt.sma(c, SMA_12MO_4H).shift(1)).fillna(False).values


def align_bull_to(df, src_df, src_bull):
    """Re-express a regime (boolean array on src_df's 4h grid) onto df's 4h timestamps via
    merge_asof backward. Used for MODE B: gate alts on BTC's regime (BTC bull/bear sampled at
    or before each alt 4h bar). Causal: backward direction only looks at past/current BTC bars."""
    ts = pd.to_datetime(df["timestamp"])
    src_ts = pd.to_datetime(src_df["timestamp"])
    return pd.merge_asof(
        pd.DataFrame({"ts": ts}).sort_values("ts"),
        pd.DataFrame({"ts": src_ts, "b": np.asarray(src_bull, bool)}).sort_values("ts"),
        on="ts", direction="backward")["b"].fillna(False).astype(bool).values


# ----------------------------------------------------------------------------------------
def run_coin(df, bull, bear_slow):
    """COMBINED config = backtest_myv3_final.run defaults (atr3.5/be1%/short0.25/s_atr4/s_cap12%),
    long+short, lev1, pyramid 2R frac1.0. bear here is the slow 12mo-SMA gate."""
    return run(df, bull, bear_slow, allow_long=True, allow_short=True)


def count_trades(df, bull, bear_slow):
    """Re-run a thin clone of the engine purely to count entries (long & short separately)."""
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    a = bt.atr(df, 14).values; n = len(df)
    FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT
    cash = 1.0; units = 0.0; side = 0; entry = 0.0; stop = 0.0; R = 0.0; pyrd = False; notional0 = 0.0
    armedL = True; armedS = True
    nlong = nshort = 0
    # mirror backtest_myv3_final.run defaults
    short_size = 0.25; pyr_r = 2.0; pyr_frac = 1.0; be_r = 1.0; atr_mult = 3.5
    sl_cap = 0.08; be_buf = 0.01; s_atr = 4.0; s_cap = 0.12; s_pyr_r = 2.0; lev = 1.0
    for i in range(16, n - 1):
        oN, hN, lN, cN = o[i+1], h[i+1], l[i+1], c[i+1]
        if not bull[i]: armedL = True
        if not bear_slow[i]: armedS = True
        if side != 0:
            hit = (lN <= stop) if side == 1 else (hN >= stop)
            regime_out = (not bull[i]) if side == 1 else (not bear_slow[i])
            if hit or regime_out:
                fpx = (stop if hit else oN); units = 0.0; side = 0; pyrd = False
            else:
                prof = ((cN-entry)/R) if side == 1 else ((entry-cN)/R)
                if prof >= be_r:
                    be = entry*(1+be_buf) if side == 1 else entry*(1-be_buf)
                    stop = max(stop, be) if side == 1 else min(stop, be)
                pr = s_pyr_r if side == -1 else pyr_r
                if pr is not None and not pyrd and prof >= pr:
                    pyrd = True; stop = max(stop, entry) if side == 1 else min(stop, entry)
        if side == 0:
            go = 0
            if bull[i] and armedL: go = 1
            elif bear_slow[i] and armedS: go = -1
            if go != 0:
                if go == 1:
                    st = max(c[i]-atr_mult*a[i], c[i]*(1-sl_cap)); entry = oN*(1+SLIP)
                else:
                    st = min(c[i]+s_atr*a[i], c[i]*(1+s_cap)); entry = oN*(1-SLIP)
                ok = (entry-st > 0) if go == 1 else (st-entry > 0)
                if ok:
                    stop = st; R = abs(entry-st); side = go; pyrd = False
                    if go == 1: armedL = False; nlong += 1
                    else: armedS = False; nshort += 1
    return nlong, nshort


def yr(s, y):
    seg = s[s.index.year == y]
    return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg) > 20 else None


def stats_row(label, s, nl, ns):
    cg, dd, rr = m(s)
    o = m(s[s.index >= s.index[int(len(s)*0.6)]])
    rng = f"{s.index[0].date()}->{s.index[-1].date()}"
    print(f"  {label:<22}{cg*100:>6.0f}%{dd*100:>6.0f}%{rr:>7.2f}{o[2]:>7.2f}"
          f"{nl:>5}{ns:>5}{nl+ns:>5}   {rng}")
    return s


def basket_curve(series_list):
    """Equal-weight, rebalanced basket. Reindex each curve onto the common 4h grid (ffill),
    convert each to per-bar returns, average the returns across coins each bar (= daily/bar
    rebalance to equal weight), compound. Window = where ALL coins have data."""
    # common window
    start = max(s.index[0] for s in series_list)
    end = min(s.index[-1] for s in series_list)
    rets = []
    grid = None
    for s in series_list:
        s = s[(s.index >= start) & (s.index <= end)]
        r = s.pct_change().fillna(0.0)
        if grid is None:
            grid = r.index
        rets.append(r.reindex(grid).fillna(0.0))
    avg = pd.concat(rets, axis=1).mean(axis=1)   # equal-weight rebalanced return per bar
    eq = (1 + avg).cumprod()
    return eq


# ----------------------------------------------------------------------------------------
def main():
    print("=" * 100)
    print("MULTI-COIN my-V3 COMBINED — does the edge generalize? (4h, 1x, honest fees)")
    print("=" * 100)

    # canonical BTC base (volume CSV path) — anti-bug reference
    bdf, bbull, _bbear = btc_regimes()
    bbear_slow = (bdf["close"] < bt.sma(bdf["close"], SMA_12MO_4H).shift(1)).fillna(False).values
    bs = run(bdf, bbull, bbear_slow, allow_long=True, allow_short=True)
    cg, dd, rr = m(bs); o = m(bs[bs.index >= bs.index[int(len(bs)*0.6)]])
    print(f"\n[ANCHOR] canonical BTC base (volume CSV, backtest_myv3_final): "
          f"CAGR {cg*100:.0f}%  DD {dd*100:.0f}%  ret/DD {rr:.2f}  OOS {o[2]:.2f}  "
          f"(expect ~98/-31/3.15/5.06)")

    # build generalized regimes once per coin
    reg = {}
    for coin in COINS:
        df, bull, bear_own = build_regime(coin)
        reg[coin] = dict(df=df, bull=bull, bear_own=bear_own, short=slow_short_gate(df))

    hdr = (f"  {'coin/mode':<22}{'CAGR':>7}{'DD':>6}{'ret/DD':>7}{'OOS':>7}"
           f"{'#L':>5}{'#S':>5}{'#tot':>5}   date-range")

    # ----- MODE A: each coin on its OWN regime -----
    print("\n" + "-" * 100)
    print("MODE A — each coin gated by its OWN 4h+daily regime")
    print("-" * 100); print(hdr)
    seriesA = {}
    for coin in COINS:
        r = reg[coin]
        s = run_coin(r["df"], r["bull"], r["short"])
        nl, ns = count_trades(r["df"], r["bull"], r["short"])
        seriesA[coin] = stats_row(coin, s, nl, ns)

    # ----- MODE B: alts on BTC's regime, BTC on its own -----
    print("\n" + "-" * 100)
    print("MODE B — ALTS gated by BTC's regime (bull AND slow-short), BTC on its own")
    print("-" * 100); print(hdr)
    seriesB = {}
    btc_df = reg["BTCUSDT"]["df"]; btc_bull = reg["BTCUSDT"]["bull"]; btc_short = reg["BTCUSDT"]["short"]
    for coin in COINS:
        r = reg[coin]
        if coin == "BTCUSDT":
            bull_use, short_use = r["bull"], r["short"]
        else:
            bull_use = align_bull_to(r["df"], btc_df, btc_bull)
            short_use = align_bull_to(r["df"], btc_df, btc_short)
        s = run_coin(r["df"], bull_use, short_use)
        nl, ns = count_trades(r["df"], bull_use, short_use)
        seriesB[coin] = stats_row(coin, s, nl, ns)

    # ----- EQUAL-WEIGHT BASKETS -----
    def report_basket(tag, series, n_trades_total):
        eq = basket_curve(list(series.values()))
        cg, dd, rr = m(eq); o = m(eq[eq.index >= eq.index[int(len(eq)*0.6)]])
        print(f"\n  {tag}: CAGR {cg*100:.0f}%  DD {dd*100:.0f}%  ret/DD {rr:.2f}  "
              f"OOS {o[2]:.2f}  total-trades {n_trades_total}  "
              f"window {eq.index[0].date()}->{eq.index[-1].date()}")
        print("    year-by-year:", end=" ")
        for y in range(2021, 2027):
            v = yr(eq, y)
            print(f"{y} {('%+.0f%%' % v) if v is not None else '--'}", end="   ")
        print()
        return eq

    print("\n" + "=" * 100)
    print("EQUAL-WEIGHT REBALANCED BASKET (window = all 4 coins have data, ~2021-01 on)")
    print("=" * 100)
    ntA = sum(count_trades(reg[c]["df"], reg[c]["bull"], reg[c]["short"])[0] +
              count_trades(reg[c]["df"], reg[c]["bull"], reg[c]["short"])[1] for c in COINS)
    eqA = report_basket("BASKET (Mode A regimes)", seriesA, ntA)

    # Mode B trade totals
    ntB = 0
    for coin in COINS:
        r = reg[coin]
        if coin == "BTCUSDT":
            bu, su = r["bull"], r["short"]
        else:
            bu = align_bull_to(r["df"], btc_df, btc_bull); su = align_bull_to(r["df"], btc_df, btc_short)
        nl, ns = count_trades(r["df"], bu, su); ntB += nl + ns
    eqB = report_basket("BASKET (Mode B regimes)", seriesB, ntB)

    # ----- BTC-alone reference over the SAME basket window -----
    print("\n" + "-" * 100)
    print("BTC-ALONE over the basket window (for the diversification comparison)")
    print("-" * 100)
    start = eqA.index[0]; end = eqA.index[-1]
    bA = seriesA["BTCUSDT"]; bw = bA[(bA.index >= start) & (bA.index <= end)]
    cg, dd, rr = m(bw); o = m(bw[bw.index >= bw.index[int(len(bw)*0.6)]])
    nl, ns = count_trades(reg["BTCUSDT"]["df"], reg["BTCUSDT"]["bull"], reg["BTCUSDT"]["short"])
    print(f"  BTC-alone (basket window): CAGR {cg*100:.0f}%  DD {dd*100:.0f}%  "
          f"ret/DD {rr:.2f}  OOS {o[2]:.2f}  trades {nl+ns}")


if __name__ == "__main__":
    main()
