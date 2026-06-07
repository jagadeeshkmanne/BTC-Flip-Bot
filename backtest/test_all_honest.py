"""
HONEST test of ALL 4 bots (v1, v1.1, v2, v5):
  - Pessimistic intra-bar conflict (SL wins)
  - Linear sizing (fixed $5K, no compounding) for true per-trade truth
  - Year-by-year breakdown to expose any single-window dominance
  - Detailed exit breakdown to address "most exits are BE-DCA / SL"
"""
import pandas as pd
import numpy as np
from datetime import timedelta

CSV_PATH = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv"

INITIAL_BAL   = 5000.0
LEVERAGE      = 3
PER_LEG_NOTIONAL = 7125.0
RSI_LEN, RSI_LONG, RSI_SHORT = 9, 30, 70
EMA_FAST, EMA_SLOW = 20, 50
GAP_MIN, ATR_LEN, ATR_MAX_PCT = 0.0025, 14, 0.006
DCA_TRIGGER, TP_L1, TP_DCA = 0.005, 0.005, 0.0025
SL_FROM_WORST = 0.006
FEE_RATE, SLIPPAGE = 0.0004, 0.0002
COOLDOWN_MIN, DAILY_STOP = 15, -200.0
TIME_SL_BARS, WEEKEND_MULT = 72, 2.0


BOT_CONFIGS = {
    "v1": {
        "use_dca": True, "use_time_sl": False, "smart_time_sl": False,
        "hour_block": set(), "h1_move_max": None, "single_leg_sl_from_entry": False,
    },
    "v1.1": {
        "use_dca": True, "use_time_sl": True, "smart_time_sl": True,
        "hour_block": set(), "h1_move_max": None, "single_leg_sl_from_entry": False,
    },
    "v2": {
        "use_dca": True, "use_time_sl": False, "smart_time_sl": False,
        "hour_block": {12, 13}, "h1_move_max": 0.02, "single_leg_sl_from_entry": False,
    },
    "v5": {
        "use_dca": False, "use_time_sl": False, "smart_time_sl": False,
        "hour_block": {12, 13}, "h1_move_max": 0.02, "single_leg_sl_from_entry": True,
    },
}


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


def prep_data(df, days=None, year=None):
    last_ts = df["timestamp"].iloc[-1]
    if year is not None:
        warmup_start = pd.Timestamp(f"{year-1}-12-25")
        end = pd.Timestamp(f"{year+1}-01-01")
        df_calc = df[(df["timestamp"] >= warmup_start) & (df["timestamp"] < end)].copy().reset_index(drop=True)
        cutoff = pd.Timestamp(f"{year}-01-01")
    else:
        cutoff = last_ts - timedelta(days=days)
        warmup_cut = last_ts - timedelta(days=days + 5)
        df_calc = df[df["timestamp"] >= warmup_cut].copy().reset_index(drop=True)

    df_calc["rsi5"] = wilder_rsi(df_calc["close"], RSI_LEN)
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


def run(bt, cfg, mode="pessimistic", compound=False):
    """Run with pessimistic intra-bar conflict + linear sizing by default."""
    balance = INITIAL_BAL
    peak = INITIAL_BAL
    max_dd = 0.0
    position = None
    cooldown_until = None
    daily_pnl = {}
    trades = []
    exits = {"TP": 0, "BE-DCA": 0, "SL": 0, "TREND_FLIP": 0, "TIME_SL": 0}
    sl_from_entry = 0.005

    def compute_pnl(side, avg_entry, exit_px, qty):
        eff_exit = exit_px * (1 - SLIPPAGE) if side == "LONG" else exit_px * (1 + SLIPPAGE)
        gross = (eff_exit - avg_entry) * qty if side == "LONG" else (avg_entry - eff_exit) * qty
        fees = (avg_entry + eff_exit) * qty * FEE_RATE
        return gross - fees

    def open_position(ts, side, price, weekend, bal):
        mult = WEEKEND_MULT if weekend else 1.0
        bal_scale = (bal / INITIAL_BAL) if compound else 1.0
        eff_entry = price * (1 + SLIPPAGE) if side == "LONG" else price * (1 - SLIPPAGE)
        leg_notional = PER_LEG_NOTIONAL * mult * bal_scale
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
        leg_notional = PER_LEG_NOTIONAL * mult * bal_scale
        qty = leg_notional / eff
        pos["entries"].append((eff, qty))
        pos["total_qty"] += qty
        pos["avg_entry"] = (pos["entries"][0][0] + pos["entries"][1][0]) / 2

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
        trades.append({"net": net, "reason": reason})
        exits[reason] += 1

    ts_arr, open_arr = bt["timestamp"].values, bt["open"].values
    high_arr, low_arr, close_arr = bt["high"].values, bt["low"].values, bt["close"].values
    rsi_arr, atrp_arr = bt["rsi5"].values, bt["atr_pct"].values
    trend_arr, gap_arr = bt["trend"].values, bt["gap_pct"].values

    if cfg["h1_move_max"] is not None:
        h1_move_arr = np.abs(bt["close"].pct_change(12).values)
    else:
        h1_move_arr = None

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

            if cfg["use_dca"] and legs == 1:
                l1 = position["entries"][0][0]
                trigger = l1 * (1 - DCA_TRIGGER) if side == "LONG" else l1 * (1 + DCA_TRIGGER)
                hit = (side == "LONG" and l <= trigger) or (side == "SHORT" and h >= trigger)
                if hit:
                    add_dca(position, trigger); legs = 2; l2_filled_this_bar = True

            avg = position["avg_entry"]
            if legs == 1:
                worst = position["entries"][0][0]
                if cfg["single_leg_sl_from_entry"]:
                    sl_dist = sl_from_entry
                else:
                    sl_dist = SL_FROM_WORST
                if side == "LONG":
                    tp_px = avg * (1 + TP_L1); sl_px = worst * (1 - sl_dist)
                else:
                    tp_px = avg * (1 - TP_L1); sl_px = worst * (1 + sl_dist)
            else:
                if side == "LONG":
                    tp_px = avg * (1 + TP_DCA); sl_px = avg
                else:
                    tp_px = avg * (1 - TP_DCA); sl_px = avg

            exited = False
            if not l2_filled_this_bar:
                if side == "LONG":
                    tp_hit = h >= tp_px; sl_hit = l <= sl_px
                else:
                    tp_hit = l <= tp_px; sl_hit = h >= sl_px

                if tp_hit and sl_hit:
                    # PESSIMISTIC: SL wins on conflict
                    if mode == "pessimistic":
                        reason = "SL" if legs == 1 else "BE-DCA"
                        close_pos(position, ts, sl_px, reason); position = None; exited = True
                    else:
                        close_pos(position, ts, tp_px, "TP"); position = None; exited = True
                elif tp_hit:
                    close_pos(position, ts, tp_px, "TP"); position = None; exited = True
                elif sl_hit:
                    reason = "SL" if legs == 1 else "BE-DCA"
                    close_pos(position, ts, sl_px, reason); position = None; exited = True

            if not exited and position is not None and not np.isnan(trend) and trend != 0:
                et = position["entry_trend"]
                if (side == "LONG" and et == 1 and trend == -1) or (side == "SHORT" and et == -1 and trend == 1):
                    close_pos(position, ts, c, "TREND_FLIP"); position = None; exited = True

            if not exited and position is not None and cfg["use_time_sl"]:
                bars_open = i - position["open_bar_idx"]
                if bars_open >= TIME_SL_BARS:
                    net_now = compute_pnl(side, position["avg_entry"], c, position["total_qty"])
                    if not cfg["smart_time_sl"] or net_now < 0:
                        close_pos(position, ts, c, "TIME_SL"); position = None; exited = True

        if position is None and pending_entry is None:
            if cooldown_until is not None and ts < cooldown_until: continue
            d = ts.date()
            if daily_pnl.get(d, 0.0) <= DAILY_STOP: continue
            if np.isnan(rsi) or np.isnan(atrp) or np.isnan(trend) or np.isnan(gap): continue
            if atrp > ATR_MAX_PCT or gap < GAP_MIN: continue
            if cfg["hour_block"] and ts.hour in cfg["hour_block"]: continue
            if cfg["h1_move_max"] is not None:
                h1m = h1_move_arr[i]
                if not np.isnan(h1m) and h1m > cfg["h1_move_max"]: continue
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
        "profit": profit, "max_dd": max_dd * 100, "pf": pf, "exits": exits,
        "sum_wins": sum_wins, "sum_losses": sum_losses,
    }


def main():
    print("Loading data...")
    df = pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    print(f"Last ts: {df['timestamp'].iloc[-1]}\n")

    # 5-year honest test for all 4 bots
    bt = prep_data(df, days=1825)
    print(f"5-year data: {len(bt)} 5m bars\n")
    print("═" * 110)
    print(f"  HONEST 5-YEAR TEST — pessimistic intra-bar conflict + LINEAR sizing (fixed $5K)")
    print("═" * 110)
    print(f"\n{'BOT':<7} {'Trades':>7} {'Win%':>6} {'5y Profit':>12} {'/yr':>10} {'/mo':>8} {'MaxDD':>7} {'PF':>6}")
    print("─" * 110)

    results = {}
    for name, cfg in BOT_CONFIGS.items():
        r = run(bt, cfg, mode="pessimistic", compound=False)
        results[name] = r
        per_year = r["profit"] / 5
        per_month = r["profit"] / 60
        sign = "+" if r["profit"] >= 0 else "-"
        print(f"{name:<7} {r['trades']:>7} {r['wr']:>5.1f}% {sign}${abs(r['profit']):>9,.0f}  {sign}${abs(per_year):>7,.0f} {sign}${abs(per_month):>6,.0f} {r['max_dd']:>6.2f}% {r['pf']:>5.2f}")
    print("─" * 110)

    print(f"\n{'═' * 110}")
    print(f"  EXIT REASON BREAKDOWN (5-year, honest)")
    print("═" * 110)
    print(f"\n{'BOT':<7} {'TP':>6} {'TP%':>6} {'BE-DCA':>7} {'BE%':>6} {'SL':>6} {'SL%':>6} {'TREND':>7} {'TIME_SL':>8}")
    print("─" * 110)
    for name, r in results.items():
        ex = r["exits"]
        total = r["trades"]
        tp_pct = ex["TP"] / total * 100 if total else 0
        be_pct = ex["BE-DCA"] / total * 100 if total else 0
        sl_pct = ex["SL"] / total * 100 if total else 0
        print(f"{name:<7} {ex['TP']:>6} {tp_pct:>5.1f}% {ex['BE-DCA']:>7} {be_pct:>5.1f}% {ex['SL']:>6} {sl_pct:>5.1f}% {ex['TREND_FLIP']:>7} {ex['TIME_SL']:>8}")
    print("─" * 110)

    # Year by year
    print(f"\n{'═' * 110}")
    print(f"  YEAR-BY-YEAR PROFIT — exposes overfit / single-year dominance")
    print("═" * 110)
    print(f"\n{'YEAR':<6} {'v1':>10} {'v1.1':>10} {'v2':>10} {'v5':>10}")
    print("─" * 110)
    yearly = {y: {} for y in [2021, 2022, 2023, 2024, 2025, 2026]}
    for y in [2021, 2022, 2023, 2024, 2025, 2026]:
        try:
            bt_y = prep_data(df, year=y)
            if len(bt_y) < 100: continue
            line = f"{y:<6}"
            for name, cfg in BOT_CONFIGS.items():
                r = run(bt_y, cfg, mode="pessimistic", compound=False)
                yearly[y][name] = r["profit"]
                sign = "+" if r["profit"] >= 0 else "-"
                line += f" {sign}${abs(r['profit']):>7,.0f}"
            print(line)
        except Exception as e:
            print(f"{y}: error — {e}")
    print("─" * 110)

    # Total per bot (sum of yearly)
    print(f"\n{'TOTAL':<6}", end="")
    for name in BOT_CONFIGS.keys():
        total = sum(yearly[y].get(name, 0) for y in yearly)
        sign = "+" if total >= 0 else "-"
        print(f" {sign}${abs(total):>7,.0f}", end="")
    print()

    print(f"\n{'═' * 110}")
    print(f"  FINAL VERDICT")
    print("═" * 110)
    for name, r in results.items():
        per_month = r["profit"] / 60
        ex = r["exits"]
        be_or_sl_pct = (ex["BE-DCA"] + ex["SL"]) / r["trades"] * 100 if r["trades"] else 0
        verdict = "✓ KEEP" if r["profit"] > 5000 else ("⚠ MARGINAL" if r["profit"] > 0 else "✗ KILL")
        sign = "+" if r["profit"] >= 0 else "-"
        print(f"  {name:<7} {verdict:<12} {sign}${abs(per_month):>5,.0f}/mo  | WR {r['wr']:.0f}% | BE+SL exits {be_or_sl_pct:.0f}% | PF {r['pf']:.2f}")


if __name__ == "__main__":
    main()
