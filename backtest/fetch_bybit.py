"""Fetch BTCUSDT linear perp 15m + 1d klines from Bybit, extending cache as needed.

Bybit v5 public API. Linear category. Earliest kline ~2020-03-30 for BTCUSDT.P.
Cache files: data/cache/BYBIT_BTCUSDT_{15,1d}.csv
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
BASE = "https://api.bybit.com"
SYMBOL = "BTCUSDT"
CATEGORY = "linear"

INTERVALS = {
    "15": (CACHE / f"BYBIT_{SYMBOL}_15m.csv", 15 * 60_000),
    "D":  (CACHE / f"BYBIT_{SYMBOL}_1d.csv",  24 * 60 * 60_000),
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
    fmt = "%Y-%m-%d %H:%M:%S" if " " in rows[0]["timestamp"] else "%Y-%m-%d"
    first = dt.datetime.strptime(rows[0]["timestamp"], fmt)
    last = dt.datetime.strptime(rows[-1]["timestamp"], fmt)
    return rows, to_ms(first), to_ms(last)


def fetch_klines(interval: str, start_ms: int, end_ms: int, max_retries: int = 5) -> list[list]:
    """Bybit returns up to 1000 candles per request, NEWEST first."""
    step_ms = INTERVALS[interval][1]
    out_by_ts: dict[int, list] = {}
    cursor_end = end_ms
    while cursor_end > start_ms:
        params = {
            "category": CATEGORY,
            "symbol": SYMBOL,
            "interval": interval,
            "start": start_ms,
            "end": cursor_end,
            "limit": 1000,
        }
        for attempt in range(max_retries):
            try:
                r = requests.get(f"{BASE}/v5/market/kline", params=params, timeout=15)
                r.raise_for_status()
                data = r.json()
                if data.get("retCode") != 0:
                    raise RuntimeError(f"Bybit retCode {data.get('retCode')}: {data.get('retMsg')}")
                rows = data.get("result", {}).get("list", [])
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                time.sleep(2 ** attempt)
        if not rows:
            break
        # Bybit returns newest-first; capture by timestamp to dedup
        for k in rows:
            ts = int(k[0])
            if start_ms <= ts < end_ms and ts not in out_by_ts:
                out_by_ts[ts] = k
        # Advance cursor backward to the oldest kline returned - 1 step
        oldest_ts = min(int(k[0]) for k in rows)
        next_end = oldest_ts
        if next_end >= cursor_end:
            break
        cursor_end = next_end
        if len(rows) < 1000:
            break
    return [out_by_ts[ts] for ts in sorted(out_by_ts.keys())]


def fmt_row(k: list, interval: str) -> dict:
    ts = from_ms(int(k[0]))
    s = ts.strftime("%Y-%m-%d") if interval == "D" else ts.strftime("%Y-%m-%d %H:%M:%S")
    # Bybit v5 list format: [start, open, high, low, close, volume, turnover]
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
    print(f"[Bybit {interval}] existing rows={len(rows)}  "
          f"range=[{from_ms(first_ms) if first_ms else '—'} .. {from_ms(last_ms) if last_ms else '—'}]")

    target_start_ms = to_ms(target_start)
    target_end_ms = to_ms(target_end)
    new_rows: list[dict] = []

    if first_ms is None:
        ks = fetch_klines(interval, target_start_ms, target_end_ms)
        new_rows = [fmt_row(k, interval) for k in ks]
    else:
        if target_start_ms < first_ms:
            print(f"[Bybit {interval}] backfilling {from_ms(target_start_ms)} → {from_ms(first_ms)}")
            ks = fetch_klines(interval, target_start_ms, first_ms)
            new_rows.extend(fmt_row(k, interval) for k in ks)
        new_rows.extend(rows)
        if last_ms + step_ms < target_end_ms:
            print(f"[Bybit {interval}] forward fetching {from_ms(last_ms + step_ms)} → {from_ms(target_end_ms)}")
            ks = fetch_klines(interval, last_ms + step_ms, target_end_ms)
            new_rows.extend(fmt_row(k, interval) for k in ks)

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
    print(f"[Bybit {interval}] wrote {len(deduped)} rows to {path}")
    return path


def main():
    end = dt.datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    # Bybit BTCUSDT perp earliest: ~2020-03-30
    start = dt.datetime(2020, 3, 1)
    print(f"Target window: {start} .. {end}")
    update_cache("D",  start - dt.timedelta(days=10), end)
    update_cache("15", start, end)


if __name__ == "__main__":
    main()
