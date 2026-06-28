#!/usr/bin/env python3
"""backtest_quicktp_dynlev.py — honest test of a QUICK-TP "scalp" strategy on 4h/1d with
DYNAMIC (volatility-targeted) leverage, long AND short, trend-aligned entries.

This exists because the user asked for the opposite of STRATEGY.md's findings (quick TP +
dynamic leverage). So we build it for real and report the HONEST numbers under the same rules:
  - signals on CLOSED bars, fills at NEXT bar OPEN
  - fee 0.055%/side + 0.05% slippage/side, costs scale with leverage (notional = lev x equity)
  - intrabar TP/SL on REAL high/low, LIQUIDATION-FIRST then STOP-FIRST then TP on a straddle
  - leverage liquidation modeled (linear perp): adverse move >= 1/lev - mmr  =>  margin wiped
  - in-sample / out-of-sample 60/40 split; metrics = CAGR + maxDD + ret/DD

Dynamic leverage = vol target: lev_i = clamp(target_vol / realized_vol_i, lev_min, lev_max),
realized_vol = ATR/price (per-bar). Lever UP when calm, DOWN when volatile.

Entry modes (always trend-aligned to 4h EMA50/200):
  breakout : long on close > prior `brk`-bar high (uptrend); short on close < prior-low (downtrend)
  pullback : long on RSI dipping < `rsi_lo` within an uptrend; short on RSI > `rsi_hi` in downtrend
  macd     : long on MACD cross-up in uptrend; short on cross-down in downtrend
"""
from __future__ import annotations
import os, sys, itertools
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT
MMR = 0.005  # maintenance margin rate for liquidation model


def signals(df, mode, brk=10, rsi_lo=40, rsi_hi=60, allow_long=True, allow_short=True):
    c = df["close"]
    up = (bt.ema(c, 50) > bt.ema(c, 200))
    dn = ~up
    if mode == "breakout":
        hh = df["high"].rolling(brk).max().shift(1)
        ll = df["low"].rolling(brk).min().shift(1)
        long_sig = (c > hh) & up
        short_sig = (c < ll) & dn
    elif mode == "pullback":
        r = bt.rsi(c, 14)
        long_sig = (r < rsi_lo) & up
        short_sig = (r > rsi_hi) & dn
    elif mode == "macd":
        ml = bt.ema(c, 12) - bt.ema(c, 26); sig = bt.ema(ml, 9)
        x_up = (ml > sig) & (ml.shift(1) <= sig.shift(1))
        x_dn = (ml < sig) & (ml.shift(1) >= sig.shift(1))
        long_sig = x_up & up
        short_sig = x_dn & dn
    else:
        raise ValueError(mode)
    if not allow_long:  long_sig = long_sig & False
    if not allow_short: short_sig = short_sig & False
    return long_sig.fillna(False).values, short_sig.fillna(False).values


def run(df, mode="breakout", tp=0.01, sl=0.01, max_hold=6, trail_atr=None,
        target_vol=0.02, lev_min=1.0, lev_max=5.0, lev_fixed=None,
        atr_n=14, **kw):
    """Stateful leveraged single-position engine. Returns (equity Series, n_trades, wr, pf,
    n_liquidations).

    tp=None  -> no fixed take-profit (let winners run).
    trail_atr=k -> ATR trailing stop that only ratchets toward profit (ride winners, cut losers).
    """
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    a = bt.atr(df, atr_n).values
    rv = a / c  # realized per-bar vol proxy
    long_sig, short_sig = signals(df, mode, **kw)
    n = len(df); bal = 1.0
    side = 0; entry = lev = 0.0; tpx = slx = liqx = trail = 0.0; held = 0
    eq = np.ones(n); trades = []; liqs = 0

    def lev_for(i):
        if lev_fixed is not None:
            return lev_fixed
        if rv[i] <= 1e-9 or np.isnan(rv[i]):
            return lev_min
        return float(np.clip(target_vol / rv[i], lev_min, lev_max))

    def close_at(px, dirn):
        nonlocal bal, side
        fpx = px * (1 - SLIP * dirn)
        raw = (fpx / entry - 1) if dirn == 1 else (entry - fpx) / entry
        r = lev * raw - 2 * FEE * lev          # fees/slip on notional = lev x equity
        bal *= max(1 + r, 1e-9); trades.append(r); side = 0

    start = max(atr_n + 2, 205)  # need EMA200 warmed up
    for i in range(start, n - 1):
        oN, hN, lN, cN = o[i + 1], h[i + 1], l[i + 1], c[i + 1]
        if side != 0:
            held += 1
            eff_sl = max(slx, trail) if (side == 1 and trail) else (
                     min(slx, trail) if (side == -1 and trail) else slx)
            if side == 1:
                if lN <= liqx:   liqs += 1; close_at(liqx, 1)      # liquidation first
                elif lN <= eff_sl: close_at(eff_sl, 1)             # stop / trail (cut loser)
                elif tp and hN >= tpx: close_at(tpx, 1)            # optional fixed TP
                elif max_hold and held >= max_hold: close_at(oN, 1)
                elif trail_atr: trail = max(trail, cN - trail_atr * a[i + 1])  # ratchet up
            else:
                if hN >= liqx:   liqs += 1; close_at(liqx, -1)
                elif hN >= eff_sl: close_at(eff_sl, -1)
                elif tp and lN <= tpx: close_at(tpx, -1)
                elif max_hold and held >= max_hold: close_at(oN, -1)
                elif trail_atr: trail = min(trail, cN + trail_atr * a[i + 1])  # ratchet down
        if side == 0:
            if long_sig[i]:
                lev = lev_for(i); side = 1; entry = oN * (1 + SLIP); held = 0
                tpx = entry * (1 + tp) if tp else 0.0; slx = entry * (1 - sl)
                trail = (entry - trail_atr * a[i + 1]) if trail_atr else 0.0
                liqx = entry * (1 - (1 / lev - MMR))               # long liquidation price
            elif short_sig[i]:
                lev = lev_for(i); side = -1; entry = oN * (1 - SLIP); held = 0
                tpx = entry * (1 - tp) if tp else 0.0; slx = entry * (1 + sl)
                trail = (entry + trail_atr * a[i + 1]) if trail_atr else 0.0
                liqx = entry * (1 + (1 / lev - MMR))
        # mark-to-market equity
        if side == 0:
            eq[i + 1] = bal
        else:
            raw = (cN / entry - 1) if side == 1 else (entry - cN) / entry
            eq[i + 1] = max(bal * (1 + lev * raw), 1e-9)
    eqs = pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[start:]
    tr = np.array(trades); w = tr[tr > 0]; loss = tr[tr <= 0]
    wr = len(w) / len(tr) * 100 if len(tr) else 0.0
    pf = (w.sum() / -loss.sum()) if len(loss) and loss.sum() < 0 else (float("inf") if len(w) else 0.0)
    return eqs, len(tr), wr, pf, liqs


def monthly_stats(eq):
    """Income lens: % positive months, worst single month, # of months, avg month."""
    eq = eq.dropna()
    if len(eq) < 30:
        return dict(pos=0.0, worst=-1.0, nmonths=0, avg=0.0)
    m = eq.resample("ME").last().pct_change().dropna()
    if len(m) == 0:
        return dict(pos=0.0, worst=-1.0, nmonths=0, avg=0.0)
    return dict(pos=(m > 0).mean() * 100, worst=m.min(), nmonths=len(m), avg=m.mean())


def evaluate(df, **cfg):
    eq, nt, wr, pf, liqs = run(df, **cfg)
    full = bt.metrics(eq)
    is_eq, oos_eq = bt.oos_split(eq, 0.6)
    ms = monthly_stats(eq)
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    return dict(cfg=cfg, n=nt, wr=wr, pf=pf, liq=liqs, tpy=nt / yrs,
                cagr=full[0], dd=full[1], rdd=full[2],
                pos_m=ms["pos"], worst_m=ms["worst"], avg_m=ms["avg"], nmonths=ms["nmonths"],
                is_rdd=bt.metrics(is_eq)[2], is_cagr=bt.metrics(is_eq)[0],
                oos_rdd=bt.metrics(oos_eq)[2], oos_cagr=bt.metrics(oos_eq)[0],
                oos_dd=bt.metrics(oos_eq)[1])


if __name__ == "__main__":
    coin = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    tf = sys.argv[2] if len(sys.argv) > 2 else "4h"
    df = bt.load(coin, tf)
    print(f"# {coin} {tf}  bars={len(df)}  {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}\n")

    mode_arg = sys.argv[3] if len(sys.argv) > 3 else "ride"
    grid = []
    if mode_arg == "quicktp":            # original fixed quick-TP sweep (the bad one)
        for mode in ["breakout", "pullback", "macd"]:
            for tp in [0.005, 0.01, 0.02]:
                for sl in [0.01, 0.02]:
                    for mh in [3, 6, 12]:
                        for lev_cfg in [dict(lev_fixed=1.0), dict(lev_fixed=3.0),
                                        dict(target_vol=0.02, lev_min=1.0, lev_max=5.0),
                                        dict(target_vol=0.03, lev_min=1.0, lev_max=8.0)]:
                            grid.append(dict(mode=mode, tp=tp, sl=sl, max_hold=mh, **lev_cfg))
    else:                                # RIDE winners / cut losers: no fixed TP, ATR trail
        for mode in ["breakout", "pullback", "macd"]:
            for sl in [0.02, 0.04, 0.06]:           # initial hard stop (cut loser)
                for trail in [2.0, 3.0, 5.0]:        # ATR trail multiple (ride winner)
                    for lev_cfg in [dict(lev_fixed=1.0), dict(lev_fixed=2.0),
                                    dict(target_vol=0.025, lev_min=1.0, lev_max=4.0),
                                    dict(target_vol=0.04, lev_min=1.0, lev_max=6.0)]:
                        grid.append(dict(mode=mode, tp=None, sl=sl, max_hold=None,
                                         trail_atr=trail, **lev_cfg))

    rows = [evaluate(df, **g) for g in grid]
    rows = [r for r in rows if r["n"] >= 20]  # need enough trades to mean anything
    # INCOME lens: sort by % positive months, but only among configs that actually made money
    rows.sort(key=lambda r: (r["pf"] > 1.0, r["pos_m"], r["oos_rdd"]), reverse=True)

    print(f"{'mode':9} {'exit':>16} {'sl':>5} {'lev':>12} "
          f"{'t/yr':>5} {'wr%':>5} {'pf':>5} {'CAGR':>7} {'maxDD':>7} "
          f"{'pos_mo%':>7} {'worst_mo':>8} {'OOS_rDD':>7}")
    print("-" * 110)
    for r in rows[:25]:
        c = r["cfg"]
        lev = (f"fix{c.get('lev_fixed'):.0f}x" if "lev_fixed" in c
               else f"vt{c['target_vol']:.0%}<{c['lev_max']:.0f}x")
        exit_d = (f"trail{c['trail_atr']:.0f}xATR" if c.get("trail_atr")
                  else f"tp{c['tp']:.1%}/mh{c['max_hold']}")
        print(f"{c['mode']:9} {exit_d:>16} {c['sl']:>5.1%} {lev:>12} "
              f"{r['tpy']:>5.0f} {r['wr']:>5.1f} {r['pf']:>5.2f} {r['cagr']:>7.1%} {r['dd']:>7.1%} "
              f"{r['pos_m']:>7.0f} {r['worst_m']:>8.1%} {r['oos_rdd']:>7.2f}")
    # reference benchmark
    bh = bt.buyhold(df); m = bt.metrics(bh)
    print(f"\n  ref buy&hold {coin} {tf}:  CAGR {m[0]:.1%}  maxDD {m[1]:.1%}  r/DD {m[2]:.2f}")
