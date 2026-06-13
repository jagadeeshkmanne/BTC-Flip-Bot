#!/usr/bin/env python3
"""dca_1pct_day.py — honest test of "DCA every 5/15 min, exit at +1%, daily".

RULE (user idea 2026-06-11):
  - every N minutes buy a fixed slice (day-start equity / fills-per-day) spot
  - whenever unrealized profit >= +1% of cost basis (checked on closed bars),
    SELL ALL next bar open -> done buying for that day
  - EOD variant: liquidate any open inventory at next day's first open
    CARRY variant: keep inventory overnight, keep DCAing next day w/ cash
  - fees 0.10% + slip 0.02% per side (spot), charged on every fill

Honest: decisions on closed bars, all fills at next bar open, no fill
fictions. Benchmark: buy & hold same window. Also run at ZERO fees to
separate structure from fee drag (FINDINGS #3: wrappers = drift x exposure
- fills x costs).
"""
import pandas as pd
import numpy as np

FEE, SLIP = 0.0010, 0.0002
TARGET = 0.01
CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv"

def run(df, step_bars, eod_liquidate, fee, slip, label):
    o, cl = df["open"].values, df["close"].values
    days = df["day"].values
    cash, qty, basis = 5000.0, 0.0, 0.0
    day_start_eq, amt, done_today = 5000.0, 0.0, False
    pend_sell, pend_liq = False, False
    win_days = loss_days = 0
    day_pnl0 = 5000.0
    fees_paid = 0.0
    eq_curve = []
    peak, maxdd = 5000.0, 0.0

    fills_per_day = (288 // step_bars)
    for i in range(1, len(df)):
        new_day = days[i] != days[i - 1]
        # ---- fills at this bar's open (from last bar's decisions) ----
        if (pend_sell or (pend_liq and new_day)) and qty > 0:
            px = o[i] * (1 - slip)
            proceeds = qty * px * (1 - fee)
            fees_paid += qty * px * fee
            cash += proceeds
            qty, basis = 0.0, 0.0
            pend_sell = pend_liq = False
        if new_day:
            eq_now = cash + qty * cl[i] * (1 - fee - slip)
            if eq_now >= day_pnl0:
                win_days += 1
            else:
                loss_days += 1
            day_pnl0 = eq_now
            day_start_eq = eq_now
            amt = max(day_start_eq / fills_per_day, 0.0)
            done_today = False
        # DCA buy on schedule
        if (not done_today) and i % step_bars == 0 and cash >= amt and amt > 1:
            px = o[i] * (1 + slip)
            spend = min(amt, cash)
            fee_d = spend * fee
            fees_paid += fee_d
            qty += (spend - fee_d) / px
            basis += spend
            cash -= spend
        # ---- decisions on this bar's close ----
        if qty > 0 and basis > 0:
            mtm = qty * cl[i] * (1 - fee - slip)
            if mtm - basis >= TARGET * basis:
                pend_sell = True
                done_today = True
        if eod_liquidate and qty > 0:
            pend_liq = True
        eq = cash + qty * cl[i] * (1 - fee - slip)
        eq_curve.append(eq)
        peak = max(peak, eq)
        maxdd = max(maxdd, 1 - eq / peak)

    eq = eq_curve[-1]
    yrs = len(df) / (288 * 365.25)
    n_days = win_days + loss_days
    print(f"{label:>26}: final ${eq:9,.0f} ({(eq/5000-1)*100:+7.1f}%) | "
          f"CAGR {((eq/5000)**(1/yrs)-1)*100:+6.1f}% | maxDD {maxdd*100:5.1f}% | "
          f"win days {win_days/n_days*100:4.1f}% | fees ${fees_paid:8,.0f}")

df = pd.read_csv(CSV)
df["day"] = pd.to_datetime(df["timestamp"]).dt.date
bh = df["close"].iloc[-1] / df["open"].iloc[1] - 1
yrs = len(df) / (288 * 365.25)
print(f"window {df['timestamp'].iloc[0]} .. {df['timestamp'].iloc[-1]} ({yrs:.1f}y)")
print(f"{'BUY & HOLD':>26}: final ${5000*(1+bh):9,.0f} ({bh*100:+7.1f}%) | "
      f"CAGR {((1+bh)**(1/yrs)-1)*100:+6.1f}%")
for step, cad in [(1, "5m"), (3, "15m")]:
    for eod in (True, False):
        run(df, step, eod, FEE, SLIP, f"{cad} DCA, {'EOD-flat' if eod else 'carry'}")
print("--- zero fees (structure only) ---")
for step, cad in [(1, "5m"), (3, "15m")]:
    for eod in (True, False):
        run(df, step, eod, 0.0, 0.0, f"{cad} DCA, {'EOD-flat' if eod else 'carry'}")
