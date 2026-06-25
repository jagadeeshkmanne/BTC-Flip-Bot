#!/usr/bin/env python3
"""Download BTCUSDT Binance USD-M futures 1H OHLCV with volume."""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/cache/BTCUSDT_1h_binance_volume.csv"
URL = "https://fapi.binance.com/fapi/v1/klines"


def main() -> None:
    start = int(pd.Timestamp("2019-09-01", tz="UTC").timestamp() * 1000)
    end = int(pd.Timestamp.utcnow().timestamp() * 1000)
    rows = []
    cur = start
    while cur < end:
        r = requests.get(
            URL,
            params={"symbol": "BTCUSDT", "interval": "1h", "startTime": cur, "limit": 1500},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        rows.extend(data)
        nxt = int(data[-1][0]) + 60 * 60 * 1000
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(0.08)
    df = pd.DataFrame(
        rows,
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trades",
            "taker_buy_base",
            "taker_buy_quote",
            "ignore",
        ],
    )
    df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
    df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms")
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col])
    df = df.drop_duplicates("timestamp").sort_values("timestamp")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(f"wrote {OUT} rows={len(df)} {df.timestamp.iloc[0]} -> {df.timestamp.iloc[-1]}")


if __name__ == "__main__":
    main()
