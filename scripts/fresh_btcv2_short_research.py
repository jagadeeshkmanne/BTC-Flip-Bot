#!/usr/bin/env python3
"""Research BTC V2 short-side variants from the fresh scratch implementation.

This keeps the long engine fixed and sweeps only short entry/stop/sizing logic.
It also reports average monthly trade counts, because a high CAGR with almost no
trades is hard to operate or trust.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)

from fresh_btcv2_from_scratch import (
    BPD,
    Config,
    DATA,
    FEE,
    SLIP,
    adx,
    atr,
    build_signals,
    ema,
    load_raw,
    map_daily_to_4h,
    resample_ohlc,
    row,
    sma,
    stats,
)


@dataclass(frozen=True)
class ShortSpec:
    name: str
    drop: float
    lookback_days: int
    filter_name: str
    size_mode: str
    atr_mult: float
    pct_stop: float
    scale: float


def build_short_variant(df4: pd.DataFrame, daily: pd.DataFrame, spec: ShortSpec):
    c = df4["close"]
    dc = daily["close"]

    hh = c.rolling(spec.lookback_days * BPD).max().shift(1)
    drop_gate = ((c / hh - 1) < -spec.drop).fillna(False)

    macd_line = ema(dc, 12) - ema(dc, 26)
    macd_sig = ema(macd_line, 9)
    d_macd_bear = map_daily_to_4h(df4, daily, (macd_line < macd_sig).shift(1).fillna(False))
    d_ema_bear = map_daily_to_4h(df4, daily, (ema(dc, 50) < ema(dc, 200)).shift(1).fillna(False))

    e50 = ema(c, 50)
    e200 = ema(c, 200)
    f_ema_bear = (e50 < e200).shift(1).fillna(False).to_numpy()

    if spec.filter_name == "macd":
        filt = d_macd_bear
    elif spec.filter_name == "daily_ema":
        filt = d_ema_bear
    elif spec.filter_name == "macd_or_4h_bear":
        filt = d_macd_bear | f_ema_bear
    elif spec.filter_name == "macd_and_4h_bear":
        filt = d_macd_bear & f_ema_bear
    elif spec.filter_name == "daily_ema_or_4h_bear":
        filt = d_ema_bear | f_ema_bear
    else:
        raise ValueError(spec.filter_name)

    gate = drop_gate.to_numpy() & filt

    hh180 = c.rolling(180 * BPD).max().shift(1)
    ddh = (c / hh180 - 1).fillna(0).to_numpy()
    if spec.size_mode == "tier":
        size = np.where(ddh <= -0.30, 1.0, np.where(ddh <= -0.20, 0.50, 0.25))
    elif spec.size_mode == "small":
        size = np.where(ddh <= -0.30, 0.65, np.where(ddh <= -0.20, 0.35, 0.15))
    elif spec.size_mode == "flat25":
        size = np.full(len(c), 0.25)
    elif spec.size_mode == "flat50":
        size = np.full(len(c), 0.50)
    else:
        raise ValueError(spec.size_mode)

    return gate, size * spec.scale


def run_bt_with_short_spec(
    df: pd.DataFrame,
    long_gate: np.ndarray,
    short_gate: np.ndarray,
    short_size: np.ndarray,
    parab: np.ndarray,
    conv: np.ndarray,
    cfg: Config,
    spec: ShortSpec,
):
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    a = atr(df, 14).to_numpy()
    ts = pd.to_datetime(df["timestamp"]).to_numpy()

    cash = 1.0
    units = 0.0
    side = 0
    entry = entry0 = stop = risk = notional0 = trade_eq_entry = 0.0
    armed_l = armed_s = True
    pyramided = parab_done = lock_done = False
    eq = np.ones(len(df))
    trade_rets = []
    trade_log = []
    open_ts = None
    open_side = 0

    for i in range(300, len(df) - 1):
        o_next, h_next, l_next, c_next = o[i + 1], h[i + 1], l[i + 1], c[i + 1]

        if not long_gate[i]:
            armed_l = True
        if not short_gate[i]:
            armed_s = True

        if side != 0:
            hit_stop = (l_next <= stop) if side == 1 else (h_next >= stop)
            regime_out = (side == 1 and not long_gate[i]) or (side == -1 and not short_gate[i])
            if hit_stop or regime_out:
                raw_exit = stop if hit_stop else o_next
                fill = raw_exit * (1 - SLIP) if side == 1 else raw_exit * (1 + SLIP)
                fee = abs(units) * fill * FEE
                cash += units * fill - fee
                ret = cash / trade_eq_entry - 1 if trade_eq_entry > 0 else 0.0
                trade_rets.append(ret)
                trade_log.append(
                    {
                        "entry_ts": pd.Timestamp(open_ts),
                        "exit_ts": pd.Timestamp(ts[i + 1]),
                        "side": open_side,
                        "ret": ret,
                    }
                )
                units = 0.0
                side = 0
                pyramided = parab_done = lock_done = False
            else:
                prof_r = ((c_next - entry) / risk) if side == 1 else ((entry - c_next) / risk)
                if prof_r >= 1.0:
                    be = entry * (1 + cfg.be_buf) if side == 1 else entry * (1 - cfg.be_buf)
                    stop = max(stop, be) if side == 1 else min(stop, be)
                if side == 1 and not pyramided and prof_r >= cfg.pyr_r and cfg.pyr_frac > 0:
                    add_notional = cfg.pyr_frac * notional0
                    add_fill = c_next * (1 + SLIP)
                    add_units = add_notional / add_fill
                    cash -= add_units * add_fill + add_notional * FEE
                    entry = (units * entry + add_units * add_fill) / (units + add_units)
                    units += add_units
                    stop = max(stop, entry0)
                    pyramided = True
                if side == 1 and not lock_done and prof_r >= cfg.lock_r and cfg.lock_frac > 0:
                    fill = o_next * (1 - SLIP)
                    close_units = cfg.lock_frac * units
                    cash += close_units * fill - close_units * fill * FEE
                    units -= close_units
                    lock_done = True
                if side == 1 and not parab_done and parab[i]:
                    fill = o_next * (1 - SLIP)
                    close_units = 0.5 * units
                    cash += close_units * fill - close_units * fill * FEE
                    units -= close_units
                    parab_done = True

        if side == 0:
            if long_gate[i] and armed_l:
                st = max(c[i] - 3.5 * a[i], c[i] * (1 - 0.12))
                fill = o_next * (1 + SLIP)
                if fill > st:
                    lev = cfg.lev_lo + (cfg.lev_hi - cfg.lev_lo) * conv[i]
                    trade_eq_entry = cash
                    notional0 = cash * lev
                    units = notional0 / fill
                    cash -= units * fill + notional0 * FEE
                    side = open_side = 1
                    open_ts = ts[i + 1]
                    entry = entry0 = fill
                    stop = st
                    risk = fill - st
                    armed_l = False
                    pyramided = parab_done = lock_done = False
            elif short_gate[i] and armed_s:
                st = min(c[i] + spec.atr_mult * a[i], c[i] * (1 + spec.pct_stop))
                fill = o_next * (1 - SLIP)
                if st > fill:
                    trade_eq_entry = cash
                    notional0 = cash * short_size[i]
                    units = -notional0 / fill
                    cash -= units * fill + notional0 * FEE
                    side = open_side = -1
                    open_ts = ts[i + 1]
                    entry = entry0 = fill
                    stop = st
                    risk = st - fill
                    armed_s = False
                    pyramided = parab_done = lock_done = False

        eq[i + 1] = cash + units * c_next

    return pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[300:], np.array(trade_rets), pd.DataFrame(trade_log)


def trade_summary(log: pd.DataFrame):
    if log.empty:
        return {"long_trades": 0, "short_trades": 0, "short_pf": np.nan, "months_active": 0, "avg_trades_mo": 0.0}
    short = log[log["side"] == -1]
    sw = short[short["ret"] > 0]["ret"].sum()
    sl = short[short["ret"] <= 0]["ret"].sum()
    start = log["entry_ts"].min().to_period("M")
    end = log["entry_ts"].max().to_period("M")
    months = len(pd.period_range(start, end, freq="M"))
    return {
        "long_trades": int((log["side"] == 1).sum()),
        "short_trades": int((log["side"] == -1).sum()),
        "short_pf": float(sw / -sl) if sl < 0 else np.nan,
        "months_active": months,
        "avg_trades_mo": len(log) / months if months else 0.0,
        "avg_short_mo": len(short) / months if months else 0.0,
    }


def print_row(label: str, spec: ShortSpec, st: dict, ts: dict):
    print(
        f"{label:<30}{st['cagr']:>7.1f}%{st['dd']:>7.1f}%{st['rdd']:>6.2f}"
        f"{st['oos_cagr']:>8.1f}%{st['oos_dd']:>7.1f}%"
        f"{st['trades']:>5d}{st['pf']:>6.2f}{st['finalx']:>8.0f}x"
        f"{ts['long_trades']:>5d}{ts['short_trades']:>5d}{ts['avg_trades_mo']:>7.2f}{ts['avg_short_mo']:>7.2f}{ts['short_pf']:>7.2f}"
        f"  {spec.name}"
    )


def main():
    raw = load_raw()
    df, long_gate, base_short_gate, base_short_size, parab, conv = build_signals(raw)
    daily = resample_ohlc(raw, "1D")
    cfg = Config("balanced", lev_lo=1.0, lev_hi=2.8, short_scale=1.0, lock_frac=0.33, lock_r=5.0, pyr_frac=1.0, pyr_r=2.0)

    base_spec = ShortSpec("base drop10/lb40/macd/tier/5atr/15pct", 0.10, 40, "macd", "tier", 5.0, 0.15, 1.0)
    base_eq, base_tr, base_log = run_bt_with_short_spec(df, long_gate, base_short_gate, base_short_size, parab, conv, cfg, base_spec)
    base_stats = stats(base_eq, base_tr)
    base_ts = trade_summary(base_log)

    specs = []
    for drop, lb, filt, size, atr_m, pct, scale in product(
        [0.08, 0.10, 0.12],
        [20, 40],
        ["macd", "macd_or_4h_bear", "macd_and_4h_bear", "daily_ema_or_4h_bear"],
        ["tier", "small", "flat25"],
        [3.0, 4.0, 5.0],
        [0.08, 0.12],
        [0.75, 1.0],
    ):
        name = f"d{int(drop*100)} lb{lb} {filt} {size} {atr_m:.0f}atr {int(pct*100)}pct x{scale:.2f}"
        specs.append(ShortSpec(name, drop, lb, filt, size, atr_m, pct, scale))

    rows = []
    for spec in specs:
        sg, ss = build_short_variant(df, daily, spec)
        eq, tr, log = run_bt_with_short_spec(df, long_gate, sg, ss, parab, conv, cfg, spec)
        st = stats(eq, tr)
        ts = trade_summary(log)
        rows.append((spec, st, ts))

    rows_sorted = sorted(rows, key=lambda r: (r[1]["rdd"], r[1]["cagr"], r[1]["dd"]), reverse=True)
    safer = sorted([r for r in rows if r[1]["cagr"] >= 120], key=lambda r: (r[1]["dd"], r[1]["rdd"]), reverse=True)
    short_heavy = sorted([r for r in rows if r[2]["short_trades"] >= base_ts["short_trades"]], key=lambda r: (r[1]["rdd"], r[2]["short_pf"]), reverse=True)

    print("=" * 170)
    print("FRESH BTC V2 SHORT-SIDE RESEARCH")
    print(f"raw: {DATA} | {raw.timestamp.min()} -> {raw.timestamp.max()} | 4h bars {len(df)}")
    print("=" * 170)
    print(
        f"{'config':<30}{'CAGR':>8}{'DD':>8}{'r/DD':>6}{'OOS CAGR':>9}{'OOS DD':>8}"
        f"{'tr':>5}{'PF':>6}{'final':>9}{'long':>5}{'short':>5}{'tr/mo':>7}{'sh/mo':>7}{'shPF':>7}  spec"
    )
    print("-" * 170)
    print_row("BASE balanced", base_spec, base_stats, base_ts)

    print("\nBEST return/DD:")
    for n, (spec, st, ts) in enumerate(rows_sorted[:12], 1):
        print_row(f"rdd{n}", spec, st, ts)

    print("\nSAFEST DD with CAGR >= 120:")
    for n, (spec, st, ts) in enumerate(safer[:12], 1):
        print_row(f"safe{n}", spec, st, ts)

    print("\nBEST with at least baseline short count:")
    for n, (spec, st, ts) in enumerate(short_heavy[:12], 1):
        print_row(f"short{n}", spec, st, ts)


if __name__ == "__main__":
    main()
