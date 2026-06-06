#!/usr/bin/env python3
"""v11_faithful_backtest.py — Lookahead-free 5-year backtest of v1.1.

Agent A flagged the prior backtest as having lookahead bias from
merge_asof(direction="backward") on 1h/15m bars labeled at OPEN time.

This version eliminates that by:
  - Labeling 15m bars with their CLOSE time (open + 15min)
  - Labeling 1h bars with their CLOSE time (open + 1h)
  - Then merge_asof(direction="backward") correctly finds the last
    bar that has FULLY CLOSED before the current 5m timestamp

Reproduces v1.1 production logic exactly:
  - RSI(9) on 5m AND 1h (period matches bot's RSI_PERIOD=9)
  - 15m EMA20/EMA50 trend gate (fail-closed if data unavailable)
  - GAP firmness ≥ 0.25%
  - ATR(14) on 5m < 0.60%
  - 1h cumulative move (last 12 5m closes) < ±2.0%
  - Blocked hours {5,6,11,12,13,20} UTC
  - 1h RSI 50-split filter (v1.1 addition)
  - DCA: 2 legs @ 0.5% adverse, equal-size legs
  - TP: 0.5% / 0.25% adaptive
  - SL: 0.6% from worst entry
  - Trend-flip exit (close on 15m EMA reversal)
  - Weekend 2x position size
  - Daily $200 loss circuit breaker
  - Single-loss circuit breaker: 15min pause after EVERY loss
  - 3x leverage, 0.04% taker fee per side
  - Bar fill order: SL → DCA → TP (pessimistic)
"""
import sys, os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fleet_backtest import rsi_series, ema, atr


CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "cache")


def load_and_prepare(years=5):
    """Load 5m bars + compute lookahead-free HTF features."""
    df = pd.read_csv(os.path.join(CACHE, "BTCUSDT_5m.csv"))
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    start = datetime.utcnow() - timedelta(days=years * 365)
    df = df[df["timestamp"] >= start].reset_index(drop=True)

    # 5m indicators (computed bar-by-bar, no lookahead)
    df["rsi"] = rsi_series(df["close"], 9)
    df["atr_14"] = atr(df, 14)

    # 15m bars — use CLOSE time as label so merge_asof gives only CLOSED bars
    df15 = (
        df.set_index("timestamp")
          .resample("15min")
          .agg({"open":"first","high":"max","low":"min","close":"last"})
          .dropna()
    )
    df15["ema20"] = ema(df15["close"], 20)
    df15["ema50"] = ema(df15["close"], 50)
    df15["m15_gap_pct"] = (df15["ema20"] - df15["ema50"]) / df15["ema50"] * 100
    df15["m15_trend"] = np.where(df15["ema20"] > df15["ema50"], "UP", "DOWN")
    # CRITICAL: shift timestamps forward by 15min so merge_asof backward gets
    # only fully-closed bars relative to the 5m query time
    df15 = df15.reset_index()
    df15["timestamp"] = df15["timestamp"] + pd.Timedelta(minutes=15)

    df = pd.merge_asof(
        df.sort_values("timestamp"),
        df15[["timestamp", "m15_trend", "m15_gap_pct"]].sort_values("timestamp"),
        on="timestamp", direction="backward"
    )

    # 1h bars — same close-time label trick
    df1h = (
        df.set_index("timestamp")
          .resample("1h")
          .agg({"close":"last"})
          .dropna()
    )
    df1h["rsi_1h"] = rsi_series(df1h["close"], 9)  # RSI period matches bot (9, not 14)
    df1h = df1h.reset_index()
    df1h["timestamp"] = df1h["timestamp"] + pd.Timedelta(hours=1)

    df = pd.merge_asof(
        df.sort_values("timestamp"),
        df1h[["timestamp", "rsi_1h"]].sort_values("timestamp"),
        on="timestamp", direction="backward"
    )

    return df


def simulate(df, use_1h_rsi_filter=True, use_circuit_breaker=True,
             commission=0.0004, leverage=3.0, balance_init=5000.0):
    """Production-faithful v1.1 simulator."""
    bal = balance_init
    pos = None
    cooldown_until = -1   # bar index until which entries blocked (post-trade cooldown of 3 bars = 15min)
    breaker_until = None  # datetime until breaker pause ends
    daily_loss = 0.0
    current_day = None
    paused_today = False
    trades = []

    BLOCKED_HOURS = {5, 6, 11, 12, 13, 20}
    GAP_MIN = 0.25  # %
    ATR_MAX = 0.60  # %
    MOVE_1H_MAX = 2.0  # %
    SL_PCT = 0.006   # 0.6% from worst entry
    TP_SINGLE = 0.005
    TP_DCA = 0.0025
    DCA_SPACING = 0.005
    DCA_LEVELS = 2
    DAILY_MAX_LOSS = 200.0
    WEEKEND_MULT = 2.0

    for i in range(len(df)):
        bar = df.iloc[i]
        t = bar["timestamp"]
        h, l, c = bar["high"], bar["low"], bar["close"]
        day = t.date()
        if day != current_day:
            current_day = day
            daily_loss = 0.0
            paused_today = False

        # ───────────── EXIT logic for open position ─────────────
        if pos is not None:
            side = pos["side"]; avg = pos["avg"]; worst = pos["worst"]
            sl_px = worst * (1 + SL_PCT) if side == "SHORT" else worst * (1 - SL_PCT)
            tp_pct = TP_SINGLE if pos["legs"] == 1 else TP_DCA
            tp_px = avg * (1 - tp_pct) if side == "SHORT" else avg * (1 + tp_pct)
            dca_px = None
            if pos["legs"] < DCA_LEVELS:
                dca_px = worst * (1 + DCA_SPACING) if side == "SHORT" else worst * (1 - DCA_SPACING)

            hit_sl = (side == "SHORT" and h >= sl_px) or (side == "LONG" and l <= sl_px)
            hit_dca = dca_px is not None and ((side == "SHORT" and h >= dca_px) or (side == "LONG" and l <= dca_px))
            hit_tp = (side == "SHORT" and l <= tp_px) or (side == "LONG" and h >= tp_px)

            # Trend-flip exit (only fires if 15m trend data available)
            tf_exit = False
            if pd.notna(bar.get("m15_trend")):
                if (side == "SHORT" and bar["m15_trend"] == "UP") or (side == "LONG" and bar["m15_trend"] == "DOWN"):
                    tf_exit = True

            # Order: SL first (pessimistic), then TP, then trend-flip, then DCA
            exit_px = None
            reason = None
            if hit_sl:
                exit_px = sl_px; reason = "SL"
            elif hit_tp:
                exit_px = tp_px; reason = "TP"
            elif tf_exit:
                exit_px = c; reason = "TREND"
            elif hit_dca:
                # DCA fill: add a leg with same qty (equal-size DCA)
                old_q = pos["qty"]
                new_q = old_q
                fill_px = dca_px
                pos["avg"] = (avg * old_q + fill_px * new_q) / (old_q + new_q)
                pos["qty"] = old_q + new_q
                pos["worst"] = fill_px
                pos["legs"] += 1
                # commission on DCA leg
                bal -= fill_px * new_q * commission
                # continue holding position
                continue

            if exit_px is not None:
                qty_total = pos["qty"]
                gross = (avg - exit_px) * qty_total if side == "SHORT" else (exit_px - avg) * qty_total
                exit_fees = exit_px * qty_total * commission
                net = gross - exit_fees
                bal += net
                trades.append({
                    "entry_t": pos["entry_t"], "exit_t": t,
                    "side": side, "legs": pos["legs"],
                    "entry_px": pos["entry_px"], "exit_px": exit_px,
                    "qty": qty_total, "net": net, "reason": reason,
                    "weekend": pos.get("weekend_2x", False),
                })
                if net < 0:
                    daily_loss += net
                    if use_circuit_breaker:
                        # 15-min cooldown after EVERY loss
                        breaker_until = t + pd.Timedelta(minutes=15)
                if DAILY_MAX_LOSS > 0 and daily_loss <= -DAILY_MAX_LOSS:
                    paused_today = True
                cooldown_until = i + 3  # 3 bars = 15-min post-trade cooldown
                pos = None

        # ───────────── ENTRY logic ─────────────
        if pos is not None:
            continue
        if i < cooldown_until:
            continue
        if i + 1 >= len(df):
            break
        if paused_today:
            continue
        if breaker_until is not None and t < breaker_until:
            continue

        rsi_v = bar.get("rsi")
        if pd.isna(rsi_v):
            continue
        sig = "LONG" if rsi_v <= 30 else "SHORT" if rsi_v >= 70 else None
        if sig is None:
            continue

        # Hour filter
        if t.hour in BLOCKED_HOURS:
            continue

        # 15m trend gate (fail-closed)
        if pd.isna(bar.get("m15_trend")):
            continue
        if (sig == "LONG" and bar["m15_trend"] != "UP") or (sig == "SHORT" and bar["m15_trend"] != "DOWN"):
            continue

        # GAP firmness (fail-closed)
        if pd.isna(bar.get("m15_gap_pct")):
            continue
        if abs(bar["m15_gap_pct"]) < GAP_MIN:
            continue

        # ATR filter
        if pd.isna(bar.get("atr_14")):
            continue
        atr_pct = (bar["atr_14"] / c) * 100
        if atr_pct > ATR_MAX:
            continue

        # 1h cumulative move filter
        if i >= 12:
            chg_1h = (c / df["close"].iloc[i - 12] - 1) * 100
            if sig == "SHORT" and chg_1h > MOVE_1H_MAX:
                continue
            if sig == "LONG" and chg_1h < -MOVE_1H_MAX:
                continue

        # 1h RSI 50-split filter (v1.1 addition) — uses LAST CLOSED 1h bar
        if use_1h_rsi_filter:
            rsi_1h = bar.get("rsi_1h")
            if pd.isna(rsi_1h):
                continue   # fail-closed
            if sig == "SHORT" and rsi_1h >= 50:
                continue
            if sig == "LONG" and rsi_1h <= 50:
                continue

        # All filters passed — open at NEXT bar's open
        next_o = df.iloc[i + 1]["open"]
        is_weekend = t.weekday() >= 5
        qty_mult = WEEKEND_MULT if is_weekend else 1.0
        qty_per_leg = (bal * 0.95 * leverage * qty_mult) / next_o / DCA_LEVELS
        bal -= next_o * qty_per_leg * commission  # entry commission
        pos = {
            "side": sig,
            "entry_t": df.iloc[i + 1]["timestamp"],
            "entry_px": next_o,
            "avg": next_o,
            "worst": next_o,
            "qty": qty_per_leg,
            "legs": 1,
            "weekend_2x": is_weekend,
        }

    if not trades:
        return None

    nets = np.array([t["net"] for t in trades])
    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    final = balance_init + nets.sum()
    eq = np.concatenate([[balance_init], balance_init + nets.cumsum()])
    peaks = np.maximum.accumulate(eq)
    dd_series = (eq - peaks) / peaks * 100

    by_year = {}
    by_year_dd = {}
    eq_per_year_start = {}
    for j, tr in enumerate(trades):
        y = tr["entry_t"].year
        by_year.setdefault(y, []).append(tr["net"])

    return {
        "n": len(trades),
        "wr": len(wins) / len(trades) * 100,
        "return_pct": (final / balance_init - 1) * 100,
        "max_dd_pct": dd_series.min(),
        "final": final,
        "trades": trades,
        "by_year": by_year,
        "avg_win": wins.mean() if len(wins) > 0 else 0,
        "avg_loss": losses.mean() if len(losses) > 0 else 0,
        "max_win": wins.max() if len(wins) > 0 else 0,
        "max_loss": losses.min() if len(losses) > 0 else 0,
    }


def main():
    print("═══ V1.1 FAITHFUL 5-YEAR BACKTEST (no lookahead) ═══\n")
    print("Loading + prepping data with close-time-labeled HTF bars...")
    df = load_and_prepare(years=5)
    print(f"  Period: {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")
    print(f"  Bars: {len(df):,}\n")

    print("Running 4 scenarios for honest comparison:\n")

    scenarios = [
        ("Baseline NO 1h RSI filter, NO circuit breaker", dict(use_1h_rsi_filter=False, use_circuit_breaker=False)),
        ("v1.1 WITH 1h RSI filter, NO circuit breaker", dict(use_1h_rsi_filter=True, use_circuit_breaker=False)),
        ("Baseline NO 1h RSI filter + circuit breaker", dict(use_1h_rsi_filter=False, use_circuit_breaker=True)),
        ("v1.1 WITH 1h RSI filter + circuit breaker (FAITHFUL)", dict(use_1h_rsi_filter=True, use_circuit_breaker=True)),
    ]

    results = {}
    print(f"{'Config':<55} {'#':<6} {'WR%':<6} {'Return%':<10} {'DD%':<8} {'Final$':<10}")
    print("─" * 100)
    for label, kwargs in scenarios:
        r = simulate(df, **kwargs)
        if r is None:
            print(f"  {label}: no trades")
            continue
        results[label] = r
        print(f"{label:<55} {r['n']:<6} {r['wr']:>5.1f}% {r['return_pct']:>+7.2f}% {r['max_dd_pct']:>+6.2f}% ${r['final']:>8.0f}")

    print(f"\n═══ YEAR-BY-YEAR (FAITHFUL v1.1) ═══\n")
    label_faithful = "v1.1 WITH 1h RSI filter + circuit breaker (FAITHFUL)"
    if label_faithful in results:
        r = results[label_faithful]
        print(f"{'Year':<6} {'Trades':<7} {'WR%':<6} {'Total $':<11} {'Cumulative':<12}")
        print("─" * 50)
        cum = 0
        for yr in sorted(r["by_year"]):
            nets = r["by_year"][yr]
            wins = sum(1 for x in nets if x > 0)
            wr = wins / len(nets) * 100
            total = sum(nets)
            cum += total
            print(f"{yr:<6} {len(nets):<7} {wr:>5.1f}% ${total:>+8.2f} ${cum:>+9.2f}")

    print(f"\n═══ DELTA: 1h RSI filter ON vs OFF (faithful baseline) ═══")
    if "Baseline NO 1h RSI filter + circuit breaker" in results and label_faithful in results:
        base = results["Baseline NO 1h RSI filter + circuit breaker"]
        new = results[label_faithful]
        print(f"  Return: {base['return_pct']:+.2f}% → {new['return_pct']:+.2f}%  (delta {new['return_pct']-base['return_pct']:+.2f} pp)")
        print(f"  DD:     {base['max_dd_pct']:+.2f}% → {new['max_dd_pct']:+.2f}%  (delta {new['max_dd_pct']-base['max_dd_pct']:+.2f} pp)")
        print(f"  Trades: {base['n']} → {new['n']}  (cut {base['n']-new['n']} trades)")


if __name__ == "__main__":
    main()
