#!/usr/bin/env python3
"""
Backtest: Option A (Current — Close on MTF-Blocked Flip) vs Option B (Hold if MTF-Aligned)
Uses real Binance BTCUSDT data (5 years)
Same MACD(12,26,9) + RSI(14) strategy with 4H RSI filter

Option A (CURRENT): When opposite MACD cross fires but is MTF-blocked,
                     CLOSE the current position anyway (go flat).
Option B (HOLD):    When opposite MACD cross fires but is MTF-blocked,
                     KEEP HOLDING the position (trust the 4H trend).
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
    max_retries = 5
    while current < end_time:
        params = {"symbol": symbol, "interval": interval, "startTime": current, "limit": 1000}
        try:
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 10))
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            retries = 0
        except Exception as e:
            retries += 1
            if retries > max_retries:
                break
            time.sleep(3)
            continue
        if not data:
            break
        all_candles.extend(data)
        current = data[-1][0] + 1
        batch += 1
        if batch % 50 == 0:
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

def resample_to_htf(df_base, htf="4h"):
    df = df_base.set_index("timestamp").copy()
    ohlcv = df.resample(htf).agg({
        "open": "first", "high": "max", "low": "min",
        "close": "last", "volume": "sum", "open_time": "first",
    }).dropna()
    return ohlcv.reset_index()

def compute_htf_trend(df_base, htf="4h"):
    """4H RSI > 50 = bullish, < 50 = bearish (best performing filter)."""
    df_htf = resample_to_htf(df_base, htf)
    rsi = compute_rsi(df_htf["close"], 14)
    df_htf["trend"] = np.where(rsi > 50, 1, -1)
    df_htf_mapped = df_htf[["timestamp", "trend"]].set_index("timestamp")
    trend_mapped = df_htf_mapped.reindex(df_base["timestamp"]).ffill().reset_index(drop=True)
    return trend_mapped["trend"].fillna(0).values


# ─── Backtest Engine ───
def run_backtest(df, htf_trend, sl_pct=0.05, leverage=2, capital=5000,
                 cooldown_bars=6, hold_on_mtf_block=False, label=""):
    """
    hold_on_mtf_block:
      False = Option A (current): close position on MTF-blocked flip (go flat)
      True  = Option B (new):     hold position if aligned with HTF, ignore blocked flips
    """

    rsi = compute_rsi(df["close"], 14)
    macd_line, signal_line, macd_hist = compute_macd(df["close"], 12, 26, 9)
    vol = df["volume"].astype(float)
    vol_sma = vol.rolling(20).mean()

    trades = []
    balance = capital
    position = None
    cooldown_until = 0
    peak_balance = capital
    max_drawdown = 0
    filtered_count = 0
    mtf_holds = 0  # times we held instead of closing on blocked flip
    mtf_closes = 0  # times we closed on blocked flip

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

        macd_bull_cross = (prev_macd <= prev_signal and curr_macd > curr_signal)
        macd_bear_cross = (prev_macd >= prev_signal and curr_macd < curr_signal)

        vol_ok = curr_vol_sma > 0 and (curr_vol / curr_vol_sma) >= 0.8

        # Determine raw signal (before MTF filter)
        raw_signal = "HOLD"
        if macd_bull_cross and 30 <= curr_rsi <= 70 and vol_ok:
            raw_signal = "LONG"
        elif macd_bear_cross and 30 <= curr_rsi <= 70 and vol_ok:
            raw_signal = "SHORT"

        # Apply MTF filter to get final signal
        signal = raw_signal
        if signal != "HOLD":
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

            # Detect opposite MACD cross
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
                # Check if the opposite signal is MTF-blocked
                opposite_blocked = False
                if side == "LONG" and curr_htf == 1:
                    # We're LONG, 4H is BULL — the SHORT signal is counter-trend
                    opposite_blocked = True
                elif side == "SHORT" and curr_htf == -1:
                    # We're SHORT, 4H is BEAR — the LONG signal is counter-trend
                    opposite_blocked = True

                if opposite_blocked and hold_on_mtf_block:
                    # OPTION B: Don't close — hold the position, trust the 4H trend
                    mtf_holds += 1
                    continue
                else:
                    # OPTION A (or aligned flip): Close the position
                    if opposite_blocked:
                        mtf_closes += 1

                    balance += pnl_usd
                    trades.append({
                        "entry_time": position["entry_time"],
                        "exit_time": str(df["timestamp"].iloc[i]),
                        "side": side, "entry": entry, "exit": price,
                        "pnl_pct": pnl_pct, "pnl_usd": pnl_usd,
                        "exit_reason": "FLIP_MTF_BLOCK" if opposite_blocked else "FLIP",
                        "bars_held": i - position["bar_idx"],
                        "htf_aligned": position.get("htf_aligned", "N/A"),
                    })
                    position = None

                    # Open opposite if signal is not blocked
                    if not opposite_blocked and i > cooldown_until and signal != "HOLD":
                        new_side = "SHORT" if side == "LONG" else "LONG"
                        qty = (balance * leverage) / price
                        aligned = "YES" if ((new_side == "LONG" and curr_htf >= 0) or (new_side == "SHORT" and curr_htf <= 0)) else "NO"
                        position = {
                            "side": new_side, "entry": price, "qty": qty,
                            "sl_pct": sl_pct, "bar_idx": i,
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
                "sl_pct": sl_pct, "bar_idx": i,
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

    # Breakdown by exit reason
    flip_trades = [t for t in trades if t["exit_reason"] == "FLIP"]
    mtf_block_trades = [t for t in trades if t["exit_reason"] == "FLIP_MTF_BLOCK"]
    sl_trades = [t for t in trades if t["exit_reason"] == "SL"]

    mtf_block_wins = [t for t in mtf_block_trades if t["pnl_usd"] > 0]
    mtf_block_losses = [t for t in mtf_block_trades if t["pnl_usd"] <= 0]

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
        "sl_exits": len(sl_trades),
        "flip_exits": len(flip_trades),
        "filtered_signals": filtered_count,
        "avg_win_pct": np.mean([t["pnl_pct"]*100 for t in wins]) if wins else 0,
        "avg_loss_pct": np.mean([t["pnl_pct"]*100 for t in losses]) if losses else 0,
        "profit_factor": abs(sum(t["pnl_usd"] for t in wins)) / max(abs(sum(t["pnl_usd"] for t in losses)), 1),
        "avg_bars_held": np.mean([t["bars_held"] for t in trades]) if trades else 0,
        # MTF block specific stats
        "mtf_block_closes": len(mtf_block_trades),
        "mtf_block_wins": len(mtf_block_wins),
        "mtf_block_losses": len(mtf_block_losses),
        "mtf_block_pnl": sum(t["pnl_usd"] for t in mtf_block_trades),
        "mtf_block_avg_pnl": np.mean([t["pnl_pct"]*100 for t in mtf_block_trades]) if mtf_block_trades else 0,
        "mtf_holds": mtf_holds,
    }


def print_results(r):
    print(f"\n{'='*70}")
    print(f"  {r['label']}")
    print(f"{'='*70}")
    print(f"  Trades: {r['total_trades']}  |  Win Rate: {r['win_rate']:.1f}%  |  P&L: ${r['total_pnl_usd']:+,.0f} ({r['total_pnl_pct']:+.1f}%)")
    print(f"  Max DD: {r['max_drawdown_pct']:.1f}%  |  PF: {r['profit_factor']:.2f}  |  SL: {r['sl_exits']}  |  Flips: {r['flip_exits']}")
    print(f"  Avg Win: {r['avg_win_pct']:+.2f}%  |  Avg Loss: {r['avg_loss_pct']:.2f}%  |  Hold: {r['avg_bars_held']:.0f} bars ({r['avg_bars_held']*15/60:.1f}h)")
    if r['filtered_signals'] > 0:
        print(f"  Signals Filtered by HTF: {r['filtered_signals']}")
    if r['mtf_block_closes'] > 0:
        print(f"  MTF-Blocked Flip Closes: {r['mtf_block_closes']} ({r['mtf_block_wins']}W/{r['mtf_block_losses']}L, avg {r['mtf_block_avg_pnl']:+.2f}%, total ${r['mtf_block_pnl']:+,.0f})")
    if r['mtf_holds'] > 0:
        print(f"  MTF-Blocked Flips HELD (ignored): {r['mtf_holds']}")


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

    market = {"2021": "Bull Run", "2022": "Bear Market", "2023": "Recovery",
              "2024": "Halving Rally", "2025": "Post-Halving", "2026": "Consolidation"}

    print(f"\n  {'Year':<8} {'Trades':>7} {'WR%':>7} {'P&L $':>12} {'P&L %':>9} {'Market':>14}")
    print(f"  {'-'*60}")

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
    print("  FLIP vs HOLD BACKTEST — 5 YEAR HISTORY")
    print("  Strategy: 15m MACD + 4H RSI Filter · 2x Leverage · $5,000")
    print("  Option A: Close on MTF-blocked flip (current behavior)")
    print("  Option B: Hold position if aligned with 4H trend")
    print("=" * 70)

    df = fetch_klines("BTCUSDT", "15m", 1825)
    if len(df) < 100:
        print("  ERROR: Not enough data.")
        return

    print(f"\n  Period: {df['timestamp'].iloc[0].strftime('%Y-%m-%d')} to {df['timestamp'].iloc[-1].strftime('%Y-%m-%d')} ({len(df):,} candles)")

    print("\n  Computing 4H RSI trends...")
    htf_trend = compute_htf_trend(df, "4h")

    results = []

    # ═══════════════════════════════════════════════════════
    # Option A: Current behavior — close on MTF-blocked flip
    # ═══════════════════════════════════════════════════════
    print("\n  Running Option A (Close on MTF-blocked flip)...")
    r_a = run_backtest(df, htf_trend, sl_pct=0.05, hold_on_mtf_block=False,
                       label="OPTION A: Close on MTF-blocked flip (CURRENT)")
    results.append(r_a)
    print_results(r_a)

    # ═══════════════════════════════════════════════════════
    # Option B: Hold position if aligned with 4H trend
    # ═══════════════════════════════════════════════════════
    print("\n  Running Option B (Hold if MTF-aligned)...")
    r_b = run_backtest(df, htf_trend, sl_pct=0.05, hold_on_mtf_block=True,
                       label="OPTION B: Hold if aligned, ignore blocked flips")
    results.append(r_b)
    print_results(r_b)

    # ═══════════════════════════════════════════════════════
    # Also test Option B with tighter SL (since holds are longer)
    # ═══════════════════════════════════════════════════════
    print("\n  Running Option B with 3% SL...")
    r_b3 = run_backtest(df, htf_trend, sl_pct=0.03, hold_on_mtf_block=True,
                        label="OPTION B + 3% SL: Hold + tighter stop")
    results.append(r_b3)
    print_results(r_b3)

    print("\n  Running Option B with 7% SL...")
    r_b7 = run_backtest(df, htf_trend, sl_pct=0.07, hold_on_mtf_block=True,
                        label="OPTION B + 7% SL: Hold + wider stop")
    results.append(r_b7)
    print_results(r_b7)

    # ═══════════════════════════════════════════════════════
    # COMPARISON
    # ═══════════════════════════════════════════════════════
    print(f"\n\n{'='*115}")
    print(f"  HEAD-TO-HEAD: OPTION A vs OPTION B (5 YEARS)")
    print(f"{'='*115}")
    print(f"  {'Strategy':<52} {'Trades':>6} {'WR%':>6} {'P&L $':>14} {'P&L %':>11} {'DD%':>6} {'PF':>5} {'AvgHold':>8}")
    print(f"  {'-'*107}")

    for r in results:
        hold_hrs = r['avg_bars_held'] * 15 / 60
        print(f"  {r['label']:<52} {r['total_trades']:>6} {r['win_rate']:>5.1f}% {r['total_pnl_usd']:>+13,.0f} {r['total_pnl_pct']:>+10.1f}% {r['max_drawdown_pct']:>5.1f}% {r['profit_factor']:>4.2f} {hold_hrs:>6.1f}h")

    print(f"  {'-'*107}")

    # Winner
    best = max(results, key=lambda r: r['total_pnl_pct'] / max(r['max_drawdown_pct'], 1))
    print(f"\n  BEST RISK-ADJUSTED: {best['label']}")
    print(f"  → P&L: ${best['total_pnl_usd']:+,.0f} ({best['total_pnl_pct']:+.1f}%) | DD: {best['max_drawdown_pct']:.1f}% | PF: {best['profit_factor']:.2f}")

    # ═══════════════════════════════════════════════════════
    # DETAILED MTF-BLOCK ANALYSIS (Option A only)
    # ═══════════════════════════════════════════════════════
    if r_a['mtf_block_closes'] > 0:
        print(f"\n\n{'='*70}")
        print(f"  MTF-BLOCKED FLIP ANALYSIS (Option A)")
        print(f"{'='*70}")
        print(f"  Total MTF-blocked flip closes: {r_a['mtf_block_closes']}")
        print(f"  Wins: {r_a['mtf_block_wins']}  |  Losses: {r_a['mtf_block_losses']}")
        print(f"  Win Rate: {r_a['mtf_block_wins']/max(r_a['mtf_block_closes'],1)*100:.1f}%")
        print(f"  Avg P&L per trade: {r_a['mtf_block_avg_pnl']:+.2f}%")
        print(f"  Total P&L from these exits: ${r_a['mtf_block_pnl']:+,.0f}")
        print(f"")
        print(f"  If these were PROFITABLE on average → Option A is better (exit saves money)")
        print(f"  If these were LOSING on average → Option B is better (holding would've recovered)")

    # ═══════════════════════════════════════════════════════
    # YEARLY BREAKDOWNS
    # ═══════════════════════════════════════════════════════
    print(f"\n\n{'='*70}")
    print(f"  YEARLY BREAKDOWN — {results[0]['label']}")
    print(f"{'='*70}")
    yearly_breakdown(results[0]["trades"])

    print(f"\n  YEARLY BREAKDOWN — {results[1]['label']}")
    print(f"  {'-'*60}")
    yearly_breakdown(results[1]["trades"])

    # Save
    output = {
        "backtest_date": datetime.now(timezone.utc).isoformat(),
        "period": f"{df['timestamp'].iloc[0].strftime('%Y-%m-%d')} to {df['timestamp'].iloc[-1].strftime('%Y-%m-%d')}",
        "results": [{k: v for k, v in r.items() if k != "trades"} for r in results],
        "recommendation": best["label"],
    }
    with open("backtest_flip_vs_hold_results.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Results saved to backtest_flip_vs_hold_results.json")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
