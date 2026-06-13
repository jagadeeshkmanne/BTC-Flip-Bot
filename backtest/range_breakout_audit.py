#!/usr/bin/env python3
"""Audit whether observable range breaks explain v2.1/v2.2 losses.

All filters use only the closed signal bar and earlier bars. Entries remain at
the next 5m open. Results use the faithful engine's honest fills, taker fees,
and slippage. The fixed-cap ledger keeps the full sample comparable after the
original fixed-$5K strategy would have exhausted its account.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from fresh_honest import CONFIGS, prep, run

OOS_START = pd.Timestamp("2024-01-01")
MODE = dict(fill="honest", fee=0.00055, slip=0.0002)


def add_range_features(bt: pd.DataFrame) -> pd.DataFrame:
    out = bt.copy()
    for hours in (6, 12, 24):
        bars = hours * 12
        out[f"prior_hi_{hours}h"] = out["high"].rolling(bars).max().shift(1)
        out[f"prior_lo_{hours}h"] = out["low"].rolling(bars).min().shift(1)
    return out


def masks(bt: pd.DataFrame) -> dict[str, np.ndarray]:
    long_sig = bt["rsi"] <= 35
    short_sig = bt["rsi"] >= 65
    result: dict[str, np.ndarray] = {"baseline": np.ones(len(bt), dtype=bool)}

    for hours in (6, 12, 24):
        hi = bt[f"prior_hi_{hours}h"]
        lo = bt[f"prior_lo_{hours}h"]
        close_breakout = ((long_sig & (bt["close"] < lo)) |
                          (short_sig & (bt["close"] > hi)))
        wick_breakout = ((long_sig & (bt["low"] <= lo)) |
                         (short_sig & (bt["high"] >= hi)))
        close_inside = ((long_sig & (bt["close"] >= lo)) |
                        (short_sig & (bt["close"] <= hi)) |
                        (~long_sig & ~short_sig))
        wick_inside = ((long_sig & (bt["low"] > lo)) |
                       (short_sig & (bt["high"] < hi)) |
                       (~long_sig & ~short_sig))
        result[f"no {hours}h close breakout"] = close_inside.fillna(False).to_numpy()
        result[f"no {hours}h wick breakout"] = wick_inside.fillna(False).to_numpy()
        result[f"only {hours}h close breakout"] = close_breakout.fillna(False).to_numpy()
        result[f"only {hours}h wick breakout"] = wick_breakout.fillna(False).to_numpy()

    hi24, lo24 = bt["prior_hi_24h"], bt["prior_lo_24h"]
    width = hi24 - lo24
    # Require a small cushion inside the old range, rather than accepting a
    # signal that merely closed one tick back within the boundary.
    for cushion in (0.05, 0.10, 0.25):
        frac = cushion / 100.0
        cushioned = ((long_sig & (bt["close"] >= lo24 * (1 + frac))) |
                     (short_sig & (bt["close"] <= hi24 * (1 - frac))) |
                     (~long_sig & ~short_sig))
        result[f"24h inside +{cushion:.2f}%"] = cushioned.fillna(False).to_numpy()

    # A genuine range should not already be unusually wide. This is known at
    # signal close and avoids selecting volatility expansions as "range edges".
    width_pct = width / bt["close"]
    rolling_median = width_pct.rolling(288).median().shift(1)
    quiet_range = (width_pct <= rolling_median).fillna(False)
    result["24h inside + quiet width"] = (
        result["no 24h close breakout"] & quiet_range.to_numpy()
    )
    return result


def stats(result: dict) -> tuple[int, float, float, float]:
    nets = np.array([trade[0] for trade in result["trades"]], dtype=float)
    if not len(nets):
        return 0, 0.0, 0.0, 0.0
    wins = nets[nets > 0].sum()
    losses = nets[nets < 0].sum()
    pf = wins / abs(losses) if losses < 0 else float("inf")
    return len(nets), nets.sum(), nets.mean(), pf


def segment(bt: pd.DataFrame, start=None, end=None) -> pd.DataFrame:
    keep = np.ones(len(bt), dtype=bool)
    if start is not None:
        keep &= bt["timestamp"].to_numpy() >= np.datetime64(start)
    if end is not None:
        keep &= bt["timestamp"].to_numpy() < np.datetime64(end)
    return bt.loc[keep].reset_index(drop=True)


def main() -> None:
    full = add_range_features(prep())
    periods = {
        "IS": segment(full, end=OOS_START),
        "OOS": segment(full, start=OOS_START),
    }
    print(
        f"Data {full['timestamp'].iloc[0]} -> {full['timestamp'].iloc[-1]} | "
        "honest fills + 0.055%/side fees + 0.02% market slip"
    )
    print("Fixed-cap ledger is diagnostic; baseline fixed-$5K account exhausts in 2019.\n")

    for cfg, params in CONFIGS.items():
        print(f"== {cfg} ==")
        print(
            f"{'filter':<27} | {'IS N':>6} {'IS PnL':>10} {'IS avg':>8} {'IS PF':>6} | "
            f"{'OOS N':>6} {'OOS PnL':>10} {'OOS avg':>8} {'OOS PF':>6}"
        )
        period_masks = {name: masks(frame) for name, frame in periods.items()}
        for name in period_masks["IS"]:
            cells = []
            for period_name, frame in periods.items():
                result = run(
                    frame,
                    params["tp_dca"],
                    params["time_sl"],
                    fixed_cap=True,
                    signal_mask=period_masks[period_name][name],
                    **MODE,
                )
                cells.append(stats(result))
            (ni, pi, ai, pfi), (no, po, ao, pfo) = cells
            print(
                f"{name:<27} | {ni:>6,} ${pi:>+9,.0f} ${ai:>+7.2f} {pfi:>6.3f} | "
                f"{no:>6,} ${po:>+9,.0f} ${ao:>+7.2f} {pfo:>6.3f}"
            )
        print()


if __name__ == "__main__":
    main()
