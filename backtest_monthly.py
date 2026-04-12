#!/usr/bin/env python3
"""
Monthly P&L Breakdown: Baseline (15m only) vs Selected (15m + 4H MACD+RSI)
Tracks equity curve over time and outputs month-by-month returns.
"""

import numpy as np
import pandas as pd
import requests
import json
import time
from datetime import datetime, timezone, timedelta

# ─── Fetch Binance Klines ───
def fetch_klines(symbol="BTCUSDT", interval="15m", days=180):
    url = "https://fapi.binance.com/fapi/v1/klines"
    all_candles = []
    end_time = int(time.time() * 1000)
    start_time = int((time.time() - days * 86400) * 1000)
    print(f"  Fetching {days} days of {interval} data...")
    current = start_time
    while current < end_time:
        params = {"symbol": symbol, "interval": interval, "startTime": current, "limit": 1500}
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"    Error: {e}, retrying...")
            time.sleep(2)
            continue
        if not data: break
        all_candles.extend(data)
        current = data[-1][0] + 1
        if len(data) < 1500: break
        time.sleep(0.2)
    print(f"    Got {len(all_candles)} candles")
    df = pd.DataFrame(all_candles, columns=[
        "open_time","open","high","low","close","volume",
        "close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"
    ])
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

# ─── Resample for HTF ───
def resample_htf(df_15m, htf="4h"):
    df = df_15m.set_index("timestamp").copy()
    ohlcv = df.resample(htf).agg({
        "open":"first","high":"max","low":"min","close":"last","volume":"sum","open_time":"first"
    }).dropna().reset_index()
    return ohlcv

def compute_htf_trend(df_15m, htf="4h"):
    df_htf = resample_htf(df_15m, htf)
    macd_l, sig_l, hist = compute_macd(df_htf["close"], 12, 26, 9)
    rsi = compute_rsi(df_htf["close"], 14)
    df_htf["trend"] = 0
    for i in range(len(df_htf)):
        h = hist.iloc[i] if not pd.isna(hist.iloc[i]) else 0
        r = rsi.iloc[i] if not pd.isna(rsi.iloc[i]) else 50
        if h > 0 and r > 45: df_htf.at[df_htf.index[i], "trend"] = 1
        elif h < 0 and r < 55: df_htf.at[df_htf.index[i], "trend"] = -1
    mapped = df_htf[["timestamp","trend"]].set_index("timestamp")
    trend_15m = mapped.reindex(df_15m["timestamp"]).ffill().reset_index(drop=True)
    return trend_15m["trend"].fillna(0).values

# ─── Backtest with equity tracking ───
def run_backtest(df, htf_trend=None, sl_pct=0.05, leverage=2, capital=5000,
                 cooldown_bars=6, use_mtf=False, label=""):
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
    equity_curve = []  # (timestamp, balance)

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

        signal = "HOLD"
        if macd_bull_cross and 30 <= curr_rsi <= 70 and vol_ok:
            signal = "LONG"
        elif macd_bear_cross and 30 <= curr_rsi <= 70 and vol_ok:
            signal = "SHORT"

        # MTF filter
        if use_mtf and signal != "HOLD" and curr_htf != 0:
            if signal == "LONG" and curr_htf == -1: signal = "HOLD"
            elif signal == "SHORT" and curr_htf == 1: signal = "HOLD"

        # Track unrealized equity
        current_equity = balance
        if position:
            side = position["side"]
            entry = position["entry"]
            qty = position["qty"]
            if side == "LONG":
                pnl_pct = (price - entry) / entry * leverage
            else:
                pnl_pct = (entry - price) / entry * leverage
            pnl_usd = pnl_pct * (entry * qty / leverage)
            current_equity = balance + pnl_usd

        # Record equity every 4 hours (every 16 bars)
        if i % 16 == 0:
            equity_curve.append({"timestamp": str(ts), "equity": current_equity})

        if position:
            side = position["side"]
            entry = position["entry"]
            qty = position["qty"]
            if side == "LONG":
                pnl_pct = (price - entry) / entry * leverage
            else:
                pnl_pct = (entry - price) / entry * leverage
            pnl_usd = pnl_pct * (entry * qty / leverage)

            hit_sl = pnl_pct <= -sl_pct
            flip_exit = (side == "LONG" and macd_bear_cross) or (side == "SHORT" and macd_bull_cross)

            if hit_sl or flip_exit:
                balance += pnl_usd
                trades.append({
                    "time": str(ts), "side": side, "entry": entry, "exit": price,
                    "pnl_usd": pnl_usd, "pnl_pct": pnl_pct,
                    "reason": "SL" if hit_sl else "FLIP",
                    "balance_after": balance,
                })
                position = None
                if hit_sl:
                    cooldown_until = i + cooldown_bars
                elif not hit_sl and signal != "HOLD":
                    # Flip into opposite
                    if use_mtf and curr_htf != 0:
                        new_side = "SHORT" if side == "LONG" else "LONG"
                        if (new_side == "LONG" and curr_htf == -1) or (new_side == "SHORT" and curr_htf == 1):
                            continue  # MTF blocks the flip entry
                    new_side = "SHORT" if side == "LONG" else "LONG"
                    qty = (balance * leverage) / price
                    position = {"side": new_side, "entry": price, "qty": qty, "bar_idx": i}
                continue

        if not position and signal != "HOLD" and i > cooldown_until:
            qty = (balance * leverage) / price
            position = {"side": signal, "entry": price, "qty": qty, "bar_idx": i}

    # Close remaining
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

    return {
        "label": label,
        "final_balance": balance,
        "total_pnl": balance - capital,
        "total_pnl_pct": (balance - capital) / capital * 100,
        "trades": trades,
        "equity_curve": equity_curve,
    }


def monthly_breakdown(result, capital=5000):
    """Extract monthly P&L from trade history."""
    trades = result["trades"]
    if not trades:
        return []

    # Group trades by month
    monthly = {}
    running_balance = capital

    for t in trades:
        month = t["time"][:7]  # "2025-10"
        if month not in monthly:
            monthly[month] = {"month": month, "start_balance": running_balance, "pnl": 0, "trades": 0, "wins": 0}
        monthly[month]["pnl"] += t["pnl_usd"]
        monthly[month]["trades"] += 1
        if t["pnl_usd"] > 0:
            monthly[month]["wins"] += 1
        running_balance = t["balance_after"]

    # Calculate end balance and return %
    months = sorted(monthly.values(), key=lambda x: x["month"])
    running = capital
    for m in months:
        m["start_balance"] = running
        m["end_balance"] = running + m["pnl"]
        m["return_pct"] = (m["pnl"] / running) * 100 if running > 0 else 0
        m["wr"] = (m["wins"] / m["trades"] * 100) if m["trades"] > 0 else 0
        running = m["end_balance"]

    return months


def cumulative_breakdown(months, capital=5000):
    """Show cumulative P&L at 1 month, 2 months, etc."""
    cumulative = []
    cum_pnl = 0
    for i, m in enumerate(months):
        cum_pnl += m["pnl"]
        cumulative.append({
            "period": f"{i+1} month{'s' if i > 0 else ''}",
            "month": m["month"],
            "monthly_pnl": m["pnl"],
            "monthly_pct": m["return_pct"],
            "cum_pnl": cum_pnl,
            "cum_pct": cum_pnl / capital * 100,
            "balance": capital + cum_pnl,
        })
    return cumulative


def main():
    print("=" * 80)
    print("  MONTHLY P&L BREAKDOWN")
    print("  Strategy: MACD(12,26,9) + RSI(14) + 4H Trend Filter · 2x · $5,000")
    print("=" * 80)

    df = fetch_klines("BTCUSDT", "15m", 180)
    date_start = df["timestamp"].iloc[0].strftime("%Y-%m-%d")
    date_end = df["timestamp"].iloc[-1].strftime("%Y-%m-%d")
    print(f"  Period: {date_start} to {date_end}\n")

    # Compute HTF trend
    print("  Computing 4H trends...")
    htf_trend = compute_htf_trend(df, "4h")

    # Run both strategies
    print("  Running Baseline (15m only)...")
    baseline = run_backtest(df, sl_pct=0.05, use_mtf=False, label="Baseline (15m Only)")

    print("  Running Selected (15m + 4H MACD+RSI)...")
    selected = run_backtest(df, htf_trend=htf_trend, sl_pct=0.05, use_mtf=True, label="4H MACD+RSI Filter")

    capital = 5000

    # Monthly breakdown
    base_months = monthly_breakdown(baseline, capital)
    sel_months = monthly_breakdown(selected, capital)

    base_cum = cumulative_breakdown(base_months, capital)
    sel_cum = cumulative_breakdown(sel_months, capital)

    # ── Print Selected Strategy Monthly ──
    print(f"\n{'='*80}")
    print(f"  SELECTED STRATEGY: 15m + 4H MACD+RSI Filter")
    print(f"{'='*80}")
    print(f"  {'Month':<10} {'Trades':>7} {'WR%':>6} {'Monthly P&L':>13} {'Monthly %':>10} {'Cumulative':>12} {'Balance':>12}")
    print(f"  {'-'*75}")

    cum = 0
    for m in sel_months:
        cum += m["pnl"]
        print(f"  {m['month']:<10} {m['trades']:>7} {m['wr']:>5.1f}% {m['pnl']:>+12,.0f} {m['return_pct']:>+9.1f}% {cum:>+11,.0f} ${m['end_balance']:>10,.0f}")

    print(f"  {'-'*75}")
    print(f"  {'TOTAL':<10} {sum(m['trades'] for m in sel_months):>7} {'':>6} {sum(m['pnl'] for m in sel_months):>+12,.0f} {selected['total_pnl_pct']:>+9.1f}% {'':>12} ${selected['final_balance']:>10,.0f}")

    # ── Print Baseline Monthly ──
    print(f"\n{'='*80}")
    print(f"  BASELINE: 15m Only (no filter)")
    print(f"{'='*80}")
    print(f"  {'Month':<10} {'Trades':>7} {'WR%':>6} {'Monthly P&L':>13} {'Monthly %':>10} {'Cumulative':>12} {'Balance':>12}")
    print(f"  {'-'*75}")

    cum = 0
    for m in base_months:
        cum += m["pnl"]
        print(f"  {m['month']:<10} {m['trades']:>7} {m['wr']:>5.1f}% {m['pnl']:>+12,.0f} {m['return_pct']:>+9.1f}% {cum:>+11,.0f} ${m['end_balance']:>10,.0f}")

    print(f"  {'-'*75}")
    print(f"  {'TOTAL':<10} {sum(m['trades'] for m in base_months):>7} {'':>6} {sum(m['pnl'] for m in base_months):>+12,.0f} {baseline['total_pnl_pct']:>+9.1f}% {'':>12} ${baseline['final_balance']:>10,.0f}")

    # ── Cumulative comparison ──
    print(f"\n{'='*80}")
    print(f"  CUMULATIVE PROFIT: How much after N months?")
    print(f"  Starting Capital: $5,000")
    print(f"{'='*80}")
    print(f"  {'Period':<12} {'4H Filter P&L':>14} {'4H Balance':>12} {'Baseline P&L':>14} {'Base Balance':>13} {'Difference':>11}")
    print(f"  {'-'*78}")

    max_months = max(len(sel_cum), len(base_cum))
    for i in range(max_months):
        sc = sel_cum[i] if i < len(sel_cum) else None
        bc = base_cum[i] if i < len(base_cum) else None
        period = f"{i+1} month{'s' if i > 0 else ''}"
        s_pnl = sc["cum_pnl"] if sc else 0
        s_bal = sc["balance"] if sc else capital
        b_pnl = bc["cum_pnl"] if bc else 0
        b_bal = bc["balance"] if bc else capital
        diff = s_pnl - b_pnl
        print(f"  {period:<12} {s_pnl:>+13,.0f} ${s_bal:>10,.0f} {b_pnl:>+13,.0f} ${b_bal:>11,.0f} {diff:>+10,.0f}")

    print(f"  {'-'*78}")

    # ── Save results for HTML visualization ──
    output = {
        "period": f"{date_start} to {date_end}",
        "capital": capital,
        "selected": {
            "label": selected["label"],
            "monthly": sel_months,
            "cumulative": sel_cum,
            "total_pnl": selected["total_pnl"],
            "total_pct": selected["total_pnl_pct"],
            "final_balance": selected["final_balance"],
            "equity_curve": selected["equity_curve"],
        },
        "baseline": {
            "label": baseline["label"],
            "monthly": base_months,
            "cumulative": base_cum,
            "total_pnl": baseline["total_pnl"],
            "total_pct": baseline["total_pnl_pct"],
            "final_balance": baseline["final_balance"],
            "equity_curve": baseline["equity_curve"],
        },
    }

    with open("backtest_monthly_results.json", "w") as f:
        json.dump(output, f, indent=2, default=str)

    print(f"\n  Results saved to backtest_monthly_results.json")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()
