#!/usr/bin/env python3
"""mtf_rsi_filter_test.py — Multi-timeframe RSI agreement filter sweep.

Baseline: v1 ULTIMATE config (matches strategies/day/bot_rsiscalp.py):
  RSI(9) 30/70 on 5m
  15m EMA20/50 trend gate + 0.25% GAP firmness
  ATR<0.60% + 1h move <2%
  Blocked hours: 5,6,11,12,13,20 UTC
  DCA 2 legs @ 0.5%, SL 0.6% from worst, TP 0.50/0.25%
  3x leverage, 0.04% commission, $5k initial

Variants tested (each adds ONE MTF-RSI filter on top of baseline):
  v0_baseline               : baseline only
  v1_5m70_1h_lt70           : SHORT needs 1h RSI < 70 (LONG needs 1h RSI > 30)
  v2_5m70_4h_lt65           : SHORT needs 4h RSI < 65 (LONG needs 4h RSI > 35)
  v3_1h_falling             : SHORT needs 1h RSI falling last 4 bars (LONG rising)
  v4_5m_AND_1h_extreme      : SHORT needs 5m≥70 AND 1h≥30 (combo of #1 mirror)
  v5_1h_not_trend_continue  : SHORT needs 1h RSI < 50 (LONG needs 1h RSI > 50)
  v6_5m_3bar_confirm        : SHORT needs 5m RSI ≥70 for 3+ consecutive bars
  v7_rsi_divergence         : SHORT requires bearish divergence over last 12 bars
"""
from __future__ import annotations
import os, sys, argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fleet_backtest import rsi_series, ema, bb, atr

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "cache")


# ─── ULTIMATE config (matches live bot_rsiscalp.py) ───
@dataclass
class Cfg:
    rsi_period:        int    = 9
    rsi_os:            float  = 30.0
    rsi_ob:            float  = 70.0
    gap_min_pct:       float  = 0.0025
    atr_max_pct:       float  = 0.60
    one_h_move_max:    float  = 2.0
    blocked_hours:     tuple  = (5, 6, 11, 12, 13, 20)
    dca_levels:        int    = 2
    sl_pct:            float  = 0.006  # ULTIMATE: 0.6% from worst
    dca_spacing:       float  = 0.005
    tp_single:         float  = 0.005
    tp_dca:            float  = 0.0025
    leverage:          float  = 3.0
    cooldown_bars:     int    = 3
    weekend_qty_mult:  float  = 2.0
    daily_max_loss:    float  = 200.0
    use_trend_flip_exit: bool = True
    # MTF-RSI filter knobs
    mtf_mode:          str    = "none"   # see VARIANTS below
    mtf_1h_short_max:  float  = 70.0     # SHORT requires 1h RSI < this
    mtf_1h_long_min:   float  = 30.0     # LONG  requires 1h RSI > this
    mtf_4h_short_max:  float  = 65.0
    mtf_4h_long_min:   float  = 35.0
    mtf_1h_falling_n:  int    = 4
    mtf_5m_confirm_n:  int    = 3
    mtf_div_lookback:  int    = 12


VARIANTS = [
    ("v0_baseline",              "none"),
    ("v1_5m70_1h_lt70",          "1h_extreme"),       # SHORT: 1h<70
    ("v2_5m70_4h_lt65",          "4h_extreme"),       # SHORT: 4h<65
    ("v3_1h_falling",            "1h_falling"),       # SHORT: 1h RSI falling last 4
    ("v4_5m_AND_1h_30",          "1h_mild_extreme"),  # SHORT: 1h>30  (mirror cond)
    ("v5_1h_no_trend_continue",  "1h_50_split"),      # SHORT: 1h<50
    ("v6_5m_3bar_confirm",       "5m_multi_bar"),     # SHORT: 5m≥70 for 3+ bars
    ("v7_rsi_divergence",        "rsi_divergence"),   # SHORT: bearish div
]


@dataclass
class Trade:
    entry_t:  pd.Timestamp
    side:     str
    entry_px: float
    qty:      float
    legs:     int   = 1
    avg:      float = 0.0
    worst:    float = 0.0
    exit_t:   pd.Timestamp = None
    exit_px:  float = 0.0
    reason:   str   = ""
    pnl:      float = 0.0


def mtf_filter_ok(i, bar, sig, df, cfg):
    """Return True if entry passes the MTF-RSI filter for this variant."""
    m = cfg.mtf_mode
    if m == "none":
        return True

    if m == "1h_extreme":
        rh = bar.get("rsi_1h")
        if pd.isna(rh): return False
        if sig == "SHORT" and rh >= cfg.mtf_1h_short_max: return False
        if sig == "LONG"  and rh <= cfg.mtf_1h_long_min:  return False
        return True

    if m == "4h_extreme":
        r4 = bar.get("rsi_4h")
        if pd.isna(r4): return False
        if sig == "SHORT" and r4 >= cfg.mtf_4h_short_max: return False
        if sig == "LONG"  and r4 <= cfg.mtf_4h_long_min:  return False
        return True

    if m == "1h_falling":
        # need cfg.mtf_1h_falling_n bars of 1h RSI; check monotone trend
        if i < cfg.mtf_1h_falling_n * 12: return False
        # sample 1h RSI at each hourly step back
        step = 12  # 12 × 5m = 1h
        vals = []
        for k in range(cfg.mtf_1h_falling_n):
            v = df["rsi_1h"].iloc[i - k*step]
            if pd.isna(v): return False
            vals.append(v)
        # vals = [now, 1h_ago, 2h_ago, 3h_ago]
        if sig == "SHORT":
            # require monotone falling (each newer val < older val)
            for k in range(len(vals)-1):
                if vals[k] >= vals[k+1]: return False
        else:  # LONG: require monotone rising
            for k in range(len(vals)-1):
                if vals[k] <= vals[k+1]: return False
        return True

    if m == "1h_mild_extreme":
        # SHORT requires 1h RSI > 30 (i.e. not already oversold)
        # LONG  requires 1h RSI < 70 (i.e. not already overbought)
        rh = bar.get("rsi_1h")
        if pd.isna(rh): return False
        if sig == "SHORT" and rh <= 30: return False
        if sig == "LONG"  and rh >= 70: return False
        return True

    if m == "1h_50_split":
        # Skip if 1h RSI in trend-continuation zone
        # SHORT: skip if 1h>50 (uptrend continuation)  → require 1h<50
        # LONG : skip if 1h<50 (downtrend continuation) → require 1h>50
        rh = bar.get("rsi_1h")
        if pd.isna(rh): return False
        if sig == "SHORT" and rh >= 50: return False
        if sig == "LONG"  and rh <= 50: return False
        return True

    if m == "5m_multi_bar":
        n = cfg.mtf_5m_confirm_n
        if i < n: return False
        last = df["rsi"].iloc[i-n+1:i+1].values
        if np.any(np.isnan(last)): return False
        if sig == "SHORT":
            return bool(np.all(last >= cfg.rsi_ob))
        else:
            return bool(np.all(last <= cfg.rsi_os))

    if m == "rsi_divergence":
        # bearish div for SHORT: price new high but RSI lower than its high in window
        # bullish div for LONG : price new low  but RSI higher than its low  in window
        n = cfg.mtf_div_lookback
        if i < n: return False
        win_high_px  = df["high"].iloc[i-n+1:i+1].max()
        win_low_px   = df["low"].iloc[i-n+1:i+1].min()
        win_rsi      = df["rsi"].iloc[i-n+1:i+1].values
        if np.all(np.isnan(win_rsi)): return False
        c_now = df["close"].iloc[i]
        r_now = df["rsi"].iloc[i]
        if pd.isna(r_now): return False
        # find prior bar in window with the highest price (for bearish div check)
        if sig == "SHORT":
            idx_max_px = df["close"].iloc[i-n+1:i].idxmax() if (i-n+1) < i else None
            if idx_max_px is None: return False
            prior_high_px  = df["close"].iloc[idx_max_px]
            prior_rsi_at_h = df["rsi"].iloc[idx_max_px]
            if pd.isna(prior_rsi_at_h): return False
            # bearish div = current price >= prior high AND current RSI < prior RSI
            return (c_now >= prior_high_px) and (r_now < prior_rsi_at_h)
        else:
            idx_min_px = df["close"].iloc[i-n+1:i].idxmin() if (i-n+1) < i else None
            if idx_min_px is None: return False
            prior_low_px  = df["close"].iloc[idx_min_px]
            prior_rsi_at_l = df["rsi"].iloc[idx_min_px]
            if pd.isna(prior_rsi_at_l): return False
            return (c_now <= prior_low_px) and (r_now > prior_rsi_at_l)

    return True  # unknown → permissive


def simulate(df, cfg, initial=5000.0, commission=0.0004):
    bal = initial
    peak = initial
    pos = None
    trades = []
    eq = []
    cd_until = -1
    daily_loss = 0.0
    cur_day = None
    daily_block_until_day = None

    rsi_arr = df["rsi"].values
    m15_t = df["m15_trend"].values
    m15_g = df["m15_gap_pct"].values
    atr_arr = df["atr_14"].values
    close_arr = df["close"].values
    high_arr = df["high"].values
    low_arr = df["low"].values
    open_arr = df["open"].values
    ts_arr = df["timestamp"].values
    hours = pd.DatetimeIndex(df["timestamp"]).hour.values
    dows  = pd.DatetimeIndex(df["timestamp"]).dayofweek.values  # 5=Sat, 6=Sun
    days  = pd.DatetimeIndex(df["timestamp"]).date

    for i in range(len(df)):
        # daily loss bookkeeping (reset on new UTC day)
        if days[i] != cur_day:
            cur_day = days[i]
            daily_loss = 0.0
            # daily_block clears once we cross to a new day
            if daily_block_until_day is not None and cur_day > daily_block_until_day:
                daily_block_until_day = None

        c, h, l, o = close_arr[i], high_arr[i], low_arr[i], open_arr[i]
        t = ts_arr[i]

        # ── manage open position ──
        if pos is not None:
            side = pos.side
            sl_px = pos.worst * (1 + cfg.sl_pct) if side == "SHORT" else pos.worst * (1 - cfg.sl_pct)
            tp_pct = cfg.tp_single if pos.legs == 1 else cfg.tp_dca
            tp_px = pos.avg * (1 - tp_pct) if side == "SHORT" else pos.avg * (1 + tp_pct)
            dca_px = None
            if pos.legs < cfg.dca_levels:
                dca_px = pos.worst * (1 + cfg.dca_spacing) if side == "SHORT" else pos.worst * (1 - cfg.dca_spacing)

            hit_sl  = (side == "SHORT" and h >= sl_px) or (side == "LONG" and l <= sl_px)
            hit_dca = dca_px is not None and ((side == "SHORT" and h >= dca_px) or (side == "LONG" and l <= dca_px))
            hit_tp  = (side == "SHORT" and l <= tp_px) or (side == "LONG" and h >= tp_px)

            closed_now = False
            if hit_sl:
                exit_px = sl_px
                gross = (pos.avg - exit_px) * pos.qty if side == "SHORT" else (exit_px - pos.avg) * pos.qty
                fees = exit_px * pos.qty * commission
                net = gross - fees
                bal += net; daily_loss += min(net, 0.0)
                pos.exit_t = t; pos.exit_px = exit_px; pos.reason = "SL"; pos.pnl = net
                trades.append(pos); pos = None; closed_now = True
                cd_until = i + cfg.cooldown_bars
            elif hit_dca:
                fill_px = dca_px
                old_q = pos.qty; new_q = old_q
                pos.avg = (pos.avg*old_q + fill_px*new_q) / (old_q+new_q)
                pos.qty = old_q + new_q
                pos.worst = fill_px
                pos.legs += 1
                new_tp_pct = cfg.tp_dca
                new_tp_px = pos.avg * (1 - new_tp_pct) if side == "SHORT" else pos.avg * (1 + new_tp_pct)
                if (side == "SHORT" and l <= new_tp_px) or (side == "LONG" and h >= new_tp_px):
                    exit_px = new_tp_px
                    gross = (pos.avg - exit_px) * pos.qty if side == "SHORT" else (exit_px - pos.avg) * pos.qty
                    fees = exit_px * pos.qty * commission
                    net = gross - fees
                    bal += net
                    if net < 0: daily_loss += net
                    pos.exit_t = t; pos.exit_px = exit_px; pos.reason = "TP"; pos.pnl = net
                    trades.append(pos); pos = None; closed_now = True
            elif hit_tp:
                exit_px = tp_px
                gross = (pos.avg - exit_px) * pos.qty if side == "SHORT" else (exit_px - pos.avg) * pos.qty
                fees = exit_px * pos.qty * commission
                net = gross - fees
                bal += net
                pos.exit_t = t; pos.exit_px = exit_px; pos.reason = "TP"; pos.pnl = net
                trades.append(pos); pos = None; closed_now = True

            # Trend-flip exit (close on 15m EMA reversal)
            if pos is not None and cfg.use_trend_flip_exit:
                trend = m15_t[i]
                if pos.side == "LONG" and trend == "DOWN":
                    exit_px = c
                    gross = (exit_px - pos.avg) * pos.qty
                    fees = exit_px * pos.qty * commission
                    net = gross - fees
                    bal += net
                    if net < 0: daily_loss += net
                    pos.exit_t = t; pos.exit_px = exit_px; pos.reason = "TF"; pos.pnl = net
                    trades.append(pos); pos = None
                    cd_until = i + cfg.cooldown_bars
                elif pos is not None and pos.side == "SHORT" and trend == "UP":
                    exit_px = c
                    gross = (pos.avg - exit_px) * pos.qty
                    fees = exit_px * pos.qty * commission
                    net = gross - fees
                    bal += net
                    if net < 0: daily_loss += net
                    pos.exit_t = t; pos.exit_px = exit_px; pos.reason = "TF"; pos.pnl = net
                    trades.append(pos); pos = None
                    cd_until = i + cfg.cooldown_bars

            # daily loss circuit breaker
            if daily_loss <= -cfg.daily_max_loss:
                daily_block_until_day = cur_day

        peak = max(peak, bal)
        eq.append(bal)

        # ── entries ──
        if pos is not None or i < cd_until: continue
        if i + 1 >= len(df): break
        if daily_block_until_day is not None: continue

        rv = rsi_arr[i]
        if np.isnan(rv): continue
        sig = None
        if rv <= cfg.rsi_os: sig = "LONG"
        elif rv >= cfg.rsi_ob: sig = "SHORT"
        if sig is None: continue

        if hours[i] in cfg.blocked_hours: continue

        # 15m trend gate
        tr = m15_t[i]
        if tr is None or (isinstance(tr, float) and np.isnan(tr)): continue
        if (sig == "LONG" and tr != "UP") or (sig == "SHORT" and tr != "DOWN"): continue

        # GAP firmness
        gap = m15_g[i]
        if np.isnan(gap) or abs(gap) < cfg.gap_min_pct*100: continue

        # ATR filter
        av = atr_arr[i]
        if np.isnan(av) or av <= 0: continue
        if (av / c) * 100 > cfg.atr_max_pct: continue

        # 1h cumulative move
        if i >= 12:
            close_1h = close_arr[i-12]
            chg_1h = (c / close_1h - 1) * 100
            if sig == "SHORT" and chg_1h > cfg.one_h_move_max: continue
            if sig == "LONG"  and chg_1h < -cfg.one_h_move_max: continue

        # MTF-RSI filter (the variant under test)
        bar = df.iloc[i]
        if not mtf_filter_ok(i, bar, sig, df, cfg): continue

        next_o = open_arr[i+1]
        q_mult = cfg.weekend_qty_mult if dows[i+1] in (5, 6) else 1.0
        qty = (bal * 0.95 * cfg.leverage) / next_o / cfg.dca_levels * q_mult
        pos = Trade(entry_t=ts_arr[i+1], side=sig, entry_px=next_o,
                    qty=qty, legs=1, avg=next_o, worst=next_o)

    if not trades:
        return {"trades":0, "final":bal, "ret_pct":0, "max_dd_pct":0, "wr":0, "pf":0,
                "wins":0, "losses":0, "trades_list":[]}

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    eq_arr = np.array(eq)
    peaks = np.maximum.accumulate(eq_arr)
    dd = (eq_arr - peaks) / peaks * 100

    return {
        "trades": len(trades), "wins": len(wins), "losses": len(losses),
        "final": bal, "ret_pct": (bal/initial - 1)*100,
        "max_dd_pct": float(dd.min()),
        "wr": len(wins)/len(trades)*100,
        "pf": (sum(t.pnl for t in wins) / abs(sum(t.pnl for t in losses))) if losses and sum(t.pnl for t in losses) < 0 else float("inf"),
        "trades_list": trades,
    }


def _year(ts):
    return pd.Timestamp(ts).year

def yearly_breakdown(trades, year):
    yr = [t for t in trades if _year(t.exit_t) == year]
    if not yr: return None
    pnl = sum(t.pnl for t in yr)
    wins = [t for t in yr if t.pnl > 0]
    return {"n": len(yr), "pnl": pnl, "wr": len(wins)/len(yr)*100}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-05-03")
    ap.add_argument("--end",   default=None)
    args = ap.parse_args()

    print("Loading 5m data...")
    df = pd.read_csv(os.path.join(CACHE, "BTCUSDT_5m.csv"))
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df[df["timestamp"] >= args.start]
    if args.end: df = df[df["timestamp"] < args.end]
    df = df.reset_index(drop=True)
    print(f"Loaded {len(df):,} bars: {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")

    print("Computing 5m indicators...")
    df["rsi"] = rsi_series(df["close"], 9)
    up, mid, lo = bb(df["close"], 20, 2.0)
    df["bb_up"], df["bb_mid"], df["bb_low"] = up, mid, lo
    df["vol_sma20"] = df["volume"].rolling(20).mean()
    df["atr_14"] = atr(df, 14)

    # 15m EMA trend gate + gap
    print("Computing 15m trend...")
    df15 = (df.set_index("timestamp")
              .resample("15min")
              .agg({"close":"last","high":"max","low":"min","open":"first"})
              .dropna())
    df15["ema20"] = ema(df15["close"], 20)
    df15["ema50"] = ema(df15["close"], 50)
    df15["m15_gap_pct"] = (df15["ema20"] - df15["ema50"]) / df15["ema50"] * 100
    df15["m15_trend"]   = np.where(df15["ema20"] > df15["ema50"], "UP", "DOWN")
    df15 = df15.reset_index()
    df = pd.merge_asof(df.sort_values("timestamp"),
                       df15[["timestamp","m15_trend","m15_gap_pct"]].sort_values("timestamp"),
                       on="timestamp", direction="backward")

    # 1h RSI (forward-filled to 5m)
    print("Computing 1h RSI...")
    df1h = (df.set_index("timestamp")["close"]
              .resample("1h").last().dropna())
    rsi_1h = rsi_series(df1h, 14)
    df["rsi_1h"] = rsi_1h.reindex(df["timestamp"], method="ffill").values

    # 4h RSI
    print("Computing 4h RSI...")
    df4h = (df.set_index("timestamp")["close"]
              .resample("4h").last().dropna())
    rsi_4h = rsi_series(df4h, 14)
    df["rsi_4h"] = rsi_4h.reindex(df["timestamp"], method="ffill").values

    print(f"\nRunning {len(VARIANTS)} variants...\n")
    results = []
    for name, mode in VARIANTS:
        cfg = Cfg(mtf_mode=mode)
        r = simulate(df, cfg)
        r["name"] = name; r["mode"] = mode
        results.append(r)
        print(f"  {name:<28} mode={mode:<18} trades={r['trades']:>5} "
              f"ret={r['ret_pct']:+8.1f}% DD={r['max_dd_pct']:+6.1f}% WR={r['wr']:5.1f}%")

    # Comparison table
    print("\n" + "═"*100)
    print(f"{'Variant':<28} {'Trades':>7} {'WR%':>6} {'Final $':>10} {'Ret %':>9} {'MaxDD %':>8} {'PF':>5}")
    print("─"*100)
    base = results[0]
    for r in results:
        delta = r["ret_pct"] - base["ret_pct"]
        pf_str = f"{r['pf']:.2f}" if r['pf'] != float("inf") else "inf"
        print(f"{r['name']:<28} {r['trades']:>7} {r['wr']:>5.1f}% ${r['final']:>9.0f} "
              f"{r['ret_pct']:>+8.1f}% {r['max_dd_pct']:>+7.1f}% {pf_str:>5}"
              + (f"   Δ{delta:+.0f}%" if r is not base else "   (baseline)"))

    # 2024 deep-dive
    print("\n" + "═"*100)
    print("2024 BREAKDOWN (BTC strong-bull / squeeze-shorts year)")
    print("─"*100)
    print(f"{'Variant':<28} {'2024 N':>7} {'2024 PnL $':>12} {'2024 WR%':>9}")
    for r in results:
        y = yearly_breakdown(r["trades_list"], 2024)
        if y is None:
            print(f"{r['name']:<28}  no trades in 2024")
        else:
            print(f"{r['name']:<28} {y['n']:>7} ${y['pnl']:>+11,.0f} {y['wr']:>8.1f}%")

    # All years
    print("\n" + "═"*100)
    print("YEAR-BY-YEAR PnL ($)  — baseline vs each variant")
    print("─"*100)
    years = sorted({_year(t.exit_t) for r in results for t in r["trades_list"]})
    hdr = f"{'Variant':<28} " + " ".join(f"{y:>10}" for y in years)
    print(hdr)
    for r in results:
        cells = []
        for y in years:
            yr = yearly_breakdown(r["trades_list"], y)
            cells.append(f"{yr['pnl']:>+10,.0f}" if yr else f"{'-':>10}")
        print(f"{r['name']:<28} " + " ".join(cells))


if __name__ == "__main__":
    main()
