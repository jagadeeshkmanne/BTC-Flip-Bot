#!/usr/bin/env python3
"""Full-history OOS test for the PVZ/VZO 4H BTCUSDT strategy.

Protocol:
- Fetch/cache Binance BTCUSDT 4H candles from 2019 to latest.
- Search configs on selection only: 2019-01-01 through 2022-12-31.
- Confirm on 2023.
- Final untouched OOS: 2024-01-01 through latest.

The final OOS ranking is only printed after configs pass selection and 2023
confirmation. This avoids choosing parameters from the OOS period.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
import requests

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[0]
sys.path.insert(0, str(SCRIPT_DIR))

from backtest_pvz_strategy import Config, config_space, run_backtest, fmt_cfg  # noqa: E402


HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]
CACHE = ROOT / "data/cache/BTCUSDT_4h_2019_binance.csv"


def fetch_binance(symbol: str = "BTCUSDT", interval: str = "4h", start: str = "2019-01-01") -> pd.DataFrame:
    if CACHE.exists():
        df = pd.read_csv(CACHE, parse_dates=["timestamp"])
        if len(df) > 1000:
            return df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

    rows = []
    cur = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    url_ok = None
    while True:
        params = {"symbol": symbol, "interval": interval, "startTime": cur, "limit": 1000}
        data = None
        for host in ([url_ok] if url_ok else HOSTS):
            try:
                r = requests.get(f"{host}/api/v3/klines", params=params, timeout=20)
                if r.status_code == 200:
                    data = r.json()
                    url_ok = host
                    break
            except requests.RequestException:
                continue
        if not data:
            break
        rows.extend(data)
        if len(data) < 1000:
            break
        cur = int(data[-1][0]) + 1
        time.sleep(0.12)

    if not rows:
        raise RuntimeError("No Binance rows fetched")
    by_open = {int(row[0]): row for row in rows}
    ordered = [by_open[k] for k in sorted(by_open)]
    df = pd.DataFrame(
        {
            "timestamp": pd.to_datetime([int(x[0]) for x in ordered], unit="ms"),
            "open": [float(x[1]) for x in ordered],
            "high": [float(x[2]) for x in ordered],
            "low": [float(x[3]) for x in ordered],
            "close": [float(x[4]) for x in ordered],
            "volume": [float(x[5]) for x in ordered],
        }
    )
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(CACHE, index=False)
    return df


def period(df: pd.DataFrame, start: str, end: str | None = None) -> pd.DataFrame:
    mask = df["timestamp"] >= pd.Timestamp(start)
    if end is not None:
        mask &= df["timestamp"] < pd.Timestamp(end)
    return df.loc[mask].reset_index(drop=True)


def robust_score(selection: dict[str, float], confirm: dict[str, float]) -> float:
    if min(selection["trades"], confirm["trades"]) < 6:
        return -1e9
    pf_floor = min(selection["profit_factor"], confirm["profit_factor"])
    return (
        pf_floor * 100
        + 0.15 * selection["net_pct"]
        + confirm["net_pct"]
        - max(0.0, selection["max_dd_pct"] - 25) * 2
        - max(0.0, confirm["max_dd_pct"] - 20) * 2
    )


def main() -> None:
    df = fetch_binance()
    sel = period(df, "2019-01-01", "2023-01-01")
    conf = period(df, "2023-01-01", "2024-01-01")
    oos = period(df, "2024-01-01")
    print(f"BTCUSDT 4h bars={len(df)} {df.timestamp.iloc[0]} -> {df.timestamp.iloc[-1]}")
    print(f"Selection: {sel.timestamp.iloc[0]} -> {sel.timestamp.iloc[-1]} bars={len(sel)}")
    print(f"Confirm:   {conf.timestamp.iloc[0]} -> {conf.timestamp.iloc[-1]} bars={len(conf)}")
    print(f"OOS:       {oos.timestamp.iloc[0]} -> {oos.timestamp.iloc[-1]} bars={len(oos)}")
    print("Costs: 0.055% fee each side + 0.05% slippage each side; next-open entries; stop-first intrabar.\n")

    rows = []
    for cfg in config_space("4h"):
        selection = run_backtest(sel, cfg)
        confirm = run_backtest(conf, cfg)
        sc = robust_score(selection, confirm)
        if sc <= -1e8:
            continue
        rows.append((sc, cfg, selection, confirm))
    rows.sort(key=lambda r: r[0], reverse=True)

    final = []
    for sc, cfg, selection, confirm in rows[:50]:
        oos_stats = run_backtest(oos, cfg)
        full_stats = run_backtest(df, cfg)
        final.append((sc, cfg, selection, confirm, oos_stats, full_stats))

    print(
        f"{'rank':>4} {'score':>8} {'cfg':<68} | "
        f"{'SEL net':>7} {'SEL PF':>6} {'SEL DD':>6} {'tr':>4} | "
        f"{'2023 net':>8} {'PF':>5} {'tr':>4} | "
        f"{'OOS net':>8} {'OOS CAGR':>8} {'OOS PF':>6} {'OOS DD':>7} {'tr':>4} | "
        f"{'FULL net':>8} {'FULL PF':>7}"
    )
    for rank, (sc, cfg, selection, confirm, oos_stats, full_stats) in enumerate(final[:15], start=1):
        print(
            f"{rank:4d} {sc:8.1f} {fmt_cfg(cfg):<68} | "
            f"{selection['net_pct']:7.2f} {selection['profit_factor']:6.2f} {selection['max_dd_pct']:6.2f} {selection['trades']:4.0f} | "
            f"{confirm['net_pct']:8.2f} {confirm['profit_factor']:5.2f} {confirm['trades']:4.0f} | "
            f"{oos_stats['net_pct']:8.2f} {oos_stats['cagr_pct']:8.2f} {oos_stats['profit_factor']:6.2f} "
            f"{oos_stats['max_dd_pct']:7.2f} {oos_stats['trades']:4.0f} | "
            f"{full_stats['net_pct']:8.2f} {full_stats['profit_factor']:7.2f}"
        )


if __name__ == "__main__":
    main()
