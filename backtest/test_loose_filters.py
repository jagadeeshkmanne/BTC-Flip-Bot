"""
Test if LOOSER filters would catch more recent trades.
Compares current v1.1 config vs variants with lower GAP threshold.
Also tests "wait N bars after L2 before BE-DCA arms" idea.
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
ATR_LEN, ATR_MAX_PCT = 14, 0.006
DCA_TRIGGER, TP_L1, TP_DCA = 0.005, 0.005, 0.0025
SL_FROM_WORST = 0.006
FEE_RATE, SLIPPAGE = 0.0004, 0.0002
COOLDOWN_MIN, DAILY_STOP = 15, -200.0
TIME_SL_BARS, WEEKEND_MULT = 72, 2.0


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


def prep_data(df, days):
    last_ts = df["timestamp"].iloc[-1]
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


def run(bt, gap_min=0.0025, be_dca_wait_bars=0, be_dca_buffer_pct=0.0):
    """
    gap_min: minimum 15m EMA gap (relax to catch more trades)
    be_dca_wait_bars: wait N bars after L2 fill before arming BE-DCA SL
    be_dca_buffer_pct: SL at avg ± this% (e.g., 0.001 = 0.1% buffer, gives breathing room)
    """
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

    def open_pos(ts, side, price, weekend, bal):
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
            position = open_pos(ts, side, o, weekend, balance)
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
                # legs == 2: BE-after-DCA, possibly with wait + buffer
                bars_since_l2 = i - position["l2_bar_idx"]
                be_armed = bars_since_l2 >= be_dca_wait_bars
                if side == "LONG":
                    tp_px = avg * (1 + TP_DCA)
                    sl_px = avg * (1 - be_dca_buffer_pct) if be_armed else -np.inf
                else:
                    tp_px = avg * (1 - TP_DCA)
                    sl_px = avg * (1 + be_dca_buffer_pct) if be_armed else np.inf

            exited = False
            if not l2_filled_this_bar:
                if side == "LONG":
                    tp_hit = h >= tp_px; sl_hit = l <= sl_px
                else:
                    tp_hit = l <= tp_px; sl_hit = h >= sl_px
                if tp_hit and sl_hit:
                    # Pessimistic: SL wins on conflict
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

            if not exited and position is not None:
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
            if atrp > ATR_MAX_PCT: continue
            if gap < gap_min: continue
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
    return {"trades": total, "wins": wins, "losses": losses, "wr": wr,
            "profit": profit, "max_dd": max_dd * 100, "pf": pf, "exits": exits}


def main():
    print("Loading data...")
    df = pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    print(f"Last ts: {df['timestamp'].iloc[-1]}\n")

    # Run sweep
    print("═" * 105)
    print("  PROBLEM #1: Strict GAP filter → few trades. Test relaxed GAP.")
    print("═" * 105)

    for days in [30, 365, 1825]:
        bt = prep_data(df, days)
        print(f"\n── {days}-day window ──")
        print(f"  {'GAP':<8} {'Trades':>7} {'Win%':>6} {'Profit':>10} {'DD':>6} {'PF':>6}")
        for gap in [0.0025, 0.0020, 0.0015, 0.0010, 0.0]:
            r = run(bt, gap_min=gap)
            label = f"≥{gap*100:.2f}%" if gap > 0 else "OFF"
            sign = "+" if r["profit"] >= 0 else "-"
            print(f"  {label:<8} {r['trades']:>7} {r['wr']:>5.1f}% {sign}${abs(r['profit']):>7,.0f} {r['max_dd']:>5.2f}% {r['pf']:>5.2f}")

    # Run BE-DCA wait sweep
    print("\n" + "═" * 105)
    print("  PROBLEM #2: BE-DCA fires too early (the trade we saw recently). Test wait-N-bars + buffer.")
    print("═" * 105)

    bt = prep_data(df, 365)  # 1 year window
    print(f"\n── 365-day window, GAP 0.25% (current) ──")
    print(f"  {'BE-CONFIG':<30} {'Trades':>7} {'Win%':>6} {'Profit':>10} {'DD':>6} {'PF':>6}")

    # Current: BE at avg, fires immediately
    r = run(bt, be_dca_wait_bars=0, be_dca_buffer_pct=0.0)
    print(f"  current (avg, no wait/buffer)  {r['trades']:>7} {r['wr']:>5.1f}% +${r['profit']:>7,.0f} {r['max_dd']:>5.2f}% {r['pf']:>5.2f}")

    # Variants
    for label, wait, buf in [
        ("wait 6 bars (30min)", 6, 0.0),
        ("wait 12 bars (60min)", 12, 0.0),
        ("buffer 0.1% below avg", 0, 0.001),
        ("buffer 0.2% below avg", 0, 0.002),
        ("wait 6 + buffer 0.1%", 6, 0.001),
        ("wait 12 + buffer 0.2%", 12, 0.002),
    ]:
        r = run(bt, be_dca_wait_bars=wait, be_dca_buffer_pct=buf)
        sign = "+" if r["profit"] >= 0 else "-"
        print(f"  {label:<30} {r['trades']:>7} {r['wr']:>5.1f}% {sign}${abs(r['profit']):>7,.0f} {r['max_dd']:>5.2f}% {r['pf']:>5.2f}")


if __name__ == "__main__":
    main()
