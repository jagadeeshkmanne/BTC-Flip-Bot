#!/usr/bin/env python3
"""fresh_btcv2_from_scratch.py

Independent audit/tune of the deployed BTC V2 idea, intentionally not importing any
project backtest modules. It only reads the raw 1h Binance BTC file and rebuilds:

- indicators
- 4h/daily resampling
- closed-bar signals
- next-open fills
- intrabar stop checks
- fee/slippage accounting
- nearby tuning sweep

This is a second implementation to catch hidden assumptions in older scripts.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "cache" / "BTCUSDT_1h_binance_ext.csv"

FEE = 0.00055
SLIP = 0.00050
BPD = 6  # 4h bars per day


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()],
        axis=1,
    ).max(axis=1)
    return ema(tr, n)


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    pdm = ((up > dn) & (up > 0)) * up.clip(lower=0)
    ndm = ((dn > up) & (dn > 0)) * dn.clip(lower=0)
    a = atr(df, n).replace(0, np.nan)
    pdi = 100 * ema(pdm, n) / a
    ndi = 100 * ema(ndm, n) / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return ema(dx, n).fillna(0)


def load_raw() -> pd.DataFrame:
    raw = pd.read_csv(DATA, parse_dates=["timestamp"])
    raw = raw.sort_values("timestamp").drop_duplicates("timestamp")
    return raw


def resample_ohlc(raw: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in raw.columns:
        agg["volume"] = "sum"
    return raw.set_index("timestamp").resample(rule).agg(agg).dropna().reset_index()


def map_daily_to_4h(df4: pd.DataFrame, daily: pd.DataFrame, values: pd.Series) -> np.ndarray:
    left = pd.DataFrame({"timestamp": pd.to_datetime(df4["timestamp"])})
    right = pd.DataFrame({"timestamp": pd.to_datetime(daily["timestamp"]), "v": values.to_numpy()})
    out = pd.merge_asof(left.sort_values("timestamp"), right.sort_values("timestamp"), on="timestamp")["v"]
    return out.fillna(False).astype(bool).to_numpy()


@dataclass(frozen=True)
class Config:
    name: str
    lev_lo: float = 1.0
    lev_hi: float = 2.5
    short_scale: float = 1.0
    lock_frac: float = 0.33
    lock_r: float = 6.0
    pyr_frac: float = 1.0
    pyr_r: float = 2.0
    be_buf: float = 0.01


def build_signals(raw: pd.DataFrame):
    df = resample_ohlc(raw, "4h")
    daily = resample_ohlc(raw, "1D")
    c = df["close"]
    dc = daily["close"]

    e50 = ema(c, 50)
    e200 = ema(c, 200)
    f_bull = (e50 > e200).to_numpy()

    d_bull_daily = (ema(dc, 50) > ema(dc, 200)).shift(1).fillna(False)
    d_bull = map_daily_to_4h(df, daily, d_bull_daily)

    macro = (c > sma(c, 9 * 30 * BPD).shift(1)).fillna(False).to_numpy()
    long_gate = f_bull & d_bull & macro

    hh40 = c.rolling(40 * BPD).max().shift(1)
    drop10 = ((c / hh40 - 1) < -0.10).fillna(False).to_numpy()
    macd_line = ema(dc, 12) - ema(dc, 26)
    macd_sig = ema(macd_line, 9)
    d_macd_bear_daily = (macd_line < macd_sig).shift(1).fillna(False)
    d_macd_bear = map_daily_to_4h(df, daily, d_macd_bear_daily)
    short_gate = drop10 & d_macd_bear

    hh180 = c.rolling(180 * BPD).max().shift(1)
    ddh = (c / hh180 - 1).fillna(0).to_numpy()
    short_size = np.where(ddh <= -0.30, 1.0, np.where(ddh <= -0.20, 0.50, 0.25))

    parab = (c > 2.2 * sma(c, 140 * BPD).shift(1)).fillna(False).to_numpy()

    ax = adx(df, 14).shift(1).fillna(0)
    egap = ((e50 - e200) / e200).shift(1).fillna(0)
    conv = np.clip(ax.to_numpy() / 35.0, 0, 1) * 0.5 + np.clip(egap.to_numpy() / 0.12, 0, 1) * 0.5

    return df, long_gate, short_gate, short_size, parab, conv


def run_backtest(df: pd.DataFrame, long_gate: np.ndarray, short_gate: np.ndarray,
                 short_size: np.ndarray, parab: np.ndarray, conv: np.ndarray, cfg: Config):
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    a = atr(df, 14).to_numpy()
    ts = pd.to_datetime(df["timestamp"])

    cash = 1.0
    units = 0.0
    side = 0  # 1 long, -1 short
    entry = entry0 = stop = risk = notional0 = trade_eq_entry = 0.0
    armed_l = armed_s = True
    pyramided = parab_done = lock_done = False
    eq = np.ones(len(df))
    trades = []

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
                after = cash
                trades.append(after / trade_eq_entry - 1 if trade_eq_entry > 0 else 0)
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
                    # Live bot moves the stop to the original entry, not the new averaged entry.
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
                    side = 1
                    entry = entry0 = fill
                    stop = st
                    risk = fill - st
                    armed_l = False
                    pyramided = parab_done = lock_done = False
            elif short_gate[i] and armed_s:
                st = min(c[i] + 5.0 * a[i], c[i] * (1 + 0.15))
                fill = o_next * (1 - SLIP)
                if st > fill:
                    trade_eq_entry = cash
                    notional0 = cash * short_size[i] * cfg.short_scale
                    units = -notional0 / fill
                    cash -= units * fill + notional0 * FEE
                    side = -1
                    entry = entry0 = fill
                    stop = st
                    risk = st - fill
                    armed_s = False
                    pyramided = parab_done = lock_done = False

        eq[i + 1] = cash + units * c_next

    series = pd.Series(eq, index=ts).iloc[300:]
    return series, np.array(trades)


def stats(eq: pd.Series, trades: np.ndarray):
    eq = eq[eq > 0]
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
    dd = (eq / eq.cummax() - 1).min()
    split = eq.index[int(len(eq) * 0.6)]
    oos = eq[eq.index >= split]
    oy = (oos.index[-1] - oos.index[0]).days / 365.25
    ocagr = (oos.iloc[-1] / oos.iloc[0]) ** (1 / oy) - 1
    odd = (oos / oos.cummax() - 1).min()
    wins = trades[trades > 0]
    losses = trades[trades <= 0]
    pf = wins.sum() / -losses.sum() if len(losses) and losses.sum() < 0 else np.nan
    return {
        "cagr": cagr * 100,
        "dd": dd * 100,
        "rdd": cagr / abs(dd) if dd < 0 else 0,
        "oos_cagr": ocagr * 100,
        "oos_dd": odd * 100,
        "trades": int(len(trades)),
        "pf": float(pf),
        "finalx": eq.iloc[-1] / eq.iloc[0],
    }


def year_return(eq: pd.Series, y: int) -> float:
    seg = eq[eq.index.year == y]
    return (seg.iloc[-1] / seg.iloc[0] - 1) * 100 if len(seg) > 20 else 0.0


def row(name: str, cfg: Config, st: dict, eq: pd.Series) -> dict:
    d = dict(name=name, cfg=cfg, eq=eq, **st)
    d["green"] = all(year_return(eq, y) >= -0.5 for y in range(2018, 2027))
    d["y18"] = year_return(eq, 2018)
    d["y22"] = year_return(eq, 2022)
    d["y26"] = year_return(eq, 2026)
    return d


def fmt(r: dict) -> str:
    c = r["cfg"]
    return (
        f"{r['name']:<28}{r['cagr']:>7.1f}%{r['dd']:>7.1f}%{r['rdd']:>6.2f}"
        f"{r['oos_cagr']:>8.1f}%{r['oos_dd']:>7.1f}%{r['trades']:>5d}{r['pf']:>6.2f}"
        f"{r['finalx']:>8.0f}x  {r['y18']:+5.0f}/{r['y22']:+4.0f}/{r['y26']:+4.0f}"
        f"  L{c.lev_lo:.1f}-{c.lev_hi:.1f} S{c.short_scale:.2f} lock{c.lock_frac:.2f}@{c.lock_r:.0f} pyr{c.pyr_frac:.1f}@{c.pyr_r:.0f}"
    )


def main():
    raw = load_raw()
    df, long_gate, short_gate, short_size, parab, conv = build_signals(raw)

    base_cfg = Config("base")
    eq, tr = run_backtest(df, long_gate, short_gate, short_size, parab, conv, base_cfg)
    base = row("SCRATCH baseline", base_cfg, stats(eq, tr), eq)

    results = [base]
    levs = [(1.0, 2.0), (1.0, 2.25), (1.0, 2.5), (1.0, 2.75), (1.2, 2.5)]
    short_scales = [0.60, 0.75, 0.90, 1.00]
    locks = [(0.25, 6.0), (0.33, 5.0), (0.33, 6.0), (0.50, 6.0)]
    pyrs = [(1.0, 2.0), (1.0, 3.0), (0.5, 2.0)]
    n = 0
    for (lo, hi), ss, (lf, lr), (pf, pr) in product(levs, short_scales, locks, pyrs):
        n += 1
        cfg = Config(f"t{n}", lo, hi, ss, lf, lr, pf, pr)
        eq, tr = run_backtest(df, long_gate, short_gate, short_size, parab, conv, cfg)
        results.append(row(f"t{n}", cfg, stats(eq, tr), eq))

    valid = [r for r in results if r["green"] and r["y18"] > 0 and r["y22"] > 0 and r["y26"] > 0]

    print("=" * 132)
    print("FRESH BTC V2 FROM SCRATCH — no project backtest imports")
    print(f"raw: {DATA} | {raw.timestamp.min()} -> {raw.timestamp.max()} | 4h bars {len(df)}")
    print("=" * 132)
    print(f"{'config':<28}{'CAGR':>8}{'DD':>8}{'r/DD':>6}{'OOS CAGR':>9}{'OOS DD':>8}{'tr':>5}{'PF':>6}{'final':>9}  {'18/22/26':>14}  params")
    print("-" * 132)
    print(fmt(base))

    print("\nLOWEST DD with CAGR >= 100:")
    low = [r for r in valid if r["cagr"] >= 100]
    low.sort(key=lambda r: (r["dd"], r["cagr"]), reverse=True)
    for r in low[:10]:
        print(fmt(r))

    print("\nBEST ret/DD with CAGR >= 120 and DD no worse than baseline +2pt:")
    ratio = [r for r in valid if r["cagr"] >= 120 and r["dd"] >= base["dd"] - 2]
    ratio.sort(key=lambda r: (r["rdd"], r["cagr"]), reverse=True)
    for r in ratio[:10]:
        print(fmt(r))

    print("\nHIGHER CAGR with DD <= baseline:")
    hp = [r for r in valid if r["cagr"] > base["cagr"] and r["dd"] >= base["dd"]]
    hp.sort(key=lambda r: r["cagr"], reverse=True)
    if not hp:
        print("  NONE")
    else:
        for r in hp[:10]:
            print(fmt(r))


if __name__ == "__main__":
    main()
