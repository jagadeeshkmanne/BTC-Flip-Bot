#!/usr/bin/env python3
"""Research-only supply/demand zone refinement backtest for BTCUSDT.

This converts the video idea into mechanical rules:
  - higher timeframe break of structure defines bias
  - last small/opposite candle before an impulse becomes a supply/demand zone
  - lower timeframe zones inside that HTF zone refine the limit entry
  - use the extreme refined zone; stop beyond the refined zone; target by RR

The zone detector is an approximation of discretionary chart marking. Treat this
as a hypothesis test, not a live-ready trading system.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import pandas as pd


FEE_PCT = 0.00055
SLIP_PCT = 0.0005


@dataclass
class Zone:
    side: int
    made_i: int
    lo: float
    hi: float
    impulse_atr: float
    bos_level: float


@dataclass
class Trade:
    side: int
    entry_i: int
    exit_i: int
    entry: float
    exit: float
    stop: float
    target: float
    pnl_r: float
    pnl_cash: float
    reason: str


def load_ohlcv(path: str) -> pd.DataFrame:
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


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["atr"] = atr(out, 14)
    out["body"] = (out["close"] - out["open"]).abs()
    out["range"] = (out["high"] - out["low"]).replace(0, pd.NA)
    out["body_frac"] = out["body"] / out["range"]
    out["dir"] = 0
    out.loc[out["close"] > out["open"], "dir"] = 1
    out.loc[out["close"] < out["open"], "dir"] = -1
    return out


def pivots(df: pd.DataFrame, left: int, right: int) -> tuple[list[int], list[int]]:
    highs: list[int] = []
    lows: list[int] = []
    for i in range(left, len(df) - right):
        win = df.iloc[i - left : i + right + 1]
        if float(df.loc[i, "high"]) >= float(win["high"].max()):
            highs.append(i)
        if float(df.loc[i, "low"]) <= float(win["low"].min()):
            lows.append(i)
    return highs, lows


def last_pivot_before(piv: list[int], i: int) -> int | None:
    for x in reversed(piv):
        if x < i:
            return x
    return None


def make_zones(
    df: pd.DataFrame,
    pivot_left: int,
    pivot_right: int,
    impulse_atr: float,
    max_base_bars: int,
    max_body_frac: float,
) -> list[Zone]:
    highs, lows = pivots(df, pivot_left, pivot_right)
    zones: list[Zone] = []
    for i in range(max(pivot_left + pivot_right + 5, 30), len(df)):
        atr_i = float(df.loc[i, "atr"])
        if not atr_i or pd.isna(atr_i):
            continue

        prev_high_i = last_pivot_before(highs, i - pivot_right)
        prev_low_i = last_pivot_before(lows, i - pivot_right)
        broke_up = prev_high_i is not None and float(df.loc[i, "close"]) > float(df.loc[prev_high_i, "high"])
        broke_down = prev_low_i is not None and float(df.loc[i, "close"]) < float(df.loc[prev_low_i, "low"])
        if not (broke_up or broke_down):
            continue

        side = 1 if broke_up else -1
        impulse_start = None
        for j in range(i - 1, max(-1, i - max_base_bars - 1), -1):
            body_ok = float(df.loc[j, "body_frac"]) <= max_body_frac if not pd.isna(df.loc[j, "body_frac"]) else False
            opposite = int(df.loc[j, "dir"]) in (0, -side)
            if body_ok or opposite:
                impulse_start = j
                break
        if impulse_start is None:
            continue

        if side == 1:
            move = float(df.loc[i, "high"]) - float(df.loc[impulse_start, "low"])
            bos_level = float(df.loc[prev_high_i, "high"])
        else:
            move = float(df.loc[impulse_start, "high"]) - float(df.loc[i, "low"])
            bos_level = float(df.loc[prev_low_i, "low"])
        if move < impulse_atr * atr_i:
            continue

        lo = float(df.loc[impulse_start, "low"])
        hi = float(df.loc[impulse_start, "high"])
        zones.append(Zone(side=side, made_i=i + pivot_right, lo=lo, hi=hi, impulse_atr=move / atr_i, bos_level=bos_level))
    return zones


def refine_zone(
    ltf: pd.DataFrame,
    side: int,
    htf_zone: Zone,
    search_start: pd.Timestamp,
    search_end: pd.Timestamp,
    impulse_atr: float,
    max_base_bars: int,
    max_body_frac: float,
) -> tuple[float, float] | None:
    part = ltf[(ltf["timestamp"] >= search_start) & (ltf["timestamp"] < search_end)].copy()
    part = part[(part["low"] >= htf_zone.lo) & (part["high"] <= htf_zone.hi)]
    if part.empty:
        return None

    candidates: list[tuple[float, float, float]] = []
    idxs = list(part.index)
    idx_set = set(idxs)
    for i in idxs:
        if i + 3 >= len(ltf):
            continue
        row = ltf.loc[i]
        body_ok = float(row["body_frac"]) <= max_body_frac if not pd.isna(row["body_frac"]) else False
        opposite = int(row["dir"]) in (0, -side)
        if not (body_ok or opposite):
            continue
        look_end = min(len(ltf), i + max_base_bars + 1)
        fut = ltf.iloc[i + 1 : look_end]
        if fut.empty:
            continue
        atr_i = float(row["atr"])
        if not atr_i or pd.isna(atr_i):
            continue
        if side == 1:
            move = float(fut["high"].max()) - float(row["low"])
        else:
            move = float(row["high"]) - float(fut["low"].min())
        if move < impulse_atr * atr_i:
            continue
        if i not in idx_set:
            continue
        candidates.append((float(row["low"]), float(row["high"]), move / atr_i))

    if not candidates:
        return None
    if side == 1:
        lo, hi, _ = min(candidates, key=lambda x: x[0])
    else:
        lo, hi, _ = max(candidates, key=lambda x: x[1])
    return lo, hi


def trade_return(side: int, entry: float, exit_px: float) -> float:
    entry_fill = entry * (1 + SLIP_PCT) if side == 1 else entry * (1 - SLIP_PCT)
    exit_fill = exit_px * (1 - SLIP_PCT) if side == 1 else exit_px * (1 + SLIP_PCT)
    gross = (exit_fill - entry_fill) / entry_fill if side == 1 else (entry_fill - exit_fill) / entry_fill
    return gross - 2 * FEE_PCT


def backtest(
    ltf: pd.DataFrame,
    htf: pd.DataFrame,
    zones: list[Zone],
    *,
    rr: float,
    stop_buffer_atr: float,
    max_hold_bars: int,
    max_zone_age_bars: int,
    cooldown_bars: int,
    refine: bool,
    refine_impulse_atr: float,
    allow_long: bool,
    allow_short: bool,
) -> tuple[dict[str, float], list[Trade]]:
    cash = 1.0
    peak = 1.0
    max_dd = 0.0
    trades: list[Trade] = []
    last_exit = -cooldown_bars
    used_zones: set[int] = set()

    htf_times = list(htf["timestamp"])
    zone_cursor = 0
    active: list[tuple[int, Zone]] = []

    for i in range(200, len(ltf) - 1):
        now = ltf.loc[i, "timestamp"]
        while zone_cursor < len(zones) and htf_times[zones[zone_cursor].made_i] <= now:
            z = zones[zone_cursor]
            if (z.side == 1 and allow_long) or (z.side == -1 and allow_short):
                active.append((zone_cursor, z))
            zone_cursor += 1

        if i - last_exit < cooldown_bars:
            continue

        for zone_id, z in list(active):
            if zone_id in used_zones:
                continue
            if zone_cursor - z.made_i > max_zone_age_bars:
                used_zones.add(zone_id)
                continue

            touched = float(ltf.loc[i, "low"]) <= z.hi and float(ltf.loc[i, "high"]) >= z.lo
            if not touched:
                continue

            entry_lo, entry_hi = z.lo, z.hi
            if refine:
                htf_start = htf.loc[max(0, z.made_i - 2), "timestamp"]
                htf_end_i = min(len(htf) - 1, z.made_i + 1)
                htf_end = htf.loc[htf_end_i, "timestamp"]
                refined = refine_zone(
                    ltf,
                    z.side,
                    z,
                    htf_start,
                    htf_end,
                    refine_impulse_atr,
                    10,
                    0.55,
                )
                if refined is not None:
                    entry_lo, entry_hi = refined

            atr_i = float(ltf.loc[i, "atr"])
            if z.side == 1:
                entry = entry_hi
                stop = entry_lo - stop_buffer_atr * atr_i
                target = entry + rr * (entry - stop)
                if stop >= entry:
                    used_zones.add(zone_id)
                    continue
                entry_hit = float(ltf.loc[i, "low"]) <= entry
            else:
                entry = entry_lo
                stop = entry_hi + stop_buffer_atr * atr_i
                target = entry - rr * (stop - entry)
                if stop <= entry:
                    used_zones.add(zone_id)
                    continue
                entry_hit = float(ltf.loc[i, "high"]) >= entry
            if not entry_hit:
                continue

            risk = abs(entry - stop)
            exit_px = float(ltf.loc[min(len(ltf) - 1, i + max_hold_bars), "close"])
            reason = "time"
            exit_i = min(len(ltf) - 1, i + max_hold_bars)
            for j in range(i, min(len(ltf), i + max_hold_bars + 1)):
                hi = float(ltf.loc[j, "high"])
                lo = float(ltf.loc[j, "low"])
                stop_hit = lo <= stop if z.side == 1 else hi >= stop
                target_hit = hi >= target if z.side == 1 else lo <= target
                if stop_hit:
                    exit_px = stop
                    reason = "stop"
                    exit_i = j
                    break
                if target_hit:
                    exit_px = target
                    reason = "target"
                    exit_i = j
                    break

            pct = trade_return(z.side, entry, exit_px)
            pnl_cash = cash * pct
            cash += pnl_cash
            peak = max(peak, cash)
            max_dd = max(max_dd, (peak - cash) / peak)
            pnl_r = ((exit_px - entry) / risk) if z.side == 1 else ((entry - exit_px) / risk)
            trades.append(Trade(z.side, i, exit_i, entry, exit_px, stop, target, pnl_r, pnl_cash, reason))
            used_zones.add(zone_id)
            last_exit = exit_i
            break

    gross_profit = sum(t.pnl_cash for t in trades if t.pnl_cash > 0)
    gross_loss = -sum(t.pnl_cash for t in trades if t.pnl_cash < 0)
    wins = sum(1 for t in trades if t.pnl_cash > 0)
    stats = {
        "trades": float(len(trades)),
        "wins": float(wins),
        "win_rate": wins / len(trades) if trades else 0.0,
        "pf": gross_profit / gross_loss if gross_loss else (float("inf") if gross_profit else 0.0),
        "net_pct": (cash - 1.0) * 100.0,
        "max_dd_pct": max_dd * 100.0,
        "avg_r": sum(t.pnl_r for t in trades) / len(trades) if trades else 0.0,
        "targets": float(sum(1 for t in trades if t.reason == "target")),
        "stops": float(sum(1 for t in trades if t.reason == "stop")),
    }
    return stats, trades


def print_stats(name: str, stats: dict[str, float]) -> None:
    print(
        f"{name:20s} trades={stats['trades']:4.0f} win={stats['win_rate']*100:5.1f}% "
        f"PF={stats['pf']:.3f} net={stats['net_pct']:7.2f}% DD={stats['max_dd_pct']:6.2f}% "
        f"avgR={stats['avg_r']:.2f} targets={stats['targets']:.0f} stops={stats['stops']:.0f}"
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--ltf-csv", default="data/cache/BTCUSDT_15m_16000_bybit.csv")
    p.add_argument("--htf", choices=["1h", "4h"], default="4h")
    p.add_argument("--rr", type=float, default=3.0)
    p.add_argument("--stop-buffer-atr", type=float, default=0.10)
    p.add_argument("--max-hold-bars", type=int, default=192)
    p.add_argument("--max-zone-age-bars", type=int, default=160)
    p.add_argument("--cooldown-bars", type=int, default=8)
    p.add_argument("--sides", choices=["both", "long", "short"], default="both")
    args = p.parse_args()

    ltf = add_features(load_ohlcv(args.ltf_csv))
    htf_rule = "4h" if args.htf == "4h" else "1h"
    htf = add_features(resample_ohlcv(ltf, htf_rule))

    allow_long = args.sides in ("both", "long")
    allow_short = args.sides in ("both", "short")
    zones = make_zones(
        htf,
        pivot_left=2,
        pivot_right=2,
        impulse_atr=1.20,
        max_base_bars=5,
        max_body_frac=0.55,
    )

    print(f"data: {ltf['timestamp'].iloc[0]} -> {ltf['timestamp'].iloc[-1]}  ltf=15m htf={args.htf} zones={len(zones)}")
    for refine in (False, True):
        stats, trades = backtest(
            ltf,
            htf,
            zones,
            rr=args.rr,
            stop_buffer_atr=args.stop_buffer_atr,
            max_hold_bars=args.max_hold_bars,
            max_zone_age_bars=args.max_zone_age_bars,
            cooldown_bars=args.cooldown_bars,
            refine=refine,
            refine_impulse_atr=0.90,
            allow_long=allow_long,
            allow_short=allow_short,
        )
        print_stats("refined" if refine else "htf-zone", stats)
        if trades:
            last = trades[-5:]
            for t in last:
                ts0 = ltf.loc[t.entry_i, "timestamp"]
                ts1 = ltf.loc[t.exit_i, "timestamp"]
                side = "LONG" if t.side == 1 else "SHORT"
                print(f"  {side:5s} {ts0} -> {ts1} {t.reason:6s} R={t.pnl_r:6.2f} pnl={t.pnl_cash*100:7.3f}%")


if __name__ == "__main__":
    main()
