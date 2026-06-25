#!/usr/bin/env python3
"""Backtest a squeeze-runner strategy inspired by the pasted squeeze study.

The proprietary "slingshot squeeze" arrow is approximated with testable public
ingredients:
- TTM-style squeeze: Bollinger Bands inside Keltner Channels.
- Trend alignment: EMA fast > EMA slow > EMA trend for longs, mirror for shorts.
- Momentum turning: close-vs-SMA momentum rising in the trade direction.

Compares three entry behaviors:
- trigger: enter a starter immediately on the trigger.
- pullback: wait for a shallow 0.5 ATR pullback before entering.
- starter_add: half at trigger, add half on shallow pullback.

Deep pullback > 2 ATR exits early, matching the video's claim that deep pullbacks
are a warning rather than a better discount.
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
    coin: str
    mode: str
    side: str
    bb_len: int
    bb_std: float
    kc_mult: float
    ema_fast: int
    ema_slow: int
    ema_trend: int
    pullback_atr: float
    fail_atr: float
    max_hold: int
    max_wait: int


def load_ohlcv(symbol: str) -> pd.DataFrame:
    path = ROOT / f"data/cache/{symbol}_4h_2019_binance.csv"
    if not path.exists():
        raise FileNotFoundError(f"missing cache {path}; run walkforward_all_weather_audit.py first")
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
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


def add_features(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    out = df.copy()
    out["atr"] = atr(out, 14)
    mid = out["close"].rolling(cfg.bb_len).mean()
    sd = out["close"].rolling(cfg.bb_len).std()
    out["bb_up"] = mid + cfg.bb_std * sd
    out["bb_dn"] = mid - cfg.bb_std * sd
    out["kc_up"] = mid + cfg.kc_mult * out["atr"]
    out["kc_dn"] = mid - cfg.kc_mult * out["atr"]
    out["squeeze"] = (out["bb_up"] < out["kc_up"]) & (out["bb_dn"] > out["kc_dn"])
    out["ema_fast"] = ema(out["close"], cfg.ema_fast)
    out["ema_slow"] = ema(out["close"], cfg.ema_slow)
    out["ema_trend"] = ema(out["close"], cfg.ema_trend)
    out["mom"] = out["close"] - mid
    out["mom_chg"] = out["mom"].diff()
    return out


def make_signal(df: pd.DataFrame, cfg: Config) -> pd.Series:
    if cfg.side == "long":
        trend = (df["ema_fast"] > df["ema_slow"]) & (df["ema_slow"] > df["ema_trend"])
        momentum = (df["mom"] > 0) & (df["mom_chg"] > 0)
    else:
        trend = (df["ema_fast"] < df["ema_slow"]) & (df["ema_slow"] < df["ema_trend"])
        momentum = (df["mom"] < 0) & (df["mom_chg"] < 0)
    return (df["squeeze"] & trend & momentum & (df["atr"] > 0)).fillna(False)


def trade_return(side: str, entry: float, exit_px: float) -> float:
    if side == "long":
        entry_fill = entry * (1 + SLIP_PCT)
        exit_fill = exit_px * (1 - SLIP_PCT)
        gross = exit_fill / entry_fill - 1
    else:
        entry_fill = entry * (1 - SLIP_PCT)
        exit_fill = exit_px * (1 + SLIP_PCT)
        gross = entry_fill / exit_fill - 1
    return gross - 2 * FEE_PCT


def run_backtest(df_raw: pd.DataFrame, cfg: Config) -> dict[str, float]:
    df = add_features(df_raw, cfg)
    sig = make_signal(df, cfg).to_numpy()
    open_a = df["open"].to_numpy()
    high_a = df["high"].to_numpy()
    low_a = df["low"].to_numpy()
    close_a = df["close"].to_numpy()
    atr_a = df["atr"].to_numpy()
    squeeze_a = df["squeeze"].to_numpy()

    cash = 1.0
    peak = 1.0
    max_dd = 0.0
    trades = 0
    wins = 0
    gross_profit = 0.0
    gross_loss = 0.0
    heat_under_1 = 0
    heat_over_2 = 0
    start_i = max(cfg.bb_len, cfg.ema_trend, 50) + 5
    i = start_i

    while i < len(df) - 2:
        if not sig[i]:
            i += 1
            continue
        trigger_i = i
        trigger_close = float(close_a[trigger_i])
        a = float(atr_a[trigger_i])
        if a <= 0 or pd.isna(a):
            i += 1
            continue

        entry_i = trigger_i + 1
        entry_px = float(open_a[entry_i])
        size = 1.0 if cfg.mode == "trigger" else 0.0
        avg_entry = entry_px

        if cfg.mode == "starter_add":
            size = 0.5
            avg_entry = entry_px

        entered = size > 0
        add_done = cfg.mode != "starter_add"
        wait_end = min(len(df) - 1, trigger_i + cfg.max_wait)
        exit_end = min(len(df) - 1, trigger_i + cfg.max_hold)
        exit_i = exit_end
        exit_px = float(close_a[exit_i])

        adverse_max = 0.0
        for j in range(entry_i, exit_end + 1):
            if cfg.side == "long":
                adverse = max(0.0, trigger_close - float(low_a[j]))
                pullback_hit = float(low_a[j]) <= trigger_close - cfg.pullback_atr * a
                fail_hit = float(low_a[j]) <= trigger_close - cfg.fail_atr * a
            else:
                adverse = max(0.0, float(high_a[j]) - trigger_close)
                pullback_hit = float(high_a[j]) >= trigger_close + cfg.pullback_atr * a
                fail_hit = float(high_a[j]) >= trigger_close + cfg.fail_atr * a
            adverse_max = max(adverse_max, adverse / a)

            if not entered and cfg.mode == "pullback" and j <= wait_end and pullback_hit:
                avg_entry = trigger_close - cfg.pullback_atr * a if cfg.side == "long" else trigger_close + cfg.pullback_atr * a
                entered = True
                size = 1.0

            if entered and not add_done and pullback_hit:
                add_px = trigger_close - cfg.pullback_atr * a if cfg.side == "long" else trigger_close + cfg.pullback_atr * a
                avg_entry = (avg_entry * size + add_px * 0.5) / (size + 0.5)
                size += 0.5
                add_done = True

            if entered and fail_hit:
                exit_i = j
                exit_px = trigger_close - cfg.fail_atr * a if cfg.side == "long" else trigger_close + cfg.fail_atr * a
                break

            if entered and j > trigger_i + 1 and not bool(squeeze_a[j]):
                exit_i = min(j + 1, len(df) - 1)
                exit_px = float(open_a[exit_i])
                break

        if entered:
            ret = trade_return(cfg.side, avg_entry, exit_px) * size
            pnl = cash * ret
            cash += pnl
            trades += 1
            wins += int(pnl > 0)
            if pnl > 0:
                gross_profit += pnl
            else:
                gross_loss -= pnl
            if adverse_max < 1.0:
                heat_under_1 += 1
            if adverse_max >= 2.0:
                heat_over_2 += 1
            peak = max(peak, cash)
            max_dd = max(max_dd, (peak - cash) / peak)
        i = max(exit_i + 1, trigger_i + 2)

    years = max((df["timestamp"].iloc[-1] - df["timestamp"].iloc[start_i]).days / 365.25, 1 / 365.25)
    cagr = cash ** (1 / years) - 1 if cash > 0 else -1.0
    return {
        "net_pct": (cash - 1) * 100,
        "cagr_pct": cagr * 100,
        "max_dd_pct": max_dd * 100,
        "trades": float(trades),
        "win_rate_pct": wins / trades * 100 if trades else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else (99.0 if gross_profit else 0.0),
        "runner_pct": heat_under_1 / trades * 100 if trades else 0.0,
        "deep_heat_pct": heat_over_2 / trades * 100 if trades else 0.0,
    }


def configs_for(coin: str) -> list[Config]:
    cfgs = []
    for mode, side, bb_len, kc_mult, fast, slow, trend, pb, fail, hold, wait in product(
        ["trigger", "pullback", "starter_add"],
        ["long", "short"],
        [20],
        [2.0],
        [8, 13],
        [21, 34],
        [200],
        [0.5],
        [2.0],
        [24, 48],
        [6],
    ):
        if not (fast < slow < trend):
            continue
        cfgs.append(Config(coin, mode, side, bb_len, 2.0, kc_mult, fast, slow, trend, pb, fail, hold, wait))
    return cfgs


def score(full: dict[str, float], ins: dict[str, float], oos: dict[str, float]) -> float:
    if min(full["trades"], ins["trades"], oos["trades"]) < 5:
        return -1e9
    pf_floor = min(full["profit_factor"], ins["profit_factor"], oos["profit_factor"])
    return pf_floor * 100 + oos["cagr_pct"] + 0.25 * full["cagr_pct"] - max(0, full["max_dd_pct"] - 30) * 2


def fmt_cfg(cfg: Config) -> str:
    return (
        f"{cfg.coin} {cfg.mode} {cfg.side} sq{cfg.bb_len}/kc{cfg.kc_mult:g} "
        f"ema{cfg.ema_fast}/{cfg.ema_slow}/{cfg.ema_trend} pb={cfg.pullback_atr:g} "
        f"fail={cfg.fail_atr:g} hold={cfg.max_hold} wait={cfg.max_wait}"
    )


def main() -> None:
    symbols = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "BNB": "BNBUSDT", "SOL": "SOLUSDT"}
    print("Squeeze runner audit, 4h Binance caches")
    print("Costs: 0.055% fee each side + 0.05% slippage each side. Entry next open except pullback limit approximations.\n")

    all_rows = []
    for coin, symbol in symbols.items():
        df = load_ohlcv(symbol)
        split = int(len(df) * 0.60)
        ins_df = df.iloc[:split].reset_index(drop=True)
        oos_df = df.iloc[split:].reset_index(drop=True)
        rows = []
        for cfg in configs_for(coin):
            full = run_backtest(df, cfg)
            if full["trades"] < 8:
                continue
            ins = run_backtest(ins_df, cfg)
            oos = run_backtest(oos_df, cfg)
            rows.append((score(full, ins, oos), cfg, full, ins, oos))
            all_rows.append((score(full, ins, oos), cfg, full, ins, oos))
        rows.sort(key=lambda r: r[0], reverse=True)
        print(f"=== {coin} {df.timestamp.iloc[0]} -> {df.timestamp.iloc[-1]} ===")
        print(
            f"{'rank':>4} {'score':>8} {'cfg':<78} | "
            f"{'FULL CAGR':>9} {'PF':>5} {'DD':>6} {'tr':>4} {'run%':>5} {'deep%':>6} | "
            f"{'OOS CAGR':>8} {'OOS PF':>6} {'OOS DD':>7} {'OOS tr':>6}"
        )
        for rank, (sc, cfg, full, ins, oos) in enumerate(rows[:8], start=1):
            print(
                f"{rank:4d} {sc:8.1f} {fmt_cfg(cfg):<78} | "
                f"{full['cagr_pct']:9.1f} {full['profit_factor']:5.2f} {full['max_dd_pct']:6.1f} {full['trades']:4.0f} "
                f"{full['runner_pct']:5.0f} {full['deep_heat_pct']:6.0f} | "
                f"{oos['cagr_pct']:8.1f} {oos['profit_factor']:6.2f} {oos['max_dd_pct']:7.1f} {oos['trades']:6.0f}"
            )
        print()

    all_rows.sort(key=lambda r: r[0], reverse=True)
    print("=== best overall ===")
    for rank, (sc, cfg, full, ins, oos) in enumerate(all_rows[:12], start=1):
        print(
            f"{rank:2d}. score={sc:6.1f} {fmt_cfg(cfg)} | "
            f"FULL CAGR={full['cagr_pct']:.1f}% PF={full['profit_factor']:.2f} DD={full['max_dd_pct']:.1f}% tr={full['trades']:.0f} | "
            f"OOS CAGR={oos['cagr_pct']:.1f}% PF={oos['profit_factor']:.2f} DD={oos['max_dd_pct']:.1f}% tr={oos['trades']:.0f}"
        )


if __name__ == "__main__":
    main()
