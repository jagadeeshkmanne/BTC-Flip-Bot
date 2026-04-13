#!/usr/bin/env python3
"""
Backtest: 1H Entry vs 15m Entry — Multi-Timeframe Comparison
Uses real Binance BTCUSDT data (up to 5 years / 1825 days)
Same MACD(12,26,9) + RSI(14) strategy — compares 1H vs 15m entry timeframes.
"""

import numpy as np
import pandas as pd
import requests
import json
import time
from datetime import datetime, timezone

# ─── Fetch Binance Klines ───
def fetch_klines(symbol="BTCUSDT", interval="1h", days=1825):
    """Fetch historical klines from Binance public API."""
    url = "https://api.binance.com/api/v3/klines"
    all_candles = []
    end_time = int(time.time() * 1000)
    start_time = int((time.time() - days * 86400) * 1000)

    print(f"  Fetching {days} days ({days/365:.1f} years) of {interval} data for {symbol}...")

    current = start_time
    batch = 0
    retries = 0
    max_retries = 5
    while current < end_time:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": current,
            "limit": 1000
        }
        try:
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 10))
                print(f"    Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            retries = 0
        except Exception as e:
            retries += 1
            if retries > max_retries:
                print(f"    Too many errors, stopping at {len(all_candles)} candles")
                break
            print(f"    Error: {e}, retry {retries}/{max_retries}...")
            time.sleep(3)
            continue

        if not data:
            break

        all_candles.extend(data)
        current = data[-1][0] + 1
        batch += 1

        if batch % 20 == 0:
            days_fetched = (current - start_time) / 86400000
            print(f"    ... {len(all_candles)} candles ({days_fetched:.0f} days fetched)")

        if len(data) < 1000:
            break

        time.sleep(0.15)

    print(f"    Got {len(all_candles):,} candles")

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
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ─── Resample to higher timeframe ───
def resample_to_htf(df_base, htf="4h"):
    """Resample candles to higher timeframe."""
    df = df_base.set_index("timestamp").copy()

    ohlcv = df.resample(htf).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "open_time": "first",
    }).dropna()

    ohlcv = ohlcv.reset_index()
    return ohlcv


# ─── Get HTF trend at each bar ───
def compute_htf_trend(df_base, htf="4h", method="rsi"):
    """
    Compute higher timeframe trend and map it back to base timeframe bars.
    Methods: macd, macd_cross, ema, rsi, macd_rsi
    """
    df_htf = resample_to_htf(df_base, htf)

    if method == "macd":
        macd_l, sig_l, hist = compute_macd(df_htf["close"], 12, 26, 9)
        df_htf["trend"] = np.where(hist > 0, 1, -1)

    elif method == "macd_cross":
        macd_l, sig_l, hist = compute_macd(df_htf["close"], 12, 26, 9)
        df_htf["trend"] = np.where(macd_l > sig_l, 1, -1)

    elif method == "ema":
        ema50 = compute_ema(df_htf["close"], 50)
        df_htf["trend"] = np.where(df_htf["close"] > ema50, 1, -1)

    elif method == "rsi":
        rsi = compute_rsi(df_htf["close"], 14)
        df_htf["trend"] = np.where(rsi > 50, 1, -1)

    elif method == "macd_rsi":
        macd_l, sig_l, hist = compute_macd(df_htf["close"], 12, 26, 9)
        rsi = compute_rsi(df_htf["close"], 14)
        trend = np.zeros(len(df_htf))
        for i in range(len(df_htf)):
            if hist.iloc[i] > 0 and rsi.iloc[i] > 45:
                trend[i] = 1
            elif hist.iloc[i] < 0 and rsi.iloc[i] < 55:
                trend[i] = -1
            else:
                trend[i] = 0
        df_htf["trend"] = trend

    else:
        df_htf["trend"] = 0

    # Map HTF trend to base bars using forward fill
    df_htf_mapped = df_htf[["timestamp", "trend"]].copy()
    df_htf_mapped = df_htf_mapped.set_index("timestamp")

    trend_mapped = df_htf_mapped.reindex(df_base["timestamp"]).ffill()
    trend_mapped = trend_mapped.reset_index(drop=True)

    return trend_mapped["trend"].fillna(0).values


# ─── Backtest Engine ───
def run_backtest(df, htf_trend=None, sl_pct=0.05, leverage=2, capital=5000,
                 cooldown_bars=2, mtf_mode="none", label="", bar_minutes=60):
    """
    Run backtest with MACD+RSI entry logic.
    bar_minutes: minutes per bar (60 for 1H, 15 for 15m) — used for display only.
    """

    rsi = compute_rsi(df["close"], 14)
    macd_line, signal_line, macd_hist = compute_macd(df["close"], 12, 26, 9)
    vol = df["volume"].astype(float)
    vol_sma = vol.rolling(20).mean()

    if htf_trend is None:
        htf_trend = np.zeros(len(df))

    trades = []
    balance = capital
    position = None
    cooldown_until = 0
    peak_balance = capital
    max_drawdown = 0
    filtered_count = 0

    for i in range(50, len(df)):
        price = float(df["close"].iloc[i])

        curr_rsi = float(rsi.iloc[i]) if not pd.isna(rsi.iloc[i]) else 50
        curr_macd = float(macd_line.iloc[i]) if not pd.isna(macd_line.iloc[i]) else 0
        prev_macd = float(macd_line.iloc[i-1]) if not pd.isna(macd_line.iloc[i-1]) else 0
        curr_signal = float(signal_line.iloc[i]) if not pd.isna(signal_line.iloc[i]) else 0
        prev_signal = float(signal_line.iloc[i-1]) if not pd.isna(signal_line.iloc[i-1]) else 0
        curr_vol = float(vol.iloc[i]) if not pd.isna(vol.iloc[i]) else 0
        curr_vol_sma = float(vol_sma.iloc[i]) if not pd.isna(vol_sma.iloc[i]) else 1
        curr_htf = htf_trend[i] if i < len(htf_trend) else 0

        # Detect MACD crossovers
        macd_bull_cross = (prev_macd <= prev_signal and curr_macd > curr_signal)
        macd_bear_cross = (prev_macd >= prev_signal and curr_macd < curr_signal)

        # Volume filter
        vol_ok = curr_vol_sma > 0 and (curr_vol / curr_vol_sma) >= 0.8

        # Determine raw signal
        signal = "HOLD"
        if macd_bull_cross and 30 <= curr_rsi <= 70 and vol_ok:
            signal = "LONG"
        elif macd_bear_cross and 30 <= curr_rsi <= 70 and vol_ok:
            signal = "SHORT"

        # ── Apply MTF filter ──
        effective_sl = sl_pct
        if signal != "HOLD" and mtf_mode != "none":
            if mtf_mode == "filter":
                if signal == "LONG" and curr_htf == -1:
                    filtered_count += 1
                    signal = "HOLD"
                elif signal == "SHORT" and curr_htf == 1:
                    filtered_count += 1
                    signal = "HOLD"

        # ── Manage open position ──
        if position:
            side = position["side"]
            entry = position["entry"]
            qty = position["qty"]
            pos_sl = position["sl_pct"]

            if side == "LONG":
                pnl_pct = (price - entry) / entry * leverage
            else:
                pnl_pct = (entry - price) / entry * leverage

            pnl_usd = pnl_pct * (entry * qty / leverage)

            hit_sl = pnl_pct <= -pos_sl

            flip_exit = False
            if side == "LONG" and macd_bear_cross:
                flip_exit = True
            elif side == "SHORT" and macd_bull_cross:
                flip_exit = True

            if hit_sl:
                balance += pnl_usd
                trades.append({
                    "entry_time": position["entry_time"],
                    "exit_time": str(df["timestamp"].iloc[i]),
                    "side": side, "entry": entry, "exit": price,
                    "pnl_pct": pnl_pct, "pnl_usd": pnl_usd,
                    "exit_reason": "SL",
                    "bars_held": i - position["bar_idx"],
                    "htf_aligned": position.get("htf_aligned", "N/A"),
                })
                position = None
                cooldown_until = i + cooldown_bars
                if balance > peak_balance:
                    peak_balance = balance
                dd = (peak_balance - balance) / peak_balance
                if dd > max_drawdown:
                    max_drawdown = dd
                continue

            if flip_exit:
                balance += pnl_usd
                trades.append({
                    "entry_time": position["entry_time"],
                    "exit_time": str(df["timestamp"].iloc[i]),
                    "side": side, "entry": entry, "exit": price,
                    "pnl_pct": pnl_pct, "pnl_usd": pnl_usd,
                    "exit_reason": "FLIP",
                    "bars_held": i - position["bar_idx"],
                    "htf_aligned": position.get("htf_aligned", "N/A"),
                })
                position = None

                # Flip: open opposite position
                if i > cooldown_until and signal != "HOLD":
                    new_side = "SHORT" if side == "LONG" else "LONG"
                    qty = (balance * leverage) / price
                    aligned = "YES" if ((new_side == "LONG" and curr_htf >= 0) or (new_side == "SHORT" and curr_htf <= 0)) else "NO"
                    position = {
                        "side": new_side, "entry": price, "qty": qty,
                        "sl_pct": effective_sl, "bar_idx": i,
                        "entry_time": str(df["timestamp"].iloc[i]),
                        "htf_aligned": aligned,
                    }

                if balance > peak_balance:
                    peak_balance = balance
                dd = (peak_balance - balance) / peak_balance
                if dd > max_drawdown:
                    max_drawdown = dd
                continue

        # ── Open new position ──
        if not position and signal != "HOLD" and i > cooldown_until:
            qty = (balance * leverage) / price
            aligned = "YES" if ((signal == "LONG" and curr_htf >= 0) or (signal == "SHORT" and curr_htf <= 0)) else "NO"
            position = {
                "side": signal, "entry": price, "qty": qty,
                "sl_pct": effective_sl, "bar_idx": i,
                "entry_time": str(df["timestamp"].iloc[i]),
                "htf_aligned": aligned,
            }

    # Close remaining position
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
            "htf_aligned": position.get("htf_aligned", "N/A"),
        })

    wins = [t for t in trades if t["pnl_usd"] > 0]
    losses = [t for t in trades if t["pnl_usd"] <= 0]

    aligned_trades = [t for t in trades if t.get("htf_aligned") == "YES"]
    counter_trades = [t for t in trades if t.get("htf_aligned") == "NO"]
    aligned_wins = [t for t in aligned_trades if t["pnl_usd"] > 0]
    counter_wins = [t for t in counter_trades if t["pnl_usd"] > 0]

    return {
        "label": label,
        "trades": trades,
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / max(len(trades), 1) * 100,
        "total_pnl_usd": balance - capital,
        "total_pnl_pct": (balance - capital) / capital * 100,
        "final_balance": balance,
        "max_drawdown_pct": max_drawdown * 100,
        "sl_exits": len([t for t in trades if t["exit_reason"] == "SL"]),
        "flip_exits": len([t for t in trades if t["exit_reason"] == "FLIP"]),
        "filtered_signals": filtered_count,
        "avg_win_pct": np.mean([t["pnl_pct"]*100 for t in wins]) if wins else 0,
        "avg_loss_pct": np.mean([t["pnl_pct"]*100 for t in losses]) if losses else 0,
        "profit_factor": abs(sum(t["pnl_usd"] for t in wins)) / max(abs(sum(t["pnl_usd"] for t in losses)), 1),
        "avg_bars_held": np.mean([t["bars_held"] for t in trades]) if trades else 0,
        "bar_minutes": bar_minutes,
        "aligned_trades": len(aligned_trades),
        "aligned_wr": len(aligned_wins) / max(len(aligned_trades), 1) * 100,
        "aligned_pnl": sum(t["pnl_usd"] for t in aligned_trades),
        "counter_trades": len(counter_trades),
        "counter_wr": len(counter_wins) / max(len(counter_trades), 1) * 100,
        "counter_pnl": sum(t["pnl_usd"] for t in counter_trades),
    }


def print_results(r):
    bm = r.get("bar_minutes", 60)
    print(f"\n{'='*65}")
    print(f"  {r['label']}")
    print(f"{'='*65}")
    print(f"  Trades: {r['total_trades']}  |  Win Rate: {r['win_rate']:.1f}%  |  P&L: ${r['total_pnl_usd']:+,.0f} ({r['total_pnl_pct']:+.1f}%)")
    print(f"  Max DD: {r['max_drawdown_pct']:.1f}%  |  PF: {r['profit_factor']:.2f}  |  SL: {r['sl_exits']}  |  Flips: {r['flip_exits']}")
    print(f"  Avg Win: {r['avg_win_pct']:+.2f}%  |  Avg Loss: {r['avg_loss_pct']:.2f}%  |  Hold: {r['avg_bars_held']:.0f} bars ({r['avg_bars_held']*bm/60:.1f}h)")
    if r['filtered_signals'] > 0:
        print(f"  Signals Filtered by HTF: {r['filtered_signals']}")
    if r['aligned_trades'] > 0:
        print(f"  HTF-Aligned:  {r['aligned_trades']} trades, {r['aligned_wr']:.1f}% WR, ${r['aligned_pnl']:+,.0f}")
        print(f"  Counter-Trend: {r['counter_trades']} trades, {r['counter_wr']:.1f}% WR, ${r['counter_pnl']:+,.0f}")


def yearly_breakdown(trades, capital=5000):
    if not trades:
        return

    by_year = {}
    for t in trades:
        year = t["entry_time"][:4] if t.get("entry_time") else "Unknown"
        if year not in by_year:
            by_year[year] = {"trades": 0, "wins": 0, "pnl": 0}
        by_year[year]["trades"] += 1
        if t["pnl_usd"] > 0:
            by_year[year]["wins"] += 1
        by_year[year]["pnl"] += t["pnl_usd"]

    print(f"\n  {'Year':<8} {'Trades':>7} {'WR%':>7} {'P&L $':>12} {'P&L %':>9} {'Market':>14}")
    print(f"  {'-'*60}")

    market = {
        "2021": "Bull Run",
        "2022": "Bear Market",
        "2023": "Recovery",
        "2024": "Halving Rally",
        "2025": "Post-Halving",
        "2026": "Consolidation",
    }

    cumulative = 0
    for year in sorted(by_year.keys()):
        d = by_year[year]
        wr = d["wins"] / max(d["trades"], 1) * 100
        pnl_pct = d["pnl"] / capital * 100
        cumulative += d["pnl"]
        mkt = market.get(year, "")
        print(f"  {year:<8} {d['trades']:>7} {wr:>6.1f}% {d['pnl']:>+11,.0f} {pnl_pct:>+8.1f}% {mkt:>14}")

    print(f"  {'-'*60}")
    print(f"  {'TOTAL':<8} {sum(d['trades'] for d in by_year.values()):>7} {'':>7} {cumulative:>+11,.0f} {cumulative/capital*100:>+8.1f}%")


def main():
    print("=" * 70)
    print("  1H vs 15m ENTRY BACKTEST — 5 YEAR HISTORY")
    print("  Strategy: MACD(12,26,9) + RSI(14) · 2x Leverage · $5,000")
    print("  Comparing: 1H Entry vs 15m Entry (both with HTF filters)")
    print("  Covers: Bull (2021) · Bear (2022) · Recovery (2023)")
    print("          Halving (2024) · Post-Halving (2025-2026)")
    print("=" * 70)

    # ═══════════════════════════════════════════════════════
    # FETCH DATA
    # ═══════════════════════════════════════════════════════
    df_1h = fetch_klines("BTCUSDT", "1h", 1825)
    df_15m = fetch_klines("BTCUSDT", "15m", 1825)

    if len(df_1h) < 100 or len(df_15m) < 100:
        print("  ERROR: Not enough data fetched. Check your internet connection.")
        return

    print(f"\n  1H Period: {df_1h['timestamp'].iloc[0].strftime('%Y-%m-%d')} to {df_1h['timestamp'].iloc[-1].strftime('%Y-%m-%d')} ({len(df_1h):,} candles)")
    print(f"  15m Period: {df_15m['timestamp'].iloc[0].strftime('%Y-%m-%d')} to {df_15m['timestamp'].iloc[-1].strftime('%Y-%m-%d')} ({len(df_15m):,} candles)")

    results_1h = []
    results_15m = []

    htf_methods = ["macd", "macd_cross", "ema", "rsi", "macd_rsi"]

    # ═══════════════════════════════════════════════════════
    # SECTION A: 1H ENTRY STRATEGIES
    # ═══════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print(f"  SECTION A: 1H ENTRY TIMEFRAME")
    print(f"{'='*70}")

    # 1H Baseline (no filter)
    print("\n  Running: 1H Baseline (no filter)...")
    r = run_backtest(df_1h, htf_trend=None, sl_pct=0.05, mtf_mode="none",
                     cooldown_bars=2, label="1H BASELINE (no filter) 5%SL",
                     bar_minutes=60)
    results_1h.append(r)
    print_results(r)

    # 1H + 4H filter (all methods)
    print("\n  Computing 4H trends for 1H entry...")
    for method in htf_methods:
        trend_4h = compute_htf_trend(df_1h, htf="4h", method=method)
        r = run_backtest(df_1h, htf_trend=trend_4h, sl_pct=0.05, mtf_mode="filter",
                         cooldown_bars=2, label=f"1H+4H FILTER ({method}) 5%SL",
                         bar_minutes=60)
        results_1h.append(r)
        print_results(r)

    # 1H + 1D filter (all methods)
    print("\n  Computing 1D trends for 1H entry...")
    for method in htf_methods:
        trend_1d = compute_htf_trend(df_1h, htf="1D", method=method)
        r = run_backtest(df_1h, htf_trend=trend_1d, sl_pct=0.05, mtf_mode="filter",
                         cooldown_bars=2, label=f"1H+1D FILTER ({method}) 5%SL",
                         bar_minutes=60)
        results_1h.append(r)
        print_results(r)

    # ═══════════════════════════════════════════════════════
    # SECTION B: 15m ENTRY (CURRENT CONFIG) FOR COMPARISON
    # ═══════════════════════════════════════════════════════
    print(f"\n{'='*70}")
    print(f"  SECTION B: 15m ENTRY TIMEFRAME (CURRENT CONFIG)")
    print(f"{'='*70}")

    # 15m Baseline
    print("\n  Running: 15m Baseline (no filter)...")
    r = run_backtest(df_15m, htf_trend=None, sl_pct=0.05, mtf_mode="none",
                     cooldown_bars=6, label="15m BASELINE (no filter) 5%SL",
                     bar_minutes=15)
    results_15m.append(r)
    print_results(r)

    # 15m + 4H RSI (current best config)
    print("\n  Running: 15m + 4H RSI (current config)...")
    trend_4h_15m = compute_htf_trend(df_15m, htf="4h", method="rsi")
    r = run_backtest(df_15m, htf_trend=trend_4h_15m, sl_pct=0.05, mtf_mode="filter",
                     cooldown_bars=6, label="15m+4H FILTER (rsi) 5%SL [CURRENT]",
                     bar_minutes=15)
    results_15m.append(r)
    print_results(r)

    # 15m + 4H EMA
    trend_4h_ema = compute_htf_trend(df_15m, htf="4h", method="ema")
    r = run_backtest(df_15m, htf_trend=trend_4h_ema, sl_pct=0.05, mtf_mode="filter",
                     cooldown_bars=6, label="15m+4H FILTER (ema) 5%SL",
                     bar_minutes=15)
    results_15m.append(r)
    print_results(r)

    # ═══════════════════════════════════════════════════════
    # COMPARISON TABLE
    # ═══════════════════════════════════════════════════════
    all_results = results_1h + results_15m

    print(f"\n\n{'='*120}")
    print(f"  HEAD-TO-HEAD: 1H ENTRY vs 15m ENTRY (5 YEARS)")
    print(f"{'='*120}")
    print(f"  {'Strategy':<45} {'Trades':>6} {'WR%':>6} {'P&L $':>14} {'P&L %':>11} {'DD%':>6} {'PF':>5} {'SL':>4} {'Filtered':>9}")
    print(f"  {'-'*112}")

    print(f"  {'--- 1H ENTRY ---':<45}")
    for r in results_1h:
        print(f"  {r['label']:<45} {r['total_trades']:>6} {r['win_rate']:>5.1f}% {r['total_pnl_usd']:>+13,.0f} {r['total_pnl_pct']:>+10.1f}% {r['max_drawdown_pct']:>5.1f}% {r['profit_factor']:>4.2f} {r['sl_exits']:>4} {r['filtered_signals']:>9}")

    print(f"  {'--- 15m ENTRY (CURRENT) ---':<45}")
    for r in results_15m:
        print(f"  {r['label']:<45} {r['total_trades']:>6} {r['win_rate']:>5.1f}% {r['total_pnl_usd']:>+13,.0f} {r['total_pnl_pct']:>+10.1f}% {r['max_drawdown_pct']:>5.1f}% {r['profit_factor']:>4.2f} {r['sl_exits']:>4} {r['filtered_signals']:>9}")

    print(f"  {'-'*112}")

    # Find best in each category
    best_1h = max(results_1h, key=lambda r: r['total_pnl_usd'])
    best_15m = max(results_15m, key=lambda r: r['total_pnl_usd'])

    print(f"\n  BEST 1H:  {best_1h['label']} → ${best_1h['total_pnl_usd']:+,.0f} ({best_1h['total_pnl_pct']:+.1f}%) | DD: {best_1h['max_drawdown_pct']:.1f}% | PF: {best_1h['profit_factor']:.2f}")
    print(f"  BEST 15m: {best_15m['label']} → ${best_15m['total_pnl_usd']:+,.0f} ({best_15m['total_pnl_pct']:+.1f}%) | DD: {best_15m['max_drawdown_pct']:.1f}% | PF: {best_15m['profit_factor']:.2f}")

    # Risk adjusted
    for r in all_results:
        r["risk_adj"] = r["total_pnl_pct"] / max(r["max_drawdown_pct"], 1)
    best_risk = max(all_results, key=lambda r: r["risk_adj"])
    print(f"  BEST RISK-ADJUSTED: {best_risk['label']} → {best_risk['total_pnl_pct']:+.1f}% / {best_risk['max_drawdown_pct']:.1f}% DD = {best_risk['risk_adj']:.2f}")

    # ═══════════════════════════════════════════════════════
    # YEARLY BREAKDOWNS
    # ═══════════════════════════════════════════════════════
    print(f"\n\n{'='*70}")
    print(f"  YEARLY BREAKDOWN — BEST 1H: {best_1h['label']}")
    print(f"{'='*70}")
    yearly_breakdown(best_1h["trades"])

    print(f"\n  YEARLY BREAKDOWN — BEST 15m: {best_15m['label']}")
    print(f"  {'-'*60}")
    yearly_breakdown(best_15m["trades"])

    # Save results
    output = {
        "backtest_date": datetime.now(timezone.utc).isoformat(),
        "period_1h": f"{df_1h['timestamp'].iloc[0].strftime('%Y-%m-%d')} to {df_1h['timestamp'].iloc[-1].strftime('%Y-%m-%d')}",
        "period_15m": f"{df_15m['timestamp'].iloc[0].strftime('%Y-%m-%d')} to {df_15m['timestamp'].iloc[-1].strftime('%Y-%m-%d')}",
        "candles_1h": len(df_1h),
        "candles_15m": len(df_15m),
        "results_1h": [{k: v for k, v in r.items() if k != "trades"} for r in results_1h],
        "results_15m": [{k: v for k, v in r.items() if k != "trades"} for r in results_15m],
        "best_1h": best_1h["label"],
        "best_15m": best_15m["label"],
        "best_risk_adjusted": best_risk["label"],
    }

    with open("backtest_1h_vs_15m_results.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Results saved to backtest_1h_vs_15m_results.json")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
