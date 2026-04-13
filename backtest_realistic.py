#!/usr/bin/env python3
"""
REALISTIC Backtest: Option B (Hold on MTF-blocked flip) with 4H RSI filter
Includes: trading fees, slippage, fixed sizing + capped compounding
Uses real Binance BTCUSDT 15m data (5 years)
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
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

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


# ─── Slippage Model ───
def calc_slippage(pos_usd, base_slippage=0.0003, progressive=False):
    """
    Progressive slippage: base 0.03% up to $100K,
    then +0.01% per additional $100K (market impact from large orders).
    At $500K position: 0.03% + 0.04% = 0.07%
    At $1M position: 0.03% + 0.09% = 0.12%
    """
    if not progressive or pos_usd <= 100000:
        return base_slippage
    extra = ((pos_usd - 100000) / 100000) * 0.0001
    return base_slippage + extra


# ─── Realistic Backtest Engine ───
def run_backtest(df, htf_trend, sl_pct=0.05, leverage=2, capital=5000,
                 cooldown_bars=6, hold_on_mtf_block=True,
                 fee_pct=0.0004, slippage_pct=0.0003,
                 sizing_mode="compound_capped", max_position_usd=50000,
                 capital_deploy_pct=1.0, progressive_slippage=False,
                 label=""):
    """
    Realistic backtest with fees, slippage, and funding fees.

    sizing_mode:
      "fixed"            — Always trade with initial capital amount
      "compound"         — Full compounding with capital_deploy_pct
      "compound_capped"  — Compound but cap max position size

    fee_pct: 0.04% taker fee per side (Binance Futures default)
    slippage_pct: 0.03% estimated slippage per trade
    capital_deploy_pct: fraction of balance to deploy (0.95 = 95%, 5% reserve for funding)
    Funding fee: ~0.01% every 8 hours (32 bars on 15m) on position notional
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
    total_fees = 0

    # Monthly tracking
    monthly_pnl = {}

    for i in range(50, len(df)):
        price = float(df["close"].iloc[i])
        ts = df["timestamp"].iloc[i]

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

        raw_signal = "HOLD"
        if macd_bull_cross and 30 <= curr_rsi <= 70 and vol_ok:
            raw_signal = "LONG"
        elif macd_bear_cross and 30 <= curr_rsi <= 70 and vol_ok:
            raw_signal = "SHORT"

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

            # Funding fee: ~0.01% every 8 hours = every 32 bars on 15m
            bars_in_pos = i - position["bar_idx"]
            if bars_in_pos > 0 and bars_in_pos % 32 == 0:
                funding_cost = position["capital_used"] * leverage * 0.0001  # 0.01% of notional
                total_fees += funding_cost
                balance -= funding_cost
                position.setdefault("funding_paid", 0)
                position["funding_paid"] += funding_cost

            if side == "LONG":
                pnl_pct = (price - entry) / entry * leverage
            else:
                pnl_pct = (entry - price) / entry * leverage

            pnl_usd = pnl_pct * position["capital_used"]

            hit_sl = pnl_pct <= -sl_pct

            flip_exit = False
            if side == "LONG" and macd_bear_cross:
                flip_exit = True
            elif side == "SHORT" and macd_bull_cross:
                flip_exit = True

            if hit_sl:
                # Apply exit slippage and fee
                slip = calc_slippage(position["capital_used"] * leverage, slippage_pct, progressive_slippage)
                exit_cost = position["capital_used"] * (fee_pct + slip)
                total_fees += exit_cost
                net_pnl = pnl_usd - exit_cost

                balance += net_pnl
                month_key = ts.strftime("%Y-%m")
                monthly_pnl[month_key] = monthly_pnl.get(month_key, 0) + net_pnl

                trades.append({
                    "entry_time": position["entry_time"],
                    "exit_time": str(ts),
                    "side": side, "entry": entry, "exit": price,
                    "pnl_pct": pnl_pct, "pnl_usd": net_pnl,
                    "fees": exit_cost + position["entry_fee"] + position.get("funding_paid", 0),
                    "exit_reason": "SL",
                    "bars_held": i - position["bar_idx"],
                })
                position = None
                cooldown_until = i + cooldown_bars
                if balance > peak_balance:
                    peak_balance = balance
                dd = (peak_balance - balance) / peak_balance if peak_balance > 0 else 0
                if dd > max_drawdown:
                    max_drawdown = dd
                continue

            if flip_exit:
                opposite_blocked = False
                if side == "LONG" and curr_htf == 1:
                    opposite_blocked = True
                elif side == "SHORT" and curr_htf == -1:
                    opposite_blocked = True

                if opposite_blocked and hold_on_mtf_block:
                    continue
                else:
                    slip = calc_slippage(position["capital_used"] * leverage, slippage_pct, progressive_slippage)
                    exit_cost = position["capital_used"] * (fee_pct + slip)
                    total_fees += exit_cost
                    net_pnl = pnl_usd - exit_cost

                    balance += net_pnl
                    month_key = ts.strftime("%Y-%m")
                    monthly_pnl[month_key] = monthly_pnl.get(month_key, 0) + net_pnl

                    trades.append({
                        "entry_time": position["entry_time"],
                        "exit_time": str(ts),
                        "side": side, "entry": entry, "exit": price,
                        "pnl_pct": pnl_pct, "pnl_usd": net_pnl,
                        "fees": exit_cost + position["entry_fee"] + position.get("funding_paid", 0),
                        "exit_reason": "FLIP",
                        "bars_held": i - position["bar_idx"],
                    })
                    position = None

                    # Open opposite if aligned
                    if not opposite_blocked and i > cooldown_until and signal != "HOLD":
                        new_side = "SHORT" if side == "LONG" else "LONG"

                        # Position sizing (apply capital_deploy_pct for fee reserve)
                        if sizing_mode == "fixed":
                            pos_capital = min(capital, balance) * capital_deploy_pct
                        elif sizing_mode == "compound_capped":
                            pos_capital = min(balance * capital_deploy_pct, max_position_usd)
                        else:  # full compound
                            pos_capital = balance * capital_deploy_pct

                        if pos_capital > 0:
                            slip = calc_slippage(pos_capital * leverage, slippage_pct, progressive_slippage)
                            entry_fee = pos_capital * (fee_pct + slip)
                            total_fees += entry_fee
                            qty = (pos_capital * leverage) / price
                            position = {
                                "side": new_side, "entry": price, "qty": qty,
                                "capital_used": pos_capital, "entry_fee": entry_fee,
                                "bar_idx": i, "entry_time": str(ts),
                            }

                    if balance > peak_balance:
                        peak_balance = balance
                    dd = (peak_balance - balance) / peak_balance if peak_balance > 0 else 0
                    if dd > max_drawdown:
                        max_drawdown = dd
                    continue

        # ── Open new position ──
        if not position and signal != "HOLD" and i > cooldown_until and balance > 10:
            if sizing_mode == "fixed":
                pos_capital = min(capital, balance) * capital_deploy_pct
            elif sizing_mode == "compound_capped":
                pos_capital = min(balance * capital_deploy_pct, max_position_usd)
            else:
                pos_capital = balance * capital_deploy_pct

            if pos_capital > 0:
                slip = calc_slippage(pos_capital * leverage, slippage_pct, progressive_slippage)
                entry_fee = pos_capital * (fee_pct + slip)
                total_fees += entry_fee
                qty = (pos_capital * leverage) / price
                position = {
                    "side": signal, "entry": price, "qty": qty,
                    "capital_used": pos_capital, "entry_fee": entry_fee,
                    "bar_idx": i, "entry_time": str(ts),
                }

    # Close remaining position
    if position:
        price = float(df["close"].iloc[-1])
        side = position["side"]
        entry = position["entry"]
        if side == "LONG":
            pnl_pct = (price - entry) / entry * leverage
        else:
            pnl_pct = (entry - price) / entry * leverage
        pnl_usd = pnl_pct * position["capital_used"]
        slip = calc_slippage(position["capital_used"] * leverage, slippage_pct, progressive_slippage)
        exit_cost = position["capital_used"] * (fee_pct + slip)
        total_fees += exit_cost
        net_pnl = pnl_usd - exit_cost
        balance += net_pnl
        trades.append({
            "entry_time": position["entry_time"],
            "exit_time": str(df["timestamp"].iloc[-1]),
            "side": side, "entry": entry, "exit": price,
            "pnl_pct": pnl_pct, "pnl_usd": net_pnl,
            "fees": exit_cost + position["entry_fee"] + position.get("funding_paid", 0),
            "exit_reason": "OPEN",
            "bars_held": len(df) - position["bar_idx"],
        })

    wins = [t for t in trades if t["pnl_usd"] > 0]
    losses = [t for t in trades if t["pnl_usd"] <= 0]
    sl_trades = [t for t in trades if t["exit_reason"] == "SL"]

    # Consecutive losses
    max_consec_loss = 0
    consec = 0
    for t in trades:
        if t["pnl_usd"] <= 0:
            consec += 1
            max_consec_loss = max(max_consec_loss, consec)
        else:
            consec = 0

    # Monthly stats
    profitable_months = sum(1 for v in monthly_pnl.values() if v > 0)
    total_months = len(monthly_pnl)

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
        "flip_exits": len([t for t in trades if t["exit_reason"] == "FLIP"]),
        "filtered_signals": filtered_count,
        "avg_win_pct": np.mean([t["pnl_pct"]*100 for t in wins]) if wins else 0,
        "avg_loss_pct": np.mean([t["pnl_pct"]*100 for t in losses]) if losses else 0,
        "avg_win_usd": np.mean([t["pnl_usd"] for t in wins]) if wins else 0,
        "avg_loss_usd": np.mean([t["pnl_usd"] for t in losses]) if losses else 0,
        "profit_factor": abs(sum(t["pnl_usd"] for t in wins)) / max(abs(sum(t["pnl_usd"] for t in losses)), 1),
        "avg_bars_held": np.mean([t["bars_held"] for t in trades]) if trades else 0,
        "total_fees": total_fees,
        "max_consec_losses": max_consec_loss,
        "profitable_months": profitable_months,
        "total_months": total_months,
        "monthly_pnl": monthly_pnl,
    }


def print_results(r):
    print(f"\n{'='*70}")
    print(f"  {r['label']}")
    print(f"{'='*70}")
    print(f"  Final Balance: ${r['final_balance']:,.2f} (started $5,000)")
    print(f"  Net Profit: ${r['total_pnl_usd']:+,.2f} ({r['total_pnl_pct']:+.1f}%)")
    print(f"  Total Fees Paid: ${r['total_fees']:,.2f}")
    print(f"  Trades: {r['total_trades']}  |  Win Rate: {r['win_rate']:.1f}%")
    print(f"  Max Drawdown: {r['max_drawdown_pct']:.1f}%")
    print(f"  Profit Factor: {r['profit_factor']:.2f}")
    print(f"  Avg Win: {r['avg_win_pct']:+.2f}% (${r['avg_win_usd']:+,.2f})")
    print(f"  Avg Loss: {r['avg_loss_pct']:.2f}% (${r['avg_loss_usd']:,.2f})")
    print(f"  Avg Hold: {r['avg_bars_held']:.0f} bars ({r['avg_bars_held']*15/60:.1f}h)")
    print(f"  SL Exits: {r['sl_exits']}  |  Flip Exits: {r['flip_exits']}")
    print(f"  Max Consecutive Losses: {r['max_consec_losses']}")
    print(f"  Profitable Months: {r['profitable_months']}/{r['total_months']} ({r['profitable_months']/max(r['total_months'],1)*100:.0f}%)")


def yearly_breakdown(trades, capital=5000, label=""):
    if not trades: return
    by_year = {}
    for t in trades:
        year = t["entry_time"][:4]
        if year not in by_year:
            by_year[year] = {"trades": 0, "wins": 0, "pnl": 0, "fees": 0}
        by_year[year]["trades"] += 1
        if t["pnl_usd"] > 0:
            by_year[year]["wins"] += 1
        by_year[year]["pnl"] += t["pnl_usd"]
        by_year[year]["fees"] += t.get("fees", 0)

    market = {"2021":"Bull Run","2022":"Bear Market","2023":"Recovery",
              "2024":"Halving Rally","2025":"Post-Halving","2026":"Consolidation"}

    print(f"\n  {'Year':<6} {'Trades':>7} {'WR%':>6} {'Net P&L':>12} {'Fees':>10} {'Market':>14}")
    print(f"  {'-'*62}")
    for year in sorted(by_year.keys()):
        d = by_year[year]
        wr = d["wins"] / max(d["trades"], 1) * 100
        print(f"  {year:<6} {d['trades']:>7} {wr:>5.1f}% {d['pnl']:>+11,.2f} {d['fees']:>9,.2f} {market.get(year,''):>14}")
    total_pnl = sum(d["pnl"] for d in by_year.values())
    total_fees = sum(d["fees"] for d in by_year.values())
    print(f"  {'-'*62}")
    print(f"  {'TOTAL':<6} {sum(d['trades'] for d in by_year.values()):>7} {'':>6} {total_pnl:>+11,.2f} {total_fees:>9,.2f}")


def main():
    print("=" * 70)
    print("  REALISTIC BACKTEST — OPTION B (Hold on MTF-blocked flip)")
    print("  Strategy: 15m MACD + 4H RSI Filter · 2x Leverage · $5,000")
    print("  Includes: 0.04% fees + 0.03% slippage + 0.01%/8h funding")
    print("  Capital Deploy: 95% (5% reserve for funding fees)")
    print("=" * 70)

    df = fetch_klines("BTCUSDT", "15m", 1825)
    if len(df) < 100:
        print("  ERROR: Not enough data.")
        return

    start = df['timestamp'].iloc[0].strftime('%Y-%m-%d')
    end = df['timestamp'].iloc[-1].strftime('%Y-%m-%d')
    print(f"\n  Period: {start} to {end} ({len(df):,} candles)")

    print("\n  Computing 4H RSI trends...")
    htf_trend = compute_htf_trend(df, "4h")

    starting_capital = 5000
    results = []

    # Your actual bot scenario: deploy $5K, 95% per trade, let it compound
    # BTC futures daily volume is $10-20B, so positions up to $500K-$1M fill easily
    # Beyond ~$1M, slippage increases significantly even on BTC
    # We add progressive slippage: extra 0.01% per $100K over $100K

    # ═══════════════════════════════════════════════════════
    # 1. FULL COMPOUND 95% — "Deploy $5K and leave for 5 years"
    #    With progressive slippage for large positions
    # ═══════════════════════════════════════════════════════
    print("\n  Running: FULL 95% compound — deploy $5K, leave 5 years...")
    r = run_backtest(df, htf_trend, capital=starting_capital,
                     sizing_mode="compound", max_position_usd=999999999,
                     capital_deploy_pct=0.95, progressive_slippage=True,
                     label="FULL 95% COMPOUND — Deploy $5K, Leave 5 Years")
    results.append(r)
    print_results(r)

    # ═══════════════════════════════════════════════════════
    # 2. Same but capped at $250K position (very safe for liquidity)
    # ═══════════════════════════════════════════════════════
    print("\n  Running: 95% compound, capped $250K (safe liquidity)...")
    r = run_backtest(df, htf_trend, capital=starting_capital,
                     sizing_mode="compound_capped", max_position_usd=250000,
                     capital_deploy_pct=0.95,
                     label="95% COMPOUND, CAP $250K — Safe Liquidity")
    results.append(r)
    print_results(r)

    # ═══════════════════════════════════════════════════════
    # 3. Capped at $500K position (still realistic on BTC)
    # ═══════════════════════════════════════════════════════
    print("\n  Running: 95% compound, capped $500K...")
    r = run_backtest(df, htf_trend, capital=starting_capital,
                     sizing_mode="compound_capped", max_position_usd=500000,
                     capital_deploy_pct=0.95,
                     label="95% COMPOUND, CAP $500K — Realistic on BTC")
    results.append(r)
    print_results(r)

    # ═══════════════════════════════════════════════════════
    # 4. Capped at $1M position (max for BTC futures)
    # ═══════════════════════════════════════════════════════
    print("\n  Running: 95% compound, capped $1M (max BTC)...")
    r = run_backtest(df, htf_trend, capital=starting_capital,
                     sizing_mode="compound_capped", max_position_usd=1000000,
                     capital_deploy_pct=0.95,
                     label="95% COMPOUND, CAP $1M — Max BTC Futures")
    results.append(r)
    print_results(r)

    # ═══════════════════════════════════════════════════════
    # COMPARISON TABLE
    # ═══════════════════════════════════════════════════════
    print(f"\n\n{'='*110}")
    print(f"  REALISTIC COMPARISON — ALL SIZING MODES (5 YEARS, WITH FEES)")
    print(f"{'='*110}")
    print(f"  {'Strategy':<48} {'Trades':>6} {'WR%':>6} {'Final $':>12} {'Net P&L':>12} {'DD%':>6} {'PF':>5} {'Fees':>10}")
    print(f"  {'-'*102}")

    for r in results:
        print(f"  {r['label']:<48} {r['total_trades']:>6} {r['win_rate']:>5.1f}% {r['final_balance']:>11,.2f} {r['total_pnl_usd']:>+11,.2f} {r['max_drawdown_pct']:>5.1f}% {r['profit_factor']:>4.2f} {r['total_fees']:>9,.2f}")

    print(f"  {'-'*102}")

    # ═══════════════════════════════════════════════════════
    # YEARLY BREAKDOWN for each
    # ═══════════════════════════════════════════════════════
    for r in results:
        print(f"\n{'='*70}")
        print(f"  YEARLY — {r['label']}")
        print(f"{'='*70}")
        yearly_breakdown(r["trades"], label=r["label"])

    # ═══════════════════════════════════════════════════════
    # MONTHLY P&L for the $25K cap (good balance of growth + realism)
    # ═══════════════════════════════════════════════════════
    r_primary = results[0]  # Full 95% compound
    print(f"\n\n{'='*70}")
    print(f"  MONTHLY P&L — {r_primary['label']}")
    print(f"{'='*70}")
    months = sorted(r_primary["monthly_pnl"].keys())
    print(f"\n  {'Month':<10} {'P&L':>12} {'Cumulative':>14} {'Balance':>14}")
    print(f"  {'-'*55}")
    cumulative = 0
    for m in months:
        pnl = r_primary["monthly_pnl"][m]
        cumulative += pnl
        marker = "+" if pnl > 0 else "-"
        print(f"  {m:<10} {pnl:>+11,.2f} {cumulative:>+13,.2f} {starting_capital+cumulative:>13,.2f}  {marker}")

    # ═══════════════════════════════════════════════════════
    # KEY STATS SUMMARY for each mode
    # ═══════════════════════════════════════════════════════
    total_days = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).days

    for r in results:
        avg_monthly = r["total_pnl_usd"] / max(r["total_months"], 1)
        avg_yearly = r["total_pnl_usd"] / (total_days / 365) if total_days > 0 else 0
        cagr = ((r["final_balance"] / starting_capital) ** (365 / total_days) - 1) * 100 if total_days > 0 and r["final_balance"] > 0 else 0

        print(f"\n{'='*70}")
        print(f"  {r['label']}")
        print(f"{'='*70}")
        print(f"  Starting Capital:     ${starting_capital:,}")
        print(f"  Final Balance:        ${r['final_balance']:,.2f}")
        print(f"  Total Net Profit:     ${r['total_pnl_usd']:+,.2f}")
        print(f"  Total Fees Paid:      ${r['total_fees']:,.2f} (entry/exit + funding)")
        print(f"  CAGR:                 {cagr:.1f}%")
        print(f"  Average Monthly:      ${avg_monthly:+,.2f}/month")
        print(f"  Average Yearly:       ${avg_yearly:+,.2f}/year")
        print(f"  Win Rate:             {r['win_rate']:.1f}%")
        print(f"  Profit Factor:        {r['profit_factor']:.2f}")
        print(f"  Max Drawdown:         {r['max_drawdown_pct']:.1f}%")
        print(f"  Max Consec. Losses:   {r['max_consec_losses']}")
        print(f"  Profitable Months:    {r['profitable_months']}/{r['total_months']} ({r['profitable_months']/max(r['total_months'],1)*100:.0f}%)")
        print(f"  Trades per Month:     {r['total_trades']/max(r['total_months'],1):.1f}")
        print(f"  Avg Hold Time:        {r['avg_bars_held']*15/60:.1f} hours")

    # Save
    output = {
        "backtest_date": datetime.now(timezone.utc).isoformat(),
        "period": f"{start} to {end}",
        "strategy": "Option B: Hold on MTF-blocked flip, 15m MACD + 4H RSI, 2x leverage",
        "fees": "0.04% taker + 0.03% slippage per side",
        "results": [{k: v for k, v in r.items() if k not in ("trades","monthly_pnl")} for r in results],
    }
    with open("backtest_realistic_results.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n  Results saved to backtest_realistic_results.json")


if __name__ == "__main__":
    main()
