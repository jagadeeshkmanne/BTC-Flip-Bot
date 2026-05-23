"""V2.2 strategy — exact Python backtest, monthly breakdown.

Mirrors strategies/day/strategy_sr_dca_5m.pine line-for-line:
  * 5m + 1d S/R with adaptive extend (1→2 days when range < 2%)
  * RSI(14) divergence pivot 5/5, 20-bar freshness
  * Volume ≥ 1.1× SMA-20
  * DCA: 2 levels, 0.85% spacing
  * SL: worst * (1 ∓ 2.0%); BE tightens to firstEntry * (1 ± 0.25%) after +1% favorable
  * TP hybrid: prev_mid pre-DCA, firstEntry * (1 ± 4%) post-DCA
  * EOD 20:00 UTC: close unless fav ≥ 1.5% (then hold; 24h cap)
  * One cycle per UTC day
  * Leverage cap 2×, 6% risk, $5000 starting capital, 0.04% commission per side
"""
from __future__ import annotations
import csv
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import numpy as np

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "data" / "cache"

# ───── V2.2 parameters (matches Pine defaults) ─────
INITIAL_CAPITAL = 5000.0
RISK_PCT = 0.06
DCA_LEVELS = 2
DCA_SPACING = 0.0085
SL_BELOW_WORST = 0.02
SUPPORT_ZONE = 0.0005
USE_EOD_FLATTEN = True
CLOSE_HOUR = 20
HOLD_PAST_EOD_IF_FAV = True
HOLD_MIN_FAV_PCT = 0.015
MAX_CYCLES_PER_DAY = 1
USE_VOL_FILTER = True
VOL_MULT = 1.1
RSI_PERIOD = 14
USE_DIVERGENCE = True
DIV_PIVOT_L = 5
DIV_PIVOT_R = 5
DIV_FRESH_BARS = 20
RANGE_FILTER_MODE = "extend"  # off | skip | extend
MIN_PREV_RANGE_PCT = 0.02
MAX_LOOKBACK_DAYS = 2
TP_MODE = "hybrid"  # prev_mid | hybrid
TP_POST_DCA_PCT = 0.04
PREV_MID_OFFSET_PCT = 0.001
USE_BREAKEVEN = True
BE_TRIGGER_PCT = 0.0075  # 2026-05-23: 1.0% → 0.75% per 45-day sweep
BE_BUFFER_PCT = 0.0025
LEVERAGE = 2.0
COMMISSION = 0.0004  # 0.04% per side


# ───── Helpers ─────
def rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder-smoothed RSI matching ta.rsi."""
    delta = np.diff(closes, prepend=closes[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.zeros_like(closes)
    avg_loss = np.zeros_like(closes)
    avg_gain[period] = gain[1:period + 1].mean()
    avg_loss[period] = loss[1:period + 1].mean()
    for i in range(period + 1, len(closes)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i]) / period
    rs = np.divide(avg_gain, avg_loss, out=np.zeros_like(avg_gain), where=avg_loss != 0)
    out = 100 - (100 / (1 + rs))
    out[:period] = np.nan
    return out


def sma(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full_like(arr, np.nan, dtype=float)
    if len(arr) < period:
        return out
    csum = np.cumsum(arr, dtype=float)
    out[period - 1:] = (csum[period - 1:] - np.concatenate(([0], csum[:-period]))) / period
    return out


# ───── Data load ─────
def load_5m(start: str, end: str) -> pd.DataFrame:
    df = pd.read_csv(CACHE / "BTCUSDT_5m.csv", parse_dates=["timestamp"])
    df = df[(df["timestamp"] >= start) & (df["timestamp"] < end)].reset_index(drop=True)
    df["date"] = df["timestamp"].dt.strftime("%Y-%m-%d")
    df["hour"] = df["timestamp"].dt.hour
    df["date_key"] = (
        df["timestamp"].dt.year * 10000
        + df["timestamp"].dt.month * 100
        + df["timestamp"].dt.day
    )
    return df


def load_daily_levels(start: str, end: str) -> dict[str, dict]:
    """Build per-date prev_H/prev_L cascade (1..5 day lookback) using 1d candles
    SHIFTED by [1] so today never leaks in (matches Pine `[1]`)."""
    df = pd.read_csv(CACHE / "BTCUSDT_1d.csv", parse_dates=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    high = df["high"].values
    low = df["low"].values

    # rolling N-day H/L, then shift by 1 (yesterday and earlier only)
    levels = {}
    n = len(df)
    for i in range(n):
        d = df.loc[i, "timestamp"].strftime("%Y-%m-%d")
        prev = {}
        for k in range(1, 6):  # 1..5 day lookback
            if i - k < 0:
                break
            window_high = high[max(0, i - k):i].max()
            window_low = low[max(0, i - k):i].min()
            prev[k] = (window_high, window_low)
        levels[d] = prev

    return {d: lv for d, lv in levels.items() if start[:10] <= d <= end[:10]}


def pick_levels(prev: dict) -> tuple[float, float, int]:
    """Pine cascade: in extend mode walk 1→max until range ≥ floor."""
    if 1 not in prev:
        return None, None, 0
    h1, l1 = prev[1]
    if RANGE_FILTER_MODE != "extend":
        return h1, l1, 1
    if (h1 - l1) / l1 >= MIN_PREV_RANGE_PCT:
        return h1, l1, 1
    for k in range(2, MAX_LOOKBACK_DAYS + 1):
        if k not in prev:
            break
        h, l = prev[k]
        if (h - l) / l >= MIN_PREV_RANGE_PCT:
            return h, l, k
    # cap reached: use the cap-day band (Pine semantics — never exceed maxLookbackDays)
    cap = min(MAX_LOOKBACK_DAYS, max(prev.keys()))
    h, l = prev[cap]
    return h, l, cap


# ───── Backtest ─────
@dataclass
class Trade:
    cycle_day: str
    side: str  # LONG/SHORT
    first_entry: float
    avg_entry: float
    exit_px: float
    qty: float
    pnl_usd: float
    pnl_pct: float  # vs first entry
    legs: int
    exit_reason: str  # TP/SL/BE/EOD/HOLD_EOD
    held_overnight: bool
    open_time: str
    close_time: str


def run_backtest(start: str, end: str) -> list[Trade]:
    df = load_5m(start, end)
    if df.empty:
        print(f"No 5m data in window {start}..{end}")
        return []
    levels_by_date = load_daily_levels(start, end)

    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    volumes = df["volume"].values

    rsi_arr = rsi(closes, RSI_PERIOD)
    vol_avg = sma(volumes, 20)

    n = len(df)
    # ─── Pre-pass: detect divergences (bars-since trackers)
    # Pivot at bar i is confirmed at bar i + DIV_PIVOT_R if highs[i] is the
    # max in [i-L..i+R] (and lows[i] is min for low pivot). Track last 2.
    bars_since_bear = np.full(n, 9999, dtype=int)
    bars_since_bull = np.full(n, 9999, dtype=int)
    last_PH = prev_PH = np.nan
    last_RatH = prev_RatH = np.nan
    last_PL = prev_PL = np.nan
    last_RatL = prev_RatL = np.nan
    cur_bear = 9999
    cur_bull = 9999
    L, R = DIV_PIVOT_L, DIV_PIVOT_R
    for j in range(n):
        # Confirm pivot for bar j-R
        i = j - R
        if i - L >= 0:
            window_h = highs[i - L:j + 1]
            window_l = lows[i - L:j + 1]
            if highs[i] == window_h.max() and (window_h == highs[i]).sum() == 1:
                # high pivot at bar i
                prev_PH = last_PH
                prev_RatH = last_RatH
                last_PH = highs[i]
                last_RatH = rsi_arr[i]
                if not np.isnan(prev_PH) and last_PH > prev_PH and last_RatH < prev_RatH:
                    cur_bear = 0
            if lows[i] == window_l.min() and (window_l == lows[i]).sum() == 1:
                prev_PL = last_PL
                prev_RatL = last_RatL
                last_PL = lows[i]
                last_RatL = rsi_arr[i]
                if not np.isnan(prev_PL) and last_PL < prev_PL and last_RatL > prev_RatL:
                    cur_bull = 0
        bars_since_bear[j] = cur_bear
        bars_since_bull[j] = cur_bull
        cur_bear = min(cur_bear + 1, 9999)
        cur_bull = min(cur_bull + 1, 9999)

    # ─── Main loop state ───
    equity = INITIAL_CAPITAL
    peak_equity = equity
    max_dd_pct = 0.0
    trades: list[Trade] = []

    in_position = False
    side: str = ""  # LONG/SHORT
    first_entry = np.nan
    worst_entry = np.nan
    cum_notional = 0.0
    cum_qty = 0.0
    filled = 0
    be_active = False
    cycle_open_time = ""
    eod_held_on_day = -1  # date_key
    cycle_closed_on_day = -1  # dayofmonth (Pine semantics)
    cycles_today = 0
    last_dom = -1
    held_overnight = False

    for j in range(n):
        date = df.at[j, "date"]
        date_key = int(df.at[j, "date_key"])
        dom = df.at[j, "timestamp"].day
        utc_h = df.at[j, "hour"]
        H = highs[j]
        L_ = lows[j]
        C = closes[j]
        V = volumes[j]
        v_avg = vol_avg[j]

        # New UTC day → reset per-day counters (Pine `newDay` semantics)
        if dom != last_dom and last_dom != -1:
            cycle_closed_on_day = -1
            cycles_today = 0
        last_dom = dom

        # Pick S/R levels for today
        prev = levels_by_date.get(date)
        if prev is None or 1 not in prev:
            continue
        prevH, prevL, _ = pick_levels(prev)
        if prevH is None:
            continue
        prevMid = (prevH + prevL) / 2.0
        prev_range_pct = (prevH - prevL) / prevL

        # Range gate
        if RANGE_FILTER_MODE == "skip" and prev_range_pct < MIN_PREV_RANGE_PCT:
            range_ok = False
        else:
            range_ok = True

        # Volume gate
        vol_ok = (not USE_VOL_FILTER) or (not np.isnan(v_avg) and V >= VOL_MULT * v_avg)

        # Divergence gates
        bull_div_ok = (not USE_DIVERGENCE) or bars_since_bull[j] <= DIV_FRESH_BARS
        bear_div_ok = (not USE_DIVERGENCE) or bars_since_bear[j] <= DIV_FRESH_BARS

        cycle_done = cycle_closed_on_day == dom
        in_window = (not USE_EOD_FLATTEN) or utc_h < CLOSE_HOUR

        # ════════════ POSITION MANAGEMENT (existing position) ════════════
        if in_position:
            # ─ DCA fill (recompute perLevelQty at this bar, Pine-style) ─
            if filled < DCA_LEVELS:
                worst_sl_dist = (DCA_LEVELS - 1) * DCA_SPACING + SL_BELOW_WORST
                total_notional = equity * 0.95 * RISK_PCT / worst_sl_dist
                per_leg_qty = total_notional / C / DCA_LEVELS
                qty_cap_dca = (equity * 0.95 * LEVERAGE) / C / DCA_LEVELS
                per_leg_qty = min(per_leg_qty, qty_cap_dca)
                if side == "LONG":
                    next_dca = worst_entry * (1 - DCA_SPACING)
                    if L_ <= next_dca and per_leg_qty > 0:
                        fill_px = next_dca
                        cum_notional += fill_px * per_leg_qty
                        cum_qty += per_leg_qty
                        worst_entry = fill_px
                        filled += 1
                        equity -= fill_px * per_leg_qty * COMMISSION
                else:
                    next_dca = worst_entry * (1 + DCA_SPACING)
                    if H >= next_dca and per_leg_qty > 0:
                        fill_px = next_dca
                        cum_notional += fill_px * per_leg_qty
                        cum_qty += per_leg_qty
                        worst_entry = fill_px
                        filled += 1
                        equity -= fill_px * per_leg_qty * COMMISSION

            # ─ BE-stop activation ─
            if USE_BREAKEVEN and not be_active:
                if side == "LONG":
                    fav = (C - first_entry) / first_entry
                else:
                    fav = (first_entry - C) / first_entry
                if fav >= BE_TRIGGER_PCT:
                    be_active = True

            # ─ Compute current SL / TP ─
            if side == "LONG":
                tp_px = (
                    first_entry * (1 + TP_POST_DCA_PCT)
                    if (TP_MODE == "hybrid" and filled > 1)
                    else prevMid * (1 - PREV_MID_OFFSET_PCT)
                )
                sl_raw = worst_entry * (1 - SL_BELOW_WORST)
                sl_be = first_entry * (1 + BE_BUFFER_PCT)
                sl_px = max(sl_raw, sl_be) if (USE_BREAKEVEN and be_active) else sl_raw
            else:
                tp_px = (
                    first_entry * (1 - TP_POST_DCA_PCT)
                    if (TP_MODE == "hybrid" and filled > 1)
                    else prevMid * (1 + PREV_MID_OFFSET_PCT)
                )
                sl_raw = worst_entry * (1 + SL_BELOW_WORST)
                sl_be = first_entry * (1 - BE_BUFFER_PCT)
                sl_px = min(sl_raw, sl_be) if (USE_BREAKEVEN and be_active) else sl_raw

            # ─ Check SL / TP hit on this bar (assume SL first if both possible) ─
            exit_px = None
            exit_reason = None
            if side == "LONG":
                if L_ <= sl_px:
                    exit_px = sl_px
                    exit_reason = "BE" if (USE_BREAKEVEN and be_active and abs(sl_px - sl_be) < 1e-6) else "SL"
                elif H >= tp_px:
                    exit_px = tp_px
                    exit_reason = "TP"
            else:
                if H >= sl_px:
                    exit_px = sl_px
                    exit_reason = "BE" if (USE_BREAKEVEN and be_active and abs(sl_px - sl_be) < 1e-6) else "SL"
                elif L_ <= tp_px:
                    exit_px = tp_px
                    exit_reason = "TP"

            # ─ EOD logic (only if SL/TP didn't hit this bar) ─
            if exit_px is None and USE_EOD_FLATTEN and utc_h >= CLOSE_HOUR:
                same_day_as_hold = eod_held_on_day == date_key
                if not same_day_as_hold:
                    can_hold = HOLD_PAST_EOD_IF_FAV and eod_held_on_day == -1
                    if side == "LONG":
                        fav_pct = (C - first_entry) / first_entry
                    else:
                        fav_pct = (first_entry - C) / first_entry
                    if can_hold and fav_pct >= HOLD_MIN_FAV_PCT:
                        eod_held_on_day = date_key
                        held_overnight = True
                    else:
                        exit_px = C
                        exit_reason = "EOD" if not held_overnight else "HOLD_EOD"

            if exit_px is not None:
                avg_entry = cum_notional / cum_qty
                if side == "LONG":
                    pnl_usd = (exit_px - avg_entry) * cum_qty
                else:
                    pnl_usd = (avg_entry - exit_px) * cum_qty
                pnl_usd -= exit_px * cum_qty * COMMISSION
                equity += pnl_usd
                pnl_pct = pnl_usd / INITIAL_CAPITAL * 100  # vs initial for monthly stat
                trades.append(Trade(
                    cycle_day=cycle_open_time[:10],
                    side=side,
                    first_entry=first_entry,
                    avg_entry=avg_entry,
                    exit_px=exit_px,
                    qty=cum_qty,
                    pnl_usd=pnl_usd,
                    pnl_pct=(exit_px / first_entry - 1) * 100 * (1 if side == "LONG" else -1),
                    legs=filled,
                    exit_reason=exit_reason,
                    held_overnight=held_overnight,
                    open_time=cycle_open_time,
                    close_time=df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M"),
                ))
                # Reset cycle
                cycles_today += 1
                eod_forced = USE_EOD_FLATTEN and utc_h >= CLOSE_HOUR
                if cycles_today >= MAX_CYCLES_PER_DAY or eod_forced:
                    cycle_closed_on_day = dom
                in_position = False
                side = ""
                first_entry = np.nan
                worst_entry = np.nan
                cum_notional = 0.0
                cum_qty = 0.0
                filled = 0
                be_active = False
                eod_held_on_day = -1
                held_overnight = False

                peak_equity = max(peak_equity, equity)
                dd = (peak_equity - equity) / peak_equity * 100
                if dd > max_dd_pct:
                    max_dd_pct = dd

        # ════════════ ENTRY (only if flat after position-mgmt) ════════════
        if not in_position and not cycle_done and in_window and range_ok and vol_ok:
            # LONG: low touched prev_L within zone (and not crashed > 1% past it)
            if (
                L_ <= prevL * (1 + SUPPORT_ZONE)
                and L_ > prevL * (1 - 0.01)
                and bull_div_ok
            ):
                entry_px = prevL * (1 + SUPPORT_ZONE / 2)
                # Sizing
                worst_sl_dist = (DCA_LEVELS - 1) * DCA_SPACING + SL_BELOW_WORST
                total_notional = equity * 0.95 * RISK_PCT / worst_sl_dist
                per_leg = total_notional / C / DCA_LEVELS
                qty_cap = (equity * 0.95 * LEVERAGE) / C / DCA_LEVELS
                per_leg = min(per_leg, qty_cap)
                if per_leg > 0:
                    side = "LONG"
                    first_entry = entry_px
                    worst_entry = entry_px
                    cum_notional = entry_px * per_leg
                    cum_qty = per_leg
                    filled = 1
                    in_position = True
                    be_active = False
                    eod_held_on_day = -1
                    held_overnight = False
                    cycle_open_time = df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M")
                    equity -= entry_px * per_leg * COMMISSION
            # SHORT: high touched prev_H within zone
            elif (
                H >= prevH * (1 - SUPPORT_ZONE)
                and H < prevH * (1 + 0.01)
                and bear_div_ok
            ):
                entry_px = prevH * (1 - SUPPORT_ZONE / 2)
                worst_sl_dist = (DCA_LEVELS - 1) * DCA_SPACING + SL_BELOW_WORST
                total_notional = equity * 0.95 * RISK_PCT / worst_sl_dist
                per_leg = total_notional / C / DCA_LEVELS
                qty_cap = (equity * 0.95 * LEVERAGE) / C / DCA_LEVELS
                per_leg = min(per_leg, qty_cap)
                if per_leg > 0:
                    side = "SHORT"
                    first_entry = entry_px
                    worst_entry = entry_px
                    cum_notional = entry_px * per_leg
                    cum_qty = per_leg
                    filled = 1
                    in_position = True
                    be_active = False
                    eod_held_on_day = -1
                    held_overnight = False
                    cycle_open_time = df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M")
                    equity -= entry_px * per_leg * COMMISSION

    return trades, equity, max_dd_pct


# ───── Reporting ─────
def monthly_breakdown(trades: list[Trade]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    rows = []
    for t in trades:
        ym = t.open_time[:7]
        rows.append({
            "month": ym,
            "side": t.side,
            "pnl_usd": t.pnl_usd,
            "win": 1 if t.pnl_usd > 0 else 0,
            "exit_reason": t.exit_reason,
            "held_overnight": t.held_overnight,
        })
    df = pd.DataFrame(rows)
    monthly = df.groupby("month").agg(
        trades=("pnl_usd", "size"),
        wins=("win", "sum"),
        pnl_usd=("pnl_usd", "sum"),
        gross_win=("pnl_usd", lambda x: x[x > 0].sum()),
        gross_loss=("pnl_usd", lambda x: -x[x < 0].sum()),
        held=("held_overnight", "sum"),
    ).reset_index()
    monthly["wr"] = (monthly["wins"] / monthly["trades"] * 100).round(1)
    monthly["pf"] = np.where(
        monthly["gross_loss"] > 0,
        (monthly["gross_win"] / monthly["gross_loss"]).round(2),
        np.inf,
    )
    return monthly[["month", "trades", "wins", "wr", "pnl_usd", "pf", "held"]]


def equity_walk(trades: list[Trade], starting: float = INITIAL_CAPITAL) -> pd.DataFrame:
    rows = []
    eq = starting
    peak = starting
    for t in trades:
        eq += t.pnl_usd
        peak = max(peak, eq)
        rows.append({
            "date": t.close_time[:10],
            "equity": eq,
            "dd_pct": (peak - eq) / peak * 100,
            "side": t.side,
            "exit": t.exit_reason,
            "pnl": t.pnl_usd,
        })
    return pd.DataFrame(rows)


def run_monthly_fresh(start: str, end: str) -> pd.DataFrame:
    """Run each calendar month as a separate $5k-fresh backtest.
    Avoids compounding death (where after a few losing months, equity is so
    small that subsequent monthly $ figures are misleading)."""
    import datetime as dt
    s_dt = dt.datetime.strptime(start, "%Y-%m-%d")
    e_dt = dt.datetime.strptime(end, "%Y-%m-%d")
    rows = []
    cur = dt.datetime(s_dt.year, s_dt.month, 1)
    while cur < e_dt:
        nxt = dt.datetime(cur.year + (1 if cur.month == 12 else 0),
                          1 if cur.month == 12 else cur.month + 1, 1)
        m_start = max(cur, s_dt).strftime("%Y-%m-%d")
        m_end = min(nxt, e_dt).strftime("%Y-%m-%d")
        trades, final_eq, dd = run_backtest(m_start, m_end)
        wins = sum(1 for t in trades if t.pnl_usd > 0)
        gw = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
        gl = -sum(t.pnl_usd for t in trades if t.pnl_usd < 0)
        held = sum(1 for t in trades if t.held_overnight)
        rows.append({
            "month": cur.strftime("%Y-%m"),
            "trades": len(trades),
            "wins": wins,
            "wr": round(wins / len(trades) * 100, 1) if trades else 0.0,
            "pnl_usd": round(final_eq - INITIAL_CAPITAL, 2),
            "pnl_pct": round((final_eq / INITIAL_CAPITAL - 1) * 100, 2),
            "pf": round(gw / gl, 2) if gl > 0 else (float("inf") if gw > 0 else 0),
            "max_dd_pct": round(dd, 2),
            "held": held,
        })
        cur = nxt
    return pd.DataFrame(rows)


def main():
    import datetime as dt
    end = dt.datetime.utcnow().strftime("%Y-%m-%d")
    start = (dt.datetime.utcnow() - dt.timedelta(days=365 * 5)).strftime("%Y-%m-%d")
    if len(sys.argv) >= 2:
        start = sys.argv[1]
    if len(sys.argv) >= 3:
        end = sys.argv[2]

    print(f"Backtest window: {start} → {end}")
    print(f"Strategy: V2.2 — 5m + 1d S/R + RSI div + BE-stop + EOD hold")
    print(f"Capital: ${INITIAL_CAPITAL}  Risk/cycle: {RISK_PCT*100}%  Leverage cap: {LEVERAGE}x  Commission: {COMMISSION*100}%/side")
    print()

    trades, final_equity, max_dd = run_backtest(start, end)
    if not trades:
        print("No trades.")
        return

    monthly = monthly_breakdown(trades)
    pd.set_option("display.float_format", lambda x: f"{x:,.2f}")
    print("=" * 80)
    print("MONTHLY BREAKDOWN")
    print("=" * 80)
    print(monthly.to_string(index=False))

    total_pnl = final_equity - INITIAL_CAPITAL
    total_pct = (final_equity / INITIAL_CAPITAL - 1) * 100
    wins = sum(1 for t in trades if t.pnl_usd > 0)
    wr = wins / len(trades) * 100
    gw = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
    gl = -sum(t.pnl_usd for t in trades if t.pnl_usd < 0)
    pf = gw / gl if gl > 0 else float("inf")
    held_count = sum(1 for t in trades if t.held_overnight)

    longs = [t for t in trades if t.side == "LONG"]
    shorts = [t for t in trades if t.side == "SHORT"]

    print()
    print("=" * 80)
    print("OVERALL")
    print("=" * 80)
    print(f"Final equity:    ${final_equity:,.2f}  ({total_pct:+.2f}% / ${total_pnl:+,.2f})")
    print(f"Total trades:    {len(trades)}  (LONG {len(longs)} / SHORT {len(shorts)})")
    print(f"Wins / WR:       {wins} / {wr:.1f}%")
    print(f"Profit factor:   {pf:.2f}")
    print(f"Max drawdown:    {max_dd:.2f}%")
    print(f"Held overnight:  {held_count}")
    print(f"Avg trade:       ${total_pnl / len(trades):+,.2f}")

    # Exit reason breakdown
    er_counts = defaultdict(int)
    er_pnl = defaultdict(float)
    for t in trades:
        er_counts[t.exit_reason] += 1
        er_pnl[t.exit_reason] += t.pnl_usd
    print()
    print("Exit reasons:")
    for r in sorted(er_counts.keys()):
        print(f"  {r:8s} count={er_counts[r]:4d}  pnl=${er_pnl[r]:+,.2f}")

    # Save monthly + trades
    out_dir = REPO / "backtest" / "results"
    out_dir.mkdir(exist_ok=True)
    monthly.to_csv(out_dir / "monthly_v22.csv", index=False)
    pd.DataFrame([t.__dict__ for t in trades]).to_csv(out_dir / "trades_v22.csv", index=False)

    # ─── Fresh-capital-per-month view (regime-clean) ───
    print()
    print("=" * 80)
    print("MONTHLY (each month re-starts with $5,000 — regime-clean view)")
    print("=" * 80)
    fresh = run_monthly_fresh(start, end)
    print(fresh.to_string(index=False))
    fresh.to_csv(out_dir / "monthly_fresh_v22.csv", index=False)

    # Fresh-monthly summary
    profitable_months = (fresh["pnl_pct"] > 0).sum()
    total_months = len(fresh)
    avg_pct = fresh["pnl_pct"].mean()
    median_pct = fresh["pnl_pct"].median()
    best = fresh.loc[fresh["pnl_pct"].idxmax()]
    worst = fresh.loc[fresh["pnl_pct"].idxmin()]
    print()
    print(f"Profitable months:  {profitable_months}/{total_months}  ({profitable_months/total_months*100:.1f}%)")
    print(f"Avg monthly return: {avg_pct:+.2f}%")
    print(f"Median monthly:     {median_pct:+.2f}%")
    print(f"Best month:         {best['month']}  {best['pnl_pct']:+.2f}%")
    print(f"Worst month:        {worst['month']}  {worst['pnl_pct']:+.2f}%")
    print(f"\nSaved: {out_dir}/monthly_v22.csv, trades_v22.csv, monthly_fresh_v22.csv")


if __name__ == "__main__":
    main()
