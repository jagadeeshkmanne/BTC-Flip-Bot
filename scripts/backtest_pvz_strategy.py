#!/usr/bin/env python3
"""Research-only BTCUSDT PVZ/VZO strategy search.

PVZ is treated here as a price/volume-zone strategy built around the
Volume Zone Oscillator idea:
    VZO = 100 * EMA(signed_volume, n) / EMA(volume, n)
where signed_volume is positive when close rises and negative when close falls.

Tested families:
- trend: VZO crosses into bullish/bearish territory with EMA trend confirmation.
- pullback: trend is intact, VZO flushes opposite, then recovers.
- extreme: VZO exits an extreme zone in the direction of the trend.

Honesty assumptions: closed-bar signal, next-open entry, stop-first intrabar,
fees/slippage included, chronological 60/40 split.
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
    family: str
    side: str
    vzo_len: int
    ema_len: int
    signal: float
    extreme: float
    stop_atr: float
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
    prev = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def add_features(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    out = df.copy()
    direction = out["close"].diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    signed_volume = direction * out["volume"]
    out["vzo"] = 100 * ema(signed_volume, cfg.vzo_len) / ema(out["volume"], cfg.vzo_len).replace(0, 1e-9)
    out["ema_trend"] = ema(out["close"], cfg.ema_len)
    out["atr"] = atr(out, 14)
    return out


def make_signal(df: pd.DataFrame, cfg: Config) -> pd.Series:
    vzo = df["vzo"]
    close = df["close"]
    trend_long = close > df["ema_trend"]
    trend_short = close < df["ema_trend"]

    if cfg.family == "trend":
        long_sig = (vzo > cfg.signal) & (vzo.shift(1) <= cfg.signal) & trend_long
        short_sig = (vzo < -cfg.signal) & (vzo.shift(1) >= -cfg.signal) & trend_short
    elif cfg.family == "pullback":
        long_sig = (vzo > -cfg.signal) & (vzo.shift(1) <= -cfg.signal) & (vzo.rolling(6).min() <= -cfg.extreme) & trend_long
        short_sig = (vzo < cfg.signal) & (vzo.shift(1) >= cfg.signal) & (vzo.rolling(6).max() >= cfg.extreme) & trend_short
    elif cfg.family == "extreme":
        long_sig = (vzo > -cfg.extreme) & (vzo.shift(1) <= -cfg.extreme) & trend_long
        short_sig = (vzo < cfg.extreme) & (vzo.shift(1) >= cfg.extreme) & trend_short
    else:
        raise ValueError(cfg.family)

    sig = long_sig if cfg.side == "long" else short_sig
    return (sig & (df["atr"] > 0)).fillna(False)


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


def run_backtest(df_raw: pd.DataFrame, cfg: Config) -> dict[str, float]:
    df = add_features(df_raw, cfg)
    sig = make_signal(df, cfg).to_numpy()
    open_a = df["open"].to_numpy()
    high_a = df["high"].to_numpy()
    low_a = df["low"].to_numpy()
    close_a = df["close"].to_numpy()
    atr_a = df["atr"].to_numpy()

    cash = 1.0
    peak = 1.0
    max_dd = 0.0
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0
    trade_returns: list[float] = []
    next_allowed = max(cfg.vzo_len, cfg.ema_len, 50) + 5

    for i in range(next_allowed, len(df) - 1):
        if i < next_allowed or not sig[i]:
            continue
        entry_i = i + 1
        entry = float(open_a[entry_i])
        risk = cfg.stop_atr * float(atr_a[i])
        if risk <= 0 or pd.isna(risk):
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
        "win_rate_pct": wins / trades * 100 if trades else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else (99.0 if gross_profit else 0.0),
        "avg_trade_pct": sum(trade_returns) / trades * 100 if trades else 0.0,
    }


def datasets() -> dict[str, pd.DataFrame]:
    one_h = load_ohlcv(ROOT / "data/cache/BTCUSDT_1h_12000_bybit.csv")
    fifteen = load_ohlcv(ROOT / "data/cache/BTCUSDT_15m_16000_bybit.csv")
    return {
        "15m": fifteen,
        "1h": one_h,
        "4h": resample_ohlcv(one_h, "4h"),
    }


def config_space(tf: str) -> list[Config]:
    if tf == "15m":
        vzo_lens = [14, 21, 34]
        ema_lens = [100, 200]
        signals = [5, 15, 25]
        extremes = [40, 60]
        stops = [1.0, 1.5]
        rrs = [1.5, 2.0]
        max_holds = [24, 48]
        cooldown = 4
    elif tf == "1h":
        vzo_lens = [14, 21, 34]
        ema_lens = [50, 100, 200]
        signals = [5, 15, 25]
        extremes = [40, 60]
        stops = [1.5, 2.0]
        rrs = [2.0, 3.0]
        max_holds = [48, 96]
        cooldown = 3
    elif tf == "4h":
        vzo_lens = [14, 21, 34]
        ema_lens = [50, 100, 200]
        signals = [5, 15, 25]
        extremes = [40, 60]
        stops = [1.5, 2.0]
        rrs = [2.0, 3.0]
        max_holds = [24, 48]
        cooldown = 2
    else:
        raise ValueError(tf)

    configs = []
    for family, side, vzo_len, ema_len, signal, extreme, stop_atr, rr, max_hold in product(
        ["trend", "pullback", "extreme"],
        ["long", "short"],
        vzo_lens,
        ema_lens,
        signals,
        extremes,
        stops,
        rrs,
        max_holds,
    ):
        configs.append(Config(tf, family, side, vzo_len, ema_len, signal, extreme, stop_atr, rr, max_hold, cooldown))
    return configs


def score(full: dict[str, float], ins: dict[str, float], oos: dict[str, float]) -> float:
    if min(full["trades"], ins["trades"], oos["trades"]) < 6:
        return -1e9
    pf_floor = min(full["profit_factor"], ins["profit_factor"], oos["profit_factor"])
    return pf_floor * 100 + oos["net_pct"] + 0.25 * full["net_pct"] - max(0.0, full["max_dd_pct"] - 30.0) * 2


def fmt_cfg(cfg: Config) -> str:
    return (
        f"{cfg.family} {cfg.side} vzo{cfg.vzo_len} ema{cfg.ema_len} "
        f"sig={cfg.signal:g} ext={cfg.extreme:g} stop={cfg.stop_atr:g} "
        f"rr={cfg.rr:g} hold={cfg.max_hold}"
    )


def main() -> None:
    print("PVZ/VZO BTCUSDT strategy search")
    print("Costs: 0.055% fee each side + 0.05% slippage each side; entries next open; stop-first intrabar.\n")

    for tf, df in datasets().items():
        split = int(len(df) * 0.60)
        ins_df = df.iloc[:split].reset_index(drop=True)
        oos_df = df.iloc[split:].reset_index(drop=True)
        rough_rows = []
        for cfg in config_space(tf):
            full = run_backtest(df, cfg)
            if full["trades"] < 8:
                continue
            rough = full["profit_factor"] * 100 + full["net_pct"] - max(0.0, full["max_dd_pct"] - 30.0)
            rough_rows.append((rough, cfg, full))
        rough_rows.sort(key=lambda row: row[0], reverse=True)

        rows = []
        for _, cfg, full in rough_rows[:120]:
            ins = run_backtest(ins_df, cfg)
            oos = run_backtest(oos_df, cfg)
            rows.append((score(full, ins, oos), cfg, full, ins, oos))
        rows.sort(key=lambda row: row[0], reverse=True)

        print(
            f"=== {tf} bars={len(df)} {df.timestamp.iloc[0]} -> {df.timestamp.iloc[-1]} "
            f"(OOS starts {oos_df.timestamp.iloc[0]}) ==="
        )
        print(
            f"{'rank':>4} {'score':>8} {'cfg':<68} | "
            f"{'FULL net':>8} {'CAGR':>7} {'PF':>5} {'DD':>6} {'tr':>4} | "
            f"{'IS PF':>5} {'OOS net':>8} {'OOS PF':>6} {'OOS tr':>6}"
        )
        for rank, (sc, cfg, full, ins, oos) in enumerate(rows[:12], start=1):
            print(
                f"{rank:4d} {sc:8.1f} {fmt_cfg(cfg):<68} | "
                f"{full['net_pct']:8.2f} {full['cagr_pct']:7.2f} {full['profit_factor']:5.2f} "
                f"{full['max_dd_pct']:6.2f} {full['trades']:4.0f} | "
                f"{ins['profit_factor']:5.2f} {oos['net_pct']:8.2f} {oos['profit_factor']:6.2f} {oos['trades']:6.0f}"
            )
        print()


if __name__ == "__main__":
    main()
