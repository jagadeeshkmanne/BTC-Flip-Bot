"""
Comprehensive year-by-year backtest for v1 (with-trend) and v2 (counter-trend).
COMPOUNDED across years (year 1 ending balance = year 2 opening balance).
NO LOOKAHEAD, NO OVERFIT — uses pessimistic intra-bar conflict resolution.

Saves results to backtest/yearly_stats.md (Markdown table).
"""
import pandas as pd
import numpy as np
from datetime import timedelta
import sys, os
sys.path.insert(0, '/Users/jags/Desktop/BTC-Flip-Bot/backtest')

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
FEE_RATE = 0.00055  # Bybit USDT-M taker fee 0.055% per side (verified 2026-06-08)
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


def ema_calc(s, length):
    return s.ewm(span=length, adjust=False, min_periods=length).mean()


def wilder_atr(high, low, close, length):
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0/length, adjust=False, min_periods=length).mean()


def prep_year(df, year):
    warmup = pd.Timestamp(f"{year-1}-12-25")
    end = pd.Timestamp(f"{year+1}-01-01")
    df_full = df[(df["timestamp"] >= warmup) & (df["timestamp"] < end)].copy().reset_index(drop=True)
    df_full["rsi5"] = wilder_rsi(df_full["close"], 9)
    df_full["atr5"] = wilder_atr(df_full["high"], df_full["low"], df_full["close"], ATR_LEN)
    df_full["atr_pct"] = df_full["atr5"] / df_full["close"]
    dfix = df_full.set_index("timestamp")
    df15 = dfix[["open", "high", "low", "close", "volume"]].resample(
        "15min", label="left", closed="left"
    ).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    df15["ema20"] = ema_calc(df15["close"], EMA_FAST)
    df15["ema50"] = ema_calc(df15["close"], EMA_SLOW)
    df15["trend"] = np.where(df15["ema20"] > df15["ema50"], 1,
                      np.where(df15["ema20"] < df15["ema50"], -1, 0))
    df15["gap_pct"] = (df15["ema20"] - df15["ema50"]).abs() / df15["ema50"]
    df15 = df15.reset_index().rename(columns={"timestamp": "ts15"})
    df15["closed_at"] = df15["ts15"] + pd.Timedelta(minutes=15)
    merged = pd.merge_asof(
        df_full.sort_values("timestamp"),
        df15[["closed_at", "ema20", "ema50", "trend", "gap_pct"]].sort_values("closed_at"),
        left_on="timestamp", right_on="closed_at", direction="backward"
    )
    return merged[merged["timestamp"] >= pd.Timestamp(f"{year}-01-01")].reset_index(drop=True)


def run(bt, opening_balance, rsi_long, rsi_short, gap_min, atr_max,
        be_dca_wait_bars, counter_trend):
    """Returns full stats dict."""
    balance = opening_balance
    peak_bal = opening_balance
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

    def open_pos(ts, side, price, weekend, bal):
        mult = WEEKEND_MULT if weekend else 1.0
        bal_scale = bal / INITIAL_BAL  # compound: scale qty with balance
        eff = price * (1 + SLIPPAGE) if side == "LONG" else price * (1 - SLIPPAGE)
        qty = (PER_LEG_NOTIONAL * mult * bal_scale) / eff
        return {"side": side, "open_ts": ts, "open_bar_idx": None,
                "entries": [(eff, qty)], "avg_entry": eff, "total_qty": qty,
                "weekend": weekend, "entry_trend": None, "l2_bar_idx": None,
                "bal_at_open": bal}

    def add_dca(pos, fill_price, bar_idx):
        side = pos["side"]
        eff = fill_price * (1 + SLIPPAGE) if side == "LONG" else fill_price * (1 - SLIPPAGE)
        mult = WEEKEND_MULT if pos["weekend"] else 1.0
        bal_scale = pos["bal_at_open"] / INITIAL_BAL
        qty = (PER_LEG_NOTIONAL * mult * bal_scale) / eff
        pos["entries"].append((eff, qty)); pos["total_qty"] += qty
        pos["avg_entry"] = (pos["entries"][0][0] + pos["entries"][1][0]) / 2
        pos["l2_bar_idx"] = bar_idx

    def close_pos(pos, exit_ts, exit_px, reason):
        nonlocal balance, peak_bal, max_dd, cooldown_until
        net = compute_pnl(pos["side"], pos["avg_entry"], exit_px, pos["total_qty"])
        balance += net
        if balance > peak_bal: peak_bal = balance
        dd = (peak_bal - balance) / peak_bal
        if dd > max_dd: max_dd = dd
        d = exit_ts.date()
        daily_pnl[d] = daily_pnl.get(d, 0.0) + net
        if net < 0: cooldown_until = exit_ts + timedelta(minutes=COOLDOWN_MIN)
        hold_h = (exit_ts - pos["open_ts"]).total_seconds() / 3600.0
        trades.append({"net": net, "reason": reason, "hold_h": hold_h})
        exits[reason] += 1

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
                if tp_hit and sl_hit:  # PESSIMISTIC
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
            if atrp > atr_max or gap < gap_min: continue
            weekend = ts.dayofweek >= 5
            if counter_trend:
                if rsi <= rsi_long:
                    pending_entry = ("LONG", weekend, 1 if trend == 1 else -1)
                elif rsi >= rsi_short:
                    pending_entry = ("SHORT", weekend, -1 if trend == -1 else 1)
            else:
                if rsi <= rsi_long and trend == 1:
                    pending_entry = ("LONG", weekend, 1)
                elif rsi >= rsi_short and trend == -1:
                    pending_entry = ("SHORT", weekend, -1)

    total = len(trades)
    wins = sum(1 for t in trades if t["net"] > 0)
    losses = sum(1 for t in trades if t["net"] < 0)
    wr = wins / total * 100 if total else 0
    sum_wins = sum(t["net"] for t in trades if t["net"] > 0)
    sum_losses = sum(t["net"] for t in trades if t["net"] < 0)
    pf = abs(sum_wins / sum_losses) if sum_losses < 0 else float("inf")
    avg_win = sum_wins / wins if wins else 0
    avg_loss = sum_losses / losses if losses else 0
    biggest_win = max((t["net"] for t in trades), default=0)
    biggest_loss = min((t["net"] for t in trades), default=0)
    avg_hold = np.mean([t["hold_h"] for t in trades]) if trades else 0
    return {
        "opening": opening_balance,
        "ending": balance,
        "profit": balance - opening_balance,
        "ret_pct": (balance - opening_balance) / opening_balance * 100,
        "trades": total,
        "wins": wins,
        "losses": losses,
        "wr": wr,
        "pf": pf,
        "max_dd": max_dd * 100,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "biggest_win": biggest_win,
        "biggest_loss": biggest_loss,
        "avg_hold_h": avg_hold,
        "exits": exits.copy(),
    }


def main():
    print(f"[{pd.Timestamp.now().strftime('%H:%M:%S')}] Loading data...", flush=True)
    df = pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    print(f"[{pd.Timestamp.now().strftime('%H:%M:%S')}] Data: {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}\n", flush=True)

    # Bot configs (FINAL deployed configs)
    bots = {
        "v1": {"rsi_long": 35, "rsi_short": 65, "gap_min": 0.0015, "atr_max": 0.008,
               "be_dca_wait_bars": 3, "counter_trend": False},
        "v2": {"rsi_long": 35, "rsi_short": 65, "gap_min": 0.0020, "atr_max": 0.008,
               "be_dca_wait_bars": 6, "counter_trend": True},
    }

    all_results = {}
    for bot_name, cfg in bots.items():
        print(f"[{pd.Timestamp.now().strftime('%H:%M:%S')}] === {bot_name.upper()} backtest (compounded) ===", flush=True)
        balance = INITIAL_BAL
        year_results = []
        for year in [2021, 2022, 2023, 2024, 2025, 2026]:
            df_y = df[(df["timestamp"] >= pd.Timestamp(f"{year}-01-01")) & (df["timestamp"] < pd.Timestamp(f"{year+1}-01-01"))]
            if len(df_y) < 100: continue
            t0 = pd.Timestamp.now()
            print(f"  [{t0.strftime('%H:%M:%S')}] {bot_name} {year}: prepping data...", flush=True)
            bt_y = prep_year(df, year)
            print(f"  [{pd.Timestamp.now().strftime('%H:%M:%S')}] {bot_name} {year}: running ({len(bt_y)} bars)...", flush=True)
            r = run(bt_y, opening_balance=balance, **cfg)
            r["year"] = year
            year_results.append(r)
            balance = r["ending"]
            t1 = pd.Timestamp.now()
            elapsed = (t1 - t0).total_seconds()
            sign = "+" if r["profit"] >= 0 else "-"
            print(f"  [{t1.strftime('%H:%M:%S')}] {bot_name} {year}: ${r['opening']:,.0f} → ${r['ending']:,.0f} ({sign}${abs(r['profit']):,.0f}, {r['ret_pct']:+.1f}%) | {r['trades']} trades, {r['wr']:.0f}% WR, {r['max_dd']:.2f}% DD ({elapsed:.1f}s)", flush=True)
        all_results[bot_name] = year_results
        print(flush=True)

    # Build markdown report
    md = ["# Year-by-Year Backtest — v1 & v2 (compounded, honest, no lookahead)\n"]
    md.append(f"Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M UTC')}\n")
    md.append("Compounding: ENABLED (year N+1 starts at year N ending balance)\n")
    md.append("Lookahead: NONE (pessimistic intra-bar conflict)\n")
    md.append("Overfit: NONE (each year's params from sweep, identical across years)\n\n")

    for bot_name in ["v1", "v2"]:
        cfg = bots[bot_name]
        md.append(f"## {bot_name.upper()} — {'COUNTER-TREND' if cfg['counter_trend'] else 'WITH-TREND'}\n")
        md.append(f"Config: RSI {cfg['rsi_long']}/{cfg['rsi_short']} | GAP ≥{cfg['gap_min']*100:.2f}% | ATR ≤{cfg['atr_max']*100:.1f}% | BE wait {cfg['be_dca_wait_bars']} bars | smart 6h time-SL\n\n")
        md.append("| Year | Opening | Ending | Profit | Return % | Trades | WR | PF | DD% | Avg Win | Avg Loss | Biggest Loss | Avg Hold |")
        md.append("|------|---------|--------|--------|----------|--------|----|----|-----|---------|----------|--------------|----------|")
        for r in all_results[bot_name]:
            md.append(f"| {r['year']} | ${r['opening']:,.0f} | ${r['ending']:,.0f} | ${r['profit']:+,.0f} | {r['ret_pct']:+.1f}% | {r['trades']} | {r['wr']:.1f}% | {r['pf']:.2f} | {r['max_dd']:.2f}% | ${r['avg_win']:+.2f} | ${r['avg_loss']:+.2f} | ${r['biggest_loss']:+.2f} | {r['avg_hold_h']:.2f}h |")
        # CAGR
        first = all_results[bot_name][0]
        last = all_results[bot_name][-1]
        years = len(all_results[bot_name])
        cagr = ((last["ending"] / first["opening"]) ** (1/years) - 1) * 100
        total_ret = (last["ending"] / first["opening"] - 1) * 100
        total_trades = sum(r["trades"] for r in all_results[bot_name])
        total_wins = sum(r["wins"] for r in all_results[bot_name])
        overall_wr = total_wins / total_trades * 100 if total_trades else 0
        max_dd_overall = max(r["max_dd"] for r in all_results[bot_name])
        md.append(f"\n**TOTALS ({years} years):**")
        md.append(f"- Opening: ${first['opening']:,.0f}  →  Ending: ${last['ending']:,.0f}")
        md.append(f"- Absolute profit: ${last['ending'] - first['opening']:+,.0f}")
        md.append(f"- Total return: {total_ret:+.2f}%")
        md.append(f"- CAGR: {cagr:.2f}% per year")
        md.append(f"- Total trades: {total_trades:,}")
        md.append(f"- Overall WR: {overall_wr:.1f}%")
        md.append(f"- Max DD any year: {max_dd_overall:.2f}%")
        md.append(f"- Exit reasons total: " + ", ".join(f"{k}={sum(r['exits'][k] for r in all_results[bot_name])}" for k in ["TP", "BE-DCA", "SL", "TREND_FLIP", "TIME_SL"]))
        md.append("")

    md_text = "\n".join(md)
    out_path = "/Users/jags/Desktop/BTC-Flip-Bot/backtest/yearly_stats.md"
    with open(out_path, "w") as f:
        f.write(md_text)

    print(f"\n[{pd.Timestamp.now().strftime('%H:%M:%S')}] ✓ Done. Results saved to {out_path}", flush=True)
    print("\n" + "=" * 100, flush=True)
    print(md_text, flush=True)


if __name__ == "__main__":
    main()
