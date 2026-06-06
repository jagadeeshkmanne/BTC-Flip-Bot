#!/usr/bin/env python3
"""vol_filter_sweep.py — 2026-06-06

Test volatility-based filters layered on the v1 ULTIMATE baseline:
  - BB-width (5m, 20-bar) decile gates
  - ATR rate-of-change gates
  - Daily ATR ceiling
  - ATR-adaptive position sizing
  - Volume-spike entry gate (3x SMA20)
  - Volume-spike DCA gate
  - 5m-BB width squeeze gate
  - ATR percentile (rolling) ceiling

Baseline = v1 ULTIMATE described in task:
  RSI(9) <=30 / >=70 entries
  15m EMA20/50 trend gate
  GAP firmness |EMA20-EMA50|/EMA50 >= 0.25%
  5m ATR(14) / close <= 0.60%
  1h |move| <= 2.0%
  blocked hours UTC {5,6,11,12,13,20}
  TP adaptive 0.5%/0.25%  |  SL 0.6% from worst entry
  2-leg DCA @ 0.5%
  Weekend (Sat/Sun) 2x position size
  Daily-loss circuit breaker $200 (no entries rest of UTC day)
  15m trend-flip exit
  3x leverage  |  $5,000 start

Each variant changes ONE thing vs baseline. Reports total ret, MaxDD, WR,
2024-only PnL and trade count.
"""
from __future__ import annotations
import os, sys, math
import numpy as np
import pandas as pd
from fleet_backtest import rsi_series, ema, bb, atr

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "cache")
COMMISSION = 0.0004
LEVERAGE   = 3.0
INITIAL    = 5000.0


# ───────────────────────── feature engineering ─────────────────────────
def load_and_prep():
    df = pd.read_csv(os.path.join(CACHE, "BTCUSDT_5m.csv"))
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    # 5y window
    cutoff = df["timestamp"].max() - pd.Timedelta(days=365*5)
    df = df[df["timestamp"] >= cutoff].reset_index(drop=True)

    df["rsi"] = rsi_series(df["close"], 9)
    up, mid, lo = bb(df["close"], 20, 2.0)
    df["bb_up"], df["bb_mid"], df["bb_low"] = up, mid, lo
    df["bbw"] = (up - lo) / mid * 100        # % of price
    df["atr_14"] = atr(df, 14)
    df["atr_pct"] = df["atr_14"] / df["close"] * 100
    df["vol_sma20"] = df["volume"].rolling(20).mean()

    # ATR rate-of-change (12 bars = 1h)
    df["atr_chg_1h"] = df["atr_pct"] / df["atr_pct"].shift(12) - 1

    # Daily ATR from 5m: 288 bars/day rolling
    df["atr_daily_pct"] = df["atr_pct"].rolling(288).mean()

    # BBW historical deciles over rolling 30d (8640 bars). Use rank-percentile.
    df["bbw_pct_rank"] = df["bbw"].rolling(8640, min_periods=2880).rank(pct=True)

    # ATR historical percentile rolling 30d
    df["atr_pct_rank"] = df["atr_pct"].rolling(8640, min_periods=2880).rank(pct=True)

    # 15m trend gate + gap
    df15 = df.set_index("timestamp").resample("15min").agg(
        {"close": "last", "high": "max", "low": "min", "open": "first"}).dropna()
    df15["ema20"] = ema(df15["close"], 20)
    df15["ema50"] = ema(df15["close"], 50)
    df15["m15_gap_pct"] = (df15["ema20"] - df15["ema50"]) / df15["ema50"] * 100
    df15["m15_trend"]   = np.where(df15["ema20"] > df15["ema50"], "UP", "DOWN")
    df15 = df15.reset_index()
    df = pd.merge_asof(df.sort_values("timestamp"),
                       df15[["timestamp", "m15_trend", "m15_gap_pct"]].sort_values("timestamp"),
                       on="timestamp", direction="backward")
    return df


# ──────────────────────── simulator (vectorized-ish loop) ────────────────────────
class Cfg:
    # baseline (v1 ULTIMATE)
    rsi_os = 30; rsi_ob = 70
    sl_pct = 0.006        # 0.6% from worst
    tp_single = 0.005
    tp_dca = 0.0025
    dca_levels = 2
    dca_spacing = 0.005
    gap_min_pct = 0.25    # |gap|>=0.25%
    atr_max_pct = 0.60
    one_h_move_max = 2.0
    blocked_hours = (5, 6, 11, 12, 13, 20)
    weekend_size_mult = 2.0
    daily_loss_cap = 200.0
    use_trend_flip_exit = True

    # ── variant knobs (all default = off) ──
    bbw_pct_max = 0.0          # skip entry if BBW percentile > X (e.g. 0.90)
    bbw_pct_min = 0.0          # skip entry if BBW percentile < X (e.g. 0.10)
    atr_chg_max = 0.0          # skip entry if ATR rose > X over last 1h (e.g. 0.30 = +30%)
    atr_daily_max = 0.0        # skip entry if daily ATR% > X (e.g. 0.50)
    atr_pct_rank_max = 0.0     # skip if ATR percentile > X (e.g. 0.90)
    size_scale_by_atr = False  # smaller when ATR>0.5%, larger when ATR<0.3%
    vol_spike_entry = 0.0      # skip entry if vol[i] > X * SMA20 (e.g. 3.0)
    vol_spike_dca   = 0.0      # block L2 DCA on the trigger bar


def simulate(df, cfg: Cfg):
    n = len(df)
    o   = df["open"].values
    h   = df["high"].values
    l   = df["low"].values
    c   = df["close"].values
    ts  = df["timestamp"].values
    rsi = df["rsi"].values
    atr_pct      = df["atr_pct"].values
    bbw_rank     = df["bbw_pct_rank"].values
    atr_rank     = df["atr_pct_rank"].values
    atr_chg      = df["atr_chg_1h"].values
    atr_daily    = df["atr_daily_pct"].values
    vol          = df["volume"].values
    vol_sma      = df["vol_sma20"].values
    m15_trend    = df["m15_trend"].values
    m15_gap      = df["m15_gap_pct"].values
    hours        = pd.to_datetime(df["timestamp"]).dt.hour.values
    weekday      = pd.to_datetime(df["timestamp"]).dt.weekday.values  # Sat=5, Sun=6
    day_key      = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d").values
    year         = pd.to_datetime(df["timestamp"]).dt.year.values

    bal  = INITIAL
    peak = INITIAL
    eq_curve = np.empty(n)
    pos = None
    trades = []
    cur_day = ""
    daily_pnl = 0.0
    day_blocked = False

    for i in range(n):
        # reset daily P&L
        if day_key[i] != cur_day:
            cur_day = day_key[i]
            daily_pnl = 0.0
            day_blocked = False

        # ── manage open position ──
        if pos is not None:
            side = pos["side"]
            # DCA check
            if pos["legs"] < cfg.dca_levels:
                trig = (pos["worst"] * (1 - cfg.dca_spacing)) if side == "LONG" \
                       else (pos["worst"] * (1 + cfg.dca_spacing))
                hit = (side == "LONG" and l[i] <= trig) or (side == "SHORT" and h[i] >= trig)
                if hit:
                    # variant: volume-spike DCA block
                    block = False
                    if cfg.vol_spike_dca > 0 and vol_sma[i] > 0:
                        if vol[i] > vol_sma[i] * cfg.vol_spike_dca:
                            block = True
                    if not block:
                        # equal-size leg
                        bal -= trig * pos["leg_qty"] * COMMISSION
                        old_qty = pos["qty"]; new_q = pos["leg_qty"]
                        pos["avg"] = (pos["avg"]*old_qty + trig*new_q) / (old_qty + new_q)
                        pos["qty"] = old_qty + new_q
                        pos["worst"] = trig
                        pos["legs"] += 1

            avg = pos["avg"]
            sl_px = pos["worst"] * (1 - cfg.sl_pct) if side == "LONG" else pos["worst"] * (1 + cfg.sl_pct)
            tp_pct = cfg.tp_single if pos["legs"] == 1 else cfg.tp_dca
            tp_px  = avg * (1 + tp_pct) if side == "LONG" else avg * (1 - tp_pct)

            exit_px = None; reason = None
            # SL first (pessimistic)
            if (side == "LONG" and l[i] <= sl_px) or (side == "SHORT" and h[i] >= sl_px):
                exit_px = sl_px; reason = "SL"
            elif (side == "LONG" and h[i] >= tp_px) or (side == "SHORT" and l[i] <= tp_px):
                exit_px = tp_px; reason = "TP"
            elif cfg.use_trend_flip_exit:
                tr_now = m15_trend[i]
                if (side == "LONG" and tr_now == "DOWN") or (side == "SHORT" and tr_now == "UP"):
                    exit_px = c[i]; reason = "FLIP"

            if exit_px is not None:
                qty = pos["qty"]
                gross = (exit_px - avg) * qty if side == "LONG" else (avg - exit_px) * qty
                fees  = exit_px * qty * COMMISSION
                net = gross - fees
                bal += net
                daily_pnl += net
                if daily_pnl <= -cfg.daily_loss_cap:
                    day_blocked = True
                trades.append({"t": ts[i], "side": side, "reason": reason, "net": net,
                               "year": int(year[i]), "legs": pos["legs"]})
                pos = None

        peak = max(peak, bal)
        eq_curve[i] = bal

        # ── entry check ──
        if pos is not None or i + 1 >= n or day_blocked:
            continue
        if np.isnan(rsi[i]): continue

        sig = None
        if rsi[i] <= cfg.rsi_os: sig = "LONG"
        elif rsi[i] >= cfg.rsi_ob: sig = "SHORT"
        if sig is None: continue

        # hour
        if hours[i] in cfg.blocked_hours: continue

        # 15m trend gate
        tr = m15_trend[i]
        if tr is None or (isinstance(tr, float) and np.isnan(tr)): continue
        if (sig == "LONG" and tr != "UP") or (sig == "SHORT" and tr != "DOWN"): continue

        # GAP firmness
        gp = m15_gap[i]
        if np.isnan(gp) or abs(gp) < cfg.gap_min_pct: continue

        # 5m ATR ceiling
        ap = atr_pct[i]
        if np.isnan(ap) or ap > cfg.atr_max_pct: continue

        # 1h cumulative move
        if i >= 12:
            chg = (c[i] / c[i-12] - 1) * 100
            if sig == "SHORT" and chg > cfg.one_h_move_max: continue
            if sig == "LONG"  and chg < -cfg.one_h_move_max: continue

        # ── variant filters ──
        if cfg.bbw_pct_max > 0:
            r = bbw_rank[i]
            if np.isnan(r) or r > cfg.bbw_pct_max: continue
        if cfg.bbw_pct_min > 0:
            r = bbw_rank[i]
            if np.isnan(r) or r < cfg.bbw_pct_min: continue
        if cfg.atr_chg_max > 0:
            ch = atr_chg[i]
            if np.isnan(ch) or ch > cfg.atr_chg_max: continue
        if cfg.atr_daily_max > 0:
            ad = atr_daily[i]
            if np.isnan(ad) or ad > cfg.atr_daily_max: continue
        if cfg.atr_pct_rank_max > 0:
            r = atr_rank[i]
            if np.isnan(r) or r > cfg.atr_pct_rank_max: continue
        if cfg.vol_spike_entry > 0 and vol_sma[i] > 0:
            if vol[i] > vol_sma[i] * cfg.vol_spike_entry: continue

        # ── size + entry fill at next-bar open ──
        next_o = o[i+1]
        size_mult = 1.0
        if weekday[i] >= 5:  # Sat=5, Sun=6
            size_mult *= cfg.weekend_size_mult
        if cfg.size_scale_by_atr and not np.isnan(ap):
            if ap > 0.5:    size_mult *= 0.5
            elif ap < 0.3:  size_mult *= 1.5

        notional = bal * 0.95 * LEVERAGE * size_mult
        total_qty = notional / next_o
        leg_qty   = total_qty / cfg.dca_levels  # plan equal-size legs

        bal -= next_o * leg_qty * COMMISSION
        pos = {"side": sig, "avg": next_o, "qty": leg_qty, "leg_qty": leg_qty,
               "worst": next_o, "legs": 1, "entry_i": i+1}

    # metrics
    peaks = np.maximum.accumulate(eq_curve)
    dd_series = (eq_curve - peaks) / peaks * 100
    max_dd = dd_series.min() if len(dd_series) else 0.0

    n_tr = len(trades)
    wins = [t for t in trades if t["net"] > 0]
    wr = (len(wins) / n_tr * 100) if n_tr else 0.0
    ret_pct = (bal / INITIAL - 1) * 100

    # 2024 attribution
    by_year = {}
    for t in trades:
        by_year.setdefault(t["year"], 0.0)
        by_year[t["year"]] += t["net"]
    pnl_2024 = by_year.get(2024, 0.0)
    n_2024 = sum(1 for t in trades if t["year"] == 2024)

    return {
        "final": bal, "ret_pct": ret_pct, "max_dd_pct": max_dd,
        "trades": n_tr, "wr": wr,
        "pnl_2024": pnl_2024, "n_2024": n_2024,
        "by_year": by_year,
    }


# ───────────────────────── variants ─────────────────────────
def build_variants():
    out = []

    def mk(name, **kw):
        c = Cfg()
        for k, v in kw.items(): setattr(c, k, v)
        return (name, c)

    out.append(mk("BASELINE"))

    # (1) BB-width decile gates
    out.append(mk("BBW skip top 10% (rank<=.90)",  bbw_pct_max=0.90))
    out.append(mk("BBW skip top 20% (rank<=.80)",  bbw_pct_max=0.80))
    out.append(mk("BBW skip bot 10% (rank>=.10)",  bbw_pct_min=0.10))
    out.append(mk("BBW skip bot 20% (rank>=.20)",  bbw_pct_min=0.20))
    out.append(mk("BBW middle 60% only",           bbw_pct_max=0.80, bbw_pct_min=0.20))

    # (2) ATR rate-of-change
    out.append(mk("ATR rising > 30% in 1h skip",   atr_chg_max=0.30))
    out.append(mk("ATR rising > 50% in 1h skip",   atr_chg_max=0.50))
    out.append(mk("ATR rising > 20% in 1h skip",   atr_chg_max=0.20))

    # (3) Daily ATR ceiling
    out.append(mk("Daily ATR <= 0.30%",            atr_daily_max=0.30))
    out.append(mk("Daily ATR <= 0.40%",            atr_daily_max=0.40))
    out.append(mk("Daily ATR <= 0.50%",            atr_daily_max=0.50))

    # ATR percentile rolling
    out.append(mk("ATR percentile <= 0.80",        atr_pct_rank_max=0.80))
    out.append(mk("ATR percentile <= 0.90",        atr_pct_rank_max=0.90))

    # (4) Position sizing by ATR
    out.append(mk("Size scale by ATR (0.5x/1.5x)", size_scale_by_atr=True))

    # (5) Volume-spike entry gate
    out.append(mk("Vol spike >3x SMA20 skip",      vol_spike_entry=3.0))
    out.append(mk("Vol spike >2.5x SMA20 skip",    vol_spike_entry=2.5))
    out.append(mk("Vol spike >2x SMA20 skip",      vol_spike_entry=2.0))

    # (6) Combined "best guess"
    out.append(mk("COMBO: BBW<=.90 + ATR1h<=30%", bbw_pct_max=0.90, atr_chg_max=0.30))
    out.append(mk("COMBO: BBW<=.90 + Vol<3x",     bbw_pct_max=0.90, vol_spike_entry=3.0))
    out.append(mk("COMBO: BBW<=.90 + ATR1h<=30% + Vol<3x",
                  bbw_pct_max=0.90, atr_chg_max=0.30, vol_spike_entry=3.0))

    # DCA-side vol gate (defensive)
    out.append(mk("DCA vol gate >2x",              vol_spike_dca=2.0))

    return out


def main():
    print("loading data + computing features (one pass)…", flush=True)
    df = load_and_prep()
    print(f"  bars: {len(df):,}  range: {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}", flush=True)
    variants = build_variants()
    rows = []
    for name, cfg in variants:
        r = simulate(df, cfg)
        rows.append((name, r))
        print(f"  {name:<46s}  ret {r['ret_pct']:+7.1f}%  DD {r['max_dd_pct']:6.1f}%  "
              f"WR {r['wr']:4.1f}%  N {r['trades']:>5}  '24 ${r['pnl_2024']:+,.0f} (N={r['n_2024']})",
              flush=True)

    # comparison table sorted by ret
    print("\n" + "="*110)
    print(f"{'Variant':<48s} {'Ret%':>8s} {'DD%':>7s} {'WR%':>6s} {'N':>6s} {'2024 $':>10s} {'2024 N':>7s}")
    print("-"*110)
    base_ret = rows[0][1]["ret_pct"]; base_dd = rows[0][1]["max_dd_pct"]; base_24 = rows[0][1]["pnl_2024"]
    rows_sorted = [rows[0]] + sorted(rows[1:], key=lambda x: -x[1]["ret_pct"])
    for name, r in rows_sorted:
        flag = " <-- BASE" if name == "BASELINE" else ""
        improve_24 = " ++" if r["pnl_2024"] > base_24 else ("  -" if r["pnl_2024"] < base_24 else "")
        print(f"{name:<48s} {r['ret_pct']:>+7.1f}% {r['max_dd_pct']:>+6.1f}% {r['wr']:>5.1f}% "
              f"{r['trades']:>6d} ${r['pnl_2024']:>+8,.0f}{improve_24} {r['n_2024']:>6d}{flag}")

    print("\n" + "="*110)
    print("Per-year PnL for BASELINE and any variant that beats baseline on both ret AND 2024:")
    print(f"  BASELINE per-year: " + ", ".join(f"{y}:${v:+,.0f}" for y,v in sorted(rows[0][1]['by_year'].items())))
    for name, r in rows[1:]:
        if r["ret_pct"] > base_ret and r["pnl_2024"] > base_24:
            print(f"  {name}: " + ", ".join(f"{y}:${v:+,.0f}" for y,v in sorted(r['by_year'].items())))


if __name__ == "__main__":
    main()
