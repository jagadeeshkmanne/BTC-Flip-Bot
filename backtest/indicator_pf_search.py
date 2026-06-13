#!/usr/bin/env python3
"""Search indicator entry families using the honest v2.x execution engine.

Protocol:
  - signals use the closed 5m bar; fills occur at the next 5m open
  - honest market fills, 0.055% taker fee/side, 0.02% market slippage
  - fixed-stake ledger for comparable economics after losing variants cross $0
  - selection: 2019-2022; confirmation: 2023-2024; holdout: 2025+
  - rank on the weaker of selection/confirmation PF, not full-sample PF

The live bot is unchanged. This script only supplies alternative entry sides
to fresh_honest.run; v2.1/v2.2 DCA and exit behavior remains intact.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

import fresh_honest as fh

REAL = dict(fill="honest", fee=0.00055, slip=0.0002, fixed_cap=True)
PERIODS = {
    "select": (None, pd.Timestamp("2023-01-01")),
    "confirm": (pd.Timestamp("2023-01-01"), pd.Timestamp("2025-01-01")),
    "holdout": (pd.Timestamp("2025-01-01"), None),
}
MIN_SELECT_TRADES = 40
MIN_CONFIRM_TRADES = 20
FINALISTS = 12


@dataclass
class Candidate:
    name: str
    sides: np.ndarray
    gap_min: float = 0.002
    atr_max: float = 0.80


def wilder_rsi(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    return 100 - 100 / (1 + avg_gain / avg_loss.replace(0, np.nan))


def wilder_adx(frame: pd.DataFrame, length: int = 14) -> pd.Series:
    high, low, close = frame["high"], frame["low"], frame["close"]
    up, down = high.diff(), -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=frame.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=frame.index)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / length, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    return dx.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def sides(long_condition, short_condition) -> np.ndarray:
    long_arr = np.asarray(long_condition, dtype=bool)
    short_arr = np.asarray(short_condition, dtype=bool)
    out = np.zeros(len(long_arr), dtype=np.int8)
    out[long_arr & ~short_arr] = 1
    out[short_arr & ~long_arr] = -1
    return out


def add_candidate(
    output: list[Candidate],
    name: str,
    long_condition,
    short_condition,
    gap_min: float = 0.002,
    atr_max: float = 0.80,
) -> None:
    output.append(Candidate(name, sides(long_condition, short_condition), gap_min, atr_max))


def prepare() -> pd.DataFrame:
    bt = fh.prep()
    close, high, low = bt["close"], bt["high"], bt["low"]

    for length in (7, 9, 14, 21):
        bt[f"rsi{length}"] = wilder_rsi(close, length)

    mid = close.rolling(20).mean()
    std = close.rolling(20).std()
    bt["z20"] = (close - mid) / std.replace(0, np.nan)
    lo14 = low.rolling(14).min()
    hi14 = high.rolling(14).max()
    bt["stoch14"] = 100 * (close - lo14) / (hi14 - lo14).replace(0, np.nan)
    typical = (high + low + close) / 3
    typical_mid = typical.rolling(20).mean()
    mean_dev = (typical - typical_mid).abs().rolling(20).mean()
    bt["cci20"] = (typical - typical_mid) / (0.015 * mean_dev.replace(0, np.nan))
    bt["roc1h"] = close.pct_change(12) * 100
    bt["roc4h"] = close.pct_change(48) * 100
    bt["ema20"] = close.ewm(span=20, adjust=False).mean()
    bt["ema50"] = close.ewm(span=50, adjust=False).mean()
    bt["ema200"] = close.ewm(span=200, adjust=False).mean()
    bt["macd"] = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    bt["macd_signal"] = bt["macd"].ewm(span=9, adjust=False).mean()
    bt["vol_ratio"] = bt["volume"] / bt["volume"].rolling(20).mean().replace(0, np.nan)

    prior_hi_6h = high.rolling(72).max().shift(1)
    prior_lo_6h = low.rolling(72).min().shift(1)
    prior_hi_24h = high.rolling(288).max().shift(1)
    prior_lo_24h = low.rolling(288).min().shift(1)
    bt["break6_up"], bt["break6_dn"] = close > prior_hi_6h, close < prior_lo_6h
    bt["break24_up"], bt["break24_dn"] = close > prior_hi_24h, close < prior_lo_24h

    indexed = bt.set_index("timestamp")
    hourly = indexed[["open", "high", "low", "close"]].resample(
        "1h", label="left", closed="left"
    ).agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    hourly["ema20"] = hourly["close"].ewm(span=20, adjust=False).mean()
    hourly["ema50"] = hourly["close"].ewm(span=50, adjust=False).mean()
    hourly["ema200"] = hourly["close"].ewm(span=200, adjust=False).mean()
    hourly["adx14"] = wilder_adx(hourly)
    hourly["rsi14"] = wilder_rsi(hourly["close"], 14)
    hourly_features = pd.DataFrame(
        {
            "closed_at_1h": hourly.index + pd.Timedelta(hours=1),
            "h1_up": hourly["ema20"] > hourly["ema50"],
            "h1_above200": hourly["close"] > hourly["ema200"],
            "h1_adx": hourly["adx14"],
            "h1_rsi": hourly["rsi14"],
        }
    )
    bt = pd.merge_asof(
        bt,
        hourly_features.sort_values("closed_at_1h"),
        left_on="timestamp",
        right_on="closed_at_1h",
        direction="backward",
        allow_exact_matches=True,
    )
    daily = indexed[["open", "high", "low", "close"]].resample(
        "1D", label="left", closed="left"
    ).agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    daily["ema50"] = daily["close"].ewm(span=50, adjust=False).mean()
    daily["ema200"] = daily["close"].ewm(span=200, adjust=False).mean()
    daily["rsi14"] = wilder_rsi(daily["close"], 14)
    daily_features = pd.DataFrame(
        {
            "closed_at_1d": daily.index + pd.Timedelta(days=1),
            "d1_up": daily["ema50"] > daily["ema200"],
            "d1_rsi": daily["rsi14"],
        }
    )
    return pd.merge_asof(
        bt,
        daily_features.sort_values("closed_at_1d"),
        left_on="timestamp",
        right_on="closed_at_1d",
        direction="backward",
        allow_exact_matches=True,
    )


def build_candidates(bt: pd.DataFrame) -> list[Candidate]:
    candidates: list[Candidate] = []
    h1_up = bt["h1_up"].astype("boolean").fillna(False).astype(bool)
    h1_down = ~h1_up
    adx_low = bt["h1_adx"] < 20
    adx_high = bt["h1_adx"] > 25
    vol_high = bt["vol_ratio"] > 1.25
    vol_low = bt["vol_ratio"] < 0.80
    ema5_up = (bt["ema20"] > bt["ema50"]) & (bt["ema50"] > bt["ema200"])
    ema5_down = (bt["ema20"] < bt["ema50"]) & (bt["ema50"] < bt["ema200"])

    for length in (7, 9, 14, 21):
        rsi = bt[f"rsi{length}"]
        for threshold in (25, 30, 35, 40):
            hi = 100 - threshold
            add_candidate(candidates, f"RSI{length} MR {threshold}/{hi}", rsi <= threshold, rsi >= hi)
            add_candidate(candidates, f"RSI{length} MOM {threshold}/{hi}", rsi >= hi, rsi <= threshold)
        add_candidate(
            candidates, f"RSI{length} MR 35/65 + ADX<20",
            (rsi <= 35) & adx_low, (rsi >= 65) & adx_low,
        )
        add_candidate(
            candidates, f"RSI{length} pullback with 1h trend",
            (rsi <= 40) & h1_up, (rsi >= 60) & h1_down,
        )

    for threshold in (1.0, 1.5, 2.0, 2.5):
        z = bt["z20"]
        add_candidate(candidates, f"Z20 MR {threshold:g}", z <= -threshold, z >= threshold)
        add_candidate(candidates, f"Z20 MOM {threshold:g}", z >= threshold, z <= -threshold)
        add_candidate(
            candidates, f"Z20 pullback {threshold:g} + 1h trend",
            (z <= -threshold) & h1_up, (z >= threshold) & h1_down,
        )

    for threshold in (10, 20, 30):
        stoch = bt["stoch14"]
        add_candidate(candidates, f"Stoch14 MR {threshold}/{100-threshold}",
                      stoch <= threshold, stoch >= 100 - threshold)
        add_candidate(candidates, f"Stoch14 MOM {threshold}/{100-threshold}",
                      stoch >= 100 - threshold, stoch <= threshold)

    for threshold in (100, 150, 200):
        cci = bt["cci20"]
        add_candidate(candidates, f"CCI20 MR {threshold}", cci <= -threshold, cci >= threshold)
        add_candidate(candidates, f"CCI20 MOM {threshold}", cci >= threshold, cci <= -threshold)

    for column, label in (("roc1h", "ROC1h"), ("roc4h", "ROC4h")):
        roc = bt[column]
        for threshold in (0.5, 1.0, 2.0):
            add_candidate(candidates, f"{label} MR {threshold:g}%", roc <= -threshold, roc >= threshold)
            add_candidate(candidates, f"{label} MOM {threshold:g}%", roc >= threshold, roc <= -threshold)

    add_candidate(candidates, "6h breakout", bt["break6_up"], bt["break6_dn"])
    add_candidate(candidates, "6h breakout fade", bt["break6_dn"], bt["break6_up"])
    add_candidate(candidates, "24h breakout", bt["break24_up"], bt["break24_dn"])
    add_candidate(candidates, "24h breakout fade", bt["break24_dn"], bt["break24_up"])
    add_candidate(candidates, "EMA 20/50/200 trend", ema5_up, ema5_down)
    add_candidate(candidates, "1h EMA trend + ADX>25", h1_up & adx_high, h1_down & adx_high)

    rsi9 = bt["rsi9"]
    base_long, base_short = rsi9 <= 35, rsi9 >= 65
    filters = {
        "1h aligned": (h1_up, h1_down),
        "1h counter": (h1_down, h1_up),
        "ADX<20": (adx_low, adx_low),
        "ADX>25": (adx_high, adx_high),
        "volume>1.25x": (vol_high, vol_high),
        "volume<0.8x": (vol_low, vol_low),
        "5m trend aligned": (ema5_up, ema5_down),
        "5m trend counter": (ema5_down, ema5_up),
    }
    for label, (long_filter, short_filter) in filters.items():
        add_candidate(
            candidates, f"RSI9 MR + {label}",
            base_long & long_filter, base_short & short_filter,
        )

    # Test whether the original mandatory EMA-gap gate itself is harmful.
    add_candidate(candidates, "RSI9 MR no EMA-gap gate", base_long, base_short, gap_min=0.0)
    add_candidate(candidates, "RSI9 MOM no EMA-gap gate", base_short, base_long, gap_min=0.0)

    def fresh(condition) -> pd.Series:
        condition = pd.Series(condition, index=bt.index).fillna(False).astype(bool)
        return condition & ~condition.shift(1, fill_value=False)

    def add_event(name, long_event, short_event, no_gap=True, atr_max=0.80):
        add_candidate(candidates, name, long_event, short_event, atr_max=atr_max)
        if no_gap:
            add_candidate(
                candidates, f"{name} [no gap]", long_event, short_event,
                gap_min=0.0, atr_max=atr_max,
            )

    # Event-driven mean reversion: enter once on the recovery cross, not on
    # every bar that remains oversold/overbought.
    for length in (7, 9, 14, 21):
        rsi = bt[f"rsi{length}"]
        for threshold in (25, 30, 35):
            hi = 100 - threshold
            add_event(
                f"RSI{length} recover {threshold}/{hi}",
                (rsi.shift(1) <= threshold) & (rsi > threshold),
                (rsi.shift(1) >= hi) & (rsi < hi),
            )
            add_event(
                f"RSI{length} fresh extreme {threshold}/{hi}",
                (rsi.shift(1) > threshold) & (rsi <= threshold),
                (rsi.shift(1) < hi) & (rsi >= hi),
            )

    z = bt["z20"]
    for threshold in (1.5, 2.0, 2.5):
        add_event(
            f"Z20 re-entry {threshold:g}",
            (z.shift(1) <= -threshold) & (z > -threshold),
            (z.shift(1) >= threshold) & (z < threshold),
        )
        add_event(
            f"Z20 fresh break {threshold:g}",
            (z.shift(1) < threshold) & (z >= threshold),
            (z.shift(1) > -threshold) & (z <= -threshold),
        )

    macd_up = (bt["macd"].shift(1) <= bt["macd_signal"].shift(1)) & (
        bt["macd"] > bt["macd_signal"]
    )
    macd_down = (bt["macd"].shift(1) >= bt["macd_signal"].shift(1)) & (
        bt["macd"] < bt["macd_signal"]
    )
    add_event("MACD cross", macd_up, macd_down)
    add_event("MACD cross + 1h trend", macd_up & h1_up, macd_down & h1_down)

    # Pullback recovery: trend remains aligned while the oscillator crosses
    # back toward neutral after an extreme.
    for length in (9, 14, 21):
        rsi = bt[f"rsi{length}"]
        recent_low = rsi.rolling(12).min().shift(1)
        recent_high = rsi.rolling(12).max().shift(1)
        add_event(
            f"RSI{length} trend pullback recovery",
            h1_up & (recent_low <= 30) & (rsi.shift(1) <= 40) & (rsi > 40),
            h1_down & (recent_high >= 70) & (rsi.shift(1) >= 60) & (rsi < 60),
        )

    add_event("fresh 6h breakout", fresh(bt["break6_up"]), fresh(bt["break6_dn"]))
    add_event("fresh 24h breakout", fresh(bt["break24_up"]), fresh(bt["break24_dn"]))
    add_event(
        "fresh 6h breakout + volume",
        fresh(bt["break6_up"]) & vol_high,
        fresh(bt["break6_dn"]) & vol_high,
    )
    add_event(
        "fresh 24h breakout + volume",
        fresh(bt["break24_up"]) & vol_high,
        fresh(bt["break24_dn"]) & vol_high,
    )

    # Higher-timeframe state changes become one-shot 5m events when a newly
    # closed 1h/day bar first satisfies the condition.
    h1_momo_long = h1_up & (bt["h1_rsi"] >= 60)
    h1_momo_short = h1_down & (bt["h1_rsi"] <= 40)
    add_event("fresh 1h momentum", fresh(h1_momo_long), fresh(h1_momo_short))
    add_event(
        "fresh 1h momentum + ADX",
        fresh(h1_momo_long & adx_high),
        fresh(h1_momo_short & adx_high),
    )
    d1_up = bt["d1_up"].astype("boolean").fillna(False).astype(bool)
    d1_down = ~d1_up
    d1_long = d1_up & (bt["d1_rsi"] >= 60)
    d1_short = d1_down & (bt["d1_rsi"] <= 40)
    add_event("fresh daily momentum", fresh(d1_long), fresh(d1_short), atr_max=2.0)

    # Side asymmetry is common in BTC. Keep these separate so a strong long
    # family is not averaged away by structurally weaker shorts.
    event_snapshot = list(candidates[-40:])
    for candidate in event_snapshot:
        raw = candidate.sides
        if (raw > 0).sum() >= 100:
            candidates.append(
                Candidate(
                    f"{candidate.name} LONG-only",
                    np.where(raw > 0, 1, 0).astype(np.int8),
                    candidate.gap_min,
                    candidate.atr_max,
                )
            )
        if (raw < 0).sum() >= 100:
            candidates.append(
                Candidate(
                    f"{candidate.name} SHORT-only",
                    np.where(raw < 0, -1, 0).astype(np.int8),
                    candidate.gap_min,
                    candidate.atr_max,
                )
            )
    return candidates


def period_slice(bt: pd.DataFrame, candidate: Candidate, period: str):
    start, end = PERIODS[period]
    keep = np.ones(len(bt), dtype=bool)
    if start is not None:
        keep &= bt["timestamp"].to_numpy() >= np.datetime64(start)
    if end is not None:
        keep &= bt["timestamp"].to_numpy() < np.datetime64(end)
    return bt.loc[keep].reset_index(drop=True), candidate.sides[keep]


def metrics(result: dict) -> dict:
    nets = np.array([trade[0] for trade in result["trades"]], dtype=float)
    if not len(nets):
        return {"n": 0, "pf": 0.0, "avg": 0.0, "pnl": 0.0, "wr": 0.0}
    gains = nets[nets > 0].sum()
    losses = nets[nets < 0].sum()
    return {
        "n": len(nets),
        "pf": gains / abs(losses) if losses < 0 else float("inf"),
        "avg": nets.mean(),
        "pnl": nets.sum(),
        "wr": (nets > 0).mean() * 100,
    }


def evaluate(bt: pd.DataFrame, candidate: Candidate, period: str, cfg: str = "v2.2") -> dict:
    frame, signal_sides = period_slice(bt, candidate, period)
    params = fh.CONFIGS[cfg]
    result = fh.run(
        frame,
        params["tp_dca"],
        params["time_sl"],
        signal_sides=signal_sides,
        gap_min=candidate.gap_min,
        atr_max=candidate.atr_max,
        **REAL,
    )
    return metrics(result)


def main() -> None:
    bt = prepare()
    candidates = build_candidates(bt)
    print(
        f"Data {bt['timestamp'].iloc[0]} -> {bt['timestamp'].iloc[-1]} | "
        f"{len(candidates)} entry candidates | v2.2 exits"
    )
    print("Search: 2019-22 | confirmation: 2023-24 | untouched holdout: 2025+\n")

    ranked = []
    for index, candidate in enumerate(candidates, 1):
        select = evaluate(bt, candidate, "select")
        confirm = evaluate(bt, candidate, "confirm")
        enough_trades = (
            select["n"] >= MIN_SELECT_TRADES
            and confirm["n"] >= MIN_CONFIRM_TRADES
        )
        robust_pf = min(select["pf"], confirm["pf"]) if enough_trades else 0.0
        ranked.append((robust_pf, candidate, select, confirm))
        if index % 20 == 0:
            print(f"  evaluated {index}/{len(candidates)}")

    ranked.sort(key=lambda row: (row[0], row[2]["pf"] + row[3]["pf"]), reverse=True)
    print("\nTop candidates before holdout:")
    print(
        f"{'candidate':<38} | {'SEL N':>6} {'PF':>6} {'avg$':>8} | "
        f"{'CFM N':>6} {'PF':>6} {'avg$':>8} | {'minPF':>6}"
    )
    for robust_pf, candidate, select, confirm in ranked[:20]:
        print(
            f"{candidate.name:<38} | {select['n']:>6,} {select['pf']:>6.3f} "
            f"{select['avg']:>+8.2f} | {confirm['n']:>6,} {confirm['pf']:>6.3f} "
            f"{confirm['avg']:>+8.2f} | {robust_pf:>6.3f}"
        )

    finalists = [row for row in ranked if row[0] > 0][:FINALISTS]
    print("\nFinal holdout and v2.1/v2.2 comparison:")
    print(
        f"{'candidate':<38} {'cfg':<5} | {'HOLD N':>7} {'PF':>6} "
        f"{'WR%':>6} {'avg$':>8} {'PnL$':>10}"
    )
    profitable = [
        row for row in finalists
        if row[2]["avg"] > 0 and row[3]["avg"] > 0
    ]
    if not profitable:
        print("No candidate was positive in both selection and confirmation.")
    for _, candidate, _, _ in finalists:
        for cfg in ("v2.1", "v2.2"):
            holdout = evaluate(bt, candidate, "holdout", cfg=cfg)
            print(
                f"{candidate.name:<38} {cfg:<5} | {holdout['n']:>7,} "
                f"{holdout['pf']:>6.3f} {holdout['wr']:>6.1f} "
                f"{holdout['avg']:>+8.2f} ${holdout['pnl']:>+9,.0f}"
            )


if __name__ == "__main__":
    main()
