#!/usr/bin/env python3
"""Research-only dynamic neutral grid backtest on BTCUSDT 4H.

This models a neutral futures-style grid:
  - grid center follows BTC dynamically when price drifts far enough
  - buy levels below center, sell levels above center
  - buys add/reduce long inventory, sells add/reduce short inventory
  - realized PnL is booked when inventory is reduced
  - strong-trend regime flattens and pauses the grid

The goal is to test whether a 4H dynamically moved neutral grid is viable before
building or running any live order placement.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import pandas as pd


FEE_PCT = 0.00055
SLIP_PCT = 0.0005


@dataclass
class GridConfig:
    levels_each_side: int = 8
    atr_width_mult: float = 3.0
    min_width_pct: float = 0.035
    recenter_atr_mult: float = 1.25
    max_adx: float = 28.0
    min_atr_pct: float = 0.003
    max_atr_pct: float = 0.035
    notional_per_level_frac: float = 0.06
    max_position_frac: float = 0.55


@dataclass
class GridState:
    cash: float = 1.0
    position_qty: float = 0.0
    avg_entry: float = 0.0
    center: float = 0.0
    low: float = 0.0
    high: float = 0.0
    step: float = 0.0
    active: bool = False
    fills: int = 0
    recenters: int = 0
    flatten_count: int = 0
    realized: float = 0.0


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
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    pdm = ((up > dn) & (up > 0)) * up.clip(lower=0)
    ndm = ((dn > up) & (dn > 0)) * dn.clip(lower=0)
    a = atr(df, n)
    pdi = 100 * ema(pdm, n) / a
    ndi = 100 * ema(ndm, n) / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, 1e-9)
    return ema(dx, n)


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["atr"] = atr(out, 14)
    out["atr_pct"] = out["atr"] / out["close"]
    out["adx14"] = adx(out, 14)
    out["ema50"] = ema(out["close"], 50)
    return out


def equity(st: GridState, price: float) -> float:
    return st.cash + st.position_qty * (price - st.avg_entry)


def configure_grid(st: GridState, price: float, atr_now: float, cfg: GridConfig) -> None:
    width = max(cfg.atr_width_mult * atr_now, price * cfg.min_width_pct)
    st.center = price
    st.low = max(price - width, price * 0.25)
    st.high = price + width
    st.step = width / cfg.levels_each_side
    st.active = True
    st.recenters += 1


def flatten(st: GridState, price: float) -> None:
    if abs(st.position_qty) > 0:
        exit_px = price * (1 - SLIP_PCT) if st.position_qty > 0 else price * (1 + SLIP_PCT)
        pnl = st.position_qty * (exit_px - st.avg_entry)
        fee = abs(st.position_qty) * exit_px * FEE_PCT
        st.cash += pnl - fee
        st.realized += pnl - fee
    st.position_qty = 0.0
    st.avg_entry = 0.0
    st.active = False
    st.flatten_count += 1


def fill_order(st: GridState, side: int, level: float, cfg: GridConfig) -> None:
    """side=+1 buy, side=-1 sell."""
    eq = max(equity(st, level), 1e-9)
    max_abs_qty = cfg.max_position_frac * eq / level
    qty = cfg.notional_per_level_frac * eq / level
    if abs(st.position_qty + side * qty) > max_abs_qty:
        return

    fill = level * (1 + SLIP_PCT) if side == 1 else level * (1 - SLIP_PCT)
    fee = qty * fill * FEE_PCT
    old_qty = st.position_qty
    new_qty = old_qty + side * qty

    if old_qty == 0 or old_qty * side > 0:
        # Add to same-side inventory.
        st.avg_entry = fill if old_qty == 0 else (abs(old_qty) * st.avg_entry + qty * fill) / abs(new_qty)
        st.position_qty = new_qty
        st.cash -= fee
    else:
        # Reduce or flip existing inventory.
        closing_qty = min(abs(old_qty), qty)
        pnl = closing_qty * (fill - st.avg_entry) if old_qty > 0 else closing_qty * (st.avg_entry - fill)
        st.cash += pnl - fee
        st.realized += pnl - fee
        remaining_qty = qty - closing_qty
        if remaining_qty > 1e-12:
            st.position_qty = side * remaining_qty
            st.avg_entry = fill
        else:
            st.position_qty = old_qty + side * qty
            if abs(st.position_qty) < 1e-12:
                st.position_qty = 0.0
                st.avg_entry = 0.0
    st.fills += 1


def process_path(st: GridState, start: float, end: float, cfg: GridConfig) -> None:
    if not st.active or st.step <= 0:
        return
    if end < start:
        levels = [st.center - i * st.step for i in range(1, cfg.levels_each_side + 1)]
        for level in levels:
            if end <= level < start:
                fill_order(st, 1, level, cfg)
    elif end > start:
        levels = [st.center + i * st.step for i in range(1, cfg.levels_each_side + 1)]
        for level in levels:
            if start < level <= end:
                fill_order(st, -1, level, cfg)


def backtest(df: pd.DataFrame, cfg: GridConfig) -> dict[str, float]:
    st = GridState()
    curve: list[float] = []
    active_bars = 0
    start = 220
    for i in range(start, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i - 1]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        atr_now = float(prev["atr"])
        adx_now = float(prev["adx14"])
        atr_pct = float(prev["atr_pct"])
        regime_ok = adx_now <= cfg.max_adx and cfg.min_atr_pct <= atr_pct <= cfg.max_atr_pct

        if not regime_ok:
            if st.active or abs(st.position_qty) > 0:
                flatten(st, o)
        else:
            drift = abs(o - st.center) if st.center else float("inf")
            should_recenter = (not st.active) or drift >= cfg.recenter_atr_mult * atr_now or o < st.low or o > st.high
            if should_recenter:
                configure_grid(st, o, atr_now, cfg)

        if st.active:
            active_bars += 1
            if c >= o:
                process_path(st, o, l, cfg)
                process_path(st, l, h, cfg)
                process_path(st, h, c, cfg)
            else:
                process_path(st, o, h, cfg)
                process_path(st, h, l, cfg)
                process_path(st, l, c, cfg)

        curve.append(equity(st, c))

    if abs(st.position_qty) > 0:
        flatten(st, float(df.iloc[-1]["close"]))
        curve.append(st.cash)

    s = pd.Series(curve)
    dd = s / s.cummax() - 1.0
    days = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[start]).total_seconds() / 86400
    cagr = (st.cash ** (365 / days) - 1) * 100 if days > 0 and st.cash > 0 else -100.0
    return {
        "net_pct": (st.cash - 1.0) * 100,
        "cagr_pct": cagr,
        "max_dd_pct": float(dd.min() * 100),
        "fills": float(st.fills),
        "recenters": float(st.recenters),
        "flattens": float(st.flatten_count),
        "active_pct": active_bars / max(len(df) - start, 1) * 100,
        "realized_pct": st.realized * 100,
    }


def print_stats(name: str, stats: dict[str, float]) -> None:
    print(
        f"{name:36s} net={stats['net_pct']:8.2f}% CAGR={stats['cagr_pct']:8.2f}% "
        f"DD={stats['max_dd_pct']:7.2f}% fills={stats['fills']:5.0f} "
        f"recenters={stats['recenters']:4.0f} active={stats['active_pct']:5.1f}%"
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", default="data/cache/BTCUSDT_1h_12000_bybit.csv")
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--quick", action="store_true")
    args = p.parse_args()

    raw_1h = load_ohlcv(args.csv)
    df = add_features(resample_ohlcv(raw_1h, "4h"))
    split = int(len(df) * 0.6)
    is_df = df.iloc[:split].reset_index(drop=True)
    oos_df = df.iloc[split:].reset_index(drop=True)
    print(f"data: {df.timestamp.iloc[0]} -> {df.timestamp.iloc[-1]} bars={len(df)} tf=4h")

    rows = []
    level_grid = (7, 9) if args.quick else (5, 7, 9, 12)
    width_grid = (3.0, 4.0) if args.quick else (2.0, 3.0, 4.0)
    recenter_grid = (1.25,) if args.quick else (0.75, 1.25, 1.75)
    adx_grid = (26, 30) if args.quick else (22, 26, 30, 34)
    notional_grid = (0.03, 0.05) if args.quick else (0.03, 0.05, 0.08)
    for levels in level_grid:
        for width in width_grid:
            for recenter in recenter_grid:
                for adx_max in adx_grid:
                    for notional in notional_grid:
                        cfg = GridConfig(
                            levels_each_side=levels,
                            atr_width_mult=width,
                            recenter_atr_mult=recenter,
                            max_adx=adx_max,
                            notional_per_level_frac=notional,
                        )
                        ins = backtest(is_df, cfg)
                        oos = backtest(oos_df, cfg)
                        full = backtest(df, cfg)
                        score = min(ins["cagr_pct"], oos["cagr_pct"]) - max(abs(ins["max_dd_pct"]), abs(oos["max_dd_pct"]))
                        rows.append((score, cfg, full, ins, oos))

    rows.sort(key=lambda x: x[0], reverse=True)
    print(
        f"{'config':36s} {'FULL net/CAGR/DD':>27} {'IS net/CAGR/DD':>27} {'OOS net/CAGR/DD':>27}"
    )
    for _, cfg, full, ins, oos in rows[: args.top]:
        name = (
            f"lv={cfg.levels_each_side} w={cfg.atr_width_mult:g} "
            f"rc={cfg.recenter_atr_mult:g} adx={cfg.max_adx:g} n={cfg.notional_per_level_frac:g}"
        )
        print(
            f"{name:36s} "
            f"{full['net_pct']:7.1f}/{full['cagr_pct']:6.1f}/{full['max_dd_pct']:6.1f} "
            f"{ins['net_pct']:7.1f}/{ins['cagr_pct']:6.1f}/{ins['max_dd_pct']:6.1f} "
            f"{oos['net_pct']:7.1f}/{oos['cagr_pct']:6.1f}/{oos['max_dd_pct']:6.1f}"
        )

    _, cfg, full, ins, oos = rows[0]
    print("\nBest candidate details:")
    print_stats("FULL", full)
    print_stats("IS", ins)
    print_stats("OOS", oos)
    print(f"Selected config: {cfg}")


if __name__ == "__main__":
    main()
