#!/usr/bin/env python3
"""fleet_backtest.py — Side-by-side backtest of all 5 fleet bots.

Reproduces the production bot logic for v1, v2, v3, v4, v5:
  - RSI(9) ≤30/≥70 entries
  - 15m EMA20/EMA50 trend gate
  - GAP firmness filter (v2/v3/v4)
  - Anti-breakout filter — last 3 closes vs 5m BB, vol-spike (v3/v4)
  - Hour 12-13 UTC block (all)
  - ATR + 1h cumulative move filter (all, added 2026-06-06)
  - DCA: 2 legs @ 0.5% adverse (v1/v2/v3) or NO DCA (v4/v5)
  - SL: 1% from worst (DCA) or 0.5% from entry (no-DCA)
  - TP: 0.5% (L1) / 0.25% (DCA) adaptive

Bar-fill model (conservative):
  Each closed 5m bar after entry checks SL → DCA → TP using bar high/low.
  If bar spans both SL and TP: SL wins (pessimistic).
  Entries fire at next bar open after closed-bar RSI signal.

Usage:
  python3 fleet_backtest.py [--months 6] [--start YYYY-MM-DD]
"""
from __future__ import annotations
import argparse, os, sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "cache")


# ─── indicators ───
def rsi_series(s, n=9):
    d = s.diff()
    gain = d.clip(lower=0); loss = -d.clip(upper=0)
    ag = gain.ewm(alpha=1.0/n, min_periods=n, adjust=False).mean()
    al = loss.ewm(alpha=1.0/n, min_periods=n, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def bb(s, n=20, k=2.0):
    m = s.rolling(n).mean()
    sd = s.rolling(n).std(ddof=0)
    return m + k*sd, m, m - k*sd


def atr(df, n=14):
    h_l = df["high"] - df["low"]
    h_pc = (df["high"] - df["close"].shift(1)).abs()
    l_pc = (df["low"]  - df["close"].shift(1)).abs()
    tr = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
    return tr.rolling(n).mean()


# ─── bot config ───
@dataclass
class BotConfig:
    name: str
    # entry filters
    use_gap_filter:    bool = False    # v2/v3/v4
    use_anti_breakout: bool = False    # v3/v4
    gap_min_pct:       float = 0.0025
    bb_lookback:       int = 3
    vol_spike_mult:    float = 2.0
    # new fleet-wide filters
    atr_max_pct:       float = 0.60
    one_h_move_max:    float = 2.0
    blocked_hours:     tuple = (12, 13)
    # risk management
    dca_levels:        int = 2
    sl_pct:            float = 0.01    # 1% from worst (DCA) or from entry (no-DCA)
    dca_spacing:       float = 0.005
    tp_single:         float = 0.005
    tp_dca:            float = 0.0025
    leverage:          float = 3.0
    # cooldown after loss
    cooldown_bars:     int = 3         # 15 min on 5m


BOTS = [
    BotConfig(name="v1",
              use_gap_filter=False, use_anti_breakout=False,
              dca_levels=2, sl_pct=0.01),
    BotConfig(name="v2",
              use_gap_filter=True,  use_anti_breakout=False,
              dca_levels=2, sl_pct=0.01),
    BotConfig(name="v3",
              use_gap_filter=True,  use_anti_breakout=True,
              dca_levels=2, sl_pct=0.01),
    BotConfig(name="v4",
              use_gap_filter=True,  use_anti_breakout=True,
              dca_levels=1, sl_pct=0.005),
    BotConfig(name="v5",
              use_gap_filter=False, use_anti_breakout=False,
              dca_levels=1, sl_pct=0.005),
    # v5b — proposed: v2 entries (GAP filter) + no-DCA + tight SL
    BotConfig(name="v5b",
              use_gap_filter=True, use_anti_breakout=False,
              dca_levels=1, sl_pct=0.005),
]


# ─── simulator ───
@dataclass
class Trade:
    entry_t: datetime
    side: str
    entry_px: float
    qty: float
    legs: int = 1
    avg: float = 0.0
    worst: float = 0.0
    exit_t: datetime = None
    exit_px: float = 0.0
    reason: str = ""
    pnl: float = 0.0
    mfe: float = 0.0
    mae: float = 0.0


def simulate(df, cfg: BotConfig, initial_balance=5000.0, commission=0.0004):
    """Single-bot simulation. df must have all required columns precomputed."""
    bal = initial_balance
    peak = initial_balance
    pos = None
    trades = []
    cooldown_until = -1
    eq_curve = []

    for i in range(len(df)):
        bar = df.iloc[i]
        t = bar["timestamp"]
        o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]

        # ── manage open position using THIS bar's h/l ──
        if pos is not None:
            mfe_now = ((h if pos.side == "LONG" else o) - pos.avg) / pos.avg * 100 * (1 if pos.side == "LONG" else -1)
            mae_now = ((l if pos.side == "LONG" else h) - pos.avg) / pos.avg * 100 * (1 if pos.side == "LONG" else -1)
            pos.mfe = max(pos.mfe, mfe_now)
            pos.mae = min(pos.mae, mae_now)

            # SL price
            sl_px = pos.worst * (1 + cfg.sl_pct) if pos.side == "SHORT" else pos.worst * (1 - cfg.sl_pct)
            # TP price (adaptive)
            tp_pct = cfg.tp_single if pos.legs == 1 else cfg.tp_dca
            tp_px = pos.avg * (1 - tp_pct) if pos.side == "SHORT" else pos.avg * (1 + tp_pct)
            # DCA price (next leg if not max)
            dca_px = None
            if pos.legs < cfg.dca_levels:
                dca_px = pos.worst * (1 + cfg.dca_spacing) if pos.side == "SHORT" else pos.worst * (1 - cfg.dca_spacing)

            # Check fills in order: SL → DCA → TP (pessimistic)
            hit_sl  = (pos.side == "SHORT" and h >= sl_px) or (pos.side == "LONG" and l <= sl_px)
            hit_dca = dca_px and ((pos.side == "SHORT" and h >= dca_px) or (pos.side == "LONG" and l <= dca_px))
            hit_tp  = (pos.side == "SHORT" and l <= tp_px) or (pos.side == "LONG" and h >= tp_px)

            if hit_sl:
                # SL filled
                exit_px = sl_px
                gross = (pos.avg - exit_px) * pos.qty if pos.side == "SHORT" else (exit_px - pos.avg) * pos.qty
                fees = exit_px * pos.qty * commission
                net = gross - fees
                bal += net
                pos.exit_t = t; pos.exit_px = exit_px; pos.reason = "SL"; pos.pnl = net
                trades.append(pos)
                cooldown_until = i + cfg.cooldown_bars
                pos = None
            elif hit_dca:
                # L2 DCA fill
                fill_px = dca_px
                old_qty = pos.qty
                new_qty = old_qty  # equal-size leg
                pos.avg = (pos.avg * old_qty + fill_px * new_qty) / (old_qty + new_qty)
                pos.qty = old_qty + new_qty
                pos.worst = fill_px
                pos.legs += 1
                # Re-check TP at new avg + adaptive
                new_tp_pct = cfg.tp_dca
                new_tp_px = pos.avg * (1 - new_tp_pct) if pos.side == "SHORT" else pos.avg * (1 + new_tp_pct)
                if (pos.side == "SHORT" and l <= new_tp_px) or (pos.side == "LONG" and h >= new_tp_px):
                    exit_px = new_tp_px
                    gross = (pos.avg - exit_px) * pos.qty if pos.side == "SHORT" else (exit_px - pos.avg) * pos.qty
                    fees = exit_px * pos.qty * commission
                    net = gross - fees
                    bal += net
                    pos.exit_t = t; pos.exit_px = exit_px; pos.reason = "TP"; pos.pnl = net
                    trades.append(pos)
                    pos = None
            elif hit_tp:
                # TP filled (only if SL/DCA didn't fire first)
                exit_px = tp_px
                gross = (pos.avg - exit_px) * pos.qty if pos.side == "SHORT" else (exit_px - pos.avg) * pos.qty
                fees = exit_px * pos.qty * commission
                net = gross - fees
                bal += net
                pos.exit_t = t; pos.exit_px = exit_px; pos.reason = "TP"; pos.pnl = net
                trades.append(pos)
                pos = None

        # equity tracking
        peak = max(peak, bal)
        eq_curve.append(bal)

        # ── check entry signal (only if FLAT and not on cooldown) ──
        if pos is not None or i < cooldown_until:
            continue
        if i + 1 >= len(df):
            break  # no next bar to fill entry

        # RSI signal from this CLOSED bar
        rsi_val = bar.get("rsi")
        if pd.isna(rsi_val):
            continue
        sig = None
        if rsi_val <= 30: sig = "LONG"
        elif rsi_val >= 70: sig = "SHORT"
        if sig is None: continue

        # Hour filter
        if t.hour in cfg.blocked_hours: continue

        # 15m trend gate
        m15_t = bar.get("m15_trend")
        if pd.isna(m15_t): continue
        if (sig == "LONG" and m15_t != "UP") or (sig == "SHORT" and m15_t != "DOWN"): continue

        # GAP firmness filter (v2/v3/v4)
        if cfg.use_gap_filter:
            gap = bar.get("m15_gap_pct")
            if pd.isna(gap) or abs(gap) < cfg.gap_min_pct*100: continue

        # Anti-breakout filter (v3/v4)
        if cfg.use_anti_breakout:
            bb_up = bar.get("bb_up"); bb_lo = bar.get("bb_low")
            vol_sma = bar.get("vol_sma20"); vol_now = bar.get("volume")
            if pd.isna(bb_up) or pd.isna(bb_lo) or pd.isna(vol_sma): continue
            # last 3 closes (excluding current still-forming bar — here it IS the closed bar)
            if i < cfg.bb_lookback: continue
            last_closes = df["close"].iloc[i-cfg.bb_lookback+1:i+1]
            if sig == "SHORT" and last_closes.max() > bb_up: continue
            if sig == "LONG"  and last_closes.min() < bb_lo: continue
            if vol_sma > 0 and vol_now > vol_sma * cfg.vol_spike_mult: continue

        # ATR filter (NEW)
        atr_val = bar.get("atr_14")
        if pd.isna(atr_val) or atr_val <= 0: continue
        atr_pct = (atr_val / c) * 100
        if atr_pct > cfg.atr_max_pct: continue

        # 1h cumulative move filter (NEW)
        if i >= 12:
            close_1h = df["close"].iloc[i-12]
            chg_1h = (c / close_1h - 1) * 100
            if sig == "SHORT" and chg_1h > cfg.one_h_move_max: continue
            if sig == "LONG"  and chg_1h < -cfg.one_h_move_max: continue

        # ── all filters passed: open at NEXT bar's open ──
        next_o = df.iloc[i+1]["open"]
        qty = (bal * 0.95 * cfg.leverage) / next_o / cfg.dca_levels
        pos = Trade(entry_t=df.iloc[i+1]["timestamp"], side=sig, entry_px=next_o,
                    qty=qty, legs=1, avg=next_o, worst=next_o)

    # return metrics
    if not trades:
        return {"name": cfg.name, "trades": 0, "wins": 0, "losses": 0,
                "final": bal, "ret_pct": 0, "max_dd_pct": 0,
                "wr": 0, "pf": 0, "avg_win": 0, "avg_loss": 0,
                "max_win": 0, "max_loss": 0, "trades_list": []}

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    eq_curve = np.array(eq_curve)
    peaks = np.maximum.accumulate(eq_curve)
    dd = (eq_curve - peaks) / peaks * 100

    return {
        "name": cfg.name,
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "final": bal,
        "ret_pct": (bal/initial_balance - 1) * 100,
        "max_dd_pct": dd.min(),
        "wr": len(wins)/len(trades)*100 if trades else 0,
        "pf": (sum(t.pnl for t in wins) / abs(sum(t.pnl for t in losses))) if losses and sum(t.pnl for t in losses) < 0 else float('inf'),
        "avg_win": np.mean([t.pnl for t in wins]) if wins else 0,
        "avg_loss": np.mean([t.pnl for t in losses]) if losses else 0,
        "max_win": max([t.pnl for t in wins], default=0),
        "max_loss": min([t.pnl for t in losses], default=0),
        "trades_list": trades,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--start", type=str, default=None, help="explicit YYYY-MM-DD (overrides --months)")
    ap.add_argument("--balance", type=float, default=5000.0)
    args = ap.parse_args()

    # Determine start date
    if args.start:
        start = datetime.fromisoformat(args.start)
    else:
        start = datetime.utcnow() - timedelta(days=args.months*30)
    print(f"Backtest start: {start.strftime('%Y-%m-%d')}")

    # Load + filter 5m data
    df = pd.read_csv(os.path.join(CACHE, "BTCUSDT_5m.csv"))
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df[df["timestamp"] >= start].reset_index(drop=True)
    print(f"Loaded {len(df):,} 5m bars covering {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")

    # Compute all required indicators on 5m
    print("Computing indicators...")
    df["rsi"] = rsi_series(df["close"], 9)
    up, mid, lo = bb(df["close"], 20, 2.0)
    df["bb_up"], df["bb_mid"], df["bb_low"] = up, mid, lo
    df["vol_sma20"] = df["volume"].rolling(20).mean()
    df["atr_14"] = atr(df, 14)

    # 15m trend gate: aggregate 5m → 15m and project back
    df15 = df.set_index("timestamp").resample("15min").agg({"close": "last", "high": "max", "low": "min", "open": "first"}).dropna()
    df15["ema20"] = ema(df15["close"], 20)
    df15["ema50"] = ema(df15["close"], 50)
    df15["m15_gap_pct"] = (df15["ema20"] - df15["ema50"]) / df15["ema50"] * 100
    df15["m15_trend"] = np.where(df15["ema20"] > df15["ema50"], "UP", "DOWN")
    df15 = df15.reset_index()

    # Project 15m values back onto 5m bars (use the LAST CLOSED 15m bar as of each 5m bar)
    df = pd.merge_asof(df.sort_values("timestamp"),
                       df15[["timestamp", "m15_trend", "m15_gap_pct"]].sort_values("timestamp"),
                       on="timestamp", direction="backward")

    print(f"Running 5 bot simulations on $5,000 initial each...\n")

    # Run each bot
    results = []
    for cfg in BOTS:
        print(f"  {cfg.name}: simulating...", end=" ")
        r = simulate(df, cfg, initial_balance=args.balance)
        results.append(r)
        print(f"{r['trades']} trades, final ${r['final']:.2f} ({r['ret_pct']:+.2f}%)")

    # Comparison table
    print("\n═══ BACKTEST RESULTS ═══")
    print(f"Period: {df['timestamp'].iloc[0].date()} → {df['timestamp'].iloc[-1].date()} ({(df['timestamp'].iloc[-1]-df['timestamp'].iloc[0]).days} days)\n")
    print(f"{'Bot':<5} {'Trades':>7} {'WR%':>6} {'Final $':>10} {'Return%':>8} {'MaxDD%':>7} {'PF':>5} {'AvgW':>7} {'AvgL':>7} {'MaxW':>7} {'MaxL':>8}")
    print("─"*95)
    for r in results:
        pf_str = f"{r['pf']:.2f}" if r['pf'] != float('inf') else "inf"
        print(f"{r['name']:<5} {r['trades']:>7} {r['wr']:>5.1f}% ${r['final']:>9.2f} {r['ret_pct']:>+7.2f}% {r['max_dd_pct']:>6.2f}% {pf_str:>5} ${r['avg_win']:>5.2f} ${r['avg_loss']:>6.2f} ${r['max_win']:>5.2f} ${r['max_loss']:>+7.2f}")

    print("\n═══ Per-bot breakdown ═══")
    for r in results:
        print(f"\n{r['name']}: {r['trades']} trades, {r['wins']} wins, {r['losses']} losses")
        print(f"  Return: {r['ret_pct']:+.2f}%  |  Max DD: {r['max_dd_pct']:.2f}%  |  Win rate: {r['wr']:.1f}%")
        print(f"  Avg win: ${r['avg_win']:.2f}  |  Avg loss: ${r['avg_loss']:.2f}  |  PF: {r['pf']:.2f}")
        print(f"  Max win: ${r['max_win']:.2f}  |  Max loss: ${r['max_loss']:+.2f}")


if __name__ == "__main__":
    main()
