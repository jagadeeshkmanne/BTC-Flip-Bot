#!/usr/bin/env python3
"""Research-only BTCUSDT shooting star backtest.

Rules:
  - detect bearish shooting-star candles on 1H or resampled 4H data
  - optional uptrend filter before the pattern
  - short next executable 1H open after the signal candle closes
  - stop above the shooting-star high plus ATR buffer
  - target fixed RR; same-bar stop/target collision is booked as stop
  - fees and slippage are included on entry and exit
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import pandas as pd


FEE_PCT = 0.00055
SLIP_PCT = 0.0005


@dataclass
class Trade:
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry: float
    stop: float
    target: float
    exit: float
    reason: str
    r_mult: float
    pnl_cash: float


def load_ohlcv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="raise")
    return df


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (
        df.set_index("timestamp")
        .resample(rule, label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["atr"] = atr(out, 14)
    out["ema50"] = ema(out["close"], 50)
    out["ema200"] = ema(out["close"], 200)
    body_high = out[["open", "close"]].max(axis=1)
    body_low = out[["open", "close"]].min(axis=1)
    out["range"] = (out["high"] - out["low"]).replace(0, pd.NA)
    out["body"] = (out["close"] - out["open"]).abs()
    out["upper_wick"] = out["high"] - body_high
    out["lower_wick"] = body_low - out["low"]
    out["body_frac"] = out["body"] / out["range"]
    out["upper_frac"] = out["upper_wick"] / out["range"]
    out["lower_frac"] = out["lower_wick"] / out["range"]
    out["close_pos"] = (out["close"] - out["low"]) / out["range"]
    out["open_pos"] = (out["open"] - out["low"]) / out["range"]
    return out


def signal_mask(
    df: pd.DataFrame,
    *,
    upper_body_min: float,
    upper_frac_min: float,
    lower_frac_max: float,
    body_frac_max: float,
    close_pos_max: float,
    min_range_atr: float,
    require_bearish: bool,
    trend_mode: str,
    trend_bars: int,
) -> pd.Series:
    body = df["body"].clip(lower=1e-9)
    mask = (
        (df["upper_wick"] >= upper_body_min * body)
        & (df["upper_frac"] >= upper_frac_min)
        & (df["lower_frac"] <= lower_frac_max)
        & (df["body_frac"] <= body_frac_max)
        & (df["close_pos"] <= close_pos_max)
        & (df["range"] >= min_range_atr * df["atr"])
    )
    if require_bearish:
        mask &= df["close"] < df["open"]
    if trend_mode == "ema50":
        mask &= df["close"] > df["ema50"]
    elif trend_mode == "ema200":
        mask &= df["close"] > df["ema200"]
    elif trend_mode == "slope":
        mask &= df["close"] > df["close"].shift(trend_bars)
    elif trend_mode == "ema50_slope":
        mask &= (df["close"] > df["ema50"]) & (df["close"] > df["close"].shift(trend_bars))
    elif trend_mode == "none":
        pass
    else:
        raise ValueError(f"unknown trend_mode={trend_mode}")
    return mask.fillna(False)


def short_return(entry: float, exit_px: float) -> float:
    entry_fill = entry * (1 - SLIP_PCT)
    exit_fill = exit_px * (1 + SLIP_PCT)
    gross = (entry_fill - exit_fill) / entry_fill
    return gross - 2 * FEE_PCT


def backtest(
    base_1h: pd.DataFrame,
    signal_df: pd.DataFrame,
    signals: pd.Series,
    *,
    signal_hours: int,
    rr: float,
    stop_buffer_atr: float,
    max_hold_hours: int,
    risk_fraction: float,
    cooldown_hours: int,
) -> tuple[dict[str, float], list[Trade]]:
    cash = 1.0
    peak = 1.0
    max_dd = 0.0
    trades: list[Trade] = []
    next_allowed_time = base_1h["timestamp"].iloc[0]

    signal_rows = signal_df.loc[signals].copy()
    for _, sig in signal_rows.iterrows():
        signal_time = sig["timestamp"]
        entry_time = signal_time + pd.Timedelta(hours=signal_hours)
        if entry_time < next_allowed_time:
            continue
        entry_candidates = base_1h.index[base_1h["timestamp"] >= entry_time]
        if len(entry_candidates) == 0:
            continue
        entry_i = int(entry_candidates[0])
        if entry_i >= len(base_1h):
            continue

        entry = float(base_1h.loc[entry_i, "open"])
        stop = float(sig["high"]) + stop_buffer_atr * float(sig["atr"])
        if stop <= entry:
            continue
        risk = stop - entry
        target = entry - rr * risk

        max_exit_i = min(len(base_1h) - 1, entry_i + max_hold_hours)
        exit_px = float(base_1h.loc[max_exit_i, "close"])
        exit_i = max_exit_i
        reason = "time"
        for j in range(entry_i, max_exit_i + 1):
            hi = float(base_1h.loc[j, "high"])
            lo = float(base_1h.loc[j, "low"])
            stop_hit = hi >= stop
            target_hit = lo <= target
            if stop_hit:
                exit_px = stop
                exit_i = j
                reason = "stop"
                break
            if target_hit:
                exit_px = target
                exit_i = j
                reason = "target"
                break

        pct = short_return(entry, exit_px)
        pnl_cash = cash * risk_fraction * pct
        cash += pnl_cash
        peak = max(peak, cash)
        max_dd = max(max_dd, (peak - cash) / peak)
        r_mult = (entry - exit_px) / risk
        trades.append(
            Trade(
                signal_time=signal_time,
                entry_time=base_1h.loc[entry_i, "timestamp"],
                exit_time=base_1h.loc[exit_i, "timestamp"],
                entry=entry,
                stop=stop,
                target=target,
                exit=exit_px,
                reason=reason,
                r_mult=r_mult,
                pnl_cash=pnl_cash,
            )
        )
        next_allowed_time = base_1h.loc[exit_i, "timestamp"] + pd.Timedelta(hours=cooldown_hours)

    gross_profit = sum(t.pnl_cash for t in trades if t.pnl_cash > 0)
    gross_loss = -sum(t.pnl_cash for t in trades if t.pnl_cash < 0)
    wins = sum(1 for t in trades if t.pnl_cash > 0)
    stats = {
        "trades": float(len(trades)),
        "wins": float(wins),
        "win_rate": wins / len(trades) if trades else 0.0,
        "pf": gross_profit / gross_loss if gross_loss else (float("inf") if gross_profit else 0.0),
        "net_pct": (cash - 1.0) * 100,
        "max_dd_pct": max_dd * 100,
        "avg_r": sum(t.r_mult for t in trades) / len(trades) if trades else 0.0,
        "targets": float(sum(1 for t in trades if t.reason == "target")),
        "stops": float(sum(1 for t in trades if t.reason == "stop")),
        "times": float(sum(1 for t in trades if t.reason == "time")),
    }
    return stats, trades


def print_stats(name: str, stats: dict[str, float]) -> None:
    print(
        f"{name:30s} trades={stats['trades']:4.0f} win={stats['win_rate']*100:5.1f}% "
        f"PF={stats['pf']:.3f} net={stats['net_pct']:7.2f}% DD={stats['max_dd_pct']:6.2f}% "
        f"avgR={stats['avg_r']:6.2f} T/S/time={stats['targets']:.0f}/{stats['stops']:.0f}/{stats['times']:.0f}"
    )


def run_case(args: argparse.Namespace, tf: str, rr: float, trend_mode: str, stop_buffer_atr: float) -> tuple[str, dict[str, float], list[Trade]]:
    base_raw = load_ohlcv(args.csv)
    if args.start:
        base_raw = base_raw[base_raw["timestamp"] >= pd.Timestamp(args.start)]
    if args.end:
        base_raw = base_raw[base_raw["timestamp"] < pd.Timestamp(args.end)]
    base = add_features(base_raw.reset_index(drop=True))
    if tf == "1h":
        signal_df = base.copy()
        signal_hours = 1
    elif tf == "4h":
        signal_df = add_features(resample_ohlcv(base, "4h"))
        signal_hours = 4
    else:
        raise ValueError(tf)

    signals = signal_mask(
        signal_df,
        upper_body_min=args.upper_body_min,
        upper_frac_min=args.upper_frac_min,
        lower_frac_max=args.lower_frac_max,
        body_frac_max=args.body_frac_max,
        close_pos_max=args.close_pos_max,
        min_range_atr=args.min_range_atr,
        require_bearish=args.require_bearish,
        trend_mode=trend_mode,
        trend_bars=args.trend_bars,
    )
    stats, trades = backtest(
        base,
        signal_df,
        signals,
        signal_hours=signal_hours,
        rr=rr,
        stop_buffer_atr=stop_buffer_atr,
        max_hold_hours=args.max_hold_hours,
        risk_fraction=args.risk_fraction,
        cooldown_hours=args.cooldown_hours,
    )
    name = f"{tf} rr={rr:g} trend={trend_mode} buf={stop_buffer_atr:g}"
    return name, stats, trades


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", default="data/cache/BTCUSDT_1h_12000_bybit.csv")
    p.add_argument("--start", default="")
    p.add_argument("--end", default="")
    p.add_argument("--tf", choices=["1h", "4h", "both"], default="both")
    p.add_argument("--rr", type=float, default=2.0)
    p.add_argument("--sweep", action="store_true")
    p.add_argument("--max-hold-hours", type=int, default=72)
    p.add_argument("--cooldown-hours", type=int, default=0)
    p.add_argument("--risk-fraction", type=float, default=1.0)
    p.add_argument("--stop-buffer-atr", type=float, default=0.10)
    p.add_argument("--upper-body-min", type=float, default=2.0)
    p.add_argument("--upper-frac-min", type=float, default=0.55)
    p.add_argument("--lower-frac-max", type=float, default=0.25)
    p.add_argument("--body-frac-max", type=float, default=0.35)
    p.add_argument("--close-pos-max", type=float, default=0.45)
    p.add_argument("--min-range-atr", type=float, default=0.70)
    p.add_argument("--trend-mode", choices=["none", "ema50", "ema200", "slope", "ema50_slope"], default="ema50_slope")
    p.add_argument("--trend-bars", type=int, default=12)
    p.add_argument("--require-bearish", action=argparse.BooleanOptionalAction, default=True)
    args = p.parse_args()

    base = load_ohlcv(args.csv)
    if args.start:
        base = base[base["timestamp"] >= pd.Timestamp(args.start)]
    if args.end:
        base = base[base["timestamp"] < pd.Timestamp(args.end)]
    base = base.reset_index(drop=True)
    print(f"data: {base['timestamp'].iloc[0]} -> {base['timestamp'].iloc[-1]} source=1h")

    tfs = ["1h", "4h"] if args.tf == "both" else [args.tf]
    cases: list[tuple[str, dict[str, float], list[Trade]]] = []
    if args.sweep:
        for tf in tfs:
            for trend in ("none", "ema50", "slope", "ema50_slope"):
                for rr in (1.5, 2.0, 3.0):
                    for buf in (0.05, 0.15, 0.30):
                        cases.append(run_case(args, tf, rr, trend, buf))
    else:
        for tf in tfs:
            cases.append(run_case(args, tf, args.rr, args.trend_mode, args.stop_buffer_atr))

    cases.sort(key=lambda x: (x[1]["pf"], x[1]["net_pct"]), reverse=True)
    for name, stats, trades in cases[:20 if args.sweep else len(cases)]:
        print_stats(name, stats)
        if not args.sweep and trades:
            for t in trades[-5:]:
                print(f"  {t.signal_time} entry={t.entry_time} exit={t.exit_time} {t.reason:6s} R={t.r_mult:6.2f} pnl={t.pnl_cash*100:7.3f}%")


if __name__ == "__main__":
    main()
