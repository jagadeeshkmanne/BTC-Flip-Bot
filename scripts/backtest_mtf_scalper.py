#!/usr/bin/env python3
"""Research-only multi-timeframe BTCUSDT scalper.

Uses a lower timeframe trigger with a higher timeframe trend filter:
  - base entries from 15m cached data
  - 1h EMA trend is forward-filled onto 15m candles
  - EMA reclaim/cross trigger after a pullback
  - ATR bracket stop/target with max-hold timeout
  - short and long variants; fees/slippage included; stop-first intrabar fills
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import pandas as pd


FEE_PCT = 0.00055
SLIP_PCT = 0.0005


@dataclass
class Trade:
    side: int
    entry_i: int
    exit_i: int
    entry: float
    exit: float
    stop: float
    target: float
    reason: str
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
    pc = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


def add_features(df: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    out = df.copy()
    out[f"{prefix}atr"] = atr(out, 14)
    out[f"{prefix}ema9"] = ema(out["close"], 9)
    out[f"{prefix}ema21"] = ema(out["close"], 21)
    out[f"{prefix}ema50"] = ema(out["close"], 50)
    out[f"{prefix}ema200"] = ema(out["close"], 200)
    out[f"{prefix}rsi14"] = rsi(out["close"], 14)
    out[f"{prefix}atr_pct"] = out[f"{prefix}atr"] / out["close"]
    return out


def attach_htf(base: pd.DataFrame, htf: pd.DataFrame) -> pd.DataFrame:
    cols = ["timestamp", "h_ema50", "h_ema200", "h_rsi14", "h_atr_pct"]
    return pd.merge_asof(
        base.sort_values("timestamp"),
        htf[cols].sort_values("timestamp"),
        on="timestamp",
        direction="backward",
    ).reset_index(drop=True)


def pct_return(side: int, entry: float, exit_px: float) -> float:
    if side == 1:
        entry_fill = entry * (1 + SLIP_PCT)
        exit_fill = exit_px * (1 - SLIP_PCT)
        gross = (exit_fill - entry_fill) / entry_fill
    else:
        entry_fill = entry * (1 - SLIP_PCT)
        exit_fill = exit_px * (1 + SLIP_PCT)
        gross = (entry_fill - exit_fill) / entry_fill
    return gross - 2 * FEE_PCT


def signal(df: pd.DataFrame, side: int, mode: str, trend: str, min_atr_pct: float) -> pd.Series:
    close = df["close"]
    reclaim_long = (close > df["ema9"]) & (close.shift(1) <= df["ema9"].shift(1)) & (df["rsi14"].shift(1) < 50)
    reclaim_short = (close < df["ema9"]) & (close.shift(1) >= df["ema9"].shift(1)) & (df["rsi14"].shift(1) > 50)
    cross_long = (df["ema9"] > df["ema21"]) & (df["ema9"].shift(1) <= df["ema21"].shift(1))
    cross_short = (df["ema9"] < df["ema21"]) & (df["ema9"].shift(1) >= df["ema21"].shift(1))

    if mode == "reclaim":
        sig = reclaim_long if side == 1 else reclaim_short
    elif mode == "cross":
        sig = cross_long if side == 1 else cross_short
    elif mode == "reclaim_or_cross":
        sig = (reclaim_long | cross_long) if side == 1 else (reclaim_short | cross_short)
    else:
        raise ValueError(mode)

    if trend == "none":
        trend_ok = pd.Series(True, index=df.index)
    elif trend == "h1_ema200":
        trend_ok = close > df["h_ema200"] if side == 1 else close < df["h_ema200"]
    elif trend == "h1_stack":
        trend_ok = (df["h_ema50"] > df["h_ema200"]) if side == 1 else (df["h_ema50"] < df["h_ema200"])
    elif trend == "h1_stack_rsi":
        trend_ok = ((df["h_ema50"] > df["h_ema200"]) & (df["h_rsi14"] > 50)) if side == 1 else (
            (df["h_ema50"] < df["h_ema200"]) & (df["h_rsi14"] < 50)
        )
    else:
        raise ValueError(trend)

    vol_ok = df["atr_pct"] >= min_atr_pct
    return (sig & trend_ok & vol_ok).fillna(False)


def backtest(
    df: pd.DataFrame,
    sig: pd.Series,
    *,
    side: int,
    rr: float,
    stop_atr: float,
    max_hold: int,
    cooldown: int,
) -> tuple[dict[str, float], list[Trade]]:
    cash = 1.0
    peak = 1.0
    max_dd = 0.0
    trades: list[Trade] = []
    next_i = 250
    for i in range(250, len(df) - 1):
        if i < next_i or not bool(sig.iloc[i]):
            continue
        entry_i = i + 1
        entry = float(df.loc[entry_i, "open"])
        atr_i = float(df.loc[i, "atr"])
        if side == 1:
            stop = entry - stop_atr * atr_i
            target = entry + rr * (entry - stop)
        else:
            stop = entry + stop_atr * atr_i
            target = entry - rr * (stop - entry)
        if stop <= 0:
            continue

        exit_i = min(len(df) - 1, entry_i + max_hold)
        exit_px = float(df.loc[exit_i, "close"])
        reason = "time"
        for j in range(entry_i, exit_i + 1):
            hi = float(df.loc[j, "high"])
            lo = float(df.loc[j, "low"])
            stop_hit = lo <= stop if side == 1 else hi >= stop
            target_hit = hi >= target if side == 1 else lo <= target
            if stop_hit:
                exit_i = j
                exit_px = stop
                reason = "stop"
                break
            if target_hit:
                exit_i = j
                exit_px = target
                reason = "target"
                break
        pnl_cash = cash * pct_return(side, entry, exit_px)
        cash += pnl_cash
        peak = max(peak, cash)
        max_dd = max(max_dd, (peak - cash) / peak)
        trades.append(Trade(side, entry_i, exit_i, entry, exit_px, stop, target, reason, pnl_cash))
        next_i = exit_i + cooldown

    gp = sum(t.pnl_cash for t in trades if t.pnl_cash > 0)
    gl = -sum(t.pnl_cash for t in trades if t.pnl_cash < 0)
    wins = sum(1 for t in trades if t.pnl_cash > 0)
    stats = {
        "trades": float(len(trades)),
        "win_rate": wins / len(trades) if trades else 0.0,
        "pf": gp / gl if gl else (float("inf") if gp else 0.0),
        "net_pct": (cash - 1) * 100,
        "max_dd_pct": max_dd * 100,
        "targets": float(sum(1 for t in trades if t.reason == "target")),
        "stops": float(sum(1 for t in trades if t.reason == "stop")),
        "times": float(sum(1 for t in trades if t.reason == "time")),
    }
    return stats, trades


def print_stats(name: str, stats: dict[str, float]) -> None:
    print(
        f"{name:38s} trades={stats['trades']:4.0f} win={stats['win_rate']*100:5.1f}% "
        f"PF={stats['pf']:.3f} net={stats['net_pct']:7.2f}% DD={stats['max_dd_pct']:6.2f}% "
        f"T/S/time={stats['targets']:.0f}/{stats['stops']:.0f}/{stats['times']:.0f}"
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", default="data/cache/BTCUSDT_15m_16000_bybit.csv")
    p.add_argument("--sweep", action="store_true")
    args = p.parse_args()

    base_raw = load_ohlcv(args.csv)
    base = add_features(base_raw)
    htf = add_features(resample_ohlcv(base_raw, "1h"), "h_")
    df = attach_htf(base, htf)
    print(f"data: {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]} base=15m htf=1h")

    rows = []
    modes = ("reclaim", "cross", "reclaim_or_cross") if args.sweep else ("reclaim",)
    trends = ("none", "h1_ema200", "h1_stack", "h1_stack_rsi") if args.sweep else ("h1_stack",)
    rrs = (1.0, 1.5, 2.0) if args.sweep else (1.5,)
    stops = (0.7, 1.0, 1.4) if args.sweep else (1.0,)
    vols = (0.0, 0.0015, 0.0025) if args.sweep else (0.0015,)
    for side in (1, -1):
        for mode in modes:
            for trend in trends:
                for rr in rrs:
                    for stop_atr in stops:
                        for min_atr_pct in vols:
                            sig = signal(df, side, mode, trend, min_atr_pct)
                            stats, trades = backtest(
                                df,
                                sig,
                                side=side,
                                rr=rr,
                                stop_atr=stop_atr,
                                max_hold=16,
                                cooldown=2,
                            )
                            side_name = "long" if side == 1 else "short"
                            name = f"{side_name} {mode} {trend} rr={rr:g} st={stop_atr:g} vol={min_atr_pct:g}"
                            rows.append((name, stats, trades))

    rows.sort(key=lambda x: (x[1]["pf"], x[1]["net_pct"]), reverse=True)
    for name, stats, trades in rows[:20 if args.sweep else len(rows)]:
        print_stats(name, stats)
        if not args.sweep:
            for t in trades[-5:]:
                print(f"  {df.loc[t.entry_i, 'timestamp']} -> {df.loc[t.exit_i, 'timestamp']} {t.reason:6s} pnl={t.pnl_cash*100:7.3f}%")


if __name__ == "__main__":
    main()
