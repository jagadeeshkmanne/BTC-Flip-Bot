"""Fetch BTCUSDT perp 5m + 1d klines from Binance, extending cache as needed.

Range target: covers a 5-year backtest window, ending today (UTC).
Cache files: data/cache/BTCUSDT_5m.csv and BTCUSDT_1d.csv.
"""
from __future__ import annotations
import csv
import os
import sys
import time
import datetime as dt
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "data" / "cache"
BASE = "https://fapi.binance.com"  # USDT-M perp
SYMBOL = "BTCUSDT"

INTERVALS = {
    "5m": (CACHE / f"{SYMBOL}_5m.csv", 5 * 60_000),
    "1d": (CACHE / f"{SYMBOL}_1d.csv", 24 * 60 * 60_000),
}


def to_ms(d: dt.datetime) -> int:
    return int(d.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def from_ms(ms: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).replace(tzinfo=None)


def read_existing(path: Path) -> tuple[list[dict], int | None, int | None]:
    if not path.exists():
        return [], None, None
    rows = []
    with path.open() as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append(row)
    if not rows:
        return [], None, None
    first = dt.datetime.strptime(rows[0]["timestamp"], "%Y-%m-%d %H:%M:%S" if " " in rows[0]["timestamp"] else "%Y-%m-%d")
    last = dt.datetime.strptime(rows[-1]["timestamp"], "%Y-%m-%d %H:%M:%S" if " " in rows[-1]["timestamp"] else "%Y-%m-%d")
    return rows, to_ms(first), to_ms(last)


def fetch_klines(interval: str, start_ms: int, end_ms: int, max_retries: int = 5) -> list[list]:
    out = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": SYMBOL,
            "interval": interval,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1500,
        }
        for attempt in range(max_retries):
            try:
                r = requests.get(f"{BASE}/fapi/v1/klines", params=params, timeout=15)
                if r.status_code == 429:
                    time.sleep(5)
                    continue
                r.raise_for_status()
                data = r.json()
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                time.sleep(2 ** attempt)
        if not data:
            break
        out.extend(data)
        last_open = data[-1][0]
        cursor = last_open + INTERVALS[interval][1]
        if len(data) < 1500:
            break
    return out


def fmt_row(k: list, interval: str) -> dict:
    ts = from_ms(k[0])
    s = ts.strftime("%Y-%m-%d") if interval == "1d" else ts.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "timestamp": s,
        "open": k[1],
        "high": k[2],
        "low": k[3],
        "close": k[4],
        "volume": k[5],
    }


def update_cache(interval: str, target_start: dt.datetime, target_end: dt.datetime) -> Path:
    path, step_ms = INTERVALS[interval]
    rows, first_ms, last_ms = read_existing(path)
    print(f"[{interval}] existing rows={len(rows)}  range=[{from_ms(first_ms) if first_ms else '—'} .. {from_ms(last_ms) if last_ms else '—'}]")

    target_start_ms = to_ms(target_start)
    target_end_ms = to_ms(target_end)
    new_rows: list[dict] = []

    if first_ms is None:
        ks = fetch_klines(interval, target_start_ms, target_end_ms)
        new_rows = [fmt_row(k, interval) for k in ks]
    else:
        # Backfill before
        if target_start_ms < first_ms:
            print(f"[{interval}] backfilling {from_ms(target_start_ms)} → {from_ms(first_ms)}")
            ks = fetch_klines(interval, target_start_ms, first_ms)
            new_rows.extend(fmt_row(k, interval) for k in ks)
        new_rows.extend(rows)
        # Forward extend
        if last_ms + step_ms < target_end_ms:
            print(f"[{interval}] forward fetching {from_ms(last_ms + step_ms)} → {from_ms(target_end_ms)}")
            ks = fetch_klines(interval, last_ms + step_ms, target_end_ms)
            new_rows.extend(fmt_row(k, interval) for k in ks)

    # Dedup + sort
    seen = set()
    deduped = []
    for r in new_rows:
        if r["timestamp"] in seen:
            continue
        seen.add(r["timestamp"])
        deduped.append(r)
    deduped.sort(key=lambda r: r["timestamp"])

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        w.writeheader()
        w.writerows(deduped)
    print(f"[{interval}] wrote {len(deduped)} rows to {path}")
    return path


def main():
    end = dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    start = end - dt.timedelta(days=365 * 5 + 5)
    print(f"target window: {start} .. {end}  (5 years)")
    update_cache("1d", start - dt.timedelta(days=10), end)
    update_cache("5m", start, end)


if __name__ == "__main__":
    main()
