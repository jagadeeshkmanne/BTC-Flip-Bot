#!/usr/bin/env python3
"""
Backtest: Single Timeframe (15m) vs Multi-Timeframe (15m + 1H / 15m + 4H)
Uses real Binance BTCUSDT data (180 days)
Same MACD(12,26,9) + RSI(14) strategy — adds higher TF trend filter.

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

    print(f"  Fetching {days} days of {interval} data for {symbol}...")

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
            print(f"    Error: {e}, retrying...")
            time.sleep(2)
            continue

        if not data:
            break

        all_candles.extend(data)
        current = data[-1][0] + 1

        if len(data) < 1500:
            break

        time.sleep(0.2)

    print(f"    Got {len(all_candles)} candles")

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

# ─── Resample 15m to higher timeframe ───
def resample_to_htf(df_15m, htf="1h"):
    """Resample 15m candles to higher timeframe."""
    df = df_15m.set_index("timestamp").copy()

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

# ─── Get HTF trend at each 15m bar ───
def compute_htf_trend(df_15m, htf="1h", method="macd"):
    """
    Compute higher timeframe trend and map it back to 15m bars.
    Returns a Series aligned with df_15m index.

    Methods:
    - "macd": HTF MACD histogram > 0 = bullish, < 0 = bearish
    - "macd_cross": HTF MACD line above signal = bullish
    - "ema": Price above EMA(50) on HTF = bullish
    - "rsi": HTF RSI > 50 = bullish
    - "macd_rsi": HTF MACD bullish AND RSI > 45 = bullish, MACD bearish AND RSI < 55 = bearish
    """
    df_htf = resample_to_htf(df_15m, htf)

    if method == "macd":
        macd_l, sig_l, hist = compute_macd(df_htf["close"], 12, 26, 9)
        df_htf["trend"] = np.where(hist > 0, 1, -1)  # 1=bull, -1=bear

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
                trend[i] = 0  # neutral — allow both directions
        df_htf["trend"] = trend

    else:
        df_htf["trend"] = 0  # no filter

    # Map HTF trend to 15m bars using forward fill
    # Each HTF bar's trend applies to 15m bars until the next HTF bar
    df_htf_mapped = df_htf[["timestamp", "trend"]].copy()
    df_htf_mapped = df_htf_mapped.set_index("timestamp")

    # Reindex to 15m timestamps and forward fill
    trend_15m = df_htf_mapped.reindex(df_15m["timestamp"]).ffill()
    trend_15m = trend_15m.reset_index(drop=True)

    return trend_15m["trend"].fillna(0).values

# ─── Backtest Engine ───
def run_backtest(df, htf_trend=None, sl_pct=0.05, leverage=2, capital=5000,
                 cooldown_bars=6, mtf_mode="none", label=""):
    """
    Run backtest with MACD+RSI entry logic.
    htf_trend: array of 1 (bull), -1 (bear), 0 (neutral) per 15m bar
    mtf_mode:
      "none" — ignore HTF (original strategy)
      "filter" — only take trades aligned with HTF trend
      "bias" — take aligned trades normally, allow counter-trend with tighter SL
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
    filtered_count = 0  # trades blocked by HTF filter

    for i in range(50, len(df)):
        price = float(df["close"].iloc[i])
        high_price = float(df["high"].iloc[i])
        low_price = float(df["low"].iloc[i])

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
                # Strict: only trade in direction of HTF trend
                if signal == "LONG" and curr_htf == -1:
                    filtered_count += 1
                    signal = "HOLD"
                elif signal == "SHORT" and curr_htf == 1:
                    filtered_count += 1
                    signal = "HOLD"
                # neutral (0) allows both directions

            elif mtf_mode == "bias":
                # Allow counter-trend but with tighter SL
                if signal == "LONG" and curr_htf == -1:
                    effective_sl = sl_pct * 0.6  # Tighter SL for counter-trend
                elif signal == "SHORT" and curr_htf == 1:
                    effective_sl = sl_pct * 0.6

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

            # Check SL
            hit_sl = pnl_pct <= -pos_sl

            # Check MACD flip exit
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

    # Aligned vs counter-trend analysis
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
        # HTF alignment stats
        "aligned_trades": len(aligned_trades),
        "aligned_wr": len(aligned_wins) / max(len(aligned_trades), 1) * 100,
        "aligned_pnl": sum(t["pnl_usd"] for t in aligned_trades),
        "counter_trades": len(counter_trades),
        "counter_wr": len(counter_wins) / max(len(counter_trades), 1) * 100,
        "counter_pnl": sum(t["pnl_usd"] for t in counter_trades),
    }


def print_results(r):
    print(f"\n{'='*65}")
    print(f"  {r['label']}")
    print(f"{'='*65}")
    print(f"  Trades: {r['total_trades']}  |  Win Rate: {r['win_rate']:.1f}%  |  P&L: ${r['total_pnl_usd']:+,.0f} ({r['total_pnl_pct']:+.1f}%)")
    print(f"  Max DD: {r['max_drawdown_pct']:.1f}%  |  PF: {r['profit_factor']:.2f}  |  SL: {r['sl_exits']}  |  Flips: {r['flip_exits']}")
    print(f"  Avg Win: {r['avg_win_pct']:+.2f}%  |  Avg Loss: {r['avg_loss_pct']:.2f}%  |  Hold: {r['avg_bars_held']:.0f} bars ({r['avg_bars_held']*15/60:.1f}h)")
    if r['filtered_signals'] > 0:
        print(f"  Signals Filtered by HTF: {r['filtered_signals']}")
    if r['aligned_trades'] > 0:
        print(f"  HTF-Aligned:  {r['aligned_trades']} trades, {r['aligned_wr']:.1f}% WR, ${r['aligned_pnl']:+,.0f}")
        print(f"  Counter-Trend: {r['counter_trades']} trades, {r['counter_wr']:.1f}% WR, ${r['counter_pnl']:+,.0f}")


def main():
    print("=" * 70)
    print("  MULTI-TIMEFRAME BACKTEST")
    print("  Strategy: MACD(12,26,9) + RSI(14) · 2x Leverage · $5,000")
    print("  Comparing: 15m Only vs 15m + 1H vs 15m + 4H")
    print("=" * 70)

    # Fetch data
    df_15m = fetch_klines("BTCUSDT", "15m", 180)

    date_start = df_15m["timestamp"].iloc[0].strftime("%Y-%m-%d")
    date_end = df_15m["timestamp"].iloc[-1].strftime("%Y-%m-%d")
    print(f"\n  Period: {date_start} to {date_end} ({len(df_15m)} candles)")

    results = []

    # ═══════════════════════════════════════════════════════
    # 1. BASELINE — Current strategy (15m only, 5% SL)
    # ═══════════════════════════════════════════════════════
    print("\n  Running: Baseline (15m only)...")
    r = run_backtest(df_15m, htf_trend=None, sl_pct=0.05, mtf_mode="none",
                     label="BASELINE: 15m Only (5% SL)")
    results.append(r)
    print_results(r)

    # Also test with 3% SL baseline
    r = run_backtest(df_15m, htf_trend=None, sl_pct=0.03, mtf_mode="none",
                     label="BASELINE: 15m Only (3% SL)")
    results.append(r)
    print_results(r)

    # ═══════════════════════════════════════════════════════
    # 2. MULTI-TIMEFRAME with 1H
    # ═══════════════════════════════════════════════════════
    print("\n  Computing 1H trends...")

    htf_methods = ["macd", "macd_cross", "ema", "rsi", "macd_rsi"]

    for method in htf_methods:
        trend_1h = compute_htf_trend(df_15m, htf="1h", method=method)

        # Filter mode (strict — only trade with HTF)
        r = run_backtest(df_15m, htf_trend=trend_1h, sl_pct=0.05, mtf_mode="filter",
                         label=f"MTF 15m+1H FILTER ({method}) 5%SL")
        results.append(r)
        print_results(r)

    # Best 1H method with 3% SL
    for method in ["macd", "macd_cross", "macd_rsi"]:
        trend_1h = compute_htf_trend(df_15m, htf="1h", method=method)
        r = run_backtest(df_15m, htf_trend=trend_1h, sl_pct=0.03, mtf_mode="filter",
                         label=f"MTF 15m+1H FILTER ({method}) 3%SL")
        results.append(r)
        print_results(r)

    # Bias mode (allow counter-trend with tighter SL)
    for method in ["macd", "macd_rsi"]:
        trend_1h = compute_htf_trend(df_15m, htf="1h", method=method)
        r = run_backtest(df_15m, htf_trend=trend_1h, sl_pct=0.05, mtf_mode="bias",
                         label=f"MTF 15m+1H BIAS ({method}) 5%SL")
        results.append(r)
        print_results(r)

    # ═══════════════════════════════════════════════════════
    # 3. MULTI-TIMEFRAME with 4H
    # ═══════════════════════════════════════════════════════
    print("\n  Computing 4H trends...")

    for method in htf_methods:
        trend_4h = compute_htf_trend(df_15m, htf="4h", method=method)

        r = run_backtest(df_15m, htf_trend=trend_4h, sl_pct=0.05, mtf_mode="filter",
                         label=f"MTF 15m+4H FILTER ({method}) 5%SL")
        results.append(r)
        print_results(r)

    # Best 4H method with 3% SL
    for method in ["macd", "macd_cross", "macd_rsi"]:
        trend_4h = compute_htf_trend(df_15m, htf="4h", method=method)
        r = run_backtest(df_15m, htf_trend=trend_4h, sl_pct=0.03, mtf_mode="filter",
                         label=f"MTF 15m+4H FILTER ({method}) 3%SL")
        results.append(r)
        print_results(r)

    # ═══════════════════════════════════════════════════════
    # SUMMARY TABLE
    # ═══════════════════════════════════════════════════════
    print(f"\n\n{'='*110}")
    print(f"  COMPARISON SUMMARY — ALL STRATEGIES")
    print(f"{'='*110}")
    print(f"  {'Strategy':<42} {'Trades':>6} {'WR%':>6} {'P&L $':>10} {'P&L %':>8} {'DD%':>6} {'PF':>5} {'SL':>4} {'Filtered':>9}")
    print(f"  {'-'*105}")

    for r in results:
        print(f"  {r['label']:<42} {r['total_trades']:>6} {r['win_rate']:>5.1f}% {r['total_pnl_usd']:>+10,.0f} {r['total_pnl_pct']:>+7.1f}% {r['max_drawdown_pct']:>5.1f}% {r['profit_factor']:>4.2f} {r['sl_exits']:>4} {r['filtered_signals']:>9}")

    print(f"  {'-'*105}")

    # Find winners
    best_pnl = max(results, key=lambda r: r['total_pnl_usd'])
    best_dd = min(results, key=lambda r: r['max_drawdown_pct'])
    best_pf = max(results, key=lambda r: r['profit_factor'])
    best_wr = max(results, key=lambda r: r['win_rate'])

    # Risk-adjusted: P&L / DD ratio
    for r in results:
        r["risk_adj"] = r["total_pnl_pct"] / max(r["max_drawdown_pct"], 1)
    best_risk = max(results, key=lambda r: r["risk_adj"])

    print(f"\n  BEST P&L:           {best_pnl['label']} → ${best_pnl['total_pnl_usd']:+,.0f} ({best_pnl['total_pnl_pct']:+.1f}%)")
    print(f"  LOWEST DRAWDOWN:    {best_dd['label']} → {best_dd['max_drawdown_pct']:.1f}%")
    print(f"  BEST PROFIT FACTOR: {best_pf['label']} → {best_pf['profit_factor']:.2f}")
    print(f"  BEST WIN RATE:      {best_wr['label']} → {best_wr['win_rate']:.1f}%")
    print(f"  BEST RISK-ADJUSTED: {best_risk['label']} → {best_risk['total_pnl_pct']:+.1f}% / {best_risk['max_drawdown_pct']:.1f}% DD = {best_risk['risk_adj']:.2f}")

    # Save results
    output = {
        "backtest_date": datetime.now(timezone.utc).isoformat(),
        "period": f"{date_start} to {date_end}",
        "candles": len(df_15m),
        "results": [{k: v for k, v in r.items() if k != "trades"} for r in results],
        "best_pnl": best_pnl["label"],
        "best_dd": best_dd["label"],
        "best_pf": best_pf["label"],
        "best_risk_adjusted": best_risk["label"],
    }

    with open("backtest_mtf_results.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Results saved to backtest_mtf_results.json")
    print(f"{'='*110}")


if __name__ == "__main__":
    main()
