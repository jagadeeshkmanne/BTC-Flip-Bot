#!/usr/bin/env python3
"""Fetch N days of 1m BTCUSDT linear-perp candles from Bybit -> npy cache."""
import sys, time
import numpy as np
import requests

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 90
BASE = "https://api.bybit.com"
end = int(time.time() // 60 * 60 * 1000)
start = end - DAYS * 86_400_000
rows = []
cur = start
while cur < end:
    r = requests.get(f"{BASE}/v5/market/kline",
                     params={"category": "linear", "symbol": "BTCUSDT",
                             "interval": "1", "start": cur, "limit": 1000},
                     timeout=15)
    lst = r.json().get("result", {}).get("list", [])
    if not lst:
        break
    lst = list(reversed(lst))
    rows.extend(lst)
    nxt = int(lst[-1][0]) + 60_000
    if nxt <= cur:
        break
    cur = nxt
    if len(rows) % 50_000 < 1000:
        print(f"  {len(rows)} bars…", flush=True)
    time.sleep(0.05)
# Jesse candle format: [timestamp_ms, open, close, high, low, volume]
arr = np.array([[int(k[0]), float(k[1]), float(k[4]), float(k[2]),
                 float(k[3]), float(k[5])] for k in rows])
# dedupe + sort
_, idx = np.unique(arr[:, 0], return_index=True)
arr = arr[idx]
np.save("/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_1m_jesse.npy", arr)
print(f"saved {len(arr)} 1m candles "
      f"({DAYS}d requested)")
