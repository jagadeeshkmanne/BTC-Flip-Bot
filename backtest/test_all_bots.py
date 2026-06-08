"""
Test ALL 4 BTC-Flip-Bot variants (v1, v1.1, v2, v5) with the canonical Python
backtester. Built on top of v11_pure_python.py — same engine, parameterized per
bot config.

Bots:
  v1   = baseline: RSI 30/70 + 15m trend + GAP 0.25% + ATR 0.6% + DCA 2 legs +
         BE-after-DCA + trend-flip exit + daily $200 stop + weekend 2x.
  v1.1 = v1 + smart 6h time-SL (only fires on loss).
  v2   = v1 + 1h-move max 2% filter + blocked UTC hours [12, 13].
  v5   = v2 entries + NO DCA + 0.5% SL from entry (single leg, tighter SL).
"""
import pandas as pd
import numpy as np
from datetime import timedelta
import sys
import argparse

CSV_PATH = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv"

INITIAL_BAL   = 5000.0
LEVERAGE      = 3
PER_LEG_NOTIONAL = 7125.0  # $2,375 margin × 3 lev

RSI_LEN       = 9
RSI_LONG      = 30
RSI_SHORT     = 70

EMA_FAST      = 20
EMA_SLOW      = 50
GAP_MIN       = 0.0025
ATR_LEN       = 14
ATR_MAX_PCT   = 0.006

DCA_TRIGGER   = 0.005
TP_L1         = 0.005
TP_DCA        = 0.0025
SL_FROM_WORST = 0.006

FEE_RATE = 0.00055
SLIPPAGE      = 0.0002

COOLDOWN_MIN  = 15
DAILY_STOP    = -200.0

TIME_SL_BARS  = 72
WEEKEND_MULT  = 2.0


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
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0/length, adjust=False, min_periods=length).mean()


# Bot configs: differ in what filters/exits are active
BOT_CONFIGS = {
    "v1": {
        "use_dca": True,
        "use_time_sl": False,
        "smart_time_sl": False,
        "hour_block": set(),
        "h1_move_max": None,
        "single_leg_sl_from_entry": False,  # v5 uses 0.5% from entry, not 0.6% from worst
    },
    "v1.1": {
        "use_dca": True,
        "use_time_sl": True,
        "smart_time_sl": True,
        "hour_block": set(),
        "h1_move_max": None,
        "single_leg_sl_from_entry": False,
    },
    "v2": {
        "use_dca": True,
        "use_time_sl": False,
        "smart_time_sl": False,
        "hour_block": {12, 13},
        "h1_move_max": 0.02,
        "single_leg_sl_from_entry": False,
    },
    "v5": {
        "use_dca": False,           # single leg, no DCA
        "use_time_sl": False,
        "smart_time_sl": False,
        "hour_block": {12, 13},
        "h1_move_max": 0.02,
        "single_leg_sl_from_entry": True,  # 0.5% from entry
    },
}


def run_backtest(bt, cfg, label):
    """Run the backtester with `cfg` on the prepared bt dataframe. Returns metrics dict."""
    balance = INITIAL_BAL
    peak    = INITIAL_BAL
    max_dd  = 0.0

    position = None
    cooldown_until = None
    daily_pnl = {}
    trades = []
    exit_reasons = {"TP": 0, "BE-DCA": 0, "SL": 0, "TREND_FLIP": 0, "TIME_SL": 0}

    sl_from_entry = 0.005  # v5: 0.5% from entry

    def compute_pnl(side, avg_entry, exit_px, qty):
        if side == "LONG":
            eff_exit = exit_px * (1 - SLIPPAGE)
            gross = (eff_exit - avg_entry) * qty
        else:
            eff_exit = exit_px * (1 + SLIPPAGE)
            gross = (avg_entry - eff_exit) * qty
        notional_in  = avg_entry * qty
        notional_out = eff_exit * qty
        fees = (notional_in + notional_out) * FEE_RATE
        return gross, fees, gross - fees

    def open_position(ts, side, price, weekend, bal):
        mult = WEEKEND_MULT if weekend else 1.0
        bal_scale = bal / INITIAL_BAL
        if side == "LONG":
            eff_entry = price * (1 + SLIPPAGE)
        else:
            eff_entry = price * (1 - SLIPPAGE)
        leg_notional = PER_LEG_NOTIONAL * mult * bal_scale
        qty = leg_notional / eff_entry
        return {
            "side": side,
            "open_ts": ts,
            "open_bar_idx": None,
            "entries": [(eff_entry, qty)],
            "avg_entry": eff_entry,
            "total_qty": qty,
            "weekend": weekend,
            "bal_at_open": bal,
            "entry_trend": None,
            "l1_price": eff_entry,
        }

    def add_dca(pos, fill_price):
        side = pos["side"]
        if side == "LONG":
            eff = fill_price * (1 + SLIPPAGE)
        else:
            eff = fill_price * (1 - SLIPPAGE)
        mult = WEEKEND_MULT if pos["weekend"] else 1.0
        bal_scale = pos["bal_at_open"] / INITIAL_BAL
        leg_notional = PER_LEG_NOTIONAL * mult * bal_scale
        qty = leg_notional / eff
        pos["entries"].append((eff, qty))
        pos["total_qty"] += qty
        p1, p2 = pos["entries"][0][0], pos["entries"][1][0]
        pos["avg_entry"] = (p1 + p2) / 2
        return pos

    def close_position(pos, exit_ts, exit_px, reason):
        nonlocal balance, peak, max_dd, cooldown_until
        gross, fees, net = compute_pnl(pos["side"], pos["avg_entry"], exit_px, pos["total_qty"])
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
        hold_hours = (exit_ts - pos["open_ts"]).total_seconds() / 3600.0
        trades.append({
            "open_ts": pos["open_ts"],
            "close_ts": exit_ts,
            "side": pos["side"],
            "avg_entry": pos["avg_entry"],
            "exit_px": exit_px,
            "qty": pos["total_qty"],
            "legs": len(pos["entries"]),
            "net": net,
            "reason": reason,
            "hold_hours": hold_hours,
        })
        exit_reasons[reason] += 1

    # Pre-extract arrays
    ts_arr     = bt["timestamp"].values
    open_arr   = bt["open"].values
    high_arr   = bt["high"].values
    low_arr    = bt["low"].values
    close_arr  = bt["close"].values
    rsi_arr    = bt["rsi5"].values
    atrp_arr   = bt["atr_pct"].values
    trend_arr  = bt["trend"].values
    gap_arr    = bt["gap_pct"].values

    # Precompute 1h move: close[t] vs close[t-12] (12 × 5m = 1h)
    if cfg["h1_move_max"] is not None:
        h1_move_arr = np.abs(bt["close"].pct_change(12).values)
    else:
        h1_move_arr = None

    n = len(bt)
    pending_entry = None

    for i in range(n):
        ts = pd.Timestamp(ts_arr[i])
        o, h, l, c = open_arr[i], high_arr[i], low_arr[i], close_arr[i]
        rsi = rsi_arr[i]
        atrp = atrp_arr[i]
        trend = trend_arr[i]
        gap = gap_arr[i]

        # Fill pending entry at this bar's OPEN
        if position is None and pending_entry is not None:
            side, weekend, entry_trend = pending_entry
            pending_entry = None
            position = open_position(ts, side, o, weekend, balance)
            position["open_bar_idx"] = i
            position["entry_trend"] = entry_trend

        # Manage open position
        if position is not None:
            side = position["side"]
            legs = len(position["entries"])
            l2_filled_this_bar = False

            # 1a) DCA L2 fill check (only if DCA is enabled)
            if cfg["use_dca"] and legs == 1:
                l1 = position["entries"][0][0]
                if side == "LONG":
                    trigger = l1 * (1 - DCA_TRIGGER)
                    if l <= trigger:
                        add_dca(position, trigger)
                        legs = 2
                        l2_filled_this_bar = True
                else:
                    trigger = l1 * (1 + DCA_TRIGGER)
                    if h >= trigger:
                        add_dca(position, trigger)
                        legs = 2
                        l2_filled_this_bar = True

            avg = position["avg_entry"]
            if legs == 1:
                tp_dist = TP_L1
                worst = position["entries"][0][0]
                if cfg["single_leg_sl_from_entry"]:
                    # v5: SL 0.5% from entry
                    if side == "LONG":
                        tp_px = avg * (1 + tp_dist)
                        sl_px = worst * (1 - sl_from_entry)
                    else:
                        tp_px = avg * (1 - tp_dist)
                        sl_px = worst * (1 + sl_from_entry)
                else:
                    # v1/v1.1/v2: SL 0.6% from worst
                    if side == "LONG":
                        tp_px = avg * (1 + tp_dist)
                        sl_px = worst * (1 - SL_FROM_WORST)
                    else:
                        tp_px = avg * (1 - tp_dist)
                        sl_px = worst * (1 + SL_FROM_WORST)
            else:
                # legs == 2 (only happens for bots with use_dca)
                tp_dist = TP_DCA
                if side == "LONG":
                    tp_px = avg * (1 + tp_dist)
                    sl_px = avg  # BE-after-DCA
                else:
                    tp_px = avg * (1 - tp_dist)
                    sl_px = avg

            exited = False
            if not l2_filled_this_bar:
                if side == "LONG":
                    if h >= tp_px:
                        close_position(position, ts, tp_px, "TP"); position = None; exited = True
                    elif l <= sl_px:
                        reason = "SL" if legs == 1 else "BE-DCA"
                        close_position(position, ts, sl_px, reason); position = None; exited = True
                else:
                    if l <= tp_px:
                        close_position(position, ts, tp_px, "TP"); position = None; exited = True
                    elif h >= sl_px:
                        reason = "SL" if legs == 1 else "BE-DCA"
                        close_position(position, ts, sl_px, reason); position = None; exited = True

            # Trend-flip exit
            if not exited and position is not None and not np.isnan(trend) and trend != 0:
                entry_trend = position["entry_trend"]
                if side == "LONG" and entry_trend == 1 and trend == -1:
                    close_position(position, ts, c, "TREND_FLIP"); position = None; exited = True
                elif side == "SHORT" and entry_trend == -1 and trend == 1:
                    close_position(position, ts, c, "TREND_FLIP"); position = None; exited = True

            # Smart 6h Time-SL
            if not exited and position is not None and cfg["use_time_sl"]:
                bars_open = i - position["open_bar_idx"]
                if bars_open >= TIME_SL_BARS:
                    _g, _f, net = compute_pnl(side, position["avg_entry"], c, position["total_qty"])
                    if not cfg["smart_time_sl"] or net < 0:
                        close_position(position, ts, c, "TIME_SL"); position = None; exited = True

        # Look for new entry signal
        if position is None and pending_entry is None:
            if cooldown_until is not None and ts < cooldown_until:
                continue
            d = ts.date()
            if daily_pnl.get(d, 0.0) <= DAILY_STOP:
                continue
            if np.isnan(rsi) or np.isnan(atrp) or np.isnan(trend) or np.isnan(gap):
                continue
            if atrp > ATR_MAX_PCT:
                continue
            if gap < GAP_MIN:
                continue
            # Hour block filter (v2/v5)
            if cfg["hour_block"] and ts.hour in cfg["hour_block"]:
                continue
            # 1h move filter (v2/v5)
            if cfg["h1_move_max"] is not None:
                h1_move = h1_move_arr[i]
                if not np.isnan(h1_move) and h1_move > cfg["h1_move_max"]:
                    continue

            weekend = ts.dayofweek >= 5
            if rsi <= RSI_LONG and trend == 1:
                pending_entry = ("LONG", weekend, 1)
            elif rsi >= RSI_SHORT and trend == -1:
                pending_entry = ("SHORT", weekend, -1)

    # Stats
    total = len(trades)
    wins = sum(1 for t in trades if t["net"] > 0)
    losses = sum(1 for t in trades if t["net"] < 0)
    flat = total - wins - losses
    wr = wins / total * 100 if total else 0
    profit = balance - INITIAL_BAL
    ret = profit / INITIAL_BAL * 100
    sum_wins = sum(t["net"] for t in trades if t["net"] > 0)
    sum_losses = sum(t["net"] for t in trades if t["net"] < 0)
    pf = abs(sum_wins / sum_losses) if sum_losses < 0 else float("inf")
    avg_hold = np.mean([t["hold_hours"] for t in trades]) if trades else 0
    biggest_win = max((t["net"] for t in trades), default=0)
    biggest_loss = min((t["net"] for t in trades), default=0)

    return {
        "label": label,
        "trades": total,
        "wins": wins,
        "losses": losses,
        "wr": wr,
        "profit": profit,
        "ret": ret,
        "max_dd": max_dd * 100,
        "pf": pf,
        "avg_hold": avg_hold,
        "biggest_win": biggest_win,
        "biggest_loss": biggest_loss,
        "exits": exit_reasons.copy(),
        "balance": balance,
    }


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
        df_calc,
        df15[["closed_at", "ema20", "ema50", "trend", "gap_pct"]].sort_values("closed_at"),
        left_on="timestamp", right_on="closed_at", direction="backward"
    )
    return merged[merged["timestamp"] >= cutoff].reset_index(drop=True)


def fmt_money(x):
    sign = "+" if x >= 0 else "-"
    return f"{sign}${abs(x):,.0f}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=60)
    args = parser.parse_args()

    print(f"Loading data...")
    df = pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    last_ts = df["timestamp"].iloc[-1]
    print(f"  Last timestamp: {last_ts}")
    print(f"  Window: last {args.days} days\n")

    bt = prep_data(df, args.days)
    print(f"  {len(bt)} 5m bars prepared\n")

    print(f"{'─' * 95}")
    print(f"  {'BOT':<6} | {'TRADES':>7} | {'WIN%':>5} | {'NET PROFIT':>12} | {'MAX DD':>7} | {'PF':>6} | {'AVG HOLD':>9} | {'BIGGEST LOSS':>12}")
    print(f"{'─' * 95}")

    results = {}
    for name, cfg in BOT_CONFIGS.items():
        r = run_backtest(bt, cfg, name)
        results[name] = r
        print(f"  {name:<6} | {r['trades']:>7} | {r['wr']:>4.1f}% | {fmt_money(r['profit']):>12} | {r['max_dd']:>6.2f}% | {r['pf']:>6.2f} | {r['avg_hold']:>7.2f}h | {fmt_money(r['biggest_loss']):>12}")

    print(f"{'─' * 95}")
    print()

    # Detailed exit breakdown per bot
    print(f"{'─' * 70}")
    print(f"  EXIT REASON BREAKDOWN")
    print(f"{'─' * 70}")
    for name, r in results.items():
        ex = r["exits"]
        print(f"  {name:<6} | TP={ex['TP']:>3} | BE-DCA={ex['BE-DCA']:>3} | SL={ex['SL']:>3} | TREND={ex['TREND_FLIP']:>3} | TIME_SL={ex['TIME_SL']:>3}")
    print(f"{'─' * 70}")


if __name__ == "__main__":
    main()
