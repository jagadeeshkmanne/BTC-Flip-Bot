"""
Comprehensive parameter sweep on 3-month bear-move data.
BTC went $85K → $60K — perfect test bed for finding robust config.
"""
import pandas as pd
import numpy as np
from datetime import timedelta
import itertools

CSV_PATH = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv"

INITIAL_BAL = 5000.0
LEVERAGE = 3
PER_LEG_NOTIONAL = 7125.0
EMA_FAST, EMA_SLOW = 20, 50
ATR_LEN = 14
DCA_TRIGGER = 0.005
TP_L1 = 0.005
TP_DCA = 0.0025
SL_FROM_WORST = 0.006
FEE_RATE = 0.00055
SLIPPAGE = 0.0002
COOLDOWN_MIN = 15
DAILY_STOP = -200.0
TIME_SL_BARS = 72
WEEKEND_MULT = 2.0


def wilder_rsi(close, length):
    delta = close.diff()
    gain = delta.clip(lower=0.0); loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0/length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1.0/length, adjust=False, min_periods=length).mean()
    return 100 - (100 / (1 + avg_gain / avg_loss))


def ema(s, length):
    return s.ewm(span=length, adjust=False, min_periods=length).mean()


def wilder_atr(high, low, close, length):
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0/length, adjust=False, min_periods=length).mean()


def prep_data(df, days, rsi_len=9):
    last_ts = df["timestamp"].iloc[-1]
    cutoff = last_ts - timedelta(days=days)
    warmup_cut = last_ts - timedelta(days=days + 5)
    df_calc = df[df["timestamp"] >= warmup_cut].copy().reset_index(drop=True)
    df_calc["rsi5"] = wilder_rsi(df_calc["close"], rsi_len)
    df_calc["atr5"] = wilder_atr(df_calc["high"], df_calc["low"], df_calc["close"], ATR_LEN)
    df_calc["atr_pct"] = df_calc["atr5"] / df_calc["close"]
    dfix = df_calc.set_index("timestamp")
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
        df_calc.sort_values("timestamp"),
        df15[["closed_at", "ema20", "ema50", "trend", "gap_pct"]].sort_values("closed_at"),
        left_on="timestamp", right_on="closed_at", direction="backward"
    )
    return merged[merged["timestamp"] >= cutoff].reset_index(drop=True)


def run(bt, rsi_long=30, rsi_short=70, gap_min=0.0025, atr_max=0.006,
        be_dca_wait_bars=0, allow_counter_trend=False, use_time_sl=True):
    balance = INITIAL_BAL
    peak, max_dd = INITIAL_BAL, 0.0
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

    def open_pos(ts, side, price, weekend):
        mult = WEEKEND_MULT if weekend else 1.0
        eff = price * (1 + SLIPPAGE) if side == "LONG" else price * (1 - SLIPPAGE)
        qty = (PER_LEG_NOTIONAL * mult) / eff
        return {"side": side, "open_ts": ts, "open_bar_idx": None,
                "entries": [(eff, qty)], "avg_entry": eff, "total_qty": qty,
                "weekend": weekend, "entry_trend": None, "l2_bar_idx": None}

    def add_dca(pos, fill_price, bar_idx):
        side = pos["side"]
        eff = fill_price * (1 + SLIPPAGE) if side == "LONG" else fill_price * (1 - SLIPPAGE)
        mult = WEEKEND_MULT if pos["weekend"] else 1.0
        qty = (PER_LEG_NOTIONAL * mult) / eff
        pos["entries"].append((eff, qty)); pos["total_qty"] += qty
        pos["avg_entry"] = (pos["entries"][0][0] + pos["entries"][1][0]) / 2
        pos["l2_bar_idx"] = bar_idx

    def close_pos(pos, exit_ts, exit_px, reason):
        nonlocal balance, peak, max_dd, cooldown_until
        net = compute_pnl(pos["side"], pos["avg_entry"], exit_px, pos["total_qty"])
        balance += net
        if balance > peak: peak = balance
        dd = (peak - balance) / peak
        if dd > max_dd: max_dd = dd
        d = exit_ts.date()
        daily_pnl[d] = daily_pnl.get(d, 0.0) + net
        if net < 0: cooldown_until = exit_ts + timedelta(minutes=COOLDOWN_MIN)
        trades.append({"net": net, "reason": reason}); exits[reason] += 1

    ts_arr = bt["timestamp"].values
    open_arr, high_arr, low_arr, close_arr = bt["open"].values, bt["high"].values, bt["low"].values, bt["close"].values
    rsi_arr, atrp_arr = bt["rsi5"].values, bt["atr_pct"].values
    trend_arr, gap_arr = bt["trend"].values, bt["gap_pct"].values
    n = len(bt)
    pending_entry = None

    for i in range(n):
        ts = pd.Timestamp(ts_arr[i])
        o, h, l, c = open_arr[i], high_arr[i], low_arr[i], close_arr[i]
        rsi, atrp, trend, gap = rsi_arr[i], atrp_arr[i], trend_arr[i], gap_arr[i]

        if position is None and pending_entry is not None:
            side, weekend, entry_trend = pending_entry
            pending_entry = None
            position = open_pos(ts, side, o, weekend)
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
                    add_dca(position, trigger, i); legs = 2; l2_filled_this_bar = True

            avg = position["avg_entry"]
            if legs == 1:
                worst = position["entries"][0][0]
                if side == "LONG":
                    tp_px = avg * (1 + TP_L1); sl_px = worst * (1 - SL_FROM_WORST)
                else:
                    tp_px = avg * (1 - TP_L1); sl_px = worst * (1 + SL_FROM_WORST)
            else:
                bars_since_l2 = i - position["l2_bar_idx"]
                be_armed = bars_since_l2 >= be_dca_wait_bars
                if side == "LONG":
                    tp_px = avg * (1 + TP_DCA)
                    sl_px = avg if be_armed else -np.inf
                else:
                    tp_px = avg * (1 - TP_DCA)
                    sl_px = avg if be_armed else np.inf

            exited = False
            if not l2_filled_this_bar:
                if side == "LONG":
                    tp_hit = h >= tp_px; sl_hit = l <= sl_px
                else:
                    tp_hit = l <= tp_px; sl_hit = h >= sl_px
                if tp_hit and sl_hit:
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
                    if net_now < 0:
                        close_pos(position, ts, c, "TIME_SL"); position = None; exited = True

        if position is None and pending_entry is None:
            if cooldown_until is not None and ts < cooldown_until: continue
            d = ts.date()
            if daily_pnl.get(d, 0.0) <= DAILY_STOP: continue
            if np.isnan(rsi) or np.isnan(atrp) or np.isnan(trend) or np.isnan(gap): continue
            if atrp > atr_max: continue
            if gap < gap_min: continue
            weekend = ts.dayofweek >= 5
            if rsi <= rsi_long:
                # LONG: require trend UP unless allow_counter_trend
                if trend == 1 or allow_counter_trend:
                    et = 1 if trend == 1 else -1
                    pending_entry = ("LONG", weekend, et)
            elif rsi >= rsi_short:
                if trend == -1 or allow_counter_trend:
                    et = -1 if trend == -1 else 1
                    pending_entry = ("SHORT", weekend, et)

    total = len(trades)
    wins = sum(1 for t in trades if t["net"] > 0)
    losses = sum(1 for t in trades if t["net"] < 0)
    wr = wins / total * 100 if total else 0
    profit = balance - INITIAL_BAL
    sum_wins = sum(t["net"] for t in trades if t["net"] > 0)
    sum_losses = sum(t["net"] for t in trades if t["net"] < 0)
    pf = abs(sum_wins / sum_losses) if sum_losses < 0 else float("inf")
    return {"trades": total, "wins": wins, "losses": losses, "wr": wr,
            "profit": profit, "max_dd": max_dd * 100, "pf": pf, "exits": exits}


def main():
    print("Loading data...")
    df = pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    print(f"Last ts: {df['timestamp'].iloc[-1]}\n")

    # 3-month window
    bt = prep_data(df, 90)
    print(f"3-month window: {len(bt)} bars")
    print(f"  Start price: ${bt['close'].iloc[0]:,.0f}")
    print(f"  End price:   ${bt['close'].iloc[-1]:,.0f}")
    print(f"  Range:       ${bt['close'].min():,.0f} - ${bt['close'].max():,.0f}\n")

    # Sweep — test for BOTH v1 (no time-SL) and v1.1 (smart time-SL)
    base_configs = [
        # (label, rsi_long, rsi_short, gap, atr, be_wait, counter_trend)
        ("CURRENT (RSI 30/70, GAP 0.25%, no BE wait)",                       30, 70, 0.0025, 0.006, 0, False),
        ("RSI 30/70, GAP 0.20%, no BE wait",                                  30, 70, 0.0020, 0.006, 0, False),
        ("RSI 30/70, GAP 0.15%, no BE wait",                                  30, 70, 0.0015, 0.006, 0, False),
        ("RSI 30/70, GAP 0.10%, no BE wait",                                  30, 70, 0.0010, 0.006, 0, False),
        ("RSI 33/67, GAP 0.25%, no BE wait",                                  33, 67, 0.0025, 0.006, 0, False),
        ("RSI 35/65, GAP 0.25%, no BE wait",                                  35, 65, 0.0025, 0.006, 0, False),
        ("RSI 33/67, GAP 0.15%, no BE wait",                                  33, 67, 0.0015, 0.006, 0, False),
        ("RSI 30/70, GAP 0.25%, BE wait 6",                                   30, 70, 0.0025, 0.006, 6, False),
        ("RSI 30/70, GAP 0.20%, BE wait 6",                                   30, 70, 0.0020, 0.006, 6, False),
        ("RSI 30/70, GAP 0.15%, BE wait 6",                                   30, 70, 0.0015, 0.006, 6, False),
        ("RSI 30/70, GAP 0.25%, COUNTER-TREND",                               30, 70, 0.0025, 0.006, 0, True),
        ("RSI 30/70, GAP 0.15%, COUNTER-TREND",                               30, 70, 0.0015, 0.006, 0, True),
        ("RSI 30/70, GAP 0.15%, ATR 0.8%, BE wait 6",                         30, 70, 0.0015, 0.008, 6, False),
        ("⭐ RSI 30/70, GAP 0.20%, BE wait 6 (best combo)",                   30, 70, 0.0020, 0.006, 6, False),
    ]

    # Two variants: v1 (no time-SL) and v1.1 (with smart 6h time-SL)
    for variant_name, use_ts in [("v1 (NO time-SL)", False), ("v1.1 (smart 6h time-SL)", True)]:
        print("\n" + "═" * 110)
        print(f"  {variant_name} — 3-MONTH PARAM SWEEP")
        print("═" * 110)
        print(f"\n{'CONFIG':<60} {'Trades':>7} {'Win%':>6} {'Profit':>10} {'DD%':>7} {'PF':>6}")
        print("─" * 110)
        for label, rl, rs, gap, atr, be_wait, ct in base_configs:
            r = run(bt, rsi_long=rl, rsi_short=rs, gap_min=gap, atr_max=atr,
                    be_dca_wait_bars=be_wait, allow_counter_trend=ct, use_time_sl=use_ts)
            sign = "+" if r["profit"] >= 0 else "-"
            print(f"  {label:<60} {r['trades']:>7} {r['wr']:>5.1f}% {sign}${abs(r['profit']):>7,.0f} {r['max_dd']:>6.2f}% {r['pf']:>5.2f}")

    # Save the v1 results for ranking
    results = []
    for label, rl, rs, gap, atr, be_wait, ct in base_configs:
        r = run(bt, rsi_long=rl, rsi_short=rs, gap_min=gap, atr_max=atr,
                be_dca_wait_bars=be_wait, allow_counter_trend=ct, use_time_sl=False)
        results.append((f"v1: {label}", r))
        r2 = run(bt, rsi_long=rl, rsi_short=rs, gap_min=gap, atr_max=atr,
                be_dca_wait_bars=be_wait, allow_counter_trend=ct, use_time_sl=True)
        results.append((f"v1.1: {label}", r2))

    # Top picks by different criteria
    print("\n" + "═" * 110)
    print("  TOP PICKS")
    print("═" * 110)
    by_profit = sorted(results, key=lambda x: x[1]["profit"], reverse=True)[:3]
    by_pf = sorted(results, key=lambda x: x[1]["pf"], reverse=True)[:3]
    by_calmar = sorted(results, key=lambda x: x[1]["profit"] / max(x[1]["max_dd"], 0.5), reverse=True)[:3]

    print("\nBy ABSOLUTE PROFIT:")
    for label, r in by_profit:
        print(f"  +${r['profit']:>6,.0f} / DD {r['max_dd']:>5.2f}% / PF {r['pf']:>4.2f}  ← {label}")
    print("\nBy PROFIT FACTOR (risk-adjusted):")
    for label, r in by_pf:
        print(f"  PF {r['pf']:>4.2f} / +${r['profit']:>6,.0f} / DD {r['max_dd']:>5.2f}%  ← {label}")
    print("\nBy CALMAR (profit / DD):")
    for label, r in by_calmar:
        ratio = r['profit'] / max(r['max_dd'], 0.5)
        print(f"  {ratio:>6.0f} / +${r['profit']:>6,.0f} / DD {r['max_dd']:>5.2f}%  ← {label}")


if __name__ == "__main__":
    main()
