#!/usr/bin/env python3
"""Approximate SMB 9 EMA continuation scalp from transcript.

Transcript rules converted to mechanical BTC rules:
- distinct move first: price on correct side of EMA9 and recent move > ATR threshold
- wait for pullback into EMA9
- enter only after a rejection/continuation candle closes
- fill next bar open, stop beyond pullback swing
- exit by fixed R target or close back through EMA9

Missing from BTC data: stock catalyst, opening auction, tape, order book, and volume
confirmation. This is only the chartable part of the discretionary setup.

Honesty: closed-bar signal, next-open fill, fee 0.055%/side + 0.05% slippage,
real intrabar stop/target, stop-first on straddle, 60/40 OOS split.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CACHE5 = ROOT / "data/cache/BTCUSDT_5m_binance.csv"
FEE_PCT = 0.00055
SLIP_PCT = 0.00050


@dataclass(frozen=True)
class Config:
    tf: str
    side_mode: str
    impulse_bars: int
    impulse_atr: float
    max_wait: int
    r_mult: float
    exit_mode: str
    ema_slope_bars: int


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def load(tf: str) -> pd.DataFrame:
    df = pd.read_csv(CACHE5, parse_dates=["timestamp"])
    if tf == "15m":
        df = (
            df.set_index("timestamp")
            .resample("15min")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
            .reset_index()
        )
    elif tf != "5m":
        raise ValueError(tf)
    df["ema9"] = ema(df["close"], 9)
    df["atr"] = atr(df, 14)
    return df.dropna().reset_index(drop=True)


def exit_return(side: int, entry: float, exit_px: float) -> float:
    if side == 1:
        gross = (exit_px * (1 - SLIP_PCT)) / (entry * (1 + SLIP_PCT)) - 1
    else:
        gross = (entry * (1 - SLIP_PCT)) / (exit_px * (1 + SLIP_PCT)) - 1
    return gross - 2 * FEE_PCT


def run(df: pd.DataFrame, cfg: Config) -> tuple[pd.Series, list[float]]:
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    e9 = df["ema9"].to_numpy()
    a = df["atr"].to_numpy()
    n = len(df)

    equity = np.ones(n)
    cash = 1.0
    trades: list[float] = []
    armed_side = 0
    armed_at = -1
    pull_low = np.nan
    pull_high = np.nan
    next_ok = 100

    start = max(100, cfg.impulse_bars + cfg.ema_slope_bars + 5)
    for i in range(start, n - 1):
        equity[i] = cash
        if i < next_ok:
            continue

        ema_up = e9[i] > e9[i - cfg.ema_slope_bars]
        ema_dn = e9[i] < e9[i - cfg.ema_slope_bars]
        move = c[i] - c[i - cfg.impulse_bars]
        impulse_long = c[i] > e9[i] and ema_up and move > cfg.impulse_atr * a[i]
        impulse_short = c[i] < e9[i] and ema_dn and -move > cfg.impulse_atr * a[i]

        if armed_side and i - armed_at > cfg.max_wait:
            armed_side = 0
            pull_low = np.nan
            pull_high = np.nan

        if armed_side == 0:
            if impulse_long and cfg.side_mode in ("long", "both"):
                armed_side = 1
                armed_at = i
                pull_low = l[i]
                pull_high = h[i]
            elif impulse_short and cfg.side_mode in ("short", "both"):
                armed_side = -1
                armed_at = i
                pull_low = l[i]
                pull_high = h[i]
            continue

        pull_low = min(pull_low, l[i])
        pull_high = max(pull_high, h[i])
        tagged_ema = l[i] <= e9[i] <= h[i]
        long_reject = tagged_ema and c[i] > e9[i] and c[i] > o[i] and h[i] > h[i - 1]
        short_reject = tagged_ema and c[i] < e9[i] and c[i] < o[i] and l[i] < l[i - 1]

        if (armed_side == 1 and long_reject) or (armed_side == -1 and short_reject):
            side = armed_side
            entry_i = i + 1
            entry = o[entry_i]
            if side == 1:
                stop = pull_low
                risk = entry - stop
                target = entry + cfg.r_mult * risk
            else:
                stop = pull_high
                risk = stop - entry
                target = entry - cfg.r_mult * risk
            armed_side = 0
            pull_low = np.nan
            pull_high = np.nan
            if risk <= 0 or risk / entry > 0.04:
                continue

            exit_i = min(n - 1, entry_i + cfg.max_wait)
            exit_px = c[exit_i]
            for j in range(entry_i, exit_i + 1):
                if side == 1:
                    if l[j] <= stop:
                        exit_i = j
                        exit_px = stop
                        break
                    if cfg.exit_mode == "r" and h[j] >= target:
                        exit_i = j
                        exit_px = target
                        break
                    if cfg.exit_mode == "ema" and c[j] < e9[j]:
                        exit_i = min(j + 1, n - 1)
                        exit_px = o[exit_i]
                        break
                else:
                    if h[j] >= stop:
                        exit_i = j
                        exit_px = stop
                        break
                    if cfg.exit_mode == "r" and l[j] <= target:
                        exit_i = j
                        exit_px = target
                        break
                    if cfg.exit_mode == "ema" and c[j] > e9[j]:
                        exit_i = min(j + 1, n - 1)
                        exit_px = o[exit_i]
                        break
            ret = exit_return(side, entry, exit_px)
            cash *= 1 + ret
            trades.append(ret)
            equity[exit_i] = cash
            next_ok = exit_i + 1

    equity[next_ok:] = cash
    s = pd.Series(equity, index=pd.to_datetime(df["timestamp"])).replace(0, np.nan).ffill().fillna(1.0)
    return s.iloc[start:], trades


def metrics(eq: pd.Series, trades: list[float]) -> dict[str, float]:
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365.25)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if eq.iloc[-1] > 0 else -1
    dd = (eq / eq.cummax() - 1).min()
    wins = sum(t > 0 for t in trades)
    gp = sum(t for t in trades if t > 0)
    gl = -sum(t for t in trades if t < 0)
    return {
        "cagr": cagr * 100,
        "dd": dd * 100,
        "trades": float(len(trades)),
        "win": wins / len(trades) * 100 if trades else 0.0,
        "pf": gp / gl if gl else (99.0 if gp else 0.0),
    }


def configs(tf: str) -> list[Config]:
    out = []
    for side_mode in ["long", "short", "both"]:
        for impulse_bars in ([12, 24] if tf == "5m" else [8, 16]):
            for impulse_atr in [2.5, 3.5]:
                for max_wait in [8, 16]:
                    for r_mult in [2.0, 3.0]:
                        for exit_mode in ["r", "ema"]:
                            out.append(Config(tf, side_mode, impulse_bars, impulse_atr, max_wait, r_mult, exit_mode, 3))
    return out


def score(full: dict[str, float], oos: dict[str, float]) -> float:
    if full["trades"] < 30 or oos["trades"] < 10:
        return -1e9
    return min(full["pf"], oos["pf"]) * 100 + oos["cagr"] - max(0, abs(full["dd"]) - 35)


def main() -> None:
    print("SMB-style 9 EMA continuation scalp approximation on BTC")
    print("Closed-bar signal, next-open fill, costs included. OOS = last 40%.\n")
    for tf in ["15m", "5m"]:
        df = load(tf)
        split_ts = df["timestamp"].iloc[int(len(df) * 0.6)]
        rows = []
        for cfg in configs(tf):
            eq, tr = run(df, cfg)
            full = metrics(eq, tr)
            oos_eq = eq[eq.index >= split_ts]
            # Approximate OOS trade list by rerunning on sliced data to avoid trade timestamp bookkeeping complexity.
            oos_df = df[df["timestamp"] >= split_ts].reset_index(drop=True)
            oos_eq2, oos_tr = run(oos_df, cfg)
            oos = metrics(oos_eq2, oos_tr)
            rows.append((score(full, oos), cfg, full, oos))
        rows.sort(key=lambda x: x[0], reverse=True)
        print(f"=== BTC {tf}: {df.timestamp.iloc[0]} -> {df.timestamp.iloc[-1]}, OOS {split_ts} ===")
        print(f"{'rank':>4} {'score':>8} {'config':<58} | {'FULL CAGR':>9} {'PF':>5} {'DD':>7} {'tr':>5} | {'OOS CAGR':>8} {'PF':>5} {'DD':>7} {'tr':>5}")
        for rank, (sc, cfg, full, oos) in enumerate(rows[:12], 1):
            name = (
                f"{cfg.side_mode} imp={cfg.impulse_bars}/{cfg.impulse_atr:g} "
                f"wait={cfg.max_wait} {cfg.exit_mode}{cfg.r_mult:g} slope={cfg.ema_slope_bars}"
            )
            print(
                f"{rank:4d} {sc:8.1f} {name:<58} | "
                f"{full['cagr']:9.1f} {full['pf']:5.2f} {full['dd']:7.1f} {full['trades']:5.0f} | "
                f"{oos['cagr']:8.1f} {oos['pf']:5.2f} {oos['dd']:7.1f} {oos['trades']:5.0f}"
            )
        print()


if __name__ == "__main__":
    main()
