#!/usr/bin/env python3
"""Walk-forward audit for the 4-coin EMA all-weather strategy.

Audits the claimed BTC+ETH+BNB+SOL equal-weight EMA8/200 long/short reverse idea.

Outputs:
- fixed strategy performance on rolling calendar OOS windows
- rolling train -> next-year test where the EMA pair is selected on the train window
- mature-period and full-period metrics vs buy-and-hold

Same baseline assumptions as the project research scripts:
- signal on closed 4h bar
- fill on next 4h open
- fee 0.055%/side + 0.05% slippage
"""
from __future__ import annotations

import time
from itertools import combinations
from pathlib import Path

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data/cache"
FEE_PCT = 0.00055
SLIP_PCT = 0.00050
HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]
COINS = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "BNB": "BNBUSDT", "SOL": "SOLUSDT"}
EMA_GRID = [(5, 200), (8, 200), (13, 200), (21, 200), (8, 100), (13, 100), (21, 100)]


def cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol}_4h_2019_binance.csv"


def fetch_binance(symbol: str, start: str = "2019-01-01") -> pd.DataFrame:
    path = cache_path(symbol)
    if path.exists():
        df = pd.read_csv(path, parse_dates=["timestamp"])
        if len(df) > 1000:
            return df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

    rows = []
    cur = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    url_ok = None
    while True:
        params = {"symbol": symbol, "interval": "4h", "startTime": cur, "limit": 1000}
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
        raise RuntimeError(f"no Binance rows fetched for {symbol}")
    seen = {int(row[0]): row for row in rows}
    ordered = [seen[k] for k in sorted(seen)]
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
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return df


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def reverse_equity(df: pd.DataFrame, fast: int, slow: int) -> pd.Series:
    close = df["close"]
    bull = ema(close, fast) > ema(close, slow)
    bal = 1.0
    side = 0
    entry = 0.0
    eq = []
    ts = []
    for i in range(slow + 5, len(df) - 1):
        nxt = float(df.iloc[i + 1]["open"])
        want = 1 if bool(bull.iloc[i]) else -1
        if side != want:
            if side == 1:
                fill = nxt * (1 - SLIP_PCT)
                bal = bal * (fill / entry) * (1 - 2 * FEE_PCT)
            elif side == -1:
                fill = nxt * (1 + SLIP_PCT)
                bal = bal * ((2 * entry - fill) / entry) * (1 - 2 * FEE_PCT)
            entry = nxt * (1 + SLIP_PCT) if want == 1 else nxt * (1 - SLIP_PCT)
            side = want
        nc = float(df.iloc[i + 1]["close"])
        value = bal * nc / entry if side == 1 else bal * (2 * entry - nc) / entry
        eq.append(value)
        ts.append(df.iloc[i + 1]["timestamp"])
    return pd.Series(eq, index=pd.to_datetime(ts)).sort_index()


def buy_hold_equity(df: pd.DataFrame) -> pd.Series:
    s = df.set_index("timestamp")["close"].astype(float)
    return s / s.iloc[0]


def portfolio_from_equities(eqs: dict[str, pd.Series], coins: tuple[str, ...]) -> pd.Series:
    rets = pd.DataFrame({coin: eqs[coin].pct_change() for coin in coins}).sort_index()
    port_ret = rets.mean(axis=1, skipna=False).dropna()
    return (1 + port_ret).cumprod()


def slice_equity(eq: pd.Series, start: str, end: str) -> pd.Series:
    seg = eq[(eq.index >= pd.Timestamp(start)) & (eq.index < pd.Timestamp(end))].copy()
    if len(seg) < 2:
        return seg
    return seg / seg.iloc[0]


def metrics(eq: pd.Series) -> dict[str, float]:
    if len(eq) < 2:
        return {"net": 0.0, "cagr": 0.0, "dd": 0.0, "months": 0.0, "pos_months": 0.0}
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1 / 365.25)
    net = (eq.iloc[-1] / eq.iloc[0] - 1) * 100
    cagr = ((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100
    dd = (eq / eq.cummax() - 1).min() * 100
    mo = eq.resample("ME").last().pct_change().dropna() * 100
    pos_months = (mo > 0.2).mean() * 100 if len(mo) else 0.0
    return {"net": float(net), "cagr": float(cagr), "dd": float(dd), "months": float(len(mo)), "pos_months": float(pos_months)}


def ret_dd_score(eq: pd.Series) -> float:
    m = metrics(eq)
    if m["dd"] >= 0:
        return -1e9
    return m["cagr"] / abs(m["dd"])


def print_metric_line(label: str, eq: pd.Series) -> None:
    m = metrics(eq)
    print(
        f"{label:<34} net={m['net']:8.1f}% CAGR={m['cagr']:7.1f}% "
        f"DD={m['dd']:7.1f}% +mo={m['pos_months']:5.1f}% months={m['months']:4.0f}"
    )


def main() -> None:
    data = {coin: fetch_binance(symbol) for coin, symbol in COINS.items()}
    print("data windows:")
    for coin, df in data.items():
        print(f"  {coin:<3} {df.timestamp.iloc[0]} -> {df.timestamp.iloc[-1]} bars={len(df)}")
    print()

    equity_cache = {
        (coin, fast, slow): reverse_equity(df, fast, slow)
        for coin, df in data.items()
        for fast, slow in EMA_GRID
    }

    fixed_eqs = {coin: equity_cache[(coin, 8, 200)] for coin in COINS}
    fixed_port = portfolio_from_equities(fixed_eqs, tuple(COINS))
    hold_eqs = {coin: buy_hold_equity(df) for coin, df in data.items()}
    hold_port = portfolio_from_equities(hold_eqs, tuple(COINS))

    print("fixed BTC+ETH+BNB+SOL EMA8/200 reverse:")
    for start, end in [
        ("2020-08-01", "2026-07-01"),
        ("2023-01-01", "2026-07-01"),
        ("2024-01-01", "2026-07-01"),
    ]:
        print_metric_line(f"strategy {start[:4]}->{end[:4]}", slice_equity(fixed_port, start, end))
        print_metric_line(f"buyhold  {start[:4]}->{end[:4]}", slice_equity(hold_port, start, end))
    print()

    print("calendar-year OOS chunks for the fixed strategy:")
    print(f"{'period':<13} {'net%':>8} {'CAGR%':>8} {'DD%':>8} {'+mo%':>7} {'BH net%':>9}")
    for year in range(2021, 2027):
        start = f"{year}-01-01"
        end = f"{year + 1}-01-01"
        seg = slice_equity(fixed_port, start, end)
        bh = slice_equity(hold_port, start, end)
        if len(seg) < 20:
            continue
        m = metrics(seg)
        b = metrics(bh)
        print(f"{year:<13} {m['net']:8.1f} {m['cagr']:8.1f} {m['dd']:8.1f} {m['pos_months']:7.1f} {b['net']:9.1f}")
    print()

    print("walk-forward: train prior 24 months, choose best EMA pair on same 4 coins, test next 12 months")
    print(f"{'test':<11} {'chosen':<10} {'train score':>11} {'test CAGR':>10} {'test DD':>9} {'test net':>9}")
    stitched = []
    for test_year in range(2022, 2027):
        train_start = f"{test_year - 2}-01-01"
        train_end = f"{test_year}-01-01"
        test_start = f"{test_year}-01-01"
        test_end = f"{test_year + 1}-01-01"
        best = None
        for fast, slow in EMA_GRID:
            eqs = {coin: equity_cache[(coin, fast, slow)] for coin in COINS}
            port = portfolio_from_equities(eqs, tuple(COINS))
            train_eq = slice_equity(port, train_start, train_end)
            if len(train_eq) < 100:
                continue
            score = ret_dd_score(train_eq)
            if best is None or score > best[0]:
                best = (score, fast, slow, port)
        if best is None:
            continue
        score, fast, slow, port = best
        test_eq = slice_equity(port, test_start, test_end)
        if len(test_eq) < 20:
            continue
        stitched.append(test_eq.pct_change().dropna())
        m = metrics(test_eq)
        print(
            f"{test_year:<11} EMA{fast}/{slow:<5} {score:11.2f} "
            f"{m['cagr']:10.1f} {m['dd']:9.1f} {m['net']:9.1f}"
        )
    if stitched:
        wf_ret = pd.concat(stitched).sort_index()
        wf_eq = (1 + wf_ret).cumprod()
        print_metric_line("stitched WF selected", wf_eq)

    print()
    print("walk-forward: train prior 24 months, choose best basket + EMA pair, test next 12 months")
    print(f"{'test':<11} {'basket':<16} {'chosen':<10} {'test CAGR':>10} {'test DD':>9} {'test net':>9}")
    stitched = []
    coin_names = tuple(COINS)
    baskets = [combo for n in range(1, len(coin_names) + 1) for combo in combinations(coin_names, n)]
    for test_year in range(2022, 2027):
        train_start = f"{test_year - 2}-01-01"
        train_end = f"{test_year}-01-01"
        test_start = f"{test_year}-01-01"
        test_end = f"{test_year + 1}-01-01"
        best = None
        for fast, slow in EMA_GRID:
            eqs = {coin: equity_cache[(coin, fast, slow)] for coin in COINS}
            for basket in baskets:
                port = portfolio_from_equities(eqs, basket)
                train_eq = slice_equity(port, train_start, train_end)
                if len(train_eq) < 100:
                    continue
                score = ret_dd_score(train_eq)
                if best is None or score > best[0]:
                    best = (score, fast, slow, basket, port)
        if best is None:
            continue
        _, fast, slow, basket, port = best
        test_eq = slice_equity(port, test_start, test_end)
        if len(test_eq) < 20:
            continue
        stitched.append(test_eq.pct_change().dropna())
        m = metrics(test_eq)
        print(
            f"{test_year:<11} {'+'.join(basket):<16} EMA{fast}/{slow:<5} "
            f"{m['cagr']:10.1f} {m['dd']:9.1f} {m['net']:9.1f}"
        )
    if stitched:
        wf_ret = pd.concat(stitched).sort_index()
        wf_eq = (1 + wf_ret).cumprod()
        print_metric_line("stitched WF selected basket", wf_eq)


if __name__ == "__main__":
    main()
