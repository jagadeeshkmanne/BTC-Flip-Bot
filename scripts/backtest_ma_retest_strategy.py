#!/usr/bin/env python3
"""Backtest a mechanical moving-average retest strategy on BTCUSDT.

This translates the attached MA lesson into testable rules:
- trend by fast/slow moving average alignment
- enter on a pullback/retest of the fast MA in the trend direction
- optional directional candle confirmation
- stop by ATR or recent swing, target by fixed R multiple

The script uses cached Bybit candles, closed-bar signals, next-open entries,
stop-first intrabar handling, and fees/slippage.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
FEE_PCT = 0.00055
SLIP_PCT = 0.00050


@dataclass(frozen=True)
class Config:
    tf: str
    ma_kind: str
    side: str
    fast: int
    slow: int
    confirm: str
    touch_atr: float
    stop_mode: str
    stop_atr: float
    swing: int
    rr: float
    max_hold: int
    cooldown: int


def load_ohlcv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="raise")
    return df


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = (
        df.set_index("timestamp")
        .resample(rule, label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
    return out


def ma(s: pd.Series, n: int, kind: str) -> pd.Series:
    if kind == "ema":
        return s.ewm(span=n, adjust=False).mean()
    if kind == "sma":
        return s.rolling(n).mean()
    raise ValueError(kind)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def add_features(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    out = df.copy()
    out["atr"] = atr(out, 14)
    out["fast_ma"] = ma(out["close"], cfg.fast, cfg.ma_kind)
    out["slow_ma"] = ma(out["close"], cfg.slow, cfg.ma_kind)
    out["swing_low"] = out["low"].rolling(cfg.swing).min().shift(1)
    out["swing_high"] = out["high"].rolling(cfg.swing).max().shift(1)
    return out


def signal_at(df: pd.DataFrame, i: int, cfg: Config) -> bool:
    row = df.iloc[i]
    prev = df.iloc[i - 1]
    if pd.isna(row.fast_ma) or pd.isna(row.slow_ma) or pd.isna(row.atr):
        return False
    if row.atr <= 0:
        return False

    long_side = cfg.side == "long"
    if long_side:
        trend = row.fast_ma > row.slow_ma and row.close > row.slow_ma
        touched = row.low <= row.fast_ma + cfg.touch_atr * row.atr
        reclaimed = row.close > row.fast_ma
        directional = row.close > row.open
        engulf = directional and row.close > prev.high
    else:
        trend = row.fast_ma < row.slow_ma and row.close < row.slow_ma
        touched = row.high >= row.fast_ma - cfg.touch_atr * row.atr
        reclaimed = row.close < row.fast_ma
        directional = row.close < row.open
        engulf = directional and row.close < prev.low

    if cfg.confirm == "none":
        confirm = True
    elif cfg.confirm == "directional":
        confirm = directional
    elif cfg.confirm == "break":
        confirm = engulf
    else:
        raise ValueError(cfg.confirm)

    return bool(trend and touched and reclaimed and confirm)


def make_signal(df: pd.DataFrame, cfg: Config) -> pd.Series:
    if cfg.side == "long":
        trend = (df["fast_ma"] > df["slow_ma"]) & (df["close"] > df["slow_ma"])
        touched = df["low"] <= df["fast_ma"] + cfg.touch_atr * df["atr"]
        reclaimed = df["close"] > df["fast_ma"]
        directional = df["close"] > df["open"]
        breakout = directional & (df["close"] > df["high"].shift(1))
    else:
        trend = (df["fast_ma"] < df["slow_ma"]) & (df["close"] < df["slow_ma"])
        touched = df["high"] >= df["fast_ma"] - cfg.touch_atr * df["atr"]
        reclaimed = df["close"] < df["fast_ma"]
        directional = df["close"] < df["open"]
        breakout = directional & (df["close"] < df["low"].shift(1))

    if cfg.confirm == "none":
        confirm = pd.Series(True, index=df.index)
    elif cfg.confirm == "directional":
        confirm = directional
    elif cfg.confirm == "break":
        confirm = breakout
    else:
        raise ValueError(cfg.confirm)
    return (trend & touched & reclaimed & confirm & (df["atr"] > 0)).fillna(False)


def pct_return(side: str, entry: float, exit_px: float) -> float:
    if side == "long":
        entry_fill = entry * (1 + SLIP_PCT)
        exit_fill = exit_px * (1 - SLIP_PCT)
        gross = (exit_fill - entry_fill) / entry_fill
    else:
        entry_fill = entry * (1 - SLIP_PCT)
        exit_fill = exit_px * (1 + SLIP_PCT)
        gross = (entry_fill - exit_fill) / entry_fill
    return gross - 2 * FEE_PCT


def stops(entry: float, row: pd.Series, cfg: Config) -> tuple[float, float] | None:
    long_side = cfg.side == "long"
    if cfg.stop_mode == "atr":
        stop = entry - cfg.stop_atr * row.atr if long_side else entry + cfg.stop_atr * row.atr
    elif cfg.stop_mode == "swing":
        if long_side:
            if pd.isna(row.swing_low):
                return None
            stop = min(row.swing_low, row.slow_ma) - 0.20 * row.atr
        else:
            if pd.isna(row.swing_high):
                return None
            stop = max(row.swing_high, row.slow_ma) + 0.20 * row.atr
    else:
        raise ValueError(cfg.stop_mode)

    risk = entry - stop if long_side else stop - entry
    if risk <= 0:
        return None
    target = entry + cfg.rr * risk if long_side else entry - cfg.rr * risk
    return float(stop), float(target)


def run_backtest(df_raw: pd.DataFrame, cfg: Config) -> dict[str, float]:
    df = add_features(df_raw, cfg)
    sig = make_signal(df, cfg).to_numpy()
    open_a = df["open"].to_numpy()
    high_a = df["high"].to_numpy()
    low_a = df["low"].to_numpy()
    close_a = df["close"].to_numpy()
    atr_a = df["atr"].to_numpy()
    slow_a = df["slow_ma"].to_numpy()
    swing_low_a = df["swing_low"].to_numpy()
    swing_high_a = df["swing_high"].to_numpy()
    cash = 1.0
    peak = 1.0
    max_dd = 0.0
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0
    trade_returns: list[float] = []
    next_allowed = max(cfg.slow, cfg.swing, 20) + 5

    for i in range(next_allowed, len(df) - 1):
        if i < next_allowed or not sig[i]:
            continue

        entry_i = i + 1
        entry = float(open_a[entry_i])
        if cfg.stop_mode == "atr":
            stop = entry - cfg.stop_atr * atr_a[i] if cfg.side == "long" else entry + cfg.stop_atr * atr_a[i]
        elif cfg.stop_mode == "swing":
            if cfg.side == "long":
                if pd.isna(swing_low_a[i]):
                    continue
                stop = min(swing_low_a[i], slow_a[i]) - 0.20 * atr_a[i]
            else:
                if pd.isna(swing_high_a[i]):
                    continue
                stop = max(swing_high_a[i], slow_a[i]) + 0.20 * atr_a[i]
        else:
            raise ValueError(cfg.stop_mode)
        risk = entry - stop if cfg.side == "long" else stop - entry
        if risk <= 0:
            continue
        target = entry + cfg.rr * risk if cfg.side == "long" else entry - cfg.rr * risk

        exit_i = min(len(df) - 1, entry_i + cfg.max_hold)
        exit_px = float(close_a[exit_i])
        for j in range(entry_i, exit_i + 1):
            hi = float(high_a[j])
            lo = float(low_a[j])
            if cfg.side == "long":
                stop_hit = lo <= stop
                target_hit = hi >= target
            else:
                stop_hit = hi >= stop
                target_hit = lo <= target
            if stop_hit:
                exit_i = j
                exit_px = stop
                break
            if target_hit:
                exit_i = j
                exit_px = target
                break

        ret = pct_return(cfg.side, entry, exit_px)
        pnl = cash * ret
        cash += pnl
        peak = max(peak, cash)
        max_dd = max(max_dd, (peak - cash) / peak)
        trade_returns.append(ret)
        if pnl > 0:
            wins += 1
            gross_profit += pnl
        else:
            losses += 1
            gross_loss -= pnl
        next_allowed = exit_i + cfg.cooldown

    trades = wins + losses
    years = max((df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days / 365.25, 1 / 365.25)
    cagr = cash ** (1 / years) - 1 if cash > 0 else -1.0
    return {
        "net_pct": (cash - 1) * 100,
        "cagr_pct": cagr * 100,
        "max_dd_pct": max_dd * 100,
        "trades": float(trades),
        "win_rate_pct": (wins / trades * 100) if trades else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else (99.0 if gross_profit > 0 else 0.0),
        "avg_trade_pct": (sum(trade_returns) / trades * 100) if trades else 0.0,
    }


def config_space(tf: str) -> list[Config]:
    if tf == "15m":
        ma_kinds = ["ema"]
        fasts = [10, 20]
        slows = [50, 100]
        touch_atrs = [0.20, 0.50]
        stop_atrs = [1.0]
        swings = [12, 32]
        max_holds = [32, 64]
        cooldown = 4
    elif tf == "1h":
        ma_kinds = ["ema"]
        fasts = [20, 34]
        slows = [50, 100, 200]
        touch_atrs = [0.25, 0.60]
        stop_atrs = [1.5]
        swings = [12, 24]
        max_holds = [48, 96]
        cooldown = 3
    elif tf == "4h":
        ma_kinds = ["ema", "sma"]
        fasts = [20, 50]
        slows = [50, 100, 200]
        touch_atrs = [0.30, 0.70]
        stop_atrs = [1.5]
        swings = [10, 20]
        max_holds = [24, 48]
        cooldown = 2
    else:
        raise ValueError(tf)

    configs: list[Config] = []
    for ma_kind, side, fast, slow, confirm, touch_atr, stop_mode, stop_atr, swing, rr, max_hold in product(
        ma_kinds,
        ["long", "short"],
        fasts,
        slows,
        ["none", "directional", "break"],
        touch_atrs,
        ["atr", "swing"],
        stop_atrs,
        swings,
        [2.0, 3.0],
        max_holds,
    ):
        if fast >= slow:
            continue
        configs.append(
            Config(
                tf=tf,
                ma_kind=ma_kind,
                side=side,
                fast=fast,
                slow=slow,
                confirm=confirm,
                touch_atr=touch_atr,
                stop_mode=stop_mode,
                stop_atr=stop_atr,
                swing=swing,
                rr=rr,
                max_hold=max_hold,
                cooldown=cooldown,
            )
        )
    return configs


def datasets() -> dict[str, pd.DataFrame]:
    one_h = load_ohlcv(ROOT / "data/cache/BTCUSDT_1h_12000_bybit.csv")
    fifteen = load_ohlcv(ROOT / "data/cache/BTCUSDT_15m_16000_bybit.csv")
    return {
        "15m": fifteen,
        "1h": one_h,
        "4h": resample_ohlcv(one_h, "4h"),
    }


def score(full: dict[str, float], ins: dict[str, float], oos: dict[str, float]) -> float:
    if min(full["trades"], ins["trades"], oos["trades"]) < 6:
        return -1e9
    pf_floor = min(full["profit_factor"], ins["profit_factor"], oos["profit_factor"])
    return (
        pf_floor * 100
        + oos["net_pct"]
        + full["net_pct"] * 0.25
        - max(0.0, full["max_dd_pct"] - 25.0) * 2
    )


def fmt_cfg(c: Config) -> str:
    return (
        f"{c.side} {c.ma_kind}{c.fast}/{c.slow} confirm={c.confirm} "
        f"touch={c.touch_atr} stop={c.stop_mode}:{c.stop_atr}/sw{c.swing} "
        f"rr={c.rr:g} hold={c.max_hold}"
    )


def main() -> None:
    all_data = datasets()
    print("MA retest strategy, BTCUSDT Bybit cached data")
    print("Costs: 0.055% fee each side + 0.05% slippage each side; entries next open; stop-first intrabar.\n")

    for tf, df in all_data.items():
        split = int(len(df) * 0.60)
        ins_df = df.iloc[:split].reset_index(drop=True)
        oos_df = df.iloc[split:].reset_index(drop=True)
        full_rows = []
        for cfg in config_space(tf):
            full = run_backtest(df, cfg)
            if full["trades"] < 8:
                continue
            rough_score = full["profit_factor"] * 100 + full["net_pct"] - max(0.0, full["max_dd_pct"] - 30.0)
            full_rows.append((rough_score, cfg, full))
        full_rows.sort(key=lambda x: x[0], reverse=True)

        rows = []
        for _, cfg, full in full_rows[:120]:
            ins = run_backtest(ins_df, cfg)
            oos = run_backtest(oos_df, cfg)
            rows.append((score(full, ins, oos), cfg, full, ins, oos))
        rows.sort(key=lambda x: x[0], reverse=True)

        print(
            f"=== {tf} bars={len(df)} {df.timestamp.iloc[0]} -> {df.timestamp.iloc[-1]} "
            f"(OOS starts {oos_df.timestamp.iloc[0]}) ==="
        )
        print(
            f"{'rank':>4} {'score':>8} {'cfg':<86} | "
            f"{'FULL net':>8} {'PF':>5} {'DD':>6} {'tr':>4} | "
            f"{'IS PF':>5} {'OOS net':>8} {'OOS PF':>6} {'OOS tr':>6}"
        )
        for rank, (sc, cfg, full, ins, oos) in enumerate(rows[:12], start=1):
            print(
                f"{rank:4d} {sc:8.1f} {fmt_cfg(cfg):<86} | "
                f"{full['net_pct']:8.2f} {full['profit_factor']:5.2f} {full['max_dd_pct']:6.2f} {full['trades']:4.0f} | "
                f"{ins['profit_factor']:5.2f} {oos['net_pct']:8.2f} {oos['profit_factor']:6.2f} {oos['trades']:6.0f}"
            )
        print()


if __name__ == "__main__":
    main()
