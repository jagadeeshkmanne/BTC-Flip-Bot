#!/usr/bin/env python3
"""
backtest_sr_dca.py — Support/Resistance with DCA on 1h.

Logic:
  - Support S = prior day's low; Resistance R = prior day's high (the classic pivot)
  - Daily bias: prior-day EMA50 direction (for which side to trade)
  - Long setup (daily bias bull):
      * L1: price touches S zone (within 0.2%) → 33% size
      * L2: price drops 1% below L1 entry → DCA +33%
      * L3: price drops another 1% → DCA +33%
      * TP: price reaches midpoint of prior day's range (or resistance)
      * SL: worst entry - 2% (close all)
  - Short setup: mirror
  - Max 1 position cycle per UTC day
  - Force flatten at UTC 23:00
"""
import os, sys, time
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import requests

LEVERAGE       = 2.0
RISK_PCT       = 0.05      # 5% total risk (balanced target from sweep)
DCA_LEVELS     = 3
DCA_SPACING    = 0.01      # 1% between DCA levels
SL_BELOW_WORST = 0.02      # 2% below worst entry
SUPPORT_ZONE   = 0.002     # 0.2% zone around S/R
TP_ABOVE_AVG   = 0.01      # +1% from AVG entry (after DCA) — new
USE_FIXED_TP   = True      # True = 1% from avg; False = prev_mid
CLOSE_HOUR     = 23
EMA_BIAS_LEN   = 50
COMMISSION_PCT = 0.0004
SLIPPAGE_PCT   = 0.000005

INITIAL_EQUITY = 5000.0
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "cache")


def fetch_klines(symbol: str, interval: str = "1h", start_date: datetime = None) -> pd.DataFrame:
    cache = os.path.join(CACHE_DIR, f"{symbol}_{interval}.csv")
    if os.path.exists(cache):
        return pd.read_csv(cache, parse_dates=["timestamp"])
    print(f"  {symbol}: fetching {interval}...")
    BASE = "https://fapi.binance.com/fapi/v1/klines"
    all_bars = []
    default_start = datetime(2024, 1, 1, tzinfo=timezone.utc) if interval in ("1m", "3m", "5m") else datetime(2019, 9, 1, tzinfo=timezone.utc)
    cursor = int((start_date or default_start).timestamp() * 1000)
    while True:
        params = {"symbol": symbol, "interval": interval, "limit": 1500, "startTime": cursor}
        r = requests.get(BASE, params=params, timeout=15)
        data = r.json() if r.status_code == 200 else []
        if not data: break
        all_bars.extend(data)
        if len(data) < 1500: break
        cursor = data[-1][0] + 1
        time.sleep(0.15)
    if not all_bars: return pd.DataFrame()
    df = pd.DataFrame(all_bars, columns=["ot","open","high","low","close","volume","ct","qav","trades","tbbav","tbqav","ig"])
    df = df.astype({"open":float,"high":float,"low":float,"close":float,"volume":float})
    df["timestamp"] = pd.to_datetime(df["ot"], unit="ms")
    df = df[["timestamp","open","high","low","close","volume"]]
    df.to_csv(cache, index=False)
    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().reset_index(drop=True)
    df["date"] = df["timestamp"].dt.normalize()
    df["utc_hour"] = df["timestamp"].dt.hour

    # Daily H/L/bias (prior day)
    d_idx = df.set_index("timestamp")
    daily = d_idx.resample("1D").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna()
    daily["ema50"] = daily["close"].ewm(span=EMA_BIAS_LEN, adjust=False).mean()
    daily["bias"] = np.where(daily["close"] > daily["ema50"], 1,
                    np.where(daily["close"] < daily["ema50"], -1, 0))
    # Prior day's values
    daily["prev_H"] = daily["high"].shift(1)
    daily["prev_L"] = daily["low"].shift(1)
    daily["prev_mid"] = (daily["prev_H"] + daily["prev_L"]) / 2.0
    daily["bias_prior"] = daily["bias"].shift(1).fillna(0).astype(int)

    bias_map = daily["bias_prior"].to_dict()
    ph_map = daily["prev_H"].to_dict()
    pl_map = daily["prev_L"].to_dict()
    pm_map = daily["prev_mid"].to_dict()

    df["bias_d"]  = df["date"].map(bias_map).fillna(0).astype(int)
    df["prev_H"]  = df["date"].map(ph_map)
    df["prev_L"]  = df["date"].map(pl_map)
    df["prev_mid"] = df["date"].map(pm_map)
    return df


def backtest(df: pd.DataFrame, label: str = "") -> dict:
    equity = INITIAL_EQUITY
    position = None     # {"side", "entries": [{"px": px, "qty": qty, ...}], "tp", "sl"}
    trades = []
    current_date = None
    cycle_done_today = False
    equity_curve = []

    for i in range(len(df)):
        bar = df.iloc[i]
        px = bar["close"]; hi = bar["high"]; lo = bar["low"]
        utc_h = int(bar["utc_hour"])
        bar_date = bar["date"]
        bias = int(bar["bias_d"])
        prev_H = bar["prev_H"]
        prev_L = bar["prev_L"]
        prev_mid = bar["prev_mid"]

        if bar_date != current_date:
            current_date = bar_date
            cycle_done_today = False

        if pd.isna(prev_H) or pd.isna(prev_L):
            equity_curve.append(equity); continue

        # ── Position management ──
        if position is not None:
            side = position["side"]
            entries = position["entries"]
            filled_count = len(entries)
            total_qty = sum(e["qty"] for e in entries)
            avg_entry = sum(e["px"] * e["qty"] for e in entries) / total_qty if total_qty > 0 else 0
            worst_entry = min(e["px"] for e in entries) if side == "LONG" else max(e["px"] for e in entries)

            # TP: either fixed 1% above AVG entry (stops for the day on hit), or prev_mid
            if USE_FIXED_TP:
                tp = avg_entry * (1 + TP_ABOVE_AVG) if side == "LONG" else avg_entry * (1 - TP_ABOVE_AVG)
            else:
                tp = position["tp"]
            # SL: below worst entry
            sl = worst_entry * (1 - SL_BELOW_WORST) if side == "LONG" else worst_entry * (1 + SL_BELOW_WORST)
            position["sl"] = sl  # update each bar based on current worst entry

            tp_hit = (hi >= tp) if side == "LONG" else (lo <= tp)
            sl_hit = (lo <= sl) if side == "LONG" else (hi >= sl)

            # DCA trigger: if we have <3 entries and price moved DCA_SPACING against us from last entry
            last_entry_px = entries[-1]["px"]
            dca_trigger = False
            if filled_count < DCA_LEVELS:
                if side == "LONG":
                    target_dca = last_entry_px * (1 - DCA_SPACING)
                    dca_trigger = lo <= target_dca
                    dca_price = min(target_dca, px)  # fill at target or worse
                else:
                    target_dca = last_entry_px * (1 + DCA_SPACING)
                    dca_trigger = hi >= target_dca
                    dca_price = max(target_dca, px)

            if sl_hit and not tp_hit:
                # Close everything at SL
                exit_price = sl
                fill = exit_price * (1 - SLIPPAGE_PCT) if side == "LONG" else exit_price * (1 + SLIPPAGE_PCT)
                pnl = sum(((fill - e["px"]) if side == "LONG" else (e["px"] - fill)) * e["qty"] for e in entries)
                pnl -= fill * total_qty * COMMISSION_PCT
                equity += pnl
                trades.append({"date": str(bar["timestamp"]), "side": side,
                               "entries": filled_count, "avg": avg_entry, "exit": fill,
                               "reason": "SL", "pnl": pnl})
                position = None
                cycle_done_today = True
            elif tp_hit:
                exit_price = tp
                fill = exit_price * (1 - SLIPPAGE_PCT) if side == "LONG" else exit_price * (1 + SLIPPAGE_PCT)
                pnl = sum(((fill - e["px"]) if side == "LONG" else (e["px"] - fill)) * e["qty"] for e in entries)
                pnl -= fill * total_qty * COMMISSION_PCT
                equity += pnl
                trades.append({"date": str(bar["timestamp"]), "side": side,
                               "entries": filled_count, "avg": avg_entry, "exit": fill,
                               "reason": "TP", "pnl": pnl})
                position = None
                cycle_done_today = True
            elif utc_h >= CLOSE_HOUR:
                fill = px * (1 - SLIPPAGE_PCT) if side == "LONG" else px * (1 + SLIPPAGE_PCT)
                pnl = sum(((fill - e["px"]) if side == "LONG" else (e["px"] - fill)) * e["qty"] for e in entries)
                pnl -= fill * total_qty * COMMISSION_PCT
                equity += pnl
                trades.append({"date": str(bar["timestamp"]), "side": side,
                               "entries": filled_count, "avg": avg_entry, "exit": fill,
                               "reason": "EOD", "pnl": pnl})
                position = None
                cycle_done_today = True
            elif dca_trigger:
                # Add DCA entry at trigger price
                dca_fill = dca_price * (1 + SLIPPAGE_PCT) if side == "LONG" else dca_price * (1 - SLIPPAGE_PCT)
                # per-level qty: sized so total risk if SL hits is RISK_PCT
                per_level_qty = position["per_level_qty"]
                equity -= dca_fill * per_level_qty * COMMISSION_PCT
                entries.append({"px": dca_fill, "qty": per_level_qty})
                position["entries"] = entries

        # ── Entry (first level only) ──
        if position is None and not cycle_done_today and utc_h < CLOSE_HOUR:
            want_long  = bias == 1  and lo <= prev_L * (1 + SUPPORT_ZONE) and lo > prev_L * (1 - 0.01)
            want_short = bias == -1 and hi >= prev_H * (1 - SUPPORT_ZONE) and hi < prev_H * (1 + 0.01)

            if want_long or want_short:
                side = "LONG" if want_long else "SHORT"
                # Entry at support/resistance zone
                entry_px = prev_L * (1 + SUPPORT_ZONE/2) if side == "LONG" else prev_H * (1 - SUPPORT_ZONE/2)
                entry_px *= (1 + SLIPPAGE_PCT) if side == "LONG" else (1 - SLIPPAGE_PCT)

                # Worst-case SL = entry - (DCA_LEVELS-1)*DCA_SPACING - SL_BELOW_WORST
                worst_sl_dist_pct = (DCA_LEVELS - 1) * DCA_SPACING + SL_BELOW_WORST
                # Per-level qty: risk/3 levels each at worst scenario
                # Simpler: total qty such that if SL hits worst case, loss ~ RISK_PCT
                # Total position notional = equity * RISK_PCT / worst_sl_dist_pct
                total_notional = equity * 0.95 * RISK_PCT / worst_sl_dist_pct
                per_level_qty = total_notional / entry_px / DCA_LEVELS

                qty_cap_total = (equity * 0.95 * LEVERAGE) / entry_px
                per_level_qty = min(per_level_qty, qty_cap_total / DCA_LEVELS)

                if per_level_qty > 0:
                    equity -= entry_px * per_level_qty * COMMISSION_PCT
                    # TP: prev day mid for long (bounce to middle), for short: mid
                    tp_price = prev_mid
                    position = {
                        "side": side,
                        "entries": [{"px": entry_px, "qty": per_level_qty}],
                        "tp": tp_price,
                        "per_level_qty": per_level_qty,
                    }

        equity_curve.append(equity)

    # Metrics
    eq = pd.Series(equity_curve)
    peak = eq.cummax()
    dd_series = (eq - peak) / peak
    max_dd = abs(dd_series.min()) if len(dd_series) else 0
    net_pct = (equity / INITIAL_EQUITY - 1) * 100
    wins = [t for t in trades if t["pnl"] > 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    pf = gw / gl if gl > 0 else float("inf")
    wr = len(wins) / len(trades) * 100 if trades else 0
    if len(df) >= 2:
        span_years = (df.iloc[-1]["timestamp"] - df.iloc[0]["timestamp"]).total_seconds() / (365.25 * 86400)
        cagr = ((equity / INITIAL_EQUITY) ** (1 / span_years) - 1) * 100 if span_years > 0 and equity > 0 else 0
    else:
        cagr = 0; span_years = 0
    calmar = cagr / (max_dd * 100) if max_dd > 0 else float("inf")

    # DCA depth stats
    avg_dca_depth = np.mean([t["entries"] for t in trades]) if trades else 0

    return {
        "label": label, "span_years": span_years,
        "net_pct": net_pct, "cagr": cagr, "max_dd": max_dd * 100, "calmar": calmar,
        "pf": pf, "trades": len(trades), "wr": wr,
        "trades_per_month": len(trades) / max(span_years * 12, 1),
        "avg_dca_depth": avg_dca_depth,
    }


def main():
    args = sys.argv[1:]
    interval = "1h"
    pairs = []
    start_date = None; end_date = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("5m", "15m", "1h", "4h"): interval = a
        elif a == "--from" and i + 1 < len(args): start_date = pd.Timestamp(args[i+1]); i += 1
        elif a == "--to" and i + 1 < len(args): end_date = pd.Timestamp(args[i+1]); i += 1
        else: pairs.append(a)
        i += 1
    if not pairs: pairs = ["ETHUSDT"]
    print(f"\n{'='*100}\nS/R DCA on {interval} (prior day H/L, {DCA_LEVELS} DCA levels @ {DCA_SPACING*100:.0f}% spacing, SL {SL_BELOW_WORST*100:.0f}% below worst, risk={RISK_PCT*100:.1f}%)")
    if start_date or end_date:
        print(f"  Date filter: {start_date} → {end_date}")
    print(f"{'='*100}")

    results = []
    for symbol in pairs:
        print(f"\n─── {symbol} {interval} ───")
        df = fetch_klines(symbol, interval)
        if df.empty: continue
        df = build_features(df)
        if start_date is not None:
            df = df[df["timestamp"] >= start_date].reset_index(drop=True)
        if end_date is not None:
            df = df[df["timestamp"] <= end_date].reset_index(drop=True)
        print(f"  bars: {len(df)} | {df.iloc[0]['timestamp']} → {df.iloc[-1]['timestamp']}")
        results.append(backtest(df, label=symbol))

    print(f"\n{'='*100}\nSUMMARY — S/R DCA 1h\n{'='*100}")
    print(f"{'Pair':<10} {'Span':>6} {'Net%':>10} {'CAGR%':>8} {'DD%':>7} {'Calmar':>8} {'PF':>6} {'Trades':>7} {'WR%':>6} {'T/mo':>6} {'AvgDCA':>7}")
    print("─" * 100)
    for r in results:
        print(f"{r['label']:<10} {r['span_years']:>5.1f}y {r['net_pct']:>+9.1f}% {r['cagr']:>+7.1f}% "
              f"{r['max_dd']:>6.1f}% {r['calmar']:>7.2f} {r['pf']:>5.2f} {r['trades']:>7} {r['wr']:>5.1f}% "
              f"{r['trades_per_month']:>5.1f} {r['avg_dca_depth']:>6.2f}")
    print("═" * 100)


if __name__ == "__main__":
    main()
