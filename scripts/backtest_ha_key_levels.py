#!/usr/bin/env python3
"""Research-only Heikin Ashi + key-level strategy for BTCUSDT.

This turns the video screenshots into testable rules:
  - draw key levels from real OHLC pivot highs/lows, not HA candles
  - wait for price to revisit a prior support/resistance zone
  - use Heikin Ashi only as confirmation of momentum shift
  - fill stops/targets with real OHLC, fees, and slippage

The level detector is an approximation of manual chart drawing, so treat results
as a first pass rather than a live-ready strategy.
"""
from __future__ import annotations

import argparse
import math
import os
import time
from dataclasses import dataclass

import pandas as pd
import requests


PAIR = "BTCUSDT"
BYBIT_BASE = "https://api.bybit.com"
FEE_PCT = 0.00055
SLIP_PCT = 0.0005

INTERVALS = {
    "15m": "15",
    "1h": "60",
}


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
        axis=1,
    ).max(axis=1)
    return ema(tr, n)


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, math.nan)
    return 100 - (100 / (1 + rs))


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


def fetch_bybit(symbol: str, interval: str, bars: int, cache_dir: str | None = "data/cache") -> pd.DataFrame:
    cache_path = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = os.path.join(cache_dir, f"{symbol}_{interval}_{bars}_bybit.csv")
        if os.path.exists(cache_path):
            cached = pd.read_csv(cache_path, parse_dates=["timestamp"])
            if len(cached) >= bars:
                return cached.tail(bars).reset_index(drop=True)

    rows: list[list[str]] = []
    end_ms: int | None = None
    bb_interval = INTERVALS[interval]
    while len(rows) < bars:
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": bb_interval,
            "limit": min(1000, bars - len(rows)),
        }
        if end_ms is not None:
            params["end"] = end_ms
        r = requests.get(f"{BYBIT_BASE}/v5/market/kline", params=params, timeout=20)
        r.raise_for_status()
        body = r.json()
        if body.get("retCode") != 0:
            raise RuntimeError(f"Bybit retCode={body.get('retCode')} {body.get('retMsg')}")
        batch = body.get("result", {}).get("list", [])
        if not batch:
            break
        rows.extend(batch)
        end_ms = min(int(x[0]) for x in batch) - 1
        time.sleep(0.06)

    rows = sorted({int(x[0]): x for x in rows}.values(), key=lambda x: int(x[0]))
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
            "open": [float(x[1]) for x in rows],
            "high": [float(x[2]) for x in rows],
            "low": [float(x[3]) for x in rows],
            "close": [float(x[4]) for x in rows],
            "volume": [float(x[5]) for x in rows],
        }
    ).reset_index(drop=True)
    if cache_path:
        df.to_csv(cache_path, index=False)
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["atr"] = atr(out, 14)
    out["ema200"] = ema(out["close"], 200)
    out["ema50"] = ema(out["close"], 50)
    out["rsi14"] = rsi(out["close"], 14)
    out["adx14"] = adx(out, 14)
    out["vol_ratio"] = out["volume"] / out["volume"].rolling(50).mean()

    ha_close = (out["open"] + out["high"] + out["low"] + out["close"]) / 4.0
    ha_open = [float((out.loc[0, "open"] + out.loc[0, "close"]) / 2.0)]
    for i in range(1, len(out)):
        ha_open.append((ha_open[-1] + float(ha_close.iloc[i - 1])) / 2.0)
    out["ha_open"] = ha_open
    out["ha_close"] = ha_close
    out["ha_high"] = pd.concat([out["high"], out["ha_open"], out["ha_close"]], axis=1).max(axis=1)
    out["ha_low"] = pd.concat([out["low"], out["ha_open"], out["ha_close"]], axis=1).min(axis=1)
    out["ha_green"] = out["ha_close"] > out["ha_open"]
    out["ha_red"] = out["ha_close"] < out["ha_open"]
    out["ha_body"] = (out["ha_close"] - out["ha_open"]).abs()
    out["ha_range"] = (out["ha_high"] - out["ha_low"]).replace(0, math.nan)
    out["ha_body_frac"] = out["ha_body"] / out["ha_range"]
    out["ha_no_lower_wick"] = out["ha_low"] >= pd.concat([out["ha_open"], out["ha_close"]], axis=1).min(axis=1) * 0.999999
    out["ha_no_upper_wick"] = out["ha_high"] <= pd.concat([out["ha_open"], out["ha_close"]], axis=1).max(axis=1) * 1.000001
    return out


def pivots(df: pd.DataFrame, left: int, right: int, move_lookahead: int, move_atr: float) -> tuple[list[int], list[int]]:
    lows: list[int] = []
    highs: list[int] = []
    for i in range(left, len(df) - max(right, move_lookahead)):
        lo = float(df.loc[i, "low"])
        hi = float(df.loc[i, "high"])
        win = df.iloc[i - left : i + right + 1]
        atr_i = float(df.loc[i, "atr"])
        if lo <= float(win["low"].min()) and float(df.loc[i + 1 : i + move_lookahead, "high"].max()) >= lo + move_atr * atr_i:
            lows.append(i)
        if hi >= float(win["high"].max()) and float(df.loc[i + 1 : i + move_lookahead, "low"].min()) <= hi - move_atr * atr_i:
            highs.append(i)
    return lows, highs


@dataclass
class Trade:
    side: str
    entry_i: int
    exit_i: int
    entry: float
    exit: float
    r_mult: float
    pnl: float
    reason: str


def nearest_level(
    df: pd.DataFrame,
    levels: list[int],
    i: int,
    kind: str,
    max_age: int,
    zone_atr: float,
    confirm_delay: int,
) -> float | None:
    zone = zone_atr * float(df.loc[i, "atr"])
    px = float(df.loc[i, "close"])
    best = None
    best_dist = float("inf")
    if kind == "support":
        now = float(df.loc[i, "low"])
        col = "low"
    else:
        now = float(df.loc[i, "high"])
        col = "high"
    for x in reversed(levels):
        if x >= i:
            continue
        age = i - x
        if age > max_age:
            break
        if x < confirm_delay:
            continue
        level = float(df.loc[x, col])
        if abs(now - level) <= zone:
            dist = abs(px - level)
            if dist < best_dist:
                best = level
                best_dist = dist
    return best


def indicator_filter(row: pd.Series, side: int, mode: str, adx_min: float, vol_min: float) -> bool:
    if adx_min > 0 and float(row["adx14"]) < adx_min:
        return False
    if vol_min > 0 and float(row["vol_ratio"]) < vol_min:
        return False
    rsi_now = float(row["rsi14"])
    if mode == "none":
        return True
    if mode == "rsi_pullback":
        return (40 <= rsi_now <= 62) if side == 1 else (38 <= rsi_now <= 60)
    if mode == "rsi_reversal":
        return (rsi_now < 55) if side == 1 else (rsi_now > 45)
    if mode == "rsi_momentum":
        return (rsi_now > 50) if side == 1 else (rsi_now < 50)
    if mode == "avoid_extreme":
        return (rsi_now < 72) if side == 1 else (rsi_now > 28)
    raise ValueError(f"unknown filter mode: {mode}")


def allowed_side(side: int, sides: str) -> bool:
    if sides == "both":
        return True
    if sides == "long":
        return side == 1
    if sides == "short":
        return side == -1
    raise ValueError(f"unknown sides: {sides}")


def run_backtest(
    df: pd.DataFrame,
    name: str,
    use_trend_filter: bool,
    partial_runner: bool,
    pivot_left: int,
    pivot_right: int,
    zone_atr: float,
    max_age: int,
    confirm_bars: int,
    stop_atr: float,
    rr: float,
    max_hold: int,
    strict_trend: bool = False,
    filter_mode: str = "none",
    adx_min: float = 0.0,
    vol_min: float = 0.0,
    sides: str = "both",
) -> tuple[dict[str, float], list[Trade]]:
    low_pivots, high_pivots = pivots(df, pivot_left, pivot_right, 10, 1.5)
    confirm_delay = pivot_right + 10
    cash = 1.0
    equity_curve: list[float] = []
    equity_points: list[tuple[pd.Timestamp, float]] = []
    trades: list[Trade] = []
    pos: dict[str, float] | None = None
    pending: dict[str, float] | None = None
    gross_profit = 0.0
    gross_loss = 0.0
    exposed = 0

    start = max(220, confirm_delay + pivot_left + 1)
    for i in range(start, len(df) - 1):
        row = df.loc[i]
        next_row = df.loc[i + 1]
        if pos is not None:
            side = pos["side"]
            stop = pos["stop"]
            target = pos["target"]
            entry = pos["entry"]
            qty_frac = pos["qty_frac"]
            exit_price = None
            reason = None
            if side == 1:
                if float(next_row["low"]) <= stop:
                    exit_price, reason = stop * (1.0 - SLIP_PCT), "stop"
                elif float(next_row["high"]) >= target and not bool(pos["took_partial"]):
                    if partial_runner:
                        gain = 0.5 * qty_frac * ((target * (1.0 - SLIP_PCT)) / entry - 1.0)
                        cash *= 1.0 + gain
                        pos["qty_frac"] = qty_frac * 0.5
                        pos["stop"] = entry
                        pos["took_partial"] = True
                    else:
                        exit_price, reason = target * (1.0 - SLIP_PCT), "target_2r"
                elif i - int(pos["entry_i"]) >= max_hold:
                    exit_price, reason = float(next_row["open"]) * (1.0 - SLIP_PCT), "timeout"
                elif partial_runner and bool(row["ha_red"]) and bool(pos["took_partial"]):
                    exit_price, reason = float(next_row["open"]) * (1.0 - SLIP_PCT), "ha_runner_exit"
            else:
                if float(next_row["high"]) >= stop:
                    exit_price, reason = stop * (1.0 + SLIP_PCT), "stop"
                elif float(next_row["low"]) <= target and not bool(pos["took_partial"]):
                    if partial_runner:
                        gain = 0.5 * qty_frac * (entry / (target * (1.0 + SLIP_PCT)) - 1.0)
                        cash *= 1.0 + gain
                        pos["qty_frac"] = qty_frac * 0.5
                        pos["stop"] = entry
                        pos["took_partial"] = True
                    else:
                        exit_price, reason = target * (1.0 + SLIP_PCT), "target_2r"
                elif i - int(pos["entry_i"]) >= max_hold:
                    exit_price, reason = float(next_row["open"]) * (1.0 + SLIP_PCT), "timeout"
                elif partial_runner and bool(row["ha_green"]) and bool(pos["took_partial"]):
                    exit_price, reason = float(next_row["open"]) * (1.0 + SLIP_PCT), "ha_runner_exit"

            if exit_price is not None:
                if side == 1:
                    pnl = float(pos["qty_frac"]) * (exit_price / entry - 1.0)
                    r_mult = (exit_price - entry) / (entry - float(pos["initial_stop"]))
                else:
                    pnl = float(pos["qty_frac"]) * (entry / exit_price - 1.0)
                    r_mult = (entry - exit_price) / (float(pos["initial_stop"]) - entry)
                pnl -= FEE_PCT * float(pos["qty_frac"])
                cash *= 1.0 + pnl
                if pnl > 0:
                    gross_profit += pnl
                else:
                    gross_loss += -pnl
                trades.append(Trade("long" if side == 1 else "short", int(pos["entry_i"]), i + 1, entry, exit_price, r_mult, pnl, str(reason)))
                pos = None

        if pos is None:
            if pending is not None and i > int(pending["expires"]):
                pending = None
            support = nearest_level(df, low_pivots, i, "support", max_age, zone_atr, confirm_delay)
            resistance = nearest_level(df, high_pivots, i, "resistance", max_age, zone_atr, confirm_delay)
            long_trend_ok = float(row["close"]) > float(row["ema200"])
            short_trend_ok = float(row["close"]) < float(row["ema200"])
            if strict_trend:
                long_trend_ok = long_trend_ok and float(row["ema50"]) > float(row["ema200"])
                short_trend_ok = short_trend_ok and float(row["ema50"]) < float(row["ema200"])

            if support is not None and allowed_side(1, sides) and (not use_trend_filter or long_trend_ok):
                pending = {"side": 1, "level": support, "expires": i + confirm_bars}
            elif resistance is not None and allowed_side(-1, sides) and (not use_trend_filter or short_trend_ok):
                pending = {"side": -1, "level": resistance, "expires": i + confirm_bars}

            if pending is not None:
                side = int(pending["side"])
                passes_filter = indicator_filter(row, side, filter_mode, adx_min, vol_min)
                early_body = 0.10 <= float(row["ha_body_frac"]) <= 0.70
                long_confirm = passes_filter and side == 1 and bool(row["ha_green"]) and bool(row["ha_no_lower_wick"]) and early_body
                short_confirm = passes_filter and side == -1 and bool(row["ha_red"]) and bool(row["ha_no_upper_wick"]) and early_body
                if long_confirm or short_confirm:
                    entry = float(next_row["open"]) * (1.0 + SLIP_PCT * side)
                    atr_i = float(row["atr"])
                    if side == 1:
                        stop = min(float(pending["level"]) - stop_atr * atr_i, float(row["low"]) - 0.1 * atr_i)
                        risk = entry - stop
                        target = entry + rr * risk
                    else:
                        stop = max(float(pending["level"]) + stop_atr * atr_i, float(row["high"]) + 0.1 * atr_i)
                        risk = stop - entry
                        target = entry - rr * risk
                    if risk > 0:
                        cash *= 1.0 - FEE_PCT
                        pos = {
                            "side": side,
                            "entry_i": i + 1,
                            "entry": entry,
                            "stop": stop,
                            "initial_stop": stop,
                            "target": target,
                            "qty_frac": 1.0,
                            "took_partial": False,
                        }
                        pending = None

        mark = float(next_row["close"])
        if pos is None:
            equity = cash
        elif int(pos["side"]) == 1:
            equity = cash * (1.0 + float(pos["qty_frac"]) * (mark / float(pos["entry"]) - 1.0))
        else:
            equity = cash * (1.0 + float(pos["qty_frac"]) * (float(pos["entry"]) / mark - 1.0))
        equity_curve.append(equity)
        equity_points.append((pd.Timestamp(next_row["timestamp"]), equity))
        exposed += int(pos is not None)

    eq = pd.Series(equity_curve)
    dd = eq / eq.cummax() - 1.0 if len(eq) else pd.Series([0.0])
    wins = sum(1 for t in trades if t.r_mult > 0)
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    return {
        "name": name,
        "trades": float(len(trades)),
        "win_rate": wins / len(trades) * 100.0 if trades else 0.0,
        "net_pct": (cash - 1.0) * 100.0,
        "pf": pf,
        "max_dd_pct": float(dd.min() * 100.0),
        "exposure_pct": exposed / max(len(equity_curve), 1) * 100.0,
        "avg_r": sum(t.r_mult for t in trades) / len(trades) if trades else 0.0,
        "equity_points": equity_points,
    }, trades


def print_results(label: str, df: pd.DataFrame, variants: list[tuple[str, bool, bool]], args: argparse.Namespace) -> None:
    print(label)
    print("name,trades,win_rate,net_pct,pf,max_dd_pct,exposure_pct,avg_r")
    for name, trend, partial in variants:
        res, _ = run_backtest(
            df=df,
            name=name,
            use_trend_filter=trend,
            partial_runner=partial,
            pivot_left=args.pivot_left,
            pivot_right=args.pivot_right,
            zone_atr=args.zone_atr,
            max_age=args.max_age,
            confirm_bars=args.confirm_bars,
            stop_atr=args.stop_atr,
            rr=args.rr,
            max_hold=args.max_hold,
            strict_trend=args.strict_trend,
            filter_mode=args.filter,
            adx_min=args.adx_min,
            vol_min=args.vol_min,
            sides=args.sides,
        )
        print(
            f"{res['name']},{res['trades']:.0f},{res['win_rate']:.1f},{res['net_pct']:.2f},"
            f"{res['pf']:.3f},{res['max_dd_pct']:.2f},{res['exposure_pct']:.1f},{res['avg_r']:.2f}"
        )


def print_monthly(df: pd.DataFrame, args: argparse.Namespace) -> None:
    res, trades = run_backtest(
        df=df,
        name=f"{args.sides}_monthly",
        use_trend_filter=args.strict_trend,
        partial_runner=False,
        pivot_left=args.pivot_left,
        pivot_right=args.pivot_right,
        zone_atr=args.zone_atr,
        max_age=args.max_age,
        confirm_bars=args.confirm_bars,
        stop_atr=args.stop_atr,
        rr=args.rr,
        max_hold=args.max_hold,
        strict_trend=args.strict_trend,
        filter_mode=args.filter,
        adx_min=args.adx_min,
        vol_min=args.vol_min,
        sides=args.sides,
    )
    print("summary,trades,win_rate,net_pct,pf,max_dd_pct,exposure_pct,avg_r")
    print(
        f"{res['name']},{res['trades']:.0f},{res['win_rate']:.1f},{res['net_pct']:.2f},"
        f"{res['pf']:.3f},{res['max_dd_pct']:.2f},{res['exposure_pct']:.1f},{res['avg_r']:.2f}"
    )
    print()
    print("month,trades,wins,losses,profit_pct,sum_r,avg_r")
    rows = []
    for t in trades:
        month = pd.Timestamp(df.loc[t.exit_i, "timestamp"]).strftime("%Y-%m")
        rows.append({"month": month, "win": int(t.pnl > 0), "r": t.r_mult, "pnl": t.pnl})
    if not rows:
        return
    mdf = pd.DataFrame(rows)
    grouped = mdf.groupby("month")
    eq = pd.DataFrame(res["equity_points"], columns=["timestamp", "equity"])
    eq["month"] = eq["timestamp"].dt.strftime("%Y-%m")
    month_ends = eq.groupby("month")["equity"].last()
    prev_equity = 1.0
    for month, g in grouped:
        trades_n = len(g)
        wins = int(g["win"].sum())
        losses = trades_n - wins
        end_equity = float(month_ends.loc[month])
        profit_pct = (end_equity / prev_equity - 1.0) * 100.0
        prev_equity = end_equity
        sum_r = float(g["r"].sum())
        avg_r = float(g["r"].mean())
        print(f"{month},{trades_n},{wins},{losses},{profit_pct:.2f},{sum_r:.2f},{avg_r:.2f}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=PAIR)
    p.add_argument("--interval", choices=sorted(INTERVALS), required=True)
    p.add_argument("--bars", type=int, default=12000)
    p.add_argument("--pivot-left", type=int, default=6)
    p.add_argument("--pivot-right", type=int, default=6)
    p.add_argument("--zone-atr", type=float, default=0.55)
    p.add_argument("--max-age", type=int, default=650)
    p.add_argument("--confirm-bars", type=int, default=8)
    p.add_argument("--stop-atr", type=float, default=0.25)
    p.add_argument("--rr", type=float, default=2.0)
    p.add_argument("--max-hold", type=int, default=96)
    p.add_argument("--strict-trend", action="store_true")
    p.add_argument("--filter", choices=["none", "rsi_pullback", "rsi_reversal", "rsi_momentum", "avoid_extreme"], default="none")
    p.add_argument("--adx-min", type=float, default=0.0)
    p.add_argument("--vol-min", type=float, default=0.0)
    p.add_argument("--sides", choices=["both", "long", "short"], default="both")
    p.add_argument("--sweep", action="store_true")
    p.add_argument("--monthly", action="store_true")
    args = p.parse_args()

    df = add_indicators(fetch_bybit(args.symbol, args.interval, args.bars))
    print(f"symbol={args.symbol} interval={args.interval} bars={len(df)} from={df.timestamp.iloc[0]} to={df.timestamp.iloc[-1]}")
    print(
        "rules=real-OHLC pivot support/resistance revisit + HA early strong confirmation; "
        "entries next open; stop beyond level; target 2R; fees/slippage included"
    )
    if args.monthly:
        print_monthly(df, args)
        return
    if args.sweep:
        print("sweep_name,pivot_left,zone_atr,max_age,confirm_bars,trades,win_rate,net_pct,pf,max_dd_pct,exposure_pct,avg_r")
        for pivot_left in [8, 14, 22]:
            for zone_atr in [0.20, 0.35, 0.55]:
                for max_age in [120, 300, 650]:
                    for confirm_bars in [3, 6, 10]:
                        res, _ = run_backtest(
                            df=df,
                            name="ema200_strict_full_2r",
                            use_trend_filter=True,
                            partial_runner=False,
                            pivot_left=pivot_left,
                            pivot_right=pivot_left,
                            zone_atr=zone_atr,
                            max_age=max_age,
                            confirm_bars=confirm_bars,
                            stop_atr=args.stop_atr,
                            rr=args.rr,
                            max_hold=args.max_hold,
                            strict_trend=True,
                            filter_mode=args.filter,
                            adx_min=args.adx_min,
                            vol_min=args.vol_min,
                            sides=args.sides,
                        )
                        print(
                            f"{res['name']},{pivot_left},{zone_atr:.2f},{max_age},{confirm_bars},"
                            f"{res['trades']:.0f},{res['win_rate']:.1f},{res['net_pct']:.2f},"
                            f"{res['pf']:.3f},{res['max_dd_pct']:.2f},{res['exposure_pct']:.1f},{res['avg_r']:.2f}"
                        )
        return

    variants = [
        ("video_levels_ha_full_2r", False, False),
        ("video_levels_ha_partial_runner", False, True),
        ("ema200_filtered_full_2r", True, False),
        ("ema200_filtered_partial_runner", True, True),
    ]
    print_results("full_sample", df, variants, args)
    print()
    for label, start, end in [
        ("period_2025", "2025-01-01", "2026-01-01"),
        ("period_2026_ytd", "2026-01-01", None),
    ]:
        mask = df["timestamp"] >= pd.Timestamp(start)
        if end is not None:
            mask &= df["timestamp"] < pd.Timestamp(end)
        sub = df.loc[mask].reset_index(drop=True)
        if len(sub) > 300:
            print_results(label, sub, variants, args)
            print()


if __name__ == "__main__":
    main()
