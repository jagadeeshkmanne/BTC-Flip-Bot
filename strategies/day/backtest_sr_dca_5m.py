#!/usr/bin/env python3
"""
backtest_sr_dca_5m.py — faithful Python simulation of strategy_sr_dca_5m.pine.

Uses core.py (which matches Pine defaults) and walks each 5m bar with the
same order-of-operations Pine uses: SL → TP → EOD → DCA → L1 entry.

Run:
  python3 backtest_sr_dca_5m.py BTCUSDT
  python3 backtest_sr_dca_5m.py BTCUSDT --from 2026-03-16 --to 2026-04-23
  python3 backtest_sr_dca_5m.py BTCUSDT ETHUSDT --from 2025-05-01
"""
import os, sys
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core as C

INITIAL_EQUITY = 5000.0
COMMISSION_PCT = 0.0004   # 0.04% per fill (matches Pine)
SLIPPAGE_PCT   = 0.000005 # ~0.5bp — Pine uses 3 ticks, ~similar for crypto

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "cache")


def load_data(symbol: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    df5 = pd.read_csv(os.path.join(CACHE_DIR, f"{symbol}_5m.csv"), parse_dates=["timestamp"])
    d1_path = os.path.join(CACHE_DIR, f"{symbol}_1d.csv")
    if os.path.exists(d1_path):
        d1 = pd.read_csv(d1_path, parse_dates=["timestamp"])
    else:
        # Resample from 5m
        r = df5.set_index("timestamp").resample("1D").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna().reset_index()
        d1 = r
    return df5, d1


def backtest(symbol: str, start=None, end=None, verbose=False) -> dict:
    df5, d1 = load_data(symbol)
    df = C.build_features(df5, d1)
    if start is not None:
        df = df[df["timestamp"] >= pd.Timestamp(start)].reset_index(drop=True)
    if end is not None:
        df = df[df["timestamp"] <= pd.Timestamp(end)].reset_index(drop=True)
    if df.empty:
        return {"label": symbol, "error": "no data in window"}

    equity = INITIAL_EQUITY
    position = None   # {"side", "entries":[{px,qty}], "first_entry", "worst_entry", "per_level_qty"}
    pending = None    # {"side", "target_px", "placed_date"} — persistent limit order
    trades = []       # per-cycle summary
    legs = []         # per-leg fills (for TV-style counting)
    cycle_done_on_day = None
    equity_curve = []
    last_date = None

    for i in range(len(df)):
        bar = df.iloc[i]
        ts = bar["timestamp"]
        bar_date = bar["date"]
        hi = float(bar["high"]); lo = float(bar["low"]); px = float(bar["close"])
        utc_h = int(bar["utc_hour"])

        # New-day reset: clear stale pending order + cycle marker
        if last_date is not None and bar_date != last_date:
            pending = None
            cycle_done_on_day = None
        last_date = bar_date

        # ── 1. Check if existing pending limit fills on this bar ──
        if pending is not None and position is None:
            side = pending["side"]; target = pending["target_px"]
            fillable = (lo <= target) if side == "LONG" else (hi >= target)
            if fillable:
                fill_px = target * (1 + SLIPPAGE_PCT) if side == "LONG" else target * (1 - SLIPPAGE_PCT)
                q = C.per_level_qty(equity, fill_px)
                if q > 0:
                    equity -= fill_px * q * COMMISSION_PCT
                    position = {
                        "side": side,
                        "entries": [{"px": fill_px, "qty": q, "leg": "L1", "entry_ts": ts}],
                        "first_entry": fill_px,
                        "worst_entry": fill_px,
                        "per_level_qty": q,
                        "open_ts": ts,
                    }
                pending = None

        # ── 2. Position management (SL → TP → EOD → DCA) ──
        if position is not None:
            side = position["side"]
            entries = position["entries"]
            filled = len(entries)
            total_qty = sum(e["qty"] for e in entries)
            avg_entry = sum(e["px"] * e["qty"] for e in entries) / total_qty

            sl = C.sl_price(side, position["worst_entry"])
            tp = C.tp_price(side, bar["prev_mid"])

            # Suppress SL/TP on the same bar as the L1 entry (Pine wouldn't see them trigger
            # at the same close event that just filled the entry).
            is_entry_bar = (entries[0]["entry_ts"] == ts and filled == 1)

            if is_entry_bar:
                sl_hit = False; tp_hit = False
            else:
                sl_hit = (lo <= sl) if side == "LONG" else (hi >= sl)
                tp_hit = (hi >= tp) if side == "LONG" else (lo <= tp)

            exit_reason = None; exit_px = None
            if sl_hit and not tp_hit:
                exit_reason = "SL"; exit_px = sl
            elif tp_hit and not sl_hit:
                exit_reason = "TP"; exit_px = tp
            elif sl_hit and tp_hit:
                exit_reason = "SL"; exit_px = sl

            if exit_reason is None and utc_h >= C.CLOSE_HOUR:
                exit_reason = "EOD"; exit_px = px

            if exit_reason is not None:
                fill = exit_px * (1 - SLIPPAGE_PCT) if side == "LONG" else exit_px * (1 + SLIPPAGE_PCT)
                pnl = sum(((fill - e["px"]) if side == "LONG" else (e["px"] - fill)) * e["qty"] for e in entries)
                pnl -= fill * total_qty * COMMISSION_PCT
                equity += pnl
                trades.append({
                    "ts": ts, "open_ts": position["open_ts"], "side": side, "entries": filled,
                    "avg_entry": avg_entry, "exit": fill, "reason": exit_reason, "pnl": pnl,
                    "equity_after": equity,
                })
                # Record each leg separately for TV-style counting
                for e in entries:
                    leg_pnl = ((fill - e["px"]) if side == "LONG" else (e["px"] - fill)) * e["qty"]
                    leg_pnl -= (fill * e["qty"] + e["px"] * e["qty"]) * (COMMISSION_PCT / 2)  # approx
                    legs.append({"entry_ts": e["entry_ts"], "exit_ts": ts, "side": side,
                                 "leg": e["leg"], "entry_px": e["px"], "exit_px": fill,
                                 "pnl": leg_pnl, "reason": exit_reason})
                position = None
                cycle_done_on_day = bar_date
            else:
                if filled < C.DCA_LEVELS:
                    next_dca = C.dca_price(side, position["worst_entry"])
                    dca_hit = (lo <= next_dca) if side == "LONG" else (hi >= next_dca)
                    if dca_hit:
                        q = position["per_level_qty"]
                        fill = next_dca * (1 + SLIPPAGE_PCT) if side == "LONG" else next_dca * (1 - SLIPPAGE_PCT)
                        equity -= fill * q * COMMISSION_PCT
                        entries.append({"px": fill, "qty": q, "leg": f"L{filled+1}", "entry_ts": ts})
                        position["worst_entry"] = next_dca

        # ── 3. Cancel pending if we're past close-hour ──
        if pending is not None and utc_h >= C.CLOSE_HOUR:
            pending = None

        # ── 4. Signal check — place new pending (and check same-bar fill) ──
        if position is None and pending is None and cycle_done_on_day != bar_date and utc_h < C.CLOSE_HOUR:
            sig = C.evaluate_signal(df, i)
            if sig.side is not None:
                side = sig.side
                prev_h = float(bar["prev_H"]); prev_l = float(bar["prev_L"])
                entry_target = C.entry_price_zone(side, prev_h, prev_l)
                pending = {"side": side, "target_px": entry_target, "placed_date": bar_date}

                # Pine fills same-bar if bar's range crossed the limit
                fillable = (lo <= entry_target) if side == "LONG" else (hi >= entry_target)
                if fillable:
                    fill_px = entry_target * (1 + SLIPPAGE_PCT) if side == "LONG" else entry_target * (1 - SLIPPAGE_PCT)
                    q = C.per_level_qty(equity, fill_px)
                    if q > 0:
                        equity -= fill_px * q * COMMISSION_PCT
                        position = {
                            "side": side,
                            "entries": [{"px": fill_px, "qty": q, "leg": "L1", "entry_ts": ts}],
                            "first_entry": fill_px,
                            "worst_entry": fill_px,
                            "per_level_qty": q,
                            "open_ts": ts,
                        }
                    pending = None

        equity_curve.append(equity)

    # ── Metrics ──
    eq = pd.Series(equity_curve)
    peak = eq.cummax()
    max_dd = abs(((eq - peak) / peak).min()) if len(eq) else 0
    net_pct = (equity / INITIAL_EQUITY - 1) * 100
    wins = [t for t in trades if t["pnl"] > 0]
    gw = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    pf = gw / gl if gl > 0 else float("inf")
    wr = 100 * len(wins) / len(trades) if trades else 0
    span_years = (df.iloc[-1]["timestamp"] - df.iloc[0]["timestamp"]).total_seconds() / (365.25 * 86400)
    cagr = ((equity / INITIAL_EQUITY) ** (1 / span_years) - 1) * 100 if span_years > 0 and equity > 0 else 0

    by_reason = {}
    for t in trades:
        by_reason.setdefault(t["reason"], []).append(t["pnl"])

    # Leg-level metrics (matches TV counting)
    leg_wins = [l for l in legs if l["pnl"] > 0]
    leg_wr = 100 * len(leg_wins) / len(legs) if legs else 0

    return {
        "label": symbol,
        "span_years": span_years,
        "start": df.iloc[0]["timestamp"], "end": df.iloc[-1]["timestamp"],
        "final_equity": equity,
        "net_pct": net_pct, "cagr": cagr, "max_dd_pct": max_dd * 100,
        "calmar": cagr / (max_dd * 100) if max_dd > 0 else float("inf"),
        "pf": pf, "cycles": len(trades), "wr": wr,
        "legs": len(legs), "leg_wr": leg_wr,
        "by_reason": {k: (len(v), sum(v)) for k, v in by_reason.items()},
        "trade_list": trades, "leg_list": legs,
    }


def print_result(r: dict):
    print(f"\n─── {r['label']} ───")
    print(f"  window:   {r['start']} → {r['end']}  ({r['span_years']:.2f}y)")
    print(f"  equity:   ${INITIAL_EQUITY:,.0f} → ${r['final_equity']:,.2f}")
    print(f"  net:      {r['net_pct']:+.2f}%    CAGR: {r['cagr']:+.2f}%")
    print(f"  max DD:   {r['max_dd_pct']:.2f}%    Calmar: {r['calmar']:.2f}")
    print(f"  PF:       {r['pf']:.2f}    cycles: {r['cycles']}    legs: {r['legs']}")
    print(f"  WR:       cycle {r['wr']:.1f}%   leg {r['leg_wr']:.1f}%")
    if r["by_reason"]:
        parts = [f"{k}={n} ({pnl:+.0f})" for k, (n, pnl) in r["by_reason"].items()]
        print(f"  by exit:  {'  '.join(parts)}")


def main():
    args = sys.argv[1:]
    pairs = []; start = None; end = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--from" and i + 1 < len(args): start = args[i+1]; i += 1
        elif a == "--to" and i + 1 < len(args): end = args[i+1]; i += 1
        else: pairs.append(a)
        i += 1
    if not pairs: pairs = ["BTCUSDT"]

    print(f"{'='*78}")
    print(f"S/R DCA 5m Day Trader — Python port of strategy_sr_dca_5m.pine")
    print(f"  DCA={C.DCA_LEVELS}×{C.DCA_SPACING*100:.0f}%  risk={C.RISK_PCT*100:.0f}%  SL={C.SL_BELOW_WORST*100:.0f}% below worst")
    print(f"  filters: vol ≥{C.VOL_MULT}× avg, RSI {C.RSI_LOW}-{C.RSI_HIGH}")
    print(f"  bias: 1h EMA{C.EMA_BIAS_LEN}")
    if start or end: print(f"  window:  {start} → {end}")
    print(f"{'='*78}")

    results = []
    for s in pairs:
        r = backtest(s, start=start, end=end)
        if "error" in r:
            print(f"{s}: {r['error']}"); continue
        results.append(r)
        print_result(r)

    if len(results) > 1:
        print(f"\n{'='*78}\nSUMMARY\n{'='*78}")
        print(f"{'Pair':<10}{'Net%':>10}{'DD%':>8}{'Calmar':>9}{'PF':>7}{'Cycles':>8}{'Legs':>6}{'lWR%':>7}")
        for r in results:
            print(f"{r['label']:<10}{r['net_pct']:>+9.2f}%{r['max_dd_pct']:>7.2f}%"
                  f"{r['calmar']:>8.2f}{r['pf']:>7.2f}{r['cycles']:>8}{r['legs']:>6}{r['leg_wr']:>6.1f}%")


if __name__ == "__main__":
    main()
