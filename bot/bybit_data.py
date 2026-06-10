"""data_bybit.py — Bybit public klines + ticker fetchers, Binance-compatible output.

2026-06-05: replaces direct Binance fapi calls in all RSI bots. Same return shape
as the old Binance fetchers so the rest of bot_rsiscalp*.py is untouched.

Bybit V5 reference:
  Klines: GET /v5/market/kline?category=linear&symbol=BTCUSDT&interval=5&limit=200
          response.result.list = [[startMs, open, high, low, close, volume, turnover], ...]
          → NEWEST first; we reverse to oldest-first (Binance order).
  Ticker: GET /v5/market/tickers?category=linear&symbol=BTCUSDT
          response.result.list[0].lastPrice
"""
from __future__ import annotations
import logging
import requests
import pandas as pd

BYBIT_BASE = "https://api.bybit.com"
CATEGORY = "linear"   # USDT-margined perpetual (same instrument the bots trade)

# Binance interval → Bybit interval token. Bybit uses minutes as integers.
_INTERVAL_MAP = {
    "1m":  "1",
    "3m":  "3",
    "5m":  "5",
    "15m": "15",
    "30m": "30",
    "1h":  "60",
    "2h":  "120",
    "4h":  "240",
    "6h":  "360",
    "12h": "720",
    "1d":  "D",
    "1w":  "W",
    "1M":  "M",
}


def fetch_klines(interval: str, limit: int = 500, symbol: str = "BTCUSDT",
                 log: logging.Logger | None = None) -> pd.DataFrame | None:
    """Returns a DataFrame with columns: timestamp, open, high, low, close, volume.
    OLDEST-first ordering, matching the previous Binance fetcher."""
    bb_int = _INTERVAL_MAP.get(interval)
    if bb_int is None:
        if log: log.error(f"unsupported interval for Bybit: {interval}")
        return None
    # Bybit caps limit at 1000; for safety match Binance's typical max.
    limit = max(1, min(limit, 1000))
    try:
        r = requests.get(
            f"{BYBIT_BASE}/v5/market/kline",
            params={"category": CATEGORY, "symbol": symbol, "interval": bb_int, "limit": limit},
            timeout=10,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("retCode") != 0:
            if log: log.error(f"Bybit klines retCode={body.get('retCode')} {body.get('retMsg')}")
            return None
        rows = body.get("result", {}).get("list", [])
        if not rows:
            return None
        # Bybit returns newest-first; reverse to oldest-first.
        rows = list(reversed(rows))
        return pd.DataFrame([{
            "timestamp": pd.to_datetime(int(k[0]), unit="ms"),
            "open":  float(k[1]),
            "high":  float(k[2]),
            "low":   float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        } for k in rows])
    except Exception as e:
        if log: log.error(f"Bybit klines fetch failed ({interval}): {e}")
        return None


def fetch_live_price(symbol: str = "BTCUSDT", log: logging.Logger | None = None) -> float | None:
    """Returns the latest trade price as a float, or None on failure."""
    try:
        r = requests.get(
            f"{BYBIT_BASE}/v5/market/tickers",
            params={"category": CATEGORY, "symbol": symbol},
            timeout=10,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("retCode") != 0:
            if log: log.error(f"Bybit ticker retCode={body.get('retCode')} {body.get('retMsg')}")
            return None
        rows = body.get("result", {}).get("list", [])
        if not rows:
            return None
        px = rows[0].get("lastPrice") or rows[0].get("markPrice")
        return float(px) if px is not None else None
    except Exception as e:
        if log: log.error(f"Bybit live price fetch failed: {e}")
        return None
