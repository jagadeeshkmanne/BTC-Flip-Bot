"""
HONEST realistic v1.1 backtest with all known biases removed:
  1. Intra-bar lookahead: pessimistic (SL fires first if both possible)
  2. Position size CAPPED at realistic Bybit liquidity ($100K notional max)
  3. Show linear (no compounding) vs realistic compounding
  4. Year-by-year breakdown to expose any single-window dominance
"""
import pandas as pd
import numpy as np
from datetime import timedelta

CSV_PATH = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv"

INITIAL_BAL   = 5000.0
LEVERAGE      = 3
PER_LEG_NOTIONAL_BASE = 7125.0
MAX_NOTIONAL_PER_LEG  = 100000.0  # Cap at $100K per leg (realistic liquidity)
RSI_LEN, RSI_LONG, RSI_SHORT = 9, 30, 70
EMA_FAST, EMA_SLOW = 20, 50
GAP_MIN, ATR_LEN, ATR_MAX_PCT = 0.0025, 14, 0.006
DCA_TRIGGER, TP_L1, TP_DCA = 0.005, 0.005, 0.0025
SL_FROM_WORST = 0.006
FEE_RATE, SLIPPAGE = 0.0004, 0.0002
COOLDOWN_MIN, DAILY_STOP = 15, -200.0
TIME_SL_BARS, WEEKEND_MULT = 72, 2.0


def wilder_rsi(close, length):
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0/length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1.0/length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def ema(s, length):
    return s.ewm(span=length, adjust=False, min_periods=length).mean()


def wilder_atr(high, low, close, length):
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0/length, adjust=False, min_periods=length).mean()


def prep_data(df, days):
    last_ts = df["timestamp"].iloc[-1]
    cutoff = last_ts - timedelta(days=days)
    warmup_cut = last_ts - timedelta(days=days + 5)
    df_calc = df[df["timestamp"] >= warmup_cut].copy().reset_index(drop=True)
    df_calc["rsi5"] = wilder_rsi(df_calc["close"], RSI_LEN)
    df_calc["atr5"] = wilder_atr(df_calc["high"], df_calc["low"], df_calc["close"], ATR_LEN)
    df_calc["atr_pct"] = df_calc["atr5"] / df_calc["close"]
    df_calc_ix = df_calc.set_index("timestamp")
    df15 = df_calc_ix[["open", "high", "low", "close", "volume"]].resample(
        "15min", label="left", closed="left"
    ).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    df15["ema20"] = ema(df15["close"], EMA_FAST)
    df15["ema50"] = ema(df15["close"], EMA_SLOW)
    df15["trend"] = np.where(df15["ema20"] > df15["ema50"], 1,
                      np.where(df15["ema20"] < df15["ema50"], -1, 0))
    df15["gap_pct"] = (df15["ema20"] - df15["ema50"]).abs() / df15["ema50"]
    df15 = df15.reset_index().rename(columns={"timestamp": "ts15"})
    df15["closed_at"] = df15["ts15"] + pd.Timedelta(minutes=15)
    df_calc = df_calc.sort_values("timestamp")
    merged = pd.merge_asof(
        df_calc, df15[["closed_at", "ema20", "ema50", "trend", "gap_pct"]].sort_values("closed_at"),
        left_on="timestamp", right_on="closed_at", direction="backward"
    )
    return merged[merged["timestamp"] >= cutoff].reset_index(drop=True)


def run(bt, mode="pessimistic", compound=True, cap_position=True, use_time_sl=True, smart_time_sl=True):
    """
    mode: 'pessimistic' (SL first on conflict), 'optimistic' (TP first), 'random' (split 50/50)
    compound: True = position scales with balance; False = fixed $5K basis
    cap_position: True = cap at MAX_NOTIONAL_PER_LEG
    """
    balance = INITIAL_BAL
    peak = INITIAL_BAL
    max_dd = 0.0
    position = None
    cooldown_until = None
    daily_pnl = {}
    trades = []
    exits = {"TP": 0, "BE-DCA": 0, "SL": 0, "TREND_FLIP": 0, "TIME_SL": 0}

    def compute_pnl(side, avg_entry, exit_px, qty):
        eff_exit = exit_px * (1 - SLIPPAGE) if side == "LONG" else exit_px * (1 + SLIPPAGE)
        gross = (eff_exit - avg_entry) * qty if side == "LONG" else (avg_entry - eff_exit) * qty
        fees = (avg_entry + eff_exit) * qty * FEE_RATE
        return gross - fees

    def open_position(ts, side, price, weekend, bal):
        mult = WEEKEND_MULT if weekend else 1.0
        bal_scale = (bal / INITIAL_BAL) if compound else 1.0
        eff_entry = price * (1 + SLIPPAGE) if side == "LONG" else price * (1 - SLIPPAGE)
        leg_notional = PER_LEG_NOTIONAL_BASE * mult * bal_scale
        if cap_position:
            leg_notional = min(leg_notional, MAX_NOTIONAL_PER_LEG)
        qty = leg_notional / eff_entry
        return {
            "side": side, "open_ts": ts, "open_bar_idx": None,
            "entries": [(eff_entry, qty)], "avg_entry": eff_entry,
            "total_qty": qty, "weekend": weekend, "bal_at_open": bal, "entry_trend": None,
        }

    def add_dca(pos, fill_price):
        side = pos["side"]
        eff = fill_price * (1 + SLIPPAGE) if side == "LONG" else fill_price * (1 - SLIPPAGE)
        mult = WEEKEND_MULT if pos["weekend"] else 1.0
        bal_scale = (pos["bal_at_open"] / INITIAL_BAL) if compound else 1.0
        leg_notional = PER_LEG_NOTIONAL_BASE * mult * bal_scale
        if cap_position:
            leg_notional = min(leg_notional, MAX_NOTIONAL_PER_LEG)
        qty = leg_notional / eff
        pos["entries"].append((eff, qty))
        pos["total_qty"] += qty
        pos["avg_entry"] = (pos["entries"][0][0] + pos["entries"][1][0]) / 2
        return pos

    def close_pos(pos, exit_ts, exit_px, reason):
        nonlocal balance, peak, max_dd, cooldown_until
        net = compute_pnl(pos["side"], pos["avg_entry"], exit_px, pos["total_qty"])
        balance += net
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak
        if dd > max_dd:
            max_dd = dd
        d = exit_ts.date()
        daily_pnl[d] = daily_pnl.get(d, 0.0) + net
        if net < 0:
            cooldown_until = exit_ts + timedelta(minutes=COOLDOWN_MIN)
        hold = (exit_ts - pos["open_ts"]).total_seconds() / 3600.0
        trades.append({"close_ts": exit_ts, "side": pos["side"], "net": net, "reason": reason, "hold_hours": hold})
        exits[reason] += 1

    ts_arr = bt["timestamp"].values
    open_arr = bt["open"].values
    high_arr = bt["high"].values
    low_arr = bt["low"].values
    close_arr = bt["close"].values
    rsi_arr = bt["rsi5"].values
    atrp_arr = bt["atr_pct"].values
    trend_arr = bt["trend"].values
    gap_arr = bt["gap_pct"].values
    n = len(bt)

    pending_entry = None
    for i in range(n):
        ts = pd.Timestamp(ts_arr[i])
        o, h, l, c = open_arr[i], high_arr[i], low_arr[i], close_arr[i]
        rsi, atrp, trend, gap = rsi_arr[i], atrp_arr[i], trend_arr[i], gap_arr[i]

        if position is None and pending_entry is not None:
            side, weekend, entry_trend = pending_entry
            pending_entry = None
            position = open_position(ts, side, o, weekend, balance)
            position["open_bar_idx"] = i
            position["entry_trend"] = entry_trend

        if position is not None:
            side = position["side"]
            legs = len(position["entries"])
            l2_filled_this_bar = False

            if legs == 1:
                l1 = position["entries"][0][0]
                trigger = l1 * (1 - DCA_TRIGGER) if side == "LONG" else l1 * (1 + DCA_TRIGGER)
                hit = (side == "LONG" and l <= trigger) or (side == "SHORT" and h >= trigger)
                if hit:
                    add_dca(position, trigger)
                    legs = 2
                    l2_filled_this_bar = True

            avg = position["avg_entry"]
            if legs == 1:
                worst = position["entries"][0][0]
                if side == "LONG":
                    tp_px = avg * (1 + TP_L1); sl_px = worst * (1 - SL_FROM_WORST)
                else:
                    tp_px = avg * (1 - TP_L1); sl_px = worst * (1 + SL_FROM_WORST)
            else:
                if side == "LONG":
                    tp_px = avg * (1 + TP_DCA); sl_px = avg
                else:
                    tp_px = avg * (1 - TP_DCA); sl_px = avg

            exited = False
            if not l2_filled_this_bar:
                if side == "LONG":
                    tp_hit = h >= tp_px
                    sl_hit = l <= sl_px
                else:
                    tp_hit = l <= tp_px
                    sl_hit = h >= sl_px

                if tp_hit and sl_hit:
                    # CONFLICT: both fire in same bar
                    if mode == "pessimistic":
                        # Assume SL fires first (worst case)
                        reason = "SL" if legs == 1 else "BE-DCA"
                        close_pos(position, ts, sl_px, reason); position = None; exited = True
                    elif mode == "optimistic":
                        close_pos(position, ts, tp_px, "TP"); position = None; exited = True
                    else:  # random
                        # Use open-to-tp vs open-to-sl distance heuristic
                        # If open is closer to tp, assume TP first; else SL first
                        if abs(o - tp_px) < abs(o - sl_px):
                            close_pos(position, ts, tp_px, "TP"); position = None; exited = True
                        else:
                            reason = "SL" if legs == 1 else "BE-DCA"
                            close_pos(position, ts, sl_px, reason); position = None; exited = True
                elif tp_hit:
                    close_pos(position, ts, tp_px, "TP"); position = None; exited = True
                elif sl_hit:
                    reason = "SL" if legs == 1 else "BE-DCA"
                    close_pos(position, ts, sl_px, reason); position = None; exited = True

            if not exited and position is not None and not np.isnan(trend) and trend != 0:
                et = position["entry_trend"]
                if (side == "LONG" and et == 1 and trend == -1) or (side == "SHORT" and et == -1 and trend == 1):
                    close_pos(position, ts, c, "TREND_FLIP"); position = None; exited = True

            if not exited and position is not None and use_time_sl:
                bars_open = i - position["open_bar_idx"]
                if bars_open >= TIME_SL_BARS:
                    net_now = compute_pnl(side, position["avg_entry"], c, position["total_qty"])
                    if not smart_time_sl or net_now < 0:
                        close_pos(position, ts, c, "TIME_SL"); position = None; exited = True

        if position is None and pending_entry is None:
            if cooldown_until is not None and ts < cooldown_until:
                continue
            d = ts.date()
            if daily_pnl.get(d, 0.0) <= DAILY_STOP:
                continue
            if np.isnan(rsi) or np.isnan(atrp) or np.isnan(trend) or np.isnan(gap):
                continue
            if atrp > ATR_MAX_PCT or gap < GAP_MIN:
                continue
            weekend = ts.dayofweek >= 5
            if rsi <= RSI_LONG and trend == 1:
                pending_entry = ("LONG", weekend, 1)
            elif rsi >= RSI_SHORT and trend == -1:
                pending_entry = ("SHORT", weekend, -1)

    total = len(trades)
    wins = sum(1 for t in trades if t["net"] > 0)
    losses = sum(1 for t in trades if t["net"] < 0)
    wr = wins / total * 100 if total else 0
    profit = balance - INITIAL_BAL
    sum_wins = sum(t["net"] for t in trades if t["net"] > 0)
    sum_losses = sum(t["net"] for t in trades if t["net"] < 0)
    pf = abs(sum_wins / sum_losses) if sum_losses < 0 else float("inf")
    return {
        "trades": total, "wins": wins, "losses": losses, "wr": wr,
        "profit": profit, "balance": balance, "max_dd": max_dd * 100, "pf": pf, "exits": exits,
    }


def main():
    print("Loading data...")
    df = pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    print(f"Last ts: {df['timestamp'].iloc[-1]}\n")

    # 5-year backtest with multiple modes
    bt = prep_data(df, 1825)
    print(f"5-year data: {len(bt)} 5m bars\n")
    print("═" * 100)
    print(f"  v1.1 SMART — 5 YEAR HONEST TEST")
    print("═" * 100)
    print(f"\n{'CONFIG':<55} {'Trades':>7} {'Win%':>6} {'Profit':>14} {'MaxDD':>7} {'PF':>6}")
    print("─" * 100)

    # Most optimistic (what I showed before)
    r = run(bt, mode="optimistic", compound=True, cap_position=False)
    print(f"  Optimistic + uncapped compound (what I showed before)    {r['trades']:>7} {r['wr']:>5.1f}% ${r['profit']:>12,.0f} {r['max_dd']:>6.2f}% {r['pf']:>5.2f}")

    # Honest pessimistic + cap
    r = run(bt, mode="pessimistic", compound=True, cap_position=True)
    print(f"  PESSIMISTIC + position cap $100K notional/leg (HONEST)   {r['trades']:>7} {r['wr']:>5.1f}% ${r['profit']:>12,.0f} {r['max_dd']:>6.2f}% {r['pf']:>5.2f}")

    # Linear (no compounding) — pure per-trade economics
    r = run(bt, mode="pessimistic", compound=False, cap_position=False)
    print(f"  PESSIMISTIC + NO COMPOUND (per-trade truth, fixed $5K)   {r['trades']:>7} {r['wr']:>5.1f}% ${r['profit']:>12,.0f} {r['max_dd']:>6.2f}% {r['pf']:>5.2f}")
    realistic_profit_5y = r['profit']

    print("\n" + "═" * 100)
    print(f"  REALISTIC ASSESSMENT")
    print("═" * 100)
    print(f"\nLinear no-compound result: ${realistic_profit_5y:,.0f} over 5 years")
    print(f"  Per year:  ${realistic_profit_5y/5:,.0f} (~{realistic_profit_5y/5/INITIAL_BAL*100:.0f}% per year linear)")
    print(f"  Per month: ${realistic_profit_5y/60:,.0f}")
    print(f"\nWith RESTRICTED compounding (capped at $100K/leg):")
    r_cap = run(bt, mode="pessimistic", compound=True, cap_position=True)
    print(f"  5-year:    ${r_cap['profit']:,.0f}")
    print(f"  Per year:  ${r_cap['profit']/5:,.0f}")

    # Year-by-year breakdown to expose any single-year dominance
    print(f"\n{'═' * 100}\n  YEAR-BY-YEAR (pessimistic + linear, no compound) — exposes overfitting\n{'═' * 100}\n")
    print(f"{'YEAR':<10}{'Trades':>8}{'Win%':>7}{'Profit':>12}{'PF':>7}{'BestExit':>12}")

    last_ts = df["timestamp"].iloc[-1]
    yearly_results = []
    for y in [2021, 2022, 2023, 2024, 2025, 2026]:
        df_y = df[(df["timestamp"] >= pd.Timestamp(f"{y}-01-01")) & (df["timestamp"] < pd.Timestamp(f"{y+1}-01-01"))]
        if len(df_y) < 100:
            continue
        # Use 5 days of warmup data
        warmup_start = pd.Timestamp(f"{y-1}-12-25")
        df_year_with_warmup = df[(df["timestamp"] >= warmup_start) & (df["timestamp"] < pd.Timestamp(f"{y+1}-01-01"))].copy().reset_index(drop=True)

        df_year_with_warmup["rsi5"] = wilder_rsi(df_year_with_warmup["close"], RSI_LEN)
        df_year_with_warmup["atr5"] = wilder_atr(df_year_with_warmup["high"], df_year_with_warmup["low"], df_year_with_warmup["close"], ATR_LEN)
        df_year_with_warmup["atr_pct"] = df_year_with_warmup["atr5"] / df_year_with_warmup["close"]
        dfix = df_year_with_warmup.set_index("timestamp")
        df15 = dfix[["open", "high", "low", "close", "volume"]].resample(
            "15min", label="left", closed="left"
        ).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
        df15["ema20"] = ema(df15["close"], EMA_FAST)
        df15["ema50"] = ema(df15["close"], EMA_SLOW)
        df15["trend"] = np.where(df15["ema20"] > df15["ema50"], 1,
                          np.where(df15["ema20"] < df15["ema50"], -1, 0))
        df15["gap_pct"] = (df15["ema20"] - df15["ema50"]).abs() / df15["ema50"]
        df15 = df15.reset_index().rename(columns={"timestamp": "ts15"})
        df15["closed_at"] = df15["ts15"] + pd.Timedelta(minutes=15)
        merged = pd.merge_asof(
            df_year_with_warmup.sort_values("timestamp"),
            df15[["closed_at", "ema20", "ema50", "trend", "gap_pct"]].sort_values("closed_at"),
            left_on="timestamp", right_on="closed_at", direction="backward"
        )
        bt_y = merged[(merged["timestamp"] >= pd.Timestamp(f"{y}-01-01"))].reset_index(drop=True)

        r = run(bt_y, mode="pessimistic", compound=False, cap_position=False)
        # Find biggest exit
        best_exit = max(r["exits"].items(), key=lambda x: x[1])
        print(f"{y:<10}{r['trades']:>8}{r['wr']:>6.1f}% ${r['profit']:>10,.0f}{r['pf']:>7.2f}{best_exit[0]:>11}={best_exit[1]}")
        yearly_results.append((y, r['profit']))

    # Check consistency
    print()
    profitable_years = sum(1 for _, p in yearly_results if p > 0)
    print(f"Profitable years: {profitable_years}/{len(yearly_results)}")
    if yearly_results:
        avg_year = sum(p for _, p in yearly_results) / len(yearly_results)
        print(f"Average per year: ${avg_year:,.0f}")


if __name__ == "__main__":
    main()
