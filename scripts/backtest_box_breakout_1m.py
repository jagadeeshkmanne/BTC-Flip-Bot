#!/usr/bin/env python3
"""backtest_box_breakout_1m.py — price-action "5-min box" breakout scalper on 1m bars.

Strategy (from the "no-indicator price action scalping" video):
  - Work on the 1-minute chart; group bars into 5-min buckets (timestamp minute %5==0 starts).
  - BOX = high/low range of the *previous* completed 5-min bucket (5 one-min candles).
  - Watch the current bucket at 1m resolution: the first candle to break the box is the
    "breaking candle". Break UP -> long, break DOWN -> short.
  - ENTER at the OPEN of the candle AFTER the breaking candle.
  - STOP just beyond the breaking candle's low (long) / high (short).
  - TARGET at a fixed risk:reward (default 1.5R).
  - While a position is open we stop counting; after it closes we re-arm at the next
    5-min boundary. If a bucket completes with no break, the box simply rolls to the
    just-closed bucket (equivalent to: box = previous bucket at every flat candle).

HONESTY: break detected on a CLOSED candle, fill at NEXT candle open; stop/target hit on
REAL intrabar high/low (STOP-FIRST when a candle straddles both); fee 0.055%/side +
0.05% slippage/side. In-sample / out-of-sample split reported separately.
"""
from __future__ import annotations
import argparse, os, time
import numpy as np
import pandas as pd
import requests

FEE_PCT = float(os.environ.get("FEE_PCT", 0.00055))   # set FEE_PCT=0 SLIP_PCT=0 for gross-edge check
SLIP_PCT = float(os.environ.get("SLIP_PCT", 0.0005))
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "data/cache")
HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]


def fetch_1m(symbol: str, bars: int) -> pd.DataFrame:
    """Fetch `bars` 1m klines ending now, cached to data/cache/<symbol>_1m_binance.csv."""
    path = os.path.join(CACHE, f"{symbol}_1m_binance.csv")
    if os.path.exists(path):
        df = pd.read_csv(path, parse_dates=["timestamp"])
        if len(df) >= bars:
            return df.tail(bars).reset_index(drop=True)
    # paginate backwards from now
    rows: dict[int, list] = {}
    end_ms = None
    while len(rows) < bars:
        p = {"symbol": symbol, "interval": "1m", "limit": 1000}
        if end_ms:
            p["endTime"] = end_ms
        batch = None
        for host in HOSTS:
            try:
                r = requests.get(f"{host}/api/v3/klines", params=p, timeout=20)
                if r.status_code == 200:
                    batch = r.json()
                    break
            except Exception:
                continue
        if not batch:
            break
        for x in batch:
            rows[int(x[0])] = x
        end_ms = min(int(x[0]) for x in batch) - 1
        time.sleep(0.04)
    srt = sorted(rows.values(), key=lambda x: int(x[0]))
    df = pd.DataFrame({
        "timestamp": pd.to_datetime([int(x[0]) for x in srt], unit="ms"),
        "open": [float(x[1]) for x in srt],
        "high": [float(x[2]) for x in srt],
        "low": [float(x[3]) for x in srt],
        "close": [float(x[4]) for x in srt],
    })
    df.to_csv(path, index=False)
    return df.tail(bars).reset_index(drop=True)


FLOOR = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h",
         "2h": "2h", "4h": "4h", "6h": "6h", "12h": "12h", "1d": "1D"}


def load_exec(coin, tf, bars):
    """Execution-timeframe OHLC. 1m via cached Binance fetch; everything else via bt_helpers."""
    if tf == "1m":
        return fetch_1m(coin, bars)
    import bt_helpers
    return bt_helpers.load(coin, tf).reset_index(drop=True)


def metrics(eq):
    e = pd.Series(eq)
    if len(e) < 2:
        return 0.0, 0.0
    dd = e / e.cummax() - 1
    return (e.iloc[-1] - 1) * 100, float(dd.min() * 100)


def backtest(df, box_tf, *, rr=1.5, stop_buf=0.0, break_mode="close", allow_short=True):
    """box_tf: pandas-floor rule key (e.g. '15m','1h','4h') — box = prior HIGHER-TF candle's
    high/low range; df is the lower (execution) timeframe.
    break_mode: 'close' (candle closes beyond box) or 'wick' (high/low pierces box).
    stop_buf: extra buffer beyond breaking-candle extreme, as fraction of price."""
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    n = len(df)

    # box-bucket id per exec bar: dense rank of timestamp floored to box_tf
    floored = df["timestamp"].dt.floor(FLOOR[box_tf])
    bid = floored.factorize()[0].astype(np.int64)   # 0..nb-1, contiguous & ordered
    boundary = np.empty(n, dtype=bool)               # first exec bar of a new box bucket
    boundary[0] = True
    boundary[1:] = bid[1:] != bid[:-1]
    # per-bucket high/low (the higher-TF candle)
    nb = bid[-1] + 1
    bhi = np.full(nb, -np.inf)
    blo = np.full(nb, np.inf)
    for i in range(n):
        if h[i] > bhi[bid[i]]:
            bhi[bid[i]] = h[i]
        if l[i] < blo[bid[i]]:
            blo[bid[i]] = l[i]

    bal = 1.0
    eq = []
    trades = wins = 0
    longs = shorts = 0
    pos = None          # dict(side, entry, stop, target)
    pending = None      # dict(side, stop) -> enter at next open
    armed = True        # may we look for a new break?

    for i in range(n):
        # ---- manage open position on this candle (intrabar) ----
        if pos is not None:
            exit_px = None
            if pos["side"] == "long":
                if l[i] <= pos["stop"]:
                    exit_px = pos["stop"] * (1 - SLIP_PCT)
                elif h[i] >= pos["target"]:
                    exit_px = pos["target"] * (1 - SLIP_PCT)
                if exit_px is not None:
                    bal *= (exit_px / pos["entry"]) * (1 - 2 * FEE_PCT)
            else:
                if h[i] >= pos["stop"]:
                    exit_px = pos["stop"] * (1 + SLIP_PCT)
                elif l[i] <= pos["target"]:
                    exit_px = pos["target"] * (1 + SLIP_PCT)
                if exit_px is not None:
                    bal *= (pos["entry"] / exit_px) * (1 - 2 * FEE_PCT)
            if exit_px is not None:
                trades += 1
                wins += int((exit_px > pos["entry"]) == (pos["side"] == "long"))
                pos = None
                armed = False  # re-arm only at next 5-min boundary
            eq.append(bal if pos is None else
                      bal * (c[i] / pos["entry"] if pos["side"] == "long" else pos["entry"] / c[i]))
            continue

        # ---- flat: take a pending entry filled at this open ----
        if pending is not None:
            entry_side = pending["side"]
            if entry_side == "long":
                entry = o[i] * (1 + SLIP_PCT)
                stop = pending["stop"]
                risk = entry - stop
                if risk > 0:
                    pos = {"side": "long", "entry": entry, "stop": stop,
                           "target": entry + rr * risk}
                    longs += 1
            else:
                entry = o[i] * (1 - SLIP_PCT)
                stop = pending["stop"]
                risk = stop - entry
                if risk > 0:
                    pos = {"side": "short", "entry": entry, "stop": stop,
                           "target": entry - rr * risk}
                    shorts += 1
            pending = None
            eq.append(bal)
            continue

        # ---- re-arm at bucket boundary after a close ----
        if not armed and boundary[i]:
            armed = True

        # ---- look for a break of the previous bucket's box ----
        if armed and bid[i] >= 1:
            box_hi = bhi[bid[i] - 1]
            box_lo = blo[bid[i] - 1]
            up = (c[i] > box_hi) if break_mode == "close" else (h[i] > box_hi)
            dn = (c[i] < box_lo) if break_mode == "close" else (l[i] < box_lo)
            if up:
                pending = {"side": "long", "stop": l[i] * (1 - stop_buf)}
            elif dn and allow_short:
                pending = {"side": "short", "stop": h[i] * (1 + stop_buf)}
        eq.append(bal)

    net, dd = metrics(eq)
    wr = wins / trades * 100 if trades else 0.0
    return {"net": net, "dd": dd, "trades": trades, "wr": wr,
            "longs": longs, "shorts": shorts}


# (box_tf, exec_tf) pairs to sweep — box is the higher TF, execution the lower TF
PAIRS = [
    ("5m", "1m"), ("15m", "5m"), ("30m", "5m"), ("1h", "5m"),
    ("1h", "15m"), ("4h", "15m"), ("4h", "1h"), ("1d", "1h"),
]


def run_pair(box_tf, exec_tf, symbol, bars):
    df = load_exec(symbol, exec_tf, bars)
    days = (df.timestamp.iloc[-1] - df.timestamp.iloc[0]).days
    split = int(len(df) * 0.6)
    oos = df.iloc[split:].reset_index(drop=True)
    bh_full = (df.close.iloc[-1] / df.open.iloc[0] - 1) * 100
    bh_oos = (oos.close.iloc[-1] / oos.open.iloc[0] - 1) * 100

    print(f"\n=== box {box_tf} / exec {exec_tf}  ({symbol} bars={len(df)} ~{days}d "
          f"{df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()}) "
          f"buy&hold FULL {bh_full:+.0f}% / OOS {bh_oos:+.0f}% ===")
    hdr = (f"{'variant':22s} {'FULL net%':>9} {'dd%':>7} {'tr':>6} {'wr%':>5} "
           f"{'L/S':>11} | {'OOS net%':>8} {'tr':>6} {'wr%':>5}")
    print(hdr)
    print("-" * len(hdr))
    for mode in ("close", "wick"):
        for rr in (1.5, 2.0):
            for short in (True, False):
                rf = backtest(df, box_tf, rr=rr, break_mode=mode, allow_short=short)
                ro = backtest(oos, box_tf, rr=rr, break_mode=mode, allow_short=short)
                name = f"{mode} rr{rr}{'' if short else ' Lonly'}"
                print(f"{name:22s} {rf['net']:9.1f} {rf['dd']:7.1f} {rf['trades']:6d} {rf['wr']:5.0f} "
                      f"{rf['longs']:5d}/{rf['shorts']:<5d} | "
                      f"{ro['net']:8.1f} {ro['trades']:6d} {ro['wr']:5.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--bars", type=int, default=525600)  # only used for 1m fetch
    ap.add_argument("--box", default=None, help="box timeframe (e.g. 1h)")
    ap.add_argument("--exec", dest="exec_tf", default=None, help="exec timeframe (e.g. 15m)")
    a = ap.parse_args()

    pairs = [(a.box, a.exec_tf)] if a.box and a.exec_tf else PAIRS
    for box_tf, exec_tf in pairs:
        try:
            run_pair(box_tf, exec_tf, a.symbol, a.bars)
        except Exception as e:
            print(f"\n=== box {box_tf} / exec {exec_tf}: SKIPPED ({e}) ===")


if __name__ == "__main__":
    main()
