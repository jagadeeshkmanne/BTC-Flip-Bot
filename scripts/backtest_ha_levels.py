#!/usr/bin/env python3
"""backtest_ha_levels.py — Honest backtest of the "Heikin Ashi + key levels" video strategy.

The video's actual machine (de-hyped):
  1. Auto-detect a key level (support/resistance) where price has rejected before.
  2. Wait for price to revisit that level.
  3. Heikin Ashi CONFIRMS a reversal at the level (red->green at support, green->red at resistance).
  4. Enter; stop-loss just beyond the level; take-profit at 2x the stop distance (2:1 RR).

HONESTY RULES (the whole point — the video tells you NOT to backtest HA, which is convenient):
  - Heikin Ashi is used ONLY for the confirmation signal.
  - Levels are detected on REAL price (swing pivots) with a confirmation lag => no lookahead.
  - Every fill, stop, and target is evaluated against REAL OHLC (intrabar high/low),
    never the averaged HA candle. A bar that straddles SL and TP is counted as SL (worst case).
  - Fees + slippage charged on every fill.
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field

import pandas as pd
import requests

PAIR = "BTCUSDT"
BYBIT_BASE = "https://api.bybit.com"
FEE_PCT = 0.00055
SLIP_PCT = 0.0005


def fetch_bybit_4h(symbol: str, bars: int) -> pd.DataFrame:
    rows: list[list[str]] = []
    end_ms: int | None = None
    while len(rows) < bars:
        params = {"category": "linear", "symbol": symbol, "interval": "240",
                  "limit": min(1000, bars - len(rows))}
        if end_ms is not None:
            params["end"] = end_ms
        r = requests.get(f"{BYBIT_BASE}/v5/market/kline", params=params, timeout=20)
        r.raise_for_status()
        body = r.json()
        if body.get("retCode") != 0:
            raise RuntimeError(f"Bybit retCode={body.get('retCode')} {body.get('retMsg')}")
        batch = body.get("result", {}).get("list", [])
        if not batch:
            break
        rows.extend(batch)
        end_ms = min(int(x[0]) for x in batch) - 1
        time.sleep(0.08)
    rows = sorted({int(x[0]): x for x in rows}.values(), key=lambda x: int(x[0]))
    return pd.DataFrame({
        "timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
        "open": [float(x[1]) for x in rows], "high": [float(x[2]) for x in rows],
        "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows],
        "volume": [float(x[5]) for x in rows],
    }).reset_index(drop=True)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()


def add_ha(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    ha_close = (out["open"] + out["high"] + out["low"] + out["close"]) / 4.0
    ha_open = [float((out.loc[0, "open"] + out.loc[0, "close"]) / 2.0)]
    for i in range(1, len(out)):
        ha_open.append((ha_open[-1] + float(ha_close.iloc[i - 1])) / 2.0)
    out["ha_open"] = ha_open
    out["ha_close"] = ha_close
    out["ha_green"] = out["ha_close"] > out["ha_open"]
    out["ha_red"] = out["ha_close"] < out["ha_open"]
    out["atr"] = atr(out, 14)
    return out


@dataclass
class Level:
    price: float          # zone center
    is_support: bool      # support if formed from a swing low
    touches: int
    confirmed_at: int     # bar index when this level became known (pivot + right window)
    last_trade_bar: int = -10_000   # cooldown: don't re-trade the same level immediately


def detect_pivots(df: pd.DataFrame, left: int, right: int):
    """Swing highs/lows confirmed `right` bars later (no lookahead at decision time)."""
    highs, lows = [], []
    h, l = df["high"].values, df["low"].values
    for i in range(left, len(df) - right):
        win_h = h[i - left:i + right + 1]
        win_l = l[i - left:i + right + 1]
        if h[i] == win_h.max() and (win_h == h[i]).sum() == 1:
            highs.append((i + right, float(h[i])))      # (confirmed_at, price)
        if l[i] == win_l.min() and (win_l == l[i]).sum() == 1:
            lows.append((i + right, float(l[i])))
    return highs, lows


def build_levels(df: pd.DataFrame, left: int, right: int, cluster_frac: float, min_touches: int) -> list[Level]:
    """Cluster nearby pivots into zones. A zone with >= min_touches is a valid key level."""
    highs, lows = detect_pivots(df, left, right)
    levels: list[Level] = []
    for pivots, is_support in ((lows, True), (highs, False)):
        used = [False] * len(pivots)
        for a in range(len(pivots)):
            if used[a]:
                continue
            ca, pa = pivots[a]
            members = [(ca, pa)]
            used[a] = True
            for b in range(a + 1, len(pivots)):
                if used[b]:
                    continue
                cb, pb = pivots[b]
                if abs(pb - pa) / pa <= cluster_frac:
                    members.append((cb, pb))
                    used[b] = True
            if len(members) >= min_touches:
                center = sum(p for _, p in members) / len(members)
                confirmed = sorted(c for c, _ in members)[min_touches - 1]  # known after Nth touch
                levels.append(Level(center, is_support, len(members), confirmed))
    return sorted(levels, key=lambda x: x.confirmed_at)


@dataclass
class Result:
    name: str
    trades: int = 0
    wins: int = 0
    net_pct: float = 0.0
    pf: float = 0.0
    max_dd_pct: float = 0.0
    tp_hits: int = 0
    sl_hits: int = 0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    equity: list = field(default_factory=list)


def backtest(df: pd.DataFrame, *, allow_long: bool, allow_short: bool,
             zone_frac: float, sl_buffer_atr: float, rr: float, cooldown: int,
             left: int, right: int, cluster_frac: float, min_touches: int,
             name: str, runner: bool = False, partial: float = 0.5) -> Result:
    """runner=True replicates the video's management: bank `partial` of the position
    at the 2R target, move the stop to break-even, then trail the remainder and close
    it on the first opposite-color HA candle (HA weakening). runner=False = flat 2:1."""
    levels = build_levels(df, left, right, cluster_frac, min_touches)
    cash = 1.0
    res = Result(name=name)
    pos = None  # dict: side, entry, sl, tp, cost, ...
    eq = []

    def close_leg(pos, qty_frac, exit_px):
        """Realize qty_frac of the position at exit_px (real price). Returns pnl on that leg."""
        nonlocal cash
        slip = (1 - SLIP_PCT) if pos["side"] == "long" else (1 + SLIP_PCT)
        fill = exit_px * slip
        cost_leg = pos["cost"] * qty_frac
        if pos["side"] == "long":
            proceeds = pos["qty"] * qty_frac * fill
        else:
            proceeds = cost_leg + pos["qty"] * qty_frac * (pos["entry"] - fill)
        proceeds -= proceeds * FEE_PCT
        pnl = proceeds - cost_leg
        cash += proceeds if pos["side"] == "long" else pnl
        return pnl

    def book(pnl):
        res.trades += 1
        res.wins += int(pnl > 0)
        if pnl > 0:
            res.gross_profit += pnl
        else:
            res.gross_loss += -pnl

    for i in range(max(left + right, 50), len(df) - 1):
        row = df.iloc[i]
        price = float(row["close"])
        active = [lv for lv in levels if lv.confirmed_at <= i]

        # ---- manage open position against REAL intrabar high/low of bar i ----
        if pos is not None:
            hi, lo = float(row["high"]), float(row["low"])
            long = pos["side"] == "long"
            stop_hit = (lo <= pos["sl"]) if long else (hi >= pos["sl"])
            tp_hit = (hi >= pos["tp"]) if long else (lo <= pos["tp"])

            if stop_hit:                              # SL first (worst case)
                book(close_leg(pos, pos["frac"], pos["sl"]))
                res.sl_hits += 1
                pos = None
            elif not runner:
                if tp_hit:
                    book(close_leg(pos, pos["frac"], pos["tp"]))
                    res.tp_hits += 1
                    pos = None
            else:
                # runner mode: first take partial at TP & move stop to BE, then HA-trail
                if not pos["banked"] and tp_hit:
                    book(close_leg(pos, pos["frac"] * partial, pos["tp"]))
                    res.tp_hits += 1
                    pos["frac"] *= (1 - partial)
                    pos["banked"] = True
                    pos["sl"] = pos["entry"]           # break-even stop on the runner
                if pos is not None and pos["banked"]:
                    ha_weak = bool(row["ha_red"]) if long else bool(row["ha_green"])
                    if ha_weak:                        # trail exit on real close
                        book(close_leg(pos, pos["frac"], price))
                        pos = None

        # ---- look for a new entry (one position at a time) ----
        if pos is None:
            atr_i = float(row["atr"])
            prev = df.iloc[i - 1]
            for lv in active:
                if i - lv.last_trade_bar < cooldown:
                    continue
                band = lv.price * zone_frac
                in_zone = (lv.price - band) <= float(row["low"]) and float(row["high"]) >= (lv.price - band) \
                    if lv.is_support else \
                    (lv.price + band) >= float(row["high"]) and float(row["low"]) <= (lv.price + band)
                # tighter: price actually touched the zone this bar
                touched = float(row["low"]) <= lv.price + band and float(row["high"]) >= lv.price - band
                if not touched:
                    continue
                # HA confirmation = momentum flip at the level
                ha_flip_up = bool(prev["ha_red"] and row["ha_green"])
                ha_flip_dn = bool(prev["ha_green"] and row["ha_red"])
                go_long = allow_long and lv.is_support and ha_flip_up
                go_short = allow_short and (not lv.is_support) and ha_flip_dn
                if not (go_long or go_short):
                    continue
                entry = float(df.iloc[i + 1]["open"])   # fill next bar open, REAL price
                if go_long:
                    sl = lv.price - band - sl_buffer_atr * atr_i
                    if sl >= entry:
                        continue
                    tp = entry + rr * (entry - sl)
                    fill = entry * (1 + SLIP_PCT)
                    fee = cash * FEE_PCT
                    qty = (cash - fee) / fill
                    pos = {"side": "long", "entry": fill, "sl": sl, "tp": tp, "qty": qty,
                           "cost": cash, "frac": 1.0, "banked": False}
                    cash = 0.0
                else:
                    sl = lv.price + band + sl_buffer_atr * atr_i
                    if sl <= entry:
                        continue
                    tp = entry - rr * (sl - entry)
                    fill = entry * (1 - SLIP_PCT)
                    qty = cash / fill
                    pos = {"side": "short", "entry": fill, "sl": sl, "tp": tp, "qty": qty,
                           "cost": cash, "frac": 1.0, "banked": False}
                lv.last_trade_bar = i
                break

        if pos is None:
            mark = cash
        elif pos["side"] == "long":
            mark = cash + pos["qty"] * pos["frac"] * float(row["close"])
        else:  # short: cash already holds the cost base + any realized legs
            mark = cash + pos["qty"] * pos["frac"] * (pos["entry"] - float(row["close"]))
        eq.append(mark)

    e = pd.Series(eq)
    dd = e / e.cummax() - 1.0
    res.net_pct = (e.iloc[-1] - 1.0) * 100.0 if len(e) else 0.0
    res.pf = res.gross_profit / res.gross_loss if res.gross_loss > 0 else float("inf")
    res.max_dd_pct = float(dd.min() * 100.0) if len(e) else 0.0
    res.equity = eq
    return res


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--bars", type=int, default=6000)
    p.add_argument("--symbol", default=PAIR)
    p.add_argument("--left", type=int, default=3)
    p.add_argument("--right", type=int, default=3)
    p.add_argument("--cluster-frac", type=float, default=0.01)
    p.add_argument("--min-touches", type=int, default=2)
    p.add_argument("--zone-frac", type=float, default=0.004)
    p.add_argument("--sl-buffer-atr", type=float, default=0.5)
    p.add_argument("--rr", type=float, default=2.0)
    p.add_argument("--cooldown", type=int, default=6)
    args = p.parse_args()

    df = add_ha(fetch_bybit_4h(args.symbol, args.bars))
    common = dict(zone_frac=args.zone_frac, sl_buffer_atr=args.sl_buffer_atr, rr=args.rr,
                  cooldown=args.cooldown, left=args.left, right=args.right,
                  cluster_frac=args.cluster_frac, min_touches=args.min_touches)
    n_levels = len(build_levels(df, args.left, args.right, args.cluster_frac, args.min_touches))

    print(f"symbol={args.symbol} bars={len(df)} from={df.timestamp.iloc[0]} to={df.timestamp.iloc[-1]}")
    print(f"levels detected={n_levels} | left/right={args.left}/{args.right} "
          f"cluster={args.cluster_frac} min_touches={args.min_touches} "
          f"zone={args.zone_frac} sl_buf={args.sl_buffer_atr}ATR rr={args.rr}")
    print("\nname,trades,win_rate,net_pct,pf,max_dd_pct,tp_hits,sl_hits")
    variants = [
        ("flat2to1_long", dict(allow_long=True, allow_short=False)),
        ("flat2to1_short", dict(allow_long=False, allow_short=True)),
        ("flat2to1_both", dict(allow_long=True, allow_short=True)),
        ("runner_long", dict(allow_long=True, allow_short=False, runner=True)),
        ("runner_short", dict(allow_long=False, allow_short=True, runner=True)),
        ("runner_both", dict(allow_long=True, allow_short=True, runner=True)),
    ]
    for name, flags in variants:
        r = backtest(df, name=name, **flags, **common)
        wr = r.wins / r.trades * 100.0 if r.trades else 0.0
        print(f"{r.name},{r.trades},{wr:.1f},{r.net_pct:.2f},{r.pf:.3f},{r.max_dd_pct:.2f},{r.tp_hits},{r.sl_hits}")


if __name__ == "__main__":
    main()
