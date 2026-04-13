#!/usr/bin/env python3
"""
BACKTEST: Bollinger Band Entry Filter Comparison
Tests whether adding a BB filter improves entry timing.

Strategies tested:
  1. CURRENT — No BB filter (baseline)
  2. BB MID — Only short above BB mid, only long below BB mid
  3. BB 75% — Only short in upper 25% of BB, only long in lower 25%
  4. BB + LIMIT — BB mid filter + simulated limit order (0.15% better entry)
"""

import numpy as np
import pandas as pd
import requests
import json
import time
from datetime import datetime, timezone

# ─── Fetch Binance Klines ───
def fetch_klines(symbol="BTCUSDT", interval="15m", days=1825):
    url = "https://api.binance.com/api/v3/klines"
    all_candles = []
    end_time = int(time.time() * 1000)
    start_time = int((time.time() - days * 86400) * 1000)
    print(f"  Fetching {days} days ({days/365:.1f} years) of {interval} data for {symbol}...")
    current = start_time
    batch = 0
    retries = 0
    while current < end_time:
        params = {"symbol": symbol, "interval": interval, "startTime": current, "limit": 1000}
        try:
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", 10)))
                continue
            r.raise_for_status()
            data = r.json()
            retries = 0
        except Exception as e:
            retries += 1
            if retries > 5: break
            time.sleep(3)
            continue
        if not data: break
        all_candles.extend(data)
        current = data[-1][0] + 1
        batch += 1
        if batch % 50 == 0:
            print(f"    ... {len(all_candles)} candles ({(current-start_time)/86400000:.0f} days)")
        if len(data) < 1000: break
        time.sleep(0.15)
    print(f"    Got {len(all_candles):,} candles")
    df = pd.DataFrame(all_candles, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"])
    for col in ["open","high","low","close","volume"]:
        df[col] = df[col].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.drop_duplicates(subset=["open_time"]).reset_index(drop=True)
    return df

# ─── Indicators ───
def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def compute_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def compute_macd(series, fast=12, slow=26, signal=9):
    ema_fast = compute_ema(series, fast)
    ema_slow = compute_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, signal)
    return macd_line, signal_line

def compute_bb(series, period=20, std_mult=2):
    sma = series.rolling(period).mean()
    std = series.rolling(period).std()
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    return upper, sma, lower

def resample_to_htf(df_base, htf="4h"):
    df = df_base.set_index("timestamp").copy()
    ohlcv = df.resample(htf).agg({
        "open":"first","high":"max","low":"min","close":"last","volume":"sum","open_time":"first"
    }).dropna()
    return ohlcv.reset_index()

def compute_htf_trend(df_base, htf="4h"):
    df_htf = resample_to_htf(df_base, htf)
    rsi = compute_rsi(df_htf["close"], 14)
    df_htf["trend"] = np.where(rsi > 50, 1, -1)
    df_htf_mapped = df_htf[["timestamp","trend"]].set_index("timestamp")
    trend_mapped = df_htf_mapped.reindex(df_base["timestamp"]).ffill().reset_index(drop=True)
    return trend_mapped["trend"].fillna(0).values


# ─── Backtest Engine ───
def run_backtest(df, htf_trend, bb_upper, bb_mid, bb_lower,
                 sl_pct=0.05, leverage=2, capital=5000,
                 cooldown_bars=6, fee_pct=0.0004, slippage_pct=0.0003,
                 capital_deploy_pct=0.95,
                 bb_mode="none", limit_improve_pct=0.0,
                 label=""):
    """
    bb_mode:
      "none"    — No BB filter (current strategy)
      "mid"     — Short only above BB mid, Long only below BB mid
      "upper25" — Short only in upper 25% of BB, Long only in lower 25%
    limit_improve_pct:
      Simulated limit order improvement (e.g., 0.0015 = 0.15% better entry)
    """

    rsi = compute_rsi(df["close"], 14)
    macd_line, signal_line = compute_macd(df["close"], 12, 26, 9)
    vol = df["volume"].astype(float)
    vol_sma = vol.rolling(20).mean()

    trades = []
    balance = capital
    position = None
    cooldown_until = 0
    peak_balance = capital
    max_drawdown = 0
    filtered_by_mtf = 0
    filtered_by_bb = 0
    total_fees = 0
    monthly_pnl = {}

    for i in range(50, len(df)):
        price = float(df["close"].iloc[i])
        high = float(df["high"].iloc[i])
        low = float(df["low"].iloc[i])
        ts = df["timestamp"].iloc[i]

        curr_rsi = float(rsi.iloc[i]) if not pd.isna(rsi.iloc[i]) else 50
        curr_macd = float(macd_line.iloc[i]) if not pd.isna(macd_line.iloc[i]) else 0
        prev_macd = float(macd_line.iloc[i-1]) if not pd.isna(macd_line.iloc[i-1]) else 0
        curr_signal = float(signal_line.iloc[i]) if not pd.isna(signal_line.iloc[i]) else 0
        prev_signal = float(signal_line.iloc[i-1]) if not pd.isna(signal_line.iloc[i-1]) else 0
        curr_vol = float(vol.iloc[i]) if not pd.isna(vol.iloc[i]) else 0
        curr_vol_sma = float(vol_sma.iloc[i]) if not pd.isna(vol_sma.iloc[i]) else 1
        curr_htf = htf_trend[i] if i < len(htf_trend) else 0

        curr_bb_upper = float(bb_upper.iloc[i]) if not pd.isna(bb_upper.iloc[i]) else price
        curr_bb_mid = float(bb_mid.iloc[i]) if not pd.isna(bb_mid.iloc[i]) else price
        curr_bb_lower = float(bb_lower.iloc[i]) if not pd.isna(bb_lower.iloc[i]) else price

        macd_bull_cross = (prev_macd <= prev_signal and curr_macd > curr_signal)
        macd_bear_cross = (prev_macd >= prev_signal and curr_macd < curr_signal)
        vol_ok = curr_vol_sma > 0 and (curr_vol / curr_vol_sma) >= 0.8

        raw_signal = "HOLD"
        if macd_bull_cross and 30 <= curr_rsi <= 70 and vol_ok:
            raw_signal = "LONG"
        elif macd_bear_cross and 30 <= curr_rsi <= 70 and vol_ok:
            raw_signal = "SHORT"

        # MTF filter
        signal = raw_signal
        if signal != "HOLD":
            if signal == "LONG" and curr_htf == -1:
                filtered_by_mtf += 1
                signal = "HOLD"
            elif signal == "SHORT" and curr_htf == 1:
                filtered_by_mtf += 1
                signal = "HOLD"

        # BB filter (only for new entries, not flips)
        bb_ok = True
        if signal != "HOLD" and bb_mode != "none":
            bb_range = curr_bb_upper - curr_bb_lower if curr_bb_upper > curr_bb_lower else 1
            bb_pct = (price - curr_bb_lower) / bb_range  # 0=lower band, 1=upper band

            if bb_mode == "mid":
                if signal == "SHORT" and price < curr_bb_mid:
                    bb_ok = False
                elif signal == "LONG" and price > curr_bb_mid:
                    bb_ok = False
            elif bb_mode == "upper25":
                if signal == "SHORT" and bb_pct < 0.75:
                    bb_ok = False
                elif signal == "LONG" and bb_pct > 0.25:
                    bb_ok = False

        # ── Manage open position ──
        if position:
            side = position["side"]
            entry = position["entry"]

            # Funding fee every 32 bars (8 hours)
            bars_in_pos = i - position["bar_idx"]
            if bars_in_pos > 0 and bars_in_pos % 32 == 0:
                funding_cost = position["capital_used"] * leverage * 0.0001
                total_fees += funding_cost
                balance -= funding_cost
                position.setdefault("funding_paid", 0)
                position["funding_paid"] += funding_cost

            if side == "LONG":
                pnl_pct = (price - entry) / entry * leverage
            else:
                pnl_pct = (entry - price) / entry * leverage
            pnl_usd = pnl_pct * position["capital_used"]

            # Stop loss
            if pnl_pct <= -sl_pct:
                exit_cost = position["capital_used"] * (fee_pct + slippage_pct)
                total_fees += exit_cost
                net_pnl = pnl_usd - exit_cost
                balance += net_pnl
                month_key = ts.strftime("%Y-%m")
                monthly_pnl[month_key] = monthly_pnl.get(month_key, 0) + net_pnl
                trades.append({
                    "side": side, "entry": entry, "exit": price,
                    "pnl_pct": pnl_pct, "pnl_usd": net_pnl,
                    "fees": exit_cost + position["entry_fee"] + position.get("funding_paid", 0),
                    "exit_reason": "SL", "bars_held": bars_in_pos,
                })
                position = None
                cooldown_until = i + cooldown_bars
                if balance > peak_balance: peak_balance = balance
                dd = (peak_balance - balance) / peak_balance if peak_balance > 0 else 0
                if dd > max_drawdown: max_drawdown = dd
                continue

            # Flip exit
            flip_exit = False
            if side == "LONG" and macd_bear_cross: flip_exit = True
            elif side == "SHORT" and macd_bull_cross: flip_exit = True

            if flip_exit:
                opposite_blocked = False
                if side == "LONG" and curr_htf == 1: opposite_blocked = True
                elif side == "SHORT" and curr_htf == -1: opposite_blocked = True

                if opposite_blocked:
                    continue  # Option B: hold
                else:
                    exit_cost = position["capital_used"] * (fee_pct + slippage_pct)
                    total_fees += exit_cost
                    net_pnl = pnl_usd - exit_cost
                    balance += net_pnl
                    month_key = ts.strftime("%Y-%m")
                    monthly_pnl[month_key] = monthly_pnl.get(month_key, 0) + net_pnl
                    trades.append({
                        "side": side, "entry": entry, "exit": price,
                        "pnl_pct": pnl_pct, "pnl_usd": net_pnl,
                        "fees": exit_cost + position["entry_fee"] + position.get("funding_paid", 0),
                        "exit_reason": "FLIP", "bars_held": bars_in_pos,
                    })
                    position = None

                    # Open opposite (flips always ignore BB filter — we want to flip immediately)
                    new_side = "SHORT" if side == "LONG" else "LONG"
                    pos_capital = min(balance * capital_deploy_pct, 250000)
                    if pos_capital > 0:
                        # Limit order improvement for flip entries
                        actual_entry = price
                        if limit_improve_pct > 0:
                            if new_side == "SHORT":
                                limit_price = price * (1 + limit_improve_pct)
                                if high >= limit_price: actual_entry = limit_price
                            else:
                                limit_price = price * (1 - limit_improve_pct)
                                if low <= limit_price: actual_entry = limit_price

                        entry_fee = pos_capital * (fee_pct + slippage_pct)
                        total_fees += entry_fee
                        position = {
                            "side": new_side, "entry": actual_entry,
                            "qty": (pos_capital * leverage) / actual_entry,
                            "capital_used": pos_capital, "entry_fee": entry_fee,
                            "bar_idx": i,
                        }

                    if balance > peak_balance: peak_balance = balance
                    dd = (peak_balance - balance) / peak_balance if peak_balance > 0 else 0
                    if dd > max_drawdown: max_drawdown = dd
                    continue

        # ── Open new position ──
        if not position and signal != "HOLD" and i > cooldown_until and balance > 10:
            if not bb_ok:
                filtered_by_bb += 1
                continue

            pos_capital = min(balance * capital_deploy_pct, 250000)
            if pos_capital > 0:
                # Limit order improvement
                actual_entry = price
                if limit_improve_pct > 0:
                    if signal == "SHORT":
                        limit_price = price * (1 + limit_improve_pct)
                        if high >= limit_price: actual_entry = limit_price
                    else:
                        limit_price = price * (1 - limit_improve_pct)
                        if low <= limit_price: actual_entry = limit_price

                entry_fee = pos_capital * (fee_pct + slippage_pct)
                total_fees += entry_fee
                position = {
                    "side": signal, "entry": actual_entry,
                    "qty": (pos_capital * leverage) / actual_entry,
                    "capital_used": pos_capital, "entry_fee": entry_fee,
                    "bar_idx": i,
                }

    # Close remaining
    if position:
        price = float(df["close"].iloc[-1])
        side = position["side"]
        entry = position["entry"]
        if side == "LONG": pnl_pct = (price - entry) / entry * leverage
        else: pnl_pct = (entry - price) / entry * leverage
        pnl_usd = pnl_pct * position["capital_used"]
        exit_cost = position["capital_used"] * (fee_pct + slippage_pct)
        total_fees += exit_cost
        net_pnl = pnl_usd - exit_cost
        balance += net_pnl
        trades.append({
            "side": side, "entry": entry, "exit": price,
            "pnl_pct": pnl_pct, "pnl_usd": net_pnl,
            "fees": exit_cost + position["entry_fee"] + position.get("funding_paid", 0),
            "exit_reason": "OPEN", "bars_held": len(df) - position["bar_idx"],
        })

    wins = [t for t in trades if t["pnl_usd"] > 0]
    losses = [t for t in trades if t["pnl_usd"] <= 0]
    profitable_months = sum(1 for v in monthly_pnl.values() if v > 0)
    total_months = len(monthly_pnl)

    return {
        "label": label,
        "total_trades": len(trades),
        "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / max(len(trades), 1) * 100,
        "total_pnl_usd": balance - capital,
        "final_balance": balance,
        "max_drawdown_pct": max_drawdown * 100,
        "profit_factor": abs(sum(t["pnl_usd"] for t in wins)) / max(abs(sum(t["pnl_usd"] for t in losses)), 1),
        "avg_win_pct": np.mean([t["pnl_pct"]*100 for t in wins]) if wins else 0,
        "avg_loss_pct": np.mean([t["pnl_pct"]*100 for t in losses]) if losses else 0,
        "avg_bars_held": np.mean([t["bars_held"] for t in trades]) if trades else 0,
        "total_fees": total_fees,
        "filtered_by_mtf": filtered_by_mtf,
        "filtered_by_bb": filtered_by_bb,
        "sl_exits": len([t for t in trades if t["exit_reason"] == "SL"]),
        "flip_exits": len([t for t in trades if t["exit_reason"] == "FLIP"]),
        "profitable_months": profitable_months,
        "total_months": total_months,
    }


def main():
    print("=" * 80)
    print("  BOLLINGER BAND ENTRY FILTER — BACKTEST COMPARISON")
    print("  Strategy: 15m MACD + 4H RSI · 2x Leverage · Option B · $5K · 95% Deploy")
    print("  Fees: 0.04% taker + 0.03% slippage + 0.01%/8h funding")
    print("  Position cap: $250K")
    print("=" * 80)

    df = fetch_klines("BTCUSDT", "15m", 1825)
    if len(df) < 100:
        print("  ERROR: Not enough data.")
        return

    start = df['timestamp'].iloc[0].strftime('%Y-%m-%d')
    end = df['timestamp'].iloc[-1].strftime('%Y-%m-%d')
    print(f"\n  Period: {start} to {end} ({len(df):,} candles)")

    print("\n  Computing indicators...")
    htf_trend = compute_htf_trend(df, "4h")
    bb_upper, bb_mid, bb_lower = compute_bb(df["close"], 20, 2)

    results = []

    # ─── 1. CURRENT STRATEGY (no BB filter) ───
    print("\n  Running: Current strategy (no BB filter)...")
    r = run_backtest(df, htf_trend, bb_upper, bb_mid, bb_lower,
                     bb_mode="none",
                     label="CURRENT — No BB Filter (Baseline)")
    results.append(r)

    # ─── 2. BB MID FILTER ───
    print("  Running: BB Mid filter (short above mid, long below mid)...")
    r = run_backtest(df, htf_trend, bb_upper, bb_mid, bb_lower,
                     bb_mode="mid",
                     label="BB MID — Short above mid, Long below mid")
    results.append(r)

    # ─── 3. BB UPPER 25% FILTER ───
    print("  Running: BB 75% filter (short in upper 25%, long in lower 25%)...")
    r = run_backtest(df, htf_trend, bb_upper, bb_mid, bb_lower,
                     bb_mode="upper25",
                     label="BB 75% — Short upper quarter, Long lower quarter")
    results.append(r)

    # ─── 4. BB MID + LIMIT ORDER ───
    print("  Running: BB Mid + 0.15% limit order improvement...")
    r = run_backtest(df, htf_trend, bb_upper, bb_mid, bb_lower,
                     bb_mode="mid", limit_improve_pct=0.0015,
                     label="BB MID + LIMIT — Better entries via limit orders")
    results.append(r)

    # ═══════════════════════════════════════════════════════
    # RESULTS
    # ═══════════════════════════════════════════════════════
    print(f"\n\n{'='*120}")
    print(f"  BOLLINGER BAND FILTER COMPARISON")
    print(f"{'='*120}")
    print(f"  {'Strategy':<52} {'Trades':>6} {'WR%':>6} {'Final $':>12} {'Net P&L':>12} {'DD%':>6} {'PF':>5} {'BB Blocked':>10} {'Avg Win':>8} {'Avg Loss':>8}")
    print(f"  {'-'*114}")

    for r in results:
        print(f"  {r['label']:<52} {r['total_trades']:>6} {r['win_rate']:>5.1f}% {r['final_balance']:>11,.0f} {r['total_pnl_usd']:>+11,.0f} {r['max_drawdown_pct']:>5.1f}% {r['profit_factor']:>4.2f} {r['filtered_by_bb']:>10} {r['avg_win_pct']:>+7.2f}% {r['avg_loss_pct']:>+7.2f}%")

    print(f"  {'-'*114}")

    # Detailed stats
    for r in results:
        print(f"\n  {'─'*60}")
        print(f"  {r['label']}")
        print(f"  {'─'*60}")
        print(f"  Trades: {r['total_trades']}  |  Wins: {r['wins']}  |  Losses: {r['losses']}")
        print(f"  Win Rate: {r['win_rate']:.1f}%  |  Profit Factor: {r['profit_factor']:.2f}")
        print(f"  Net Profit: ${r['total_pnl_usd']:+,.0f}  |  Final: ${r['final_balance']:,.0f}")
        print(f"  Max Drawdown: {r['max_drawdown_pct']:.1f}%")
        print(f"  Avg Win: {r['avg_win_pct']:+.2f}%  |  Avg Loss: {r['avg_loss_pct']:.2f}%")
        print(f"  Avg Hold: {r['avg_bars_held']:.0f} bars ({r['avg_bars_held']*15/60:.1f}h)")
        print(f"  SL Exits: {r['sl_exits']}  |  Flip Exits: {r['flip_exits']}")
        print(f"  Signals blocked by MTF: {r['filtered_by_mtf']}  |  Blocked by BB: {r['filtered_by_bb']}")
        print(f"  Fees Paid: ${r['total_fees']:,.0f}")
        print(f"  Profitable Months: {r['profitable_months']}/{r['total_months']} ({r['profitable_months']/max(r['total_months'],1)*100:.0f}%)")

    # ═══════════════════════════════════════════════════════
    # WINNER
    # ═══════════════════════════════════════════════════════
    best = max(results, key=lambda x: x["profit_factor"])
    baseline = results[0]

    print(f"\n\n{'='*80}")
    print(f"  VERDICT")
    print(f"{'='*80}")
    if best["label"] == baseline["label"]:
        print(f"  WINNER: Current strategy (no BB filter)")
        print(f"  BB filters did NOT improve results. Keep current setup.")
    else:
        pf_improve = ((best["profit_factor"] / baseline["profit_factor"]) - 1) * 100
        pnl_improve = ((best["total_pnl_usd"] / max(baseline["total_pnl_usd"], 1)) - 1) * 100
        print(f"  WINNER: {best['label']}")
        print(f"  Profit Factor: {baseline['profit_factor']:.2f} → {best['profit_factor']:.2f} ({pf_improve:+.1f}%)")
        print(f"  Net Profit:    ${baseline['total_pnl_usd']:+,.0f} → ${best['total_pnl_usd']:+,.0f} ({pnl_improve:+.1f}%)")
        print(f"  Win Rate:      {baseline['win_rate']:.1f}% → {best['win_rate']:.1f}%")
        print(f"  Max Drawdown:  {baseline['max_drawdown_pct']:.1f}% → {best['max_drawdown_pct']:.1f}%")
        print(f"  Trades blocked by BB: {best['filtered_by_bb']} (signals that got better entries)")
    print(f"{'='*80}")

    # Save
    output = {
        "backtest_date": datetime.now(timezone.utc).isoformat(),
        "period": f"{start} to {end}",
        "test": "Bollinger Band entry filter comparison",
        "results": [{k: v for k, v in r.items()} for r in results],
    }
    with open("backtest_bb_filter_results.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n  Results saved to backtest_bb_filter_results.json")


if __name__ == "__main__":
    main()
