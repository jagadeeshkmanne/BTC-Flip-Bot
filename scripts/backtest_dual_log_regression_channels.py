#!/usr/bin/env python3
"""Approximate Dual Log Regression Channels strategy audit.

Based on the public TradingView description of BigBeluga's indicator:
- macro log regression channel, default 300 bars
- short log regression channel, default 50 bars
- standard-deviation bands in log space
- volume delta context

This is not a source-code clone. It tests mechanical interpretations:
- mean_revert: fade short-channel extremes when macro slope agrees or delta diverges
- breakout: follow closes outside the macro channel when volume delta confirms
- confluence: trade short-channel extreme near macro-channel boundary

Honesty: closed-bar signal, next-open entry, real intrabar stop/target with stop-first,
fee 0.055%/side + 0.05% slippage, 60/40 split.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
FEE_PCT = 0.00055
SLIP_PCT = 0.00050
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "BNB": "BNBUSDT", "SOL": "SOLUSDT"}


@dataclass(frozen=True)
class Config:
    coin: str
    family: str
    side: str
    macro_len: int
    short_len: int
    dev: float
    stop_atr: float
    rr: float
    max_hold: int
    delta_min: float


def load_ohlcv(symbol: str) -> pd.DataFrame:
    path = ROOT / f"data/cache/{symbol}_4h_2019_binance.csv"
    if not path.exists():
        raise FileNotFoundError(f"missing {path}; run scripts/walkforward_all_weather_audit.py first")
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="raise")
    return df


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def log_reg_channel(close: pd.Series, length: int, dev: float, prefix: str) -> pd.DataFrame:
    y = np.log(close.to_numpy(dtype=float))
    n = len(y)
    mid = np.full(n, np.nan)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    slope = np.full(n, np.nan)
    x = np.arange(length, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()
    for i in range(length - 1, n):
        win = y[i - length + 1 : i + 1]
        y_mean = win.mean()
        b = ((x - x_mean) * (win - y_mean)).sum() / x_var
        a = y_mean - b * x_mean
        fit = a + b * x
        resid_std = float(np.std(win - fit, ddof=1))
        cur = fit[-1]
        mid[i] = np.exp(cur)
        upper[i] = np.exp(cur + dev * resid_std)
        lower[i] = np.exp(cur - dev * resid_std)
        slope[i] = b
    return pd.DataFrame(
        {
            f"{prefix}_mid": mid,
            f"{prefix}_upper": upper,
            f"{prefix}_lower": lower,
            f"{prefix}_slope": slope,
        }
    )


def add_features(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    out = df.copy()
    out["atr"] = atr(out, 14)
    out = pd.concat(
        [
            out,
            log_reg_channel(out["close"], cfg.macro_len, cfg.dev, "macro"),
            log_reg_channel(out["close"], cfg.short_len, cfg.dev, "short"),
        ],
        axis=1,
    )
    signed_vol = np.where(out["close"] >= out["open"], out["volume"], -out["volume"])
    vol_sum = out["volume"].rolling(cfg.short_len).sum().replace(0, np.nan)
    out["delta"] = pd.Series(signed_vol, index=out.index).rolling(cfg.short_len).sum() / vol_sum
    return out


def make_signal(df: pd.DataFrame, cfg: Config) -> pd.Series:
    c = df["close"]
    if cfg.family == "mean_revert":
        long_sig = (c <= df["short_lower"]) & (df["macro_slope"] > 0) & (df["delta"] > -cfg.delta_min)
        short_sig = (c >= df["short_upper"]) & (df["macro_slope"] < 0) & (df["delta"] < cfg.delta_min)
    elif cfg.family == "confluence":
        long_sig = (c <= df["short_lower"]) & (c <= df["macro_lower"] * 1.03) & (df["delta"] > -cfg.delta_min)
        short_sig = (c >= df["short_upper"]) & (c >= df["macro_upper"] * 0.97) & (df["delta"] < cfg.delta_min)
    elif cfg.family == "breakout":
        long_sig = (c > df["macro_upper"]) & (df["macro_slope"] > 0) & (df["delta"] > cfg.delta_min)
        short_sig = (c < df["macro_lower"]) & (df["macro_slope"] < 0) & (df["delta"] < -cfg.delta_min)
    else:
        raise ValueError(cfg.family)
    return (long_sig if cfg.side == "long" else short_sig).fillna(False)


def pct_return(side: str, entry: float, exit_px: float) -> float:
    if side == "long":
        gross = (exit_px * (1 - SLIP_PCT)) / (entry * (1 + SLIP_PCT)) - 1
    else:
        gross = (entry * (1 - SLIP_PCT)) / (exit_px * (1 + SLIP_PCT)) - 1
    return gross - 2 * FEE_PCT


def run_backtest(df_raw: pd.DataFrame, cfg: Config) -> dict[str, float]:
    df = df_raw.copy() if "macro_mid" in df_raw.columns else add_features(df_raw, cfg)
    sig = make_signal(df, cfg).to_numpy()
    open_a = df["open"].to_numpy()
    high_a = df["high"].to_numpy()
    low_a = df["low"].to_numpy()
    close_a = df["close"].to_numpy()
    atr_a = df["atr"].to_numpy()

    cash = 1.0
    peak = 1.0
    max_dd = 0.0
    trades = wins = 0
    gp = gl = 0.0
    start = max(cfg.macro_len, cfg.short_len, 50) + 5
    next_i = start
    for i in range(start, len(df) - 1):
        if i < next_i or not sig[i]:
            continue
        entry_i = i + 1
        entry = float(open_a[entry_i])
        risk = cfg.stop_atr * float(atr_a[i])
        if risk <= 0 or np.isnan(risk):
            continue
        if cfg.side == "long":
            stop = entry - risk
            target = entry + cfg.rr * risk
        else:
            stop = entry + risk
            target = entry - cfg.rr * risk
        exit_i = min(len(df) - 1, entry_i + cfg.max_hold)
        exit_px = float(close_a[exit_i])
        for j in range(entry_i, exit_i + 1):
            if cfg.side == "long":
                stop_hit = low_a[j] <= stop
                target_hit = high_a[j] >= target
            else:
                stop_hit = high_a[j] >= stop
                target_hit = low_a[j] <= target
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
        trades += 1
        wins += int(pnl > 0)
        if pnl > 0:
            gp += pnl
        else:
            gl -= pnl
        next_i = exit_i + 1

    years = max((df["timestamp"].iloc[-1] - df["timestamp"].iloc[start]).days / 365.25, 1 / 365.25)
    cagr = cash ** (1 / years) - 1 if cash > 0 else -1.0
    return {
        "net_pct": (cash - 1) * 100,
        "cagr_pct": cagr * 100,
        "max_dd_pct": max_dd * 100,
        "trades": float(trades),
        "win_rate_pct": wins / trades * 100 if trades else 0.0,
        "profit_factor": gp / gl if gl else (99.0 if gp else 0.0),
    }


def configs_for(coin: str) -> list[Config]:
    configs = []
    for family, side, macro_len, short_len, dev, stop_atr, rr, max_hold, delta_min in product(
        ["mean_revert", "confluence", "breakout"],
        ["long", "short"],
        [300],
        [50],
        [2.0],
        [1.5, 2.0],
        [1.5, 2.0],
        [24, 48],
        [0.15],
    ):
        configs.append(Config(coin, family, side, macro_len, short_len, dev, stop_atr, rr, max_hold, delta_min))
    return configs


def score(full: dict[str, float], ins: dict[str, float], oos: dict[str, float]) -> float:
    if min(full["trades"], ins["trades"], oos["trades"]) < 6:
        return -1e9
    pf_floor = min(full["profit_factor"], ins["profit_factor"], oos["profit_factor"])
    return pf_floor * 100 + oos["cagr_pct"] + 0.25 * full["cagr_pct"] - max(0, full["max_dd_pct"] - 35) * 2


def fmt_cfg(cfg: Config) -> str:
    return (
        f"{cfg.coin} {cfg.family} {cfg.side} M{cfg.macro_len}/S{cfg.short_len} "
        f"dev={cfg.dev:g} st={cfg.stop_atr:g} rr={cfg.rr:g} hold={cfg.max_hold} d={cfg.delta_min:g}"
    )


def main() -> None:
    print("Dual Log Regression Channels approximation, 4h Binance")
    print("Log-regression channel families: mean_revert, confluence, breakout. Costs included.\n")
    all_rows = []
    for coin, symbol in SYMBOLS.items():
        df = load_ohlcv(symbol)
        split = int(len(df) * 0.60)
        feature_cfg = Config(coin, "breakout", "long", 300, 50, 2.0, 2.0, 2.0, 48, 0.15)
        df = add_features(df, feature_cfg)
        ins_df = df.iloc[:split].reset_index(drop=True)
        oos_df = df.iloc[split:].reset_index(drop=True)
        rows = []
        for cfg in configs_for(coin):
            full = run_backtest(df, cfg)
            if full["trades"] < 8:
                continue
            ins = run_backtest(ins_df, cfg)
            oos = run_backtest(oos_df, cfg)
            row = (score(full, ins, oos), cfg, full, ins, oos)
            rows.append(row)
            all_rows.append(row)
        rows.sort(key=lambda x: x[0], reverse=True)
        print(f"=== {coin} {df.timestamp.iloc[0]} -> {df.timestamp.iloc[-1]} ===")
        print(
            f"{'rank':>4} {'score':>8} {'cfg':<72} | "
            f"{'FULL CAGR':>9} {'PF':>5} {'DD':>6} {'tr':>4} | "
            f"{'OOS CAGR':>8} {'OOS PF':>6} {'OOS DD':>7} {'OOS tr':>6}"
        )
        for rank, (sc, cfg, full, ins, oos) in enumerate(rows[:8], 1):
            print(
                f"{rank:4d} {sc:8.1f} {fmt_cfg(cfg):<72} | "
                f"{full['cagr_pct']:9.1f} {full['profit_factor']:5.2f} {full['max_dd_pct']:6.1f} {full['trades']:4.0f} | "
                f"{oos['cagr_pct']:8.1f} {oos['profit_factor']:6.2f} {oos['max_dd_pct']:7.1f} {oos['trades']:6.0f}"
            )
        print()

    all_rows.sort(key=lambda x: x[0], reverse=True)
    print("=== best overall ===")
    for rank, (sc, cfg, full, ins, oos) in enumerate(all_rows[:15], 1):
        print(
            f"{rank:2d}. score={sc:6.1f} {fmt_cfg(cfg)} | "
            f"FULL CAGR={full['cagr_pct']:.1f}% PF={full['profit_factor']:.2f} DD={full['max_dd_pct']:.1f}% tr={full['trades']:.0f} | "
            f"OOS CAGR={oos['cagr_pct']:.1f}% PF={oos['profit_factor']:.2f} DD={oos['max_dd_pct']:.1f}% tr={oos['trades']:.0f}"
        )


if __name__ == "__main__":
    main()
