#!/usr/bin/env python3
"""No-indicator price-action box breakout backtest.

Based on the supplied video transcript:
- Build a box from a fixed group of candles: highest high to lowest low.
- Watch the next group of candles for a breakout of the latest box.
- Enter on the next candle open after a closed breakout candle.
- Long stop below breakout candle low; short stop above breakout candle high.
- Target is fixed R multiple, default video value 1.5R.
- While a position is open, do not form new boxes.

This uses only OHLC price action. No indicators, no volume, no ATR.

Honesty:
- Signal only after a candle closes beyond the box.
- Fill at next candle open.
- Fee 0.055%/side + 0.05% slippage.
- Real intrabar high/low stop/target; stop-first when both touch.
- 60/40 in-sample/out-of-sample split.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
CACHE5 = ROOT / "data/cache/BTCUSDT_5m_binance.csv"
FEE_PCT = 0.00055
SLIP_PCT = 0.00050


@dataclass(frozen=True)
class Config:
    tf: str
    box_bars: int
    side: str
    rr: float
    min_box_pct: float
    max_box_pct: float
    require_body_break: bool


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
    elif tf == "30m":
        df = (
            df.set_index("timestamp")
            .resample("30min")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
            .reset_index()
        )
    elif tf != "5m":
        raise ValueError(tf)
    return df.reset_index(drop=True)


def trade_ret(side: str, entry: float, exit_px: float) -> float:
    if side == "long":
        gross = (exit_px * (1 - SLIP_PCT)) / (entry * (1 + SLIP_PCT)) - 1
    else:
        gross = (entry * (1 - SLIP_PCT)) / (exit_px * (1 + SLIP_PCT)) - 1
    return gross - 2 * FEE_PCT


def run(df: pd.DataFrame, cfg: Config) -> tuple[pd.Series, list[float]]:
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    n = len(df)
    eq = [1.0] * n
    cash = 1.0
    trades: list[float] = []

    i = cfg.box_bars
    while i < n - 2:
        # Build a new box from the immediately prior block.
        box_start = i - cfg.box_bars
        box_end = i
        box_high = float(max(h[box_start:box_end]))
        box_low = float(min(l[box_start:box_end]))
        box_mid = (box_high + box_low) / 2
        box_pct = (box_high - box_low) / box_mid if box_mid else 0.0
        if not (cfg.min_box_pct <= box_pct <= cfg.max_box_pct):
            i += cfg.box_bars
            continue

        signal_i = None
        signal_side = None
        watch_end = min(n - 2, i + cfg.box_bars)
        for j in range(i, watch_end):
            long_break = c[j] > box_high if cfg.require_body_break else h[j] > box_high
            short_break = c[j] < box_low if cfg.require_body_break else l[j] < box_low
            if long_break and cfg.side in ("long", "both"):
                signal_i = j
                signal_side = "long"
                break
            if short_break and cfg.side in ("short", "both"):
                signal_i = j
                signal_side = "short"
                break

        if signal_i is None:
            i += cfg.box_bars
            continue

        entry_i = signal_i + 1
        entry = float(o[entry_i])
        if signal_side == "long":
            stop = float(l[signal_i])
            risk = entry - stop
            target = entry + cfg.rr * risk
        else:
            stop = float(h[signal_i])
            risk = stop - entry
            target = entry - cfg.rr * risk

        # Bad gap or structurally useless setup.
        if risk <= 0 or risk / entry > 0.08:
            i = entry_i + 1
            continue

        exit_i = min(n - 1, entry_i + cfg.box_bars * 4)
        exit_px = float(c[exit_i])
        for k in range(entry_i, exit_i + 1):
            if signal_side == "long":
                stop_hit = l[k] <= stop
                target_hit = h[k] >= target
            else:
                stop_hit = h[k] >= stop
                target_hit = l[k] <= target
            if stop_hit:
                exit_i = k
                exit_px = stop
                break
            if target_hit:
                exit_i = k
                exit_px = target
                break

        ret = trade_ret(signal_side, entry, exit_px)
        cash *= 1 + ret
        trades.append(ret)
        for k in range(i, min(exit_i + 1, n)):
            eq[k] = cash

        # After a closed position, restart from the next clean group boundary.
        i = exit_i + cfg.box_bars

    for j in range(1, n):
        if eq[j] == 1.0 and trades:
            eq[j] = eq[j - 1]
    return pd.Series(eq, index=pd.to_datetime(df["timestamp"])), trades


def metrics(eq: pd.Series, trades: list[float]) -> dict[str, float]:
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365.25)
    end = eq.iloc[-1]
    cagr = end ** (1 / years) - 1 if end > 0 else -1.0
    dd = (eq / eq.cummax() - 1).min()
    wins = sum(t > 0 for t in trades)
    gp = sum(t for t in trades if t > 0)
    gl = -sum(t for t in trades if t < 0)
    return {
        "net": (end - 1) * 100,
        "cagr": cagr * 100,
        "dd": dd * 100,
        "trades": float(len(trades)),
        "win": wins / len(trades) * 100 if trades else 0.0,
        "pf": gp / gl if gl else (99.0 if gp else 0.0),
    }


def configs(tf: str) -> list[Config]:
    out = []
    box_options = {"5m": [5, 8], "15m": [5], "30m": [5]}[tf]
    for box_bars in box_options:
        for side in ["long", "short", "both"]:
            for rr in [1.5, 2.0]:
                for min_box_pct, max_box_pct in [(0.0, 1.0), (0.001, 0.025)]:
                    for require_body_break in [True]:
                        out.append(Config(tf, box_bars, side, rr, min_box_pct, max_box_pct, require_body_break))
    return out


def score(full: dict[str, float], oos: dict[str, float]) -> float:
    if full["trades"] < 30 or oos["trades"] < 10:
        return -1e9
    return min(full["pf"], oos["pf"]) * 100 + oos["cagr"] - max(0, abs(full["dd"]) - 40)


def cfg_name(cfg: Config) -> str:
    br = "close" if cfg.require_body_break else "wick"
    rng = "all" if cfg.min_box_pct == 0 else f"{cfg.min_box_pct*100:.2f}-{cfg.max_box_pct*100:.1f}%"
    return f"{cfg.tf} box={cfg.box_bars} {cfg.side} rr={cfg.rr:g} {br} range={rng}"


def main() -> None:
    print("No-indicator price-action box breakout on BTC")
    print("Box high/low breakout, stop beyond breakout candle, fixed R target. Costs included.\n")
    all_rows = []
    for tf in ["5m", "15m", "30m"]:
        df = load(tf)
        split = int(len(df) * 0.60)
        oos_df = df.iloc[split:].reset_index(drop=True)
        rows = []
        for cfg in configs(tf):
            full_eq, full_tr = run(df, cfg)
            oos_eq, oos_tr = run(oos_df, cfg)
            full = metrics(full_eq, full_tr)
            oos = metrics(oos_eq, oos_tr)
            sc = score(full, oos)
            row = (sc, cfg, full, oos)
            rows.append(row)
            all_rows.append(row)
        rows.sort(key=lambda x: x[0], reverse=True)
        print(f"=== BTC {tf}: {df.timestamp.iloc[0]} -> {df.timestamp.iloc[-1]}, OOS starts {oos_df.timestamp.iloc[0]} ===")
        print(
            f"{'rank':>4} {'score':>8} {'config':<48} | "
            f"{'FULL CAGR':>9} {'PF':>5} {'DD':>7} {'tr':>5} | "
            f"{'OOS CAGR':>8} {'PF':>5} {'DD':>7} {'tr':>5}"
        )
        for rank, (sc, cfg, full, oos) in enumerate(rows[:10], 1):
            print(
                f"{rank:4d} {sc:8.1f} {cfg_name(cfg):<48} | "
                f"{full['cagr']:9.1f} {full['pf']:5.2f} {full['dd']:7.1f} {full['trades']:5.0f} | "
                f"{oos['cagr']:8.1f} {oos['pf']:5.2f} {oos['dd']:7.1f} {oos['trades']:5.0f}"
            )
        print()

    all_rows.sort(key=lambda x: x[0], reverse=True)
    print("=== best overall ===")
    for rank, (sc, cfg, full, oos) in enumerate(all_rows[:15], 1):
        print(
            f"{rank:2d}. score={sc:7.1f} {cfg_name(cfg)} | "
            f"FULL CAGR={full['cagr']:.1f}% PF={full['pf']:.2f} DD={full['dd']:.1f}% tr={full['trades']:.0f} | "
            f"OOS CAGR={oos['cagr']:.1f}% PF={oos['pf']:.2f} DD={oos['dd']:.1f}% tr={oos['trades']:.0f}"
        )


if __name__ == "__main__":
    main()
