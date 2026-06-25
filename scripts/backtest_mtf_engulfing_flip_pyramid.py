#!/usr/bin/env python3
"""BTC MTF Engulfing Flip + Pyramid honest audit.

Translation target: pasted Pine "BTC Flip Bot V3 — MTF + SL-Flip + Pyramid + Tuned".

Important differences from the Pine marketing backtest:
- Signals are evaluated only on closed 1H bars.
- Entries/exits fill on the next 1H open where applicable.
- Stops are checked on real intrabar high/low, stop-first.
- Costs: 0.055% fee/side + 0.05% slippage/side on notional.
- Full Binance 1H cache has no volume column, so full-history runs can only use
  volume filter disabled. A shorter Bybit 1H cache tests the volume filter.

This is research-only and intentionally skeptical.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
FEE = 0.00055
SLIP = 0.00050


@dataclass(frozen=True)
class Config:
    name: str = "v3"
    leverage: float = 2.0
    rsi_len: int = 21
    atr_len: int = 20
    rsi_long_min: int = 45
    rsi_short_max: int = 55
    eng_body_mult: float = 1.0
    atr_ma_len: int = 50
    vol_sma_len: int = 20
    vol_spike_ratio: float = 1.5
    use_volume: bool = False
    sl_max_pct: float = 0.025
    sl_buf_pct: float = 0.001
    partial_tp_r: float = 6.0
    partial_frac: float = 0.15
    partial_be_buf_pct: float = 0.001
    same_dir_cd: int = 24
    gen_cd: int = 2
    use_sl_flip: bool = True
    flip_wait: int = 1
    flip_sl_cap_pct: float = 0.015
    flip_sr_lookback: int = 10
    flip_time_stop: int = 24
    use_pyramid: bool = True
    pyramid_r: float = 3.0
    pyramid_frac: float = 0.50
    dd_halt_pct: float = 0.25
    dd_halt_hours: int = 168
    allow_long: bool = True
    allow_short: bool = True
    exit_on_htf_invalid: bool = False
    max_hold_hours: int = 0


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False).mean()


def rsi(s: pd.Series, n: int) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0)
    dn = -d.clip(upper=0)
    rs = rma(up, n) / rma(dn, n).replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
        axis=1,
    ).max(axis=1)
    return rma(tr, n)


def macd(s: pd.Series) -> tuple[pd.Series, pd.Series]:
    line = ema(s, 12) - ema(s, 26)
    sig = ema(line, 9)
    return line, sig


def load_1h(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="raise")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="raise")
    return df


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df.columns:
        agg["volume"] = "sum"
    return df.set_index("timestamp").resample(rule).agg(agg).dropna().reset_index()


def add_features(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    out = df.copy()
    daily = resample_ohlc(out, "1D")
    daily["daily_ema50"] = ema(daily["close"], 50)
    daily["daily_close_prev"] = daily["close"].shift(1)
    daily["daily_ema50_prev"] = daily["daily_ema50"].shift(1)
    daily_feat = daily[["timestamp", "daily_close_prev", "daily_ema50_prev"]]

    h4 = resample_ohlc(out, "4h")
    h4["rsi4h"] = rsi(h4["close"], 14).shift(1)
    h4_feat = h4[["timestamp", "rsi4h"]]

    out = pd.merge_asof(out, daily_feat, on="timestamp", direction="backward")
    out = pd.merge_asof(out, h4_feat, on="timestamp", direction="backward")
    out["daily_bull"] = out["daily_close_prev"] > out["daily_ema50_prev"]
    out["daily_bear"] = out["daily_close_prev"] < out["daily_ema50_prev"]
    out["h4_bull"] = out["rsi4h"] > 50
    out["h4_bear"] = out["rsi4h"] < 50

    out["rsi1h"] = rsi(out["close"], cfg.rsi_len)
    out["macd"], out["macd_sig"] = macd(out["close"])
    out["atr"] = atr(out, cfg.atr_len)
    out["atr_ma"] = out["atr"].rolling(cfg.atr_ma_len).mean()
    out["high_vol"] = out["atr"] > out["atr_ma"]
    if "volume" in out.columns:
        out["vol_sma"] = out["volume"].rolling(cfg.vol_sma_len).mean()
        out["vol_ok"] = out["volume"] > cfg.vol_spike_ratio * out["vol_sma"]
    else:
        out["vol_ok"] = True

    body = (out["close"] - out["open"]).abs()
    prev_body = body.shift(1)
    out["bull_engulf"] = (
        (out["close"].shift(1) < out["open"].shift(1))
        & (out["close"] > out["open"])
        & (out["close"] >= out["open"].shift(1))
        & (out["open"] <= out["close"].shift(1))
        & (body > prev_body * cfg.eng_body_mult)
    )
    out["bear_engulf"] = (
        (out["close"].shift(1) > out["open"].shift(1))
        & (out["close"] < out["open"])
        & (out["open"] >= out["close"].shift(1))
        & (out["close"] <= out["open"].shift(1))
        & (body > prev_body * cfg.eng_body_mult)
    )
    vol_filter = out["vol_ok"] if cfg.use_volume and "volume" in df.columns else True
    out["long_signal"] = (
        cfg.allow_long
        &
        (out["rsi1h"] > cfg.rsi_long_min)
        & (out["macd"] > out["macd_sig"])
        & out["bull_engulf"]
        & out["high_vol"]
        & vol_filter
        & out["daily_bull"]
        & out["h4_bull"]
    ).fillna(False)
    out["short_signal"] = (
        cfg.allow_short
        &
        (out["rsi1h"] < cfg.rsi_short_max)
        & (out["macd"] < out["macd_sig"])
        & out["bear_engulf"]
        & out["high_vol"]
        & vol_filter
        & out["daily_bear"]
        & out["h4_bear"]
    ).fillna(False)
    out["stop_long"] = np.maximum(
        np.minimum(out["low"], out["low"].shift(1)) * (1 - cfg.sl_buf_pct),
        out["close"] * (1 - cfg.sl_max_pct),
    )
    out["stop_short"] = np.minimum(
        np.maximum(out["high"], out["high"].shift(1)) * (1 + cfg.sl_buf_pct),
        out["close"] * (1 + cfg.sl_max_pct),
    )
    return out


def cost_for_notional(notional: float) -> float:
    return notional * (FEE + SLIP)


def run(df_raw: pd.DataFrame, cfg: Config) -> tuple[pd.Series, list[dict]]:
    df = df_raw.copy() if {"long_signal", "short_signal", "stop_long", "stop_short"}.issubset(df_raw.columns) else add_features(df_raw, cfg)
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    ts = pd.to_datetime(df["timestamp"])
    long_sig = df["long_signal"].to_numpy(bool)
    short_sig = df["short_signal"].to_numpy(bool)
    daily_bull = df["daily_bull"].to_numpy(bool)
    daily_bear = df["daily_bear"].to_numpy(bool)
    h4_bull = df["h4_bull"].to_numpy(bool)
    h4_bear = df["h4_bear"].to_numpy(bool)
    stop_long = df["stop_long"].to_numpy(float)
    stop_short = df["stop_short"].to_numpy(float)

    equity = 1.0
    peak = 1.0
    eq = np.ones(len(df))
    trades: list[dict] = []
    pos = None
    pending_flip = None
    last_long_sl = -10**9
    last_short_sl = -10**9
    last_exit = -10**9
    halt_until = -1

    def mark_equity(i: int) -> float:
        if pos is None:
            return equity
        side = pos["side"]
        px = c[i]
        value = equity
        for leg in pos["legs"]:
            qty = leg["qty"]
            value += side * qty * (px - leg["entry"])
        return max(value, 0.0)

    def close_leg(leg: dict, side: int, px: float, frac: float) -> float:
        nonlocal equity
        qty = leg["qty"] * frac
        if qty <= 0:
            return 0.0
        notional_exit = qty * px
        pnl = side * qty * (px - leg["entry"])
        equity += pnl - cost_for_notional(notional_exit)
        leg["qty"] -= qty
        return pnl

    def close_all(i: int, px: float, reason: str) -> None:
        nonlocal pos, equity, last_exit, pending_flip, last_long_sl, last_short_sl
        if pos is None:
            return
        side = pos["side"]
        before = pos["start_equity"]
        for leg in pos["legs"]:
            close_leg(leg, side, px, 1.0)
        ret = equity / before - 1
        trades.append(
            {
                "entry_i": pos["entry_i"],
                "exit_i": i,
                "side": side,
                "ret": ret,
                "reason": reason,
                "flip": pos["is_flip"],
                "pyramided": pos["pyramided"],
            }
        )
        if reason == "stop":
            if side == 1:
                last_long_sl = i
                if cfg.use_sl_flip and not pos["is_flip"]:
                    pending_flip = {"side": -1, "bar": i, "ref": pos["stop"]}
            else:
                last_short_sl = i
                if cfg.use_sl_flip and not pos["is_flip"]:
                    pending_flip = {"side": 1, "bar": i, "ref": pos["stop"]}
        last_exit = i
        pos = None

    def open_position(i: int, side: int, px: float, stop: float, is_flip: bool) -> None:
        nonlocal pos, equity
        notional = equity * cfg.leverage
        qty = notional / px
        equity -= cost_for_notional(notional)
        pos = {
            "side": side,
            "legs": [{"qty": qty, "entry": px}],
            "entry": px,
            "entry_i": i,
            "stop": stop,
            "initial_risk": abs(px - stop),
            "partial_taken": False,
            "is_flip": is_flip,
            "flip_entry_i": i if is_flip else None,
            "pyramided": False,
            "start_equity": equity,
        }

    start = 300
    for i in range(start, len(df) - 1):
        eq[i] = mark_equity(i)
        peak = max(peak, eq[i])
        if eq[i] <= 0:
            eq[i:] = 0
            break

        # Manage current position on the current bar after prior-bar entry.
        if pos is not None:
            side = pos["side"]
            stop = pos["stop"]
            stop_hit = (l[i] <= stop) if side == 1 else (h[i] >= stop)
            if stop_hit:
                close_all(i, stop, "stop")
                eq[i] = equity
                continue

            if pos is not None and pos["is_flip"] and i - pos["entry_i"] >= cfg.flip_time_stop:
                close_all(i + 1, o[i + 1], "flip_time")
                continue

            if pos is not None and cfg.max_hold_hours > 0 and i - pos["entry_i"] >= cfg.max_hold_hours:
                close_all(i + 1, o[i + 1], "max_hold")
                continue

            if pos is not None and cfg.exit_on_htf_invalid:
                htf_invalid = (side == 1 and (not daily_bull[i] or not h4_bull[i])) or (
                    side == -1 and (not daily_bear[i] or not h4_bear[i])
                )
                if htf_invalid:
                    close_all(i + 1, o[i + 1], "htf_invalid")
                    continue

            if pos is not None and not pos["partial_taken"] and pos["initial_risk"] > 0:
                entry = pos["entry"]
                cur_r = ((c[i] - entry) / pos["initial_risk"]) if side == 1 else ((entry - c[i]) / pos["initial_risk"])
                if cur_r >= cfg.partial_tp_r:
                    for leg in pos["legs"]:
                        close_leg(leg, side, c[i], cfg.partial_frac)
                    pos["partial_taken"] = True
                    if cfg.partial_be_buf_pct > 0:
                        if side == 1:
                            pos["stop"] = max(pos["stop"], entry * (1 + cfg.partial_be_buf_pct))
                        else:
                            pos["stop"] = min(pos["stop"], entry * (1 - cfg.partial_be_buf_pct))

            if (
                pos is not None
                and cfg.use_pyramid
                and not pos["pyramided"]
                and not pos["is_flip"]
                and pos["initial_risk"] > 0
            ):
                entry = pos["entry"]
                cur_r = ((c[i] - entry) / pos["initial_risk"]) if side == 1 else ((entry - c[i]) / pos["initial_risk"])
                if cur_r >= cfg.pyramid_r:
                    add_notional = equity * cfg.leverage * cfg.pyramid_frac
                    add_qty = add_notional / c[i]
                    equity -= cost_for_notional(add_notional)
                    pos["legs"].append({"qty": add_qty, "entry": c[i]})
                    pos["pyramided"] = True
                    if side == 1:
                        pos["stop"] = max(pos["stop"], entry + 0.5 * pos["initial_risk"])
                    else:
                        pos["stop"] = min(pos["stop"], entry - 0.5 * pos["initial_risk"])

            if pos is not None and ((side == 1 and short_sig[i]) or (side == -1 and long_sig[i])):
                close_all(i + 1, o[i + 1], "opposite")
                continue

        # Drawdown halt only when flat.
        if pos is None and equity < peak * (1 - cfg.dd_halt_pct):
            halt_until = i + cfg.dd_halt_hours
            peak = equity

        exit_cd = i - last_exit < cfg.gen_cd
        halted = i < halt_until

        # Execute queued SL flip.
        if pending_flip and pos is None and i - pending_flip["bar"] >= cfg.flip_wait and not exit_cd and not halted:
            side = pending_flip["side"]
            if side == -1:
                swing = np.max(h[max(0, i - cfg.flip_sr_lookback + 1) : i + 1])
                stop = min(swing * (1 + cfg.sl_buf_pct), pending_flip["ref"] * (1 + cfg.flip_sl_cap_pct))
                if stop > o[i + 1]:
                    open_position(i + 1, side, o[i + 1], stop, True)
                    pending_flip = None
                    continue
            else:
                swing = np.min(l[max(0, i - cfg.flip_sr_lookback + 1) : i + 1])
                stop = max(swing * (1 - cfg.sl_buf_pct), pending_flip["ref"] * (1 - cfg.flip_sl_cap_pct))
                if stop < o[i + 1]:
                    open_position(i + 1, side, o[i + 1], stop, True)
                    pending_flip = None
                    continue
            pending_flip = None

        # Normal entries, filled next open.
        if pos is None and pending_flip is None and not exit_cd and not halted:
            if long_sig[i] and i - last_long_sl >= cfg.same_dir_cd and stop_long[i] < o[i + 1]:
                open_position(i + 1, 1, o[i + 1], stop_long[i], False)
                continue
            if short_sig[i] and i - last_short_sl >= cfg.same_dir_cd and stop_short[i] > o[i + 1]:
                open_position(i + 1, -1, o[i + 1], stop_short[i], False)
                continue

    if pos is not None:
        close_all(len(df) - 1, c[-1], "eod")
    eq[-1] = equity
    series = pd.Series(eq, index=ts).replace(0, np.nan).ffill().fillna(1.0)
    return series, trades


def metrics(eq: pd.Series, trades: list[dict]) -> dict[str, float]:
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365.25)
    end = eq.iloc[-1]
    cagr = end ** (1 / years) - 1 if end > 0 else -1.0
    dd = (eq / eq.cummax() - 1).min()
    wins = [t for t in trades if t["ret"] > 0]
    losses = [t for t in trades if t["ret"] <= 0]
    gp = sum(t["ret"] for t in wins)
    gl = -sum(t["ret"] for t in losses)
    return {
        "net": (end - 1) * 100,
        "cagr": cagr * 100,
        "dd": dd * 100,
        "trades": float(len(trades)),
        "win": len(wins) / len(trades) * 100 if trades else 0.0,
        "pf": gp / gl if gl else (99.0 if gp else 0.0),
        "flips": float(sum(t["flip"] for t in trades)),
        "pyramids": float(sum(t["pyramided"] for t in trades)),
    }


def run_one(label: str, df: pd.DataFrame, cfg: Config) -> tuple[str, dict, dict, dict]:
    split = int(len(df) * 0.60)
    eq, tr = run(df, cfg)
    full = metrics(eq, tr)
    oos_df = df.iloc[split:].reset_index(drop=True)
    oos_eq, oos_tr = run(oos_df, cfg)
    oos = metrics(oos_eq, oos_tr)
    ins_df = df.iloc[:split].reset_index(drop=True)
    ins_eq, ins_tr = run(ins_df, cfg)
    ins = metrics(ins_eq, ins_tr)
    return label, full, ins, oos


def print_rows(title: str, rows: list[tuple[str, dict, dict, dict]]) -> None:
    print(title)
    print(
        f"{'config':<36} | {'FULL CAGR':>9} {'PF':>5} {'DD':>7} {'tr':>4} {'fl':>3} {'py':>3} | "
        f"{'IS CAGR':>8} {'IS PF':>5} | {'OOS CAGR':>8} {'OOS PF':>6} {'OOS DD':>7} {'tr':>4}"
    )
    for label, full, ins, oos in rows:
        print(
            f"{label:<36} | {full['cagr']:9.1f} {full['pf']:5.2f} {full['dd']:7.1f} {full['trades']:4.0f} "
            f"{full['flips']:3.0f} {full['pyramids']:3.0f} | {ins['cagr']:8.1f} {ins['pf']:5.2f} | "
            f"{oos['cagr']:8.1f} {oos['pf']:6.2f} {oos['dd']:7.1f} {oos['trades']:4.0f}"
        )
    print()


def main() -> None:
    vol_full_path = ROOT / "data/cache/BTCUSDT_1h_binance_volume.csv"
    full_path = vol_full_path if vol_full_path.exists() else ROOT / "data/cache/BTCUSDT_1h_binance_full.csv"
    bybit_path = ROOT / "data/cache/BTCUSDT_1h_12000_bybit.csv"
    df_full = load_1h(full_path)
    df_bybit = load_1h(bybit_path)

    print("BTC MTF Engulfing Flip + Pyramid honest audit")
    print("Uses full Binance OHLCV when available; otherwise full-history rows disable volume filter.")
    print(f"Full data:  {df_full.timestamp.iloc[0]} -> {df_full.timestamp.iloc[-1]}")
    print(f"Bybit vol:  {df_bybit.timestamp.iloc[0]} -> {df_bybit.timestamp.iloc[-1]}\n")

    has_full_volume = "volume" in df_full.columns
    base = Config(name="v3", use_volume=has_full_volume)
    rows = [
        run_one("V3 exact vol, flip+pyramid", df_full, base),
        run_one("V3 vol OFF, flip+pyramid", df_full, Config(use_volume=False)),
        run_one("no flip, pyramid", df_full, Config(use_volume=has_full_volume, use_sl_flip=False, use_pyramid=True)),
        run_one("flip, no pyramid", df_full, Config(use_volume=has_full_volume, use_sl_flip=True, use_pyramid=False)),
        run_one("no flip, no pyramid", df_full, Config(use_volume=has_full_volume, use_sl_flip=False, use_pyramid=False)),
        run_one("V2 rsi14 atr14", df_full, Config(use_volume=has_full_volume, rsi_len=14, atr_len=14)),
        run_one("long only-ish", df_full, Config(use_volume=has_full_volume, rsi_short_max=0)),
        run_one("short only-ish", df_full, Config(use_volume=has_full_volume, rsi_long_min=100)),
    ]
    print_rows("=== Full 1H Binance ===", rows)

    repaired_rows = [
        run_one(
            "FIX 0.5x no flip pyramid",
            df_full,
            Config(
                use_volume=has_full_volume,
                leverage=0.5,
                use_sl_flip=False,
                use_pyramid=True,
                rsi_len=21,
                atr_len=20,
                rsi_long_min=55,
                rsi_short_max=45,
                vol_spike_ratio=1.25,
            ),
        ),
        run_one(
            "FIX 0.5x no flip no pyr",
            df_full,
            Config(
                use_volume=has_full_volume,
                leverage=0.5,
                use_sl_flip=False,
                use_pyramid=False,
                rsi_len=21,
                atr_len=20,
                rsi_long_min=55,
                rsi_short_max=45,
                vol_spike_ratio=1.25,
            ),
        ),
        run_one(
            "FIX 0.75x no flip pyramid",
            df_full,
            Config(
                use_volume=has_full_volume,
                leverage=0.75,
                use_sl_flip=False,
                use_pyramid=True,
                rsi_len=21,
                atr_len=20,
                rsi_long_min=55,
                rsi_short_max=45,
                vol_spike_ratio=1.25,
            ),
        ),
        run_one(
            "FIX 1x no flip pyramid",
            df_full,
            Config(
                use_volume=has_full_volume,
                leverage=1.0,
                use_sl_flip=False,
                use_pyramid=True,
                rsi_len=21,
                atr_len=20,
                rsi_long_min=55,
                rsi_short_max=45,
                vol_spike_ratio=1.25,
            ),
        ),
        run_one(
            "FIX 0.5x max30d",
            df_full,
            Config(
                use_volume=has_full_volume,
                leverage=0.5,
                use_sl_flip=False,
                use_pyramid=True,
                rsi_len=21,
                atr_len=20,
                rsi_long_min=55,
                rsi_short_max=45,
                vol_spike_ratio=1.25,
                max_hold_hours=720,
            ),
        ),
    ]
    print_rows("=== Repaired candidates ===", repaired_rows)

    rows2 = [
        run_one("V3 volume ON", df_bybit, Config(use_volume=True)),
        run_one("V3 volume OFF", df_bybit, Config(use_volume=False)),
        run_one("no flip/pyr vol ON", df_bybit, Config(use_volume=True, use_sl_flip=False, use_pyramid=False)),
        run_one("V2 vol ON", df_bybit, Config(use_volume=True, rsi_len=14, atr_len=14)),
    ]
    print_rows("=== Short Bybit 1H window with real volume ===", rows2)

    sweep = []
    for rsi_len in [14, 21]:
        for atr_len in [14, 20]:
            for body in [0.8, 1.0, 1.2]:
                for tp_r in [4.0, 6.0, 8.0]:
                    cfg = Config(use_volume=has_full_volume, rsi_len=rsi_len, atr_len=atr_len, eng_body_mult=body, partial_tp_r=tp_r)
                    label = f"rsi{rsi_len} atr{atr_len} body{body:g} tp{tp_r:g}"
                    sweep.append(run_one(label, df_full, cfg))
    sweep.sort(key=lambda x: (min(x[1]["pf"], x[3]["pf"]), x[3]["cagr"]), reverse=True)
    print_rows("=== Small honest sweep on full data, ranked by PF floor + OOS ===", sweep[:12])


if __name__ == "__main__":
    main()
