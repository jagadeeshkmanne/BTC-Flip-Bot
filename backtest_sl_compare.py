#!/usr/bin/env python3
"""
Backtest: Fixed 5% SL vs ATR-based SL
Uses real Binance BTCUSDT 15m data (180 days)
Same MACD(12,26,9) + RSI(14) strategy — only SL method differs.

This is a standalone script — does NOT affect the running bot.
"""

import numpy as np
import pandas as pd
import requests
import json
import time
from datetime import datetime, timezone, timedelta

# ─── Fetch Binance Klines ───
def fetch_klines(symbol="BTCUSDT", interval="15m", days=180):
    """Fetch historical klines from Binance public API."""
    url = "https://fapi.binance.com/fapi/v1/klines"
    all_candles = []
    end_time = int(time.time() * 1000)
    start_time = int((time.time() - days * 86400) * 1000)

    print(f"Fetching {days} days of {interval} data for {symbol}...")

    current = start_time
    while current < end_time:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current,
            "limit": 1500
        }
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  Error fetching: {e}, retrying...")
            time.sleep(2)
            continue

        if not data:
            break

        all_candles.extend(data)
        current = data[-1][0] + 1  # Next candle after last

        if len(data) < 1500:
            break

        time.sleep(0.2)  # Rate limit

    print(f"  Fetched {len(all_candles)} candles")

    df = pd.DataFrame(all_candles, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"
    ])

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df.drop_duplicates(subset=["open_time"]).reset_index(drop=True)

    return df

# ─── Indicators ───
def compute_rsi(df, period=14):
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def compute_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def compute_macd(df, fast=12, slow=26, signal=9):
    ema_fast = compute_ema(df["close"], fast)
    ema_slow = compute_ema(df["close"], slow)
    macd_line = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def compute_atr(df, period=14):
    high = df["high"]
    low = df["low"]
    close = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - close).abs(),
        (low - close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period).mean()

# ─── Backtest Engine ───
def run_backtest(df, sl_mode="fixed", sl_pct=0.05, atr_mult=2.5, atr_period=14,
                 leverage=2, capital=5000, cooldown_bars=6,
                 rsi_long_min=30, rsi_long_max=70, rsi_short_min=30, rsi_short_max=70):
    """
    Run backtest with identical MACD+RSI entry logic.
    sl_mode: "fixed" (fixed %) or "atr" (ATR-based dynamic)
    """

    # Compute indicators
    rsi = compute_rsi(df, 14)
    macd_line, signal_line, macd_hist = compute_macd(df, 12, 26, 9)
    atr = compute_atr(df, atr_period)
    vol = df["volume"].astype(float)
    vol_sma = vol.rolling(20).mean()

    trades = []
    balance = capital
    position = None  # {"side", "entry", "qty", "sl_price", "bar_idx"}
    cooldown_until = 0
    peak_balance = capital
    max_drawdown = 0

    for i in range(50, len(df)):  # Start after indicators warm up
        price = float(df["close"].iloc[i])

        curr_rsi = float(rsi.iloc[i]) if not pd.isna(rsi.iloc[i]) else 50
        curr_macd = float(macd_line.iloc[i]) if not pd.isna(macd_line.iloc[i]) else 0
        prev_macd = float(macd_line.iloc[i-1]) if not pd.isna(macd_line.iloc[i-1]) else 0
        curr_signal = float(signal_line.iloc[i]) if not pd.isna(signal_line.iloc[i]) else 0
        prev_signal = float(signal_line.iloc[i-1]) if not pd.isna(signal_line.iloc[i-1]) else 0
        curr_atr = float(atr.iloc[i]) if not pd.isna(atr.iloc[i]) else 0
        curr_vol = float(vol.iloc[i]) if not pd.isna(vol.iloc[i]) else 0
        curr_vol_sma = float(vol_sma.iloc[i]) if not pd.isna(vol_sma.iloc[i]) else 1

        # ── Detect MACD crossovers ──
        macd_bull_cross = (prev_macd <= prev_signal and curr_macd > curr_signal)
        macd_bear_cross = (prev_macd >= prev_signal and curr_macd < curr_signal)

        # ── Volume filter ──
        vol_ok = curr_vol_sma > 0 and (curr_vol / curr_vol_sma) >= 0.8

        # ── Determine signal ──
        signal = "HOLD"
        if macd_bull_cross and rsi_long_min <= curr_rsi <= rsi_long_max and vol_ok:
            signal = "LONG"
        elif macd_bear_cross and rsi_short_min <= curr_rsi <= rsi_short_max and vol_ok:
            signal = "SHORT"

        # ── Manage open position ──
        if position:
            side = position["side"]
            entry = position["entry"]
            qty = position["qty"]

            if side == "LONG":
                pnl_pct = (price - entry) / entry * leverage
            else:
                pnl_pct = (entry - price) / entry * leverage

            pnl_usd = pnl_pct * (entry * qty / leverage)

            # Check SL
            hit_sl = False
            if sl_mode == "fixed":
                hit_sl = pnl_pct <= -sl_pct
            else:  # ATR
                if side == "LONG":
                    hit_sl = price <= position["sl_price"]
                else:
                    hit_sl = price >= position["sl_price"]

            # Check MACD flip exit
            flip_exit = False
            if side == "LONG" and macd_bear_cross:
                flip_exit = True
            elif side == "SHORT" and macd_bull_cross:
                flip_exit = True

            if hit_sl:
                # Close on SL
                actual_pnl = pnl_usd
                balance += actual_pnl
                trades.append({
                    "entry_time": position["entry_time"],
                    "exit_time": str(df["timestamp"].iloc[i]),
                    "side": side,
                    "entry": entry,
                    "exit": price,
                    "pnl_pct": pnl_pct,
                    "pnl_usd": actual_pnl,
                    "exit_reason": "SL",
                    "bars_held": i - position["bar_idx"],
                })
                position = None
                cooldown_until = i + cooldown_bars

                # Track drawdown
                if balance > peak_balance:
                    peak_balance = balance
                dd = (peak_balance - balance) / peak_balance
                if dd > max_drawdown:
                    max_drawdown = dd
                continue

            if flip_exit:
                # Close on MACD flip
                actual_pnl = pnl_usd
                balance += actual_pnl
                trades.append({
                    "entry_time": position["entry_time"],
                    "exit_time": str(df["timestamp"].iloc[i]),
                    "side": side,
                    "entry": entry,
                    "exit": price,
                    "pnl_pct": pnl_pct,
                    "pnl_usd": actual_pnl,
                    "exit_reason": "FLIP",
                    "bars_held": i - position["bar_idx"],
                })
                position = None

                # Immediately open opposite position (flip)
                if i > cooldown_until and signal != "HOLD":
                    new_side = "SHORT" if side == "LONG" else "LONG"
                    qty = (balance * leverage) / price

                    # Calculate SL price
                    if sl_mode == "atr":
                        if new_side == "LONG":
                            new_sl = price - (curr_atr * atr_mult)
                        else:
                            new_sl = price + (curr_atr * atr_mult)
                    else:
                        new_sl = 0  # Not used for fixed

                    position = {
                        "side": new_side, "entry": price, "qty": qty,
                        "sl_price": new_sl, "bar_idx": i,
                        "entry_time": str(df["timestamp"].iloc[i]),
                    }

                # Track drawdown
                if balance > peak_balance:
                    peak_balance = balance
                dd = (peak_balance - balance) / peak_balance
                if dd > max_drawdown:
                    max_drawdown = dd
                continue

        # ── Open new position ──
        if not position and signal != "HOLD" and i > cooldown_until:
            qty = (balance * leverage) / price

            # Calculate SL price
            if sl_mode == "atr":
                if signal == "LONG":
                    sl_price = price - (curr_atr * atr_mult)
                else:
                    sl_price = price + (curr_atr * atr_mult)
            else:
                sl_price = 0

            position = {
                "side": signal, "entry": price, "qty": qty,
                "sl_price": sl_price, "bar_idx": i,
                "entry_time": str(df["timestamp"].iloc[i]),
            }

    # Close any remaining position
    if position:
        price = float(df["close"].iloc[-1])
        side = position["side"]
        entry = position["entry"]
        qty = position["qty"]
        if side == "LONG":
            pnl_pct = (price - entry) / entry * leverage
        else:
            pnl_pct = (entry - price) / entry * leverage
        pnl_usd = pnl_pct * (entry * qty / leverage)
        balance += pnl_usd
        trades.append({
            "entry_time": position["entry_time"],
            "exit_time": str(df["timestamp"].iloc[-1]),
            "side": side, "entry": entry, "exit": price,
            "pnl_pct": pnl_pct, "pnl_usd": pnl_usd,
            "exit_reason": "OPEN", "bars_held": len(df) - position["bar_idx"],
        })

    return {
        "sl_mode": sl_mode,
        "sl_param": f"{sl_pct*100}%" if sl_mode == "fixed" else f"{atr_mult}x ATR({atr_period})",
        "trades": trades,
        "total_trades": len(trades),
        "wins": len([t for t in trades if t["pnl_usd"] > 0]),
        "losses": len([t for t in trades if t["pnl_usd"] <= 0]),
        "win_rate": len([t for t in trades if t["pnl_usd"] > 0]) / max(len(trades), 1) * 100,
        "total_pnl_usd": balance - capital,
        "total_pnl_pct": (balance - capital) / capital * 100,
        "final_balance": balance,
        "max_drawdown_pct": max_drawdown * 100,
        "sl_exits": len([t for t in trades if t["exit_reason"] == "SL"]),
        "flip_exits": len([t for t in trades if t["exit_reason"] == "FLIP"]),
        "avg_bars_held": np.mean([t["bars_held"] for t in trades]) if trades else 0,
        "avg_win_pct": np.mean([t["pnl_pct"]*100 for t in trades if t["pnl_usd"] > 0]) if any(t["pnl_usd"] > 0 for t in trades) else 0,
        "avg_loss_pct": np.mean([t["pnl_pct"]*100 for t in trades if t["pnl_usd"] <= 0]) if any(t["pnl_usd"] <= 0 for t in trades) else 0,
        "profit_factor": abs(sum(t["pnl_usd"] for t in trades if t["pnl_usd"] > 0)) / max(abs(sum(t["pnl_usd"] for t in trades if t["pnl_usd"] <= 0)), 1),
    }


def print_results(r, label=""):
    """Pretty print backtest results."""
    print(f"\n{'='*60}")
    print(f"  {label}: {r['sl_mode'].upper()} SL ({r['sl_param']})")
    print(f"{'='*60}")
    print(f"  Total Trades:     {r['total_trades']}")
    print(f"  Wins / Losses:    {r['wins']}W / {r['losses']}L")
    print(f"  Win Rate:         {r['win_rate']:.1f}%")
    print(f"  Total P&L:        ${r['total_pnl_usd']:+,.2f} ({r['total_pnl_pct']:+.1f}%)")
    print(f"  Final Balance:    ${r['final_balance']:,.2f}")
    print(f"  Max Drawdown:     {r['max_drawdown_pct']:.1f}%")
    print(f"  Profit Factor:    {r['profit_factor']:.2f}")
    print(f"  Avg Win:          {r['avg_win_pct']:+.2f}%")
    print(f"  Avg Loss:         {r['avg_loss_pct']:.2f}%")
    print(f"  SL Exits:         {r['sl_exits']}")
    print(f"  MACD Flip Exits:  {r['flip_exits']}")
    print(f"  Avg Hold (bars):  {r['avg_bars_held']:.0f} ({r['avg_bars_held']*15/60:.1f}h)")
    print(f"{'='*60}")


def main():
    print("=" * 60)
    print("  BACKTEST: Fixed 5% SL  vs  ATR-based SL")
    print("  Strategy: MACD(12,26,9) + RSI(14) · 15m · 2x Leverage")
    print("  Capital: $5,000 · Pair: BTCUSDT")
    print("=" * 60)

    # Fetch data
    df = fetch_klines("BTCUSDT", "15m", 180)

    date_start = df["timestamp"].iloc[0].strftime("%Y-%m-%d")
    date_end = df["timestamp"].iloc[-1].strftime("%Y-%m-%d")
    print(f"  Period: {date_start} to {date_end} ({len(df)} candles)")

    # Run backtests
    results = []

    # 1. Fixed 5% SL (current strategy)
    r1 = run_backtest(df, sl_mode="fixed", sl_pct=0.05)
    results.append(r1)
    print_results(r1, "STRATEGY A")

    # 2. ATR-based SL with different multipliers
    for mult in [1.5, 2.0, 2.5, 3.0]:
        r = run_backtest(df, sl_mode="atr", atr_mult=mult)
        results.append(r)
        print_results(r, f"STRATEGY B{mult}")

    # 3. Fixed SL alternatives for comparison
    for pct in [0.03, 0.04, 0.06, 0.08]:
        r = run_backtest(df, sl_mode="fixed", sl_pct=pct)
        results.append(r)
        print_results(r, f"STRATEGY C{int(pct*100)}%")

    # ── Summary Table ──
    print(f"\n\n{'='*90}")
    print(f"  COMPARISON SUMMARY")
    print(f"{'='*90}")
    print(f"  {'SL Type':<20} {'Trades':>7} {'WR%':>7} {'P&L $':>10} {'P&L %':>8} {'DD%':>7} {'PF':>6} {'SL Hits':>8}")
    print(f"  {'-'*80}")

    for r in results:
        label = f"{r['sl_mode'].upper()} {r['sl_param']}"
        print(f"  {label:<20} {r['total_trades']:>7} {r['win_rate']:>6.1f}% {r['total_pnl_usd']:>+10,.0f} {r['total_pnl_pct']:>+7.1f}% {r['max_drawdown_pct']:>6.1f}% {r['profit_factor']:>5.2f} {r['sl_exits']:>8}")

    print(f"  {'-'*80}")

    # Find best
    best = max(results, key=lambda r: r['total_pnl_usd'])
    print(f"\n  BEST: {best['sl_mode'].upper()} {best['sl_param']} → ${best['total_pnl_usd']:+,.0f} ({best['total_pnl_pct']:+.1f}%)")

    worst = min(results, key=lambda r: r['max_drawdown_pct'])
    print(f"  LOWEST DD: {worst['sl_mode'].upper()} {worst['sl_param']} → {worst['max_drawdown_pct']:.1f}% max drawdown")

    best_pf = max(results, key=lambda r: r['profit_factor'])
    print(f"  BEST PF: {best_pf['sl_mode'].upper()} {best_pf['sl_param']} → {best_pf['profit_factor']:.2f} profit factor")

    # Save results to JSON
    output = {
        "backtest_date": datetime.now(timezone.utc).isoformat(),
        "period": f"{date_start} to {date_end}",
        "candles": len(df),
        "results": [{k: v for k, v in r.items() if k != "trades"} for r in results],
        "best_pnl": f"{best['sl_mode'].upper()} {best['sl_param']}",
        "best_dd": f"{worst['sl_mode'].upper()} {worst['sl_param']}",
    }

    with open("backtest_sl_results.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Results saved to backtest_sl_results.json")
    print(f"{'='*90}")


if __name__ == "__main__":
    main()
