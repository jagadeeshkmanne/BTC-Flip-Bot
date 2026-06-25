#!/usr/bin/env python3
"""EMA cross entry with Bollinger-band exit.

Idea from screenshots:
- Short: fast EMA crosses below slow EMA, enter next open.
- Exit/take-profit when price reaches the lower Bollinger Band.
- Optional safety exit on opposite EMA cross or max hold.

Long mirror is also tested:
- fast EMA crosses above slow EMA, exit at upper Bollinger Band.

Honest assumptions: closed-bar signals, next-open entry, intrabar band touch
using real high/low, fee 0.055%/side + 0.05% slippage.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
FEE_PCT = 0.00055
SLIP_PCT = 0.00050
SYMBOLS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "BNB": "BNBUSDT", "SOL": "SOLUSDT"}


@dataclass(frozen=True)
class Config:
    coin: str
    side: str
    fast: int
    slow: int
    bb_len: int
    bb_std: float
    max_hold: int
    opposite_exit: bool
    stop_buffer_atr: float


def load_ohlcv(symbol: str) -> pd.DataFrame:
    path = ROOT / f"data/cache/{symbol}_4h_2019_binance.csv"
    if not path.exists():
        raise FileNotFoundError(f"missing {path}; run scripts/walkforward_all_weather_audit.py to cache full history")
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="raise")
    return df


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def add_features(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    out = df.copy()
    prev = out["close"].shift(1)
    tr = pd.concat(
        [out["high"] - out["low"], (out["high"] - prev).abs(), (out["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    out["ema_fast"] = ema(out["close"], cfg.fast)
    out["ema_slow"] = ema(out["close"], cfg.slow)
    mid = out["close"].rolling(cfg.bb_len).mean()
    sd = out["close"].rolling(cfg.bb_len).std()
    out["bb_up"] = mid + cfg.bb_std * sd
    out["bb_low"] = mid - cfg.bb_std * sd
    out["cross_up"] = (out["ema_fast"] > out["ema_slow"]) & (out["ema_fast"].shift(1) <= out["ema_slow"].shift(1))
    out["cross_dn"] = (out["ema_fast"] < out["ema_slow"]) & (out["ema_fast"].shift(1) >= out["ema_slow"].shift(1))
    return out


def pct_return(side: str, entry: float, exit_px: float) -> float:
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
    open_a = df["open"].to_numpy()
    high_a = df["high"].to_numpy()
    low_a = df["low"].to_numpy()
    close_a = df["close"].to_numpy()
    bb_up_a = df["bb_up"].to_numpy()
    bb_low_a = df["bb_low"].to_numpy()
    cross_up_a = df["cross_up"].to_numpy()
    cross_dn_a = df["cross_dn"].to_numpy()
    cash = 1.0
    peak = 1.0
    max_dd = 0.0
    wins = 0
    trades = 0
    gross_profit = 0.0
    gross_loss = 0.0
    band_exits = 0
    opp_exits = 0
    time_exits = 0
    stop_exits = 0
    start = max(cfg.slow, cfg.bb_len) + 5
    next_i = start

    for i in range(start, len(df) - 1):
        if i < next_i:
            continue
        signal = bool(cross_up_a[i]) if cfg.side == "long" else bool(cross_dn_a[i])
        if not signal:
            continue

        entry_i = i + 1
        entry = float(open_a[entry_i])
        cross_high = float(high_a[i])
        cross_low = float(low_a[i])
        atr_i = float(df["atr"].iloc[i])
        if cfg.side == "long":
            stop = cross_low - cfg.stop_buffer_atr * atr_i
        else:
            stop = cross_high + cfg.stop_buffer_atr * atr_i
        exit_i = min(len(df) - 1, entry_i + cfg.max_hold)
        exit_px = float(close_a[exit_i])
        reason = "time"
        for j in range(entry_i, exit_i + 1):
            if cfg.side == "long":
                if float(low_a[j]) <= stop:
                    exit_i = j
                    exit_px = stop
                    reason = "stop"
                    break
                band = float(bb_up_a[j])
                if pd.notna(band) and float(high_a[j]) >= band:
                    exit_i = j
                    exit_px = band
                    reason = "band"
                    break
                if cfg.opposite_exit and bool(cross_dn_a[j]):
                    exit_i = min(j + 1, len(df) - 1)
                    exit_px = float(open_a[exit_i])
                    reason = "opposite"
                    break
            else:
                if float(high_a[j]) >= stop:
                    exit_i = j
                    exit_px = stop
                    reason = "stop"
                    break
                band = float(bb_low_a[j])
                if pd.notna(band) and float(low_a[j]) <= band:
                    exit_i = j
                    exit_px = band
                    reason = "band"
                    break
                if cfg.opposite_exit and bool(cross_up_a[j]):
                    exit_i = min(j + 1, len(df) - 1)
                    exit_px = float(open_a[exit_i])
                    reason = "opposite"
                    break

        ret = pct_return(cfg.side, entry, exit_px)
        pnl = cash * ret
        cash += pnl
        peak = max(peak, cash)
        max_dd = max(max_dd, (peak - cash) / peak)
        trades += 1
        wins += int(pnl > 0)
        if pnl > 0:
            gross_profit += pnl
        else:
            gross_loss -= pnl
        if reason == "band":
            band_exits += 1
        elif reason == "stop":
            stop_exits += 1
        elif reason == "opposite":
            opp_exits += 1
        else:
            time_exits += 1
        next_i = exit_i + 1

    years = max((df["timestamp"].iloc[-1] - df["timestamp"].iloc[start]).days / 365.25, 1 / 365.25)
    cagr = cash ** (1 / years) - 1 if cash > 0 else -1.0
    return {
        "net_pct": (cash - 1) * 100,
        "cagr_pct": cagr * 100,
        "max_dd_pct": max_dd * 100,
        "trades": float(trades),
        "win_rate_pct": wins / trades * 100 if trades else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else (99.0 if gross_profit else 0.0),
        "band_exit_pct": band_exits / trades * 100 if trades else 0.0,
        "stop_exit_pct": stop_exits / trades * 100 if trades else 0.0,
        "opp_exit_pct": opp_exits / trades * 100 if trades else 0.0,
        "time_exit_pct": time_exits / trades * 100 if trades else 0.0,
    }


def configs_for(coin: str) -> list[Config]:
    out = []
    for side, fast, slow, bb_len, bb_std, max_hold, opposite_exit, stop_buffer_atr in product(
        ["short", "long"],
        [13, 20, 21],
        [50, 100, 200],
        [20],
        [2.0, 2.5],
        [24, 48, 96],
        [False, True],
        [0.0, 0.25],
    ):
        if fast >= slow:
            continue
        out.append(Config(coin, side, fast, slow, bb_len, bb_std, max_hold, opposite_exit, stop_buffer_atr))
    return out


def score(full: dict[str, float], ins: dict[str, float], oos: dict[str, float]) -> float:
    if min(full["trades"], ins["trades"], oos["trades"]) < 6:
        return -1e9
    pf_floor = min(full["profit_factor"], ins["profit_factor"], oos["profit_factor"])
    return pf_floor * 100 + oos["cagr_pct"] + 0.25 * full["cagr_pct"] - max(0, full["max_dd_pct"] - 30) * 2


def fmt_cfg(cfg: Config) -> str:
    opp = "opp" if cfg.opposite_exit else "noopp"
    return (
        f"{cfg.coin} {cfg.side} ema{cfg.fast}/{cfg.slow} bb{cfg.bb_len}/{cfg.bb_std:g} "
        f"hold={cfg.max_hold} {opp} slbuf={cfg.stop_buffer_atr:g}"
    )


def main() -> None:
    print("EMA cross entry -> Bollinger outer-band exit, 4h Binance")
    print("Short exits on lower BB; long exits on upper BB. Costs included.\n")
    all_rows = []
    for coin, symbol in SYMBOLS.items():
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
            row = (score(full, ins, oos), cfg, full, ins, oos)
            rows.append(row)
            all_rows.append(row)
        rows.sort(key=lambda x: x[0], reverse=True)
        print(f"=== {coin} {df.timestamp.iloc[0]} -> {df.timestamp.iloc[-1]} ===")
        print(
            f"{'rank':>4} {'score':>8} {'cfg':<42} | "
            f"{'FULL CAGR':>9} {'PF':>5} {'DD':>6} {'tr':>4} {'wr':>5} {'band%':>6} {'stop%':>6} | "
            f"{'OOS CAGR':>8} {'OOS PF':>6} {'OOS DD':>7} {'OOS tr':>6}"
        )
        for rank, (sc, cfg, full, ins, oos) in enumerate(rows[:10], 1):
            print(
                f"{rank:4d} {sc:8.1f} {fmt_cfg(cfg):<42} | "
                f"{full['cagr_pct']:9.1f} {full['profit_factor']:5.2f} {full['max_dd_pct']:6.1f} "
                f"{full['trades']:4.0f} {full['win_rate_pct']:5.0f} {full['band_exit_pct']:6.0f} {full['stop_exit_pct']:6.0f} | "
                f"{oos['cagr_pct']:8.1f} {oos['profit_factor']:6.2f} {oos['max_dd_pct']:7.1f} {oos['trades']:6.0f}"
            )
        print()

    all_rows.sort(key=lambda x: x[0], reverse=True)
    print("=== best overall ===")
    for rank, (sc, cfg, full, ins, oos) in enumerate(all_rows[:15], 1):
        print(
            f"{rank:2d}. score={sc:6.1f} {fmt_cfg(cfg)} | "
            f"FULL CAGR={full['cagr_pct']:.1f}% PF={full['profit_factor']:.2f} DD={full['max_dd_pct']:.1f}% "
            f"tr={full['trades']:.0f} band={full['band_exit_pct']:.0f}% stop={full['stop_exit_pct']:.0f}% | "
            f"OOS CAGR={oos['cagr_pct']:.1f}% PF={oos['profit_factor']:.2f} DD={oos['max_dd_pct']:.1f}% tr={oos['trades']:.0f}"
        )


if __name__ == "__main__":
    main()
