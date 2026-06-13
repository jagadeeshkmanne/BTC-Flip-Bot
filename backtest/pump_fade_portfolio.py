#!/usr/bin/env python3
"""pump_fade_portfolio.py — portfolio-level honest test of the pump-fade short
(FINDINGS #10 follow-up: drawdown, liquidation, funding — the report).

Strategy: SHORT at the daily close of any Bybit USDT perp that gained >= +30%
that day; exit at close H days later. Isolated margin per position.

Portfolio rules (explicit):
  start $5,000; margin per trade = 10% of current equity; max 10 concurrent
  positions (excess events skipped, highest-turnover first); one position per
  symbol at a time; fees 0.055% + slip 0.02% per side on notional.

Liquidation (isolated short, linear perp): liq when day HIGH >= entry *
(1 + 1/L - 0.5% mmr). Liq checked BEFORE the scheduled exit on the same day
(pessimistic). Liquidated position loses its entire margin.

Funding: NOT in the sim; measured separately from ACTUAL Bybit funding history
on a sample of events and reported as a per-event drag distribution.

Universes: ALL perps, and LIQUID = trailing-30d median turnover >= $10M.
Sweep: leverage {1,2,3,5} x exit {1d,3d} x universe.
"""
from __future__ import annotations
import os, pickle, random, sys, time
import numpy as np
import pandas as pd
import requests

BASE = "https://api.bybit.com"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                     "data", "cache", "pump_fade_daily.pkl")
FEE = 0.00055 + 0.0002          # per side, on notional
PUMP = 0.30
START = 5000.0
MARGIN_FRAC = 0.10              # of equity, per position
MAX_POS = 10
MMR = 0.005
LEVS = [1, 2, 3, 5]
HORIZONS = [1, 3]
LIQ_TURNOVER = 10e6
FUND_SAMPLE = 240


def all_perps() -> list[str]:
    r = requests.get(f"{BASE}/v5/market/tickers",
                     params={"category": "linear"}, timeout=15)
    return sorted(t["symbol"] for t in r.json()["result"]["list"]
                  if t["symbol"].endswith("USDT") and "-" not in t["symbol"])


def fetch_all_daily() -> dict[str, pd.DataFrame]:
    if os.path.exists(CACHE):
        with open(CACHE, "rb") as f:
            return pickle.load(f)
    out = {}
    syms = all_perps()
    print(f"fetching daily klines for {len(syms)} perps…", file=sys.stderr)
    for i, s in enumerate(syms):
        try:
            r = requests.get(f"{BASE}/v5/market/kline",
                             params={"category": "linear", "symbol": s,
                                     "interval": "D", "limit": 1000}, timeout=15)
            rows = r.json().get("result", {}).get("list", [])
            if rows:
                rows = list(reversed(rows))
                df = pd.DataFrame([{
                    "date": pd.to_datetime(int(k[0]), unit="ms").normalize(),
                    "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4]),
                    "turnover": float(k[6])} for k in rows]).iloc[:-1]
                if len(df) >= 3:
                    out[s] = df.set_index("date")
        except Exception:
            pass
        if (i + 1) % 100 == 0:
            print(f"  …{i+1}/{len(syms)}", file=sys.stderr)
        time.sleep(0.03)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "wb") as f:
        pickle.dump(out, f)
    return out


def build_events(data: dict[str, pd.DataFrame], liquid_only: bool) -> dict:
    """date -> list of (symbol, turnover) pump events, turnover-desc."""
    ev = {}
    for s, df in data.items():
        c = df["close"].values
        med30 = df["turnover"].rolling(30, min_periods=5).median().values
        for j in range(1, len(c)):
            if c[j] / c[j - 1] - 1 >= PUMP:
                if liquid_only and not (med30[j] and med30[j] >= LIQ_TURNOVER):
                    continue
                ev.setdefault(df.index[j], []).append((s, df["turnover"].iloc[j]))
    for d in ev:
        ev[d].sort(key=lambda x: -x[1])
    return ev


def simulate(data, events, lev: float, horizon: int) -> dict:
    dates = sorted(set(d for df in data.values() for d in df.index))
    cash, positions = START, {}      # sym -> dict
    eq_curve, n_liq, n_trades, n_wins = [], 0, 0, 0
    skipped = 0
    for d in dates:
        # 1) manage open positions (liq check first — pessimistic), then exits
        for s in list(positions):
            pos = positions[s]
            if d not in data[s].index:
                continue
            bar = data[s].loc[d]
            liq_px = pos["entry"] * (1 + 1 / lev - MMR)
            if bar["high"] >= liq_px:                    # margin wiped
                positions.pop(s)
                n_liq += 1
                n_trades += 1
                continue
            pos["held"] += 1
            if pos["held"] >= horizon:                   # exit at close
                fill = bar["close"] * (1 + 0.0002)
                pnl = (pos["entry"] - fill) * pos["qty"] - fill * pos["qty"] * FEE
                cash += pos["margin"] + pnl
                n_trades += 1
                n_wins += pnl > 0
                positions.pop(s)
        # 2) mark-to-market equity
        upnl = 0.0
        for s, pos in positions.items():
            px = data[s].loc[d, "close"] if d in data[s].index else pos["entry"]
            upnl += pos["margin"] + (pos["entry"] - px) * pos["qty"]
        equity = cash + upnl
        # 3) new entries at close
        for s, _to in events.get(d, []):
            if len(positions) >= MAX_POS:
                skipped += 1
                continue
            if s in positions or equity <= 0:
                continue
            margin = equity * MARGIN_FRAC
            if margin > cash:
                skipped += 1
                continue
            entry = data[s].loc[d, "close"] * (1 - 0.0002)   # short: slip against
            notional = margin * lev
            cash -= margin + notional * FEE
            positions[s] = {"entry": entry, "qty": notional / entry,
                            "margin": margin, "held": 0}
        eq_curve.append(equity)
    eq = np.array(eq_curve)
    peak = np.maximum.accumulate(eq)
    dd = (eq / peak - 1).min()
    dret = eq[1:] / eq[:-1] - 1
    years = len(dates) / 365
    return {"final": eq[-1], "ret": eq[-1] / START - 1,
            "cagr": (max(eq[-1], 1e-9) / START) ** (1 / years) - 1,
            "maxdd": dd, "liqs": n_liq, "trades": n_trades,
            "win": n_wins / max(n_trades - n_liq, 1),
            "worst_day": dret.min() if len(dret) else 0.0, "skipped": skipped}


def funding_drag(data, events) -> None:
    """ACTUAL funding paid by a short over 1d/3d holds, sampled events."""
    flat = [(d, s) for d, lst in events.items() for s, _ in lst]
    random.seed(42)
    sample = random.sample(flat, min(FUND_SAMPLE, len(flat)))
    drags = {1: [], 3: []}
    fail = 0
    for d, s in sample:
        t0 = int(pd.Timestamp(d).timestamp() * 1000) + 86_400_000  # event close
        try:
            r = requests.get(f"{BASE}/v5/market/funding/history",
                             params={"category": "linear", "symbol": s,
                                     "startTime": t0,
                                     "endTime": t0 + 3 * 86_400_000,
                                     "limit": 200}, timeout=15)
            rows = r.json().get("result", {}).get("list", [])
            rates = sorted((int(x["fundingRateTimestamp"]),
                            float(x["fundingRate"])) for x in rows)
            # short RECEIVES positive funding, PAYS negative → pnl = +rate
            for h in (1, 3):
                rs = [rt for ts, rt in rates if ts <= t0 + h * 86_400_000]
                if rs:
                    drags[h].append(sum(rs))
        except Exception:
            fail += 1
        time.sleep(0.03)
    print(f"\nFUNDING (actual Bybit history, {len(drags[3])} sampled events; "
          f"+ = short RECEIVES, % of notional):")
    for h in (1, 3):
        a = np.array(drags[h]) * 100
        if len(a):
            print(f"  {h}d hold: mean {a.mean():+.3f}%  med {np.median(a):+.3f}%  "
                  f"p5 {np.percentile(a,5):+.3f}%  p95 {np.percentile(a,95):+.3f}%  "
                  f"paying<0: {(a<0).mean()*100:.0f}%")


def main() -> None:
    data = fetch_all_daily()
    span = (min(df.index.min() for df in data.values()),
            max(df.index.max() for df in data.values()))
    print(f"{len(data)} perps, {span[0].date()} → {span[1].date()}")
    for name, liq in [("ALL PERPS", False), (f"LIQUID (30d med turnover ≥ $10M)", True)]:
        events = build_events(data, liq)
        n_ev = sum(len(v) for v in events.values())
        print(f"\n═══ {name} — {n_ev} pump events ═══")
        print(f"{'lev':>4}{'exit':>6}{'final $':>12}{'total':>9}{'CAGR':>8}"
              f"{'maxDD':>8}{'LIQS':>6}{'trades':>8}{'win%':>6}{'worstday':>9}{'skip':>6}")
        for h in HORIZONS:
            for lev in LEVS:
                r = simulate(data, events, lev, h)
                print(f"{lev:>3}x{h:>5}d{r['final']:>12,.0f}{r['ret']*100:>+8.0f}%"
                      f"{r['cagr']*100:>+7.0f}%{r['maxdd']*100:>+7.1f}%"
                      f"{r['liqs']:>6}{r['trades']:>8}{r['win']*100:>6.0f}"
                      f"{r['worst_day']*100:>+8.2f}%{r['skipped']:>6}")
        if liq:
            funding_drag(data, events)


if __name__ == "__main__":
    main()
