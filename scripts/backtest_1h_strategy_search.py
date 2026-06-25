#!/usr/bin/env python3
"""Research-only 1H BTCUSDT strategy search.

Uses the cached 1H Bybit data and tests a small set of mechanical strategy
families with honest next-open fills, fees, slippage, stop-first intrabar fills,
and a chronological 60/40 OOS split.
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


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = ((up > down) & (up > 0)) * up.clip(lower=0)
    minus_dm = ((down > up) & (down > 0)) * down.clip(lower=0)
    a = atr(df, n)
    plus_di = 100 * ema(plus_dm, n) / a
    minus_di = 100 * ema(minus_dm, n) / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-9)
    return ema(dx, n)


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["atr"] = atr(out, 14)
    out["atr_pct"] = out["atr"] / out["close"]
    out["ema9"] = ema(out["close"], 9)
    out["ema21"] = ema(out["close"], 21)
    out["ema50"] = ema(out["close"], 50)
    out["ema100"] = ema(out["close"], 100)
    out["ema200"] = ema(out["close"], 200)
    out["rsi14"] = rsi(out["close"], 14)
    out["adx14"] = adx(out, 14)
    mid = out["close"].rolling(20).mean()
    sd = out["close"].rolling(20).std()
    out["bb_mid"] = mid
    out["bb_low"] = mid - 2 * sd
    out["bb_high"] = mid + 2 * sd
    out["vol_ratio"] = out["volume"] / out["volume"].rolling(50).mean()
    return out


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


def make_signal(df: pd.DataFrame, family: str, side: int, p1: int, p2: float, p3: float) -> pd.Series:
    c = df["close"]
    if family == "ema_cross":
        fast = ema(c, p1)
        slow = ema(c, int(p2))
        sig = (fast > slow) & (fast.shift(1) <= slow.shift(1)) if side == 1 else (
            (fast < slow) & (fast.shift(1) >= slow.shift(1))
        )
        trend = c > df["ema200"] if side == 1 else c < df["ema200"]
        return (sig & trend & (df["adx14"] >= p3)).fillna(False)

    if family == "trend_pullback":
        if side == 1:
            trend = (df["ema50"] > df["ema200"]) & (c > df["ema200"])
            pullback = (df["rsi14"].shift(1) <= p2) & (df["rsi14"] > p2) & (c > df["ema21"])
        else:
            trend = (df["ema50"] < df["ema200"]) & (c < df["ema200"])
            pullback = (df["rsi14"].shift(1) >= 100 - p2) & (df["rsi14"] < 100 - p2) & (c < df["ema21"])
        return (trend & pullback & (df["atr_pct"] >= p3)).fillna(False)

    if family == "donchian":
        hi = df["high"].rolling(p1).max().shift(1)
        lo = df["low"].rolling(p1).min().shift(1)
        if side == 1:
            sig = c > hi
            trend = df["ema50"] > df["ema200"]
        else:
            sig = c < lo
            trend = df["ema50"] < df["ema200"]
        return (sig & trend & (df["adx14"] >= p2) & (df["atr_pct"] >= p3)).fillna(False)

    if family == "bb_revert":
        if side == 1:
            sig = (c <= df["bb_low"]) & (df["rsi14"] <= p2)
            trend = c > df["ema200"] if p3 > 0 else pd.Series(True, index=df.index)
        else:
            sig = (c >= df["bb_high"]) & (df["rsi14"] >= 100 - p2)
            trend = c < df["ema200"] if p3 > 0 else pd.Series(True, index=df.index)
        return (sig & trend).fillna(False)

    if family == "range_breakout":
        mid = df["close"].rolling(p1).mean()
        width = (df["high"].rolling(p1).max() - df["low"].rolling(p1).min()) / c
        compressed = width.shift(1) <= p2
        if side == 1:
            sig = c > df["high"].rolling(p1).max().shift(1)
        else:
            sig = c < df["low"].rolling(p1).min().shift(1)
        return (sig & compressed & (c > mid if side == 1 else c < mid)).fillna(False)

    raise ValueError(family)


def run_backtest(
    df: pd.DataFrame,
    sig: pd.Series,
    *,
    side: int,
    stop_atr: float,
    rr: float,
    max_hold: int,
    cooldown: int,
) -> tuple[dict[str, float], list[Trade]]:
    cash = 1.0
    peak = 1.0
    max_dd = 0.0
    trades: list[Trade] = []
    next_i = 240
    for i in range(240, len(df) - 1):
        if i < next_i or not bool(sig.iloc[i]):
            continue
        entry_i = i + 1
        entry = float(df.loc[entry_i, "open"])
        a = float(df.loc[i, "atr"])
        if not a or pd.isna(a):
            continue
        if side == 1:
            stop = entry - stop_atr * a
            target = entry + rr * (entry - stop)
        else:
            stop = entry + stop_atr * a
            target = entry - rr * (stop - entry)

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
    return {
        "trades": float(len(trades)),
        "win_rate": wins / len(trades) if trades else 0.0,
        "pf": gp / gl if gl else (float("inf") if gp else 0.0),
        "net_pct": (cash - 1.0) * 100,
        "max_dd_pct": max_dd * 100,
        "targets": float(sum(1 for t in trades if t.reason == "target")),
        "stops": float(sum(1 for t in trades if t.reason == "stop")),
        "times": float(sum(1 for t in trades if t.reason == "time")),
    }, trades


def split_result(
    full: pd.DataFrame,
    is_df: pd.DataFrame,
    oos_df: pd.DataFrame,
    cfg: tuple,
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    family, side, p1, p2, p3, stop_atr, rr, max_hold, cooldown = cfg
    full_stats, _ = run_backtest(
        full,
        make_signal(full, family, side, p1, p2, p3),
        side=side,
        stop_atr=stop_atr,
        rr=rr,
        max_hold=max_hold,
        cooldown=cooldown,
    )
    is_stats, _ = run_backtest(
        is_df,
        make_signal(is_df, family, side, p1, p2, p3),
        side=side,
        stop_atr=stop_atr,
        rr=rr,
        max_hold=max_hold,
        cooldown=cooldown,
    )
    oos_stats, _ = run_backtest(
        oos_df,
        make_signal(oos_df, family, side, p1, p2, p3),
        side=side,
        stop_atr=stop_atr,
        rr=rr,
        max_hold=max_hold,
        cooldown=cooldown,
    )
    return full_stats, is_stats, oos_stats


def cfg_name(cfg: tuple) -> str:
    family, side, p1, p2, p3, stop_atr, rr, max_hold, cooldown = cfg
    side_name = "long" if side == 1 else "short"
    return f"{family} {side_name} p=({p1},{p2:g},{p3:g}) st={stop_atr:g} rr={rr:g} hold={max_hold}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/cache/BTCUSDT_1h_12000_bybit.csv")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()

    raw = load_ohlcv(args.csv)
    print(f"data: {raw.timestamp.iloc[0]} -> {raw.timestamp.iloc[-1]} bars={len(raw)} tf=1h")
    split = int(len(raw) * 0.6)
    full_df = add_features(raw)
    is_df = add_features(raw.iloc[:split].reset_index(drop=True))
    oos_df = add_features(raw.iloc[split:].reset_index(drop=True))

    cfgs: list[tuple] = []
    for side in (1, -1):
        for fast, slow in ((9, 21), (20, 50)) if args.quick else ((9, 21), (13, 34), (20, 50)):
            for adx_min in ((18,) if args.quick else (0, 18, 25)):
                for st in ((1.5,) if args.quick else (1.0, 1.5, 2.0)):
                    for rr in ((1.8, 2.5) if args.quick else (1.2, 1.8, 2.5)):
                        cfgs.append(("ema_cross", side, fast, slow, adx_min, st, rr, 72, 4))
        for rsi_level in ((45,) if args.quick else (40, 45, 50)):
            for atr_min in ((0.0015,) if args.quick else (0.0, 0.0015, 0.0025)):
                for st in ((1.5, 2.0) if args.quick else (1.0, 1.5, 2.0)):
                    for rr in ((1.8, 2.5) if args.quick else (1.2, 1.8, 2.5)):
                        cfgs.append(("trend_pullback", side, 0, rsi_level, atr_min, st, rr, 48, 4))
        for lookback in ((36, 55) if args.quick else (20, 36, 55, 80)):
            for adx_min in ((18, 25) if args.quick else (0, 18, 25)):
                for atr_min in ((0.0015,) if args.quick else (0.0, 0.0015)):
                    for st in ((2.0,) if args.quick else (1.5, 2.0, 2.5)):
                        for rr in ((2.2, 3.0) if args.quick else (1.5, 2.2, 3.0)):
                            cfgs.append(("donchian", side, lookback, adx_min, atr_min, st, rr, 96, 6))
        for rsi_extreme in ((25, 30) if args.quick else (20, 25, 30)):
            for trend_filter in ((0.0,) if args.quick else (0.0, 1.0)):
                for st in ((1.5,) if args.quick else (1.0, 1.5, 2.0)):
                    for rr in ((1.5, 2.0) if args.quick else (1.0, 1.5, 2.0)):
                        cfgs.append(("bb_revert", side, 0, rsi_extreme, trend_filter, st, rr, 36, 4))
        for lookback in ((20,) if args.quick else (12, 20, 36)):
            for width in ((0.04,) if args.quick else (0.025, 0.04, 0.06)):
                for st in ((1.5,) if args.quick else (1.0, 1.5, 2.0)):
                    for rr in ((1.8, 2.5) if args.quick else (1.2, 1.8, 2.5)):
                        cfgs.append(("range_breakout", side, lookback, width, 0.0, st, rr, 48, 4))

    rows = []
    for cfg in cfgs:
        full, ins, oos = split_result(full_df, is_df, oos_df, cfg)
        if full["trades"] < 20 or oos["trades"] < 8:
            continue
        rows.append((cfg, full, ins, oos))

    rows.sort(
        key=lambda x: (
            min(x[1]["pf"], x[2]["pf"], x[3]["pf"]),
            x[3]["net_pct"],
            x[1]["net_pct"],
        ),
        reverse=True,
    )

    print(
        f"{'strategy':62s} {'FULL PF':>7} {'FULL net':>9} {'F tr':>5} "
        f"{'IS PF':>7} {'IS net':>8} {'OOS PF':>7} {'OOS net':>8} {'O tr':>5}"
    )
    for cfg, full, ins, oos in rows[: args.top]:
        print(
            f"{cfg_name(cfg):62s} {full['pf']:7.3f} {full['net_pct']:9.2f} {full['trades']:5.0f} "
            f"{ins['pf']:7.3f} {ins['net_pct']:8.2f} {oos['pf']:7.3f} {oos['net_pct']:8.2f} {oos['trades']:5.0f}"
        )


if __name__ == "__main__":
    main()
