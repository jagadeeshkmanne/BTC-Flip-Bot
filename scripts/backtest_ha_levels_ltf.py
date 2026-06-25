#!/usr/bin/env python3
"""backtest_ha_levels_ltf.py — HA + Key-Levels on lower timeframes (5m/15m) with
horizontal S/R, diagonal trendlines, and a stop-loss / take-profit sweep.

Faithful to the BloFin video:
  - Levels drawn from REAL price: horizontal S/R (swing-pivot clusters) AND
    diagonal trendlines (lines through 2 swing pivots, validated by a 3rd touch).
  - Price revisits the level; Heikin Ashi confirms the reversal (red->green at a
    support, green->red at a resistance).
  - Bracket exit: SL beyond the level, TP = rr * SL distance. Optional "runner"
    management = bank 50% at TP, move stop to break-even, trail rest until HA flips.

HONESTY: HA is signal-only. Levels use real pivots with a confirmation lag (no
lookahead). Every fill/SL/TP is checked against REAL intrabar high/low; a bar that
straddles SL and TP counts as SL (worst case). Fees + slippage on every fill.
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


def fetch_bybit(symbol: str, interval: str, bars: int) -> pd.DataFrame:
    rows: list[list[str]] = []
    end_ms: int | None = None
    while len(rows) < bars:
        params = {"category": "linear", "symbol": symbol, "interval": interval,
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
        time.sleep(0.05)
    rows = sorted({int(x[0]): x for x in rows}.values(), key=lambda x: int(x[0]))
    return pd.DataFrame({
        "timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
        "open": [float(x[1]) for x in rows], "high": [float(x[2]) for x in rows],
        "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows],
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


# ─────────────────────────── level detection ───────────────────────────
@dataclass
class Level:
    is_support: bool
    confirmed_at: int           # bar index when the level became known (no lookahead)
    # horizontal: slope=0, price=const. trendline: y = anchor_price + slope*(i-anchor_idx)
    anchor_idx: int
    anchor_price: float
    slope: float = 0.0
    touches: int = 0
    expires_at: int = 10**9     # trendlines go stale after a while
    last_trade_bar: int = -10**9

    def price_at(self, i: int) -> float:
        return self.anchor_price + self.slope * (i - self.anchor_idx)


def detect_pivots(df: pd.DataFrame, left: int, right: int):
    highs, lows = [], []
    h, l = df["high"].values, df["low"].values
    for i in range(left, len(df) - right):
        wh = h[i - left:i + right + 1]
        wl = l[i - left:i + right + 1]
        if h[i] == wh.max() and (wh == h[i]).sum() == 1:
            highs.append((i, float(h[i])))
        if l[i] == wl.min() and (wl == l[i]).sum() == 1:
            lows.append((i, float(l[i])))
    return highs, lows


def build_horizontal(df, highs, lows, right, cluster_frac, min_touches) -> list[Level]:
    levels = []
    for pivots, is_support in ((lows, True), (highs, False)):
        used = [False] * len(pivots)
        for a in range(len(pivots)):
            if used[a]:
                continue
            ia, pa = pivots[a]
            members = [(ia, pa)]
            used[a] = True
            for b in range(a + 1, len(pivots)):
                if used[b]:
                    continue
                ib, pb = pivots[b]
                if abs(pb - pa) / pa <= cluster_frac:
                    members.append((ib, pb)); used[b] = True
            if len(members) >= min_touches:
                center = sum(p for _, p in members) / len(members)
                confirmed = sorted(ix for ix, _ in members)[min_touches - 1] + right
                levels.append(Level(is_support, confirmed, members[0][0], center, 0.0, len(members)))
    return levels


def build_trendlines(df, highs, lows, right, min_slope_gap, max_slope_gap,
                     touch_frac, max_extend, cap=250) -> list[Level]:
    """Lines through two same-type pivots, validated by >=1 further touch.
    Bounded: only pairs within [min,max] gap; touch-scan only over the next window."""
    levels = []
    for pivots, is_support in ((lows, True), (highs, False)):
        n = len(pivots)
        for a in range(n):
            ia, pa = pivots[a]
            for b in range(a + 1, n):
                ib, pb = pivots[b]
                gap = ib - ia
                if gap < min_slope_gap:
                    continue
                if gap > max_slope_gap:
                    break                       # pivots sorted by index -> no farther b helps
                slope = (pb - pa) / gap
                touches, last_touch_idx = 2, ib
                for c in range(b + 1, n):
                    ic, pc = pivots[c]
                    if ic - ia > max_slope_gap + max_extend:
                        break
                    proj = pa + slope * (ic - ia)
                    if proj > 0 and abs(pc - proj) / proj <= touch_frac:
                        touches += 1; last_touch_idx = ic
                if touches >= 3:
                    confirmed = last_touch_idx + right
                    levels.append(Level(is_support, confirmed, ia, pa, slope, touches,
                                        expires_at=confirmed + max_extend))
    levels.sort(key=lambda lv: lv.touches, reverse=True)   # keep strongest
    return levels[:cap]


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


def backtest(df, levels, *, allow_long, allow_short, zone_frac, sl_buffer_atr, rr,
             cooldown, runner=False, partial=0.5, name="") -> Result:
    cash = 1.0
    res = Result(name=name)
    pos = None
    eq = []

    def close_leg(pos, qty_frac, exit_px):
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

    start = 50
    for i in range(start, len(df) - 1):
        row = df.iloc[i]
        hi, lo, price = float(row["high"]), float(row["low"]), float(row["close"])

        if pos is not None:
            long = pos["side"] == "long"
            stop_hit = (lo <= pos["sl"]) if long else (hi >= pos["sl"])
            tp_hit = (hi >= pos["tp"]) if long else (lo <= pos["tp"])
            if stop_hit:
                book(close_leg(pos, pos["frac"], pos["sl"])); res.sl_hits += 1; pos = None
            elif not runner:
                if tp_hit:
                    book(close_leg(pos, pos["frac"], pos["tp"])); res.tp_hits += 1; pos = None
            else:
                if not pos["banked"] and tp_hit:
                    book(close_leg(pos, pos["frac"] * partial, pos["tp"])); res.tp_hits += 1
                    pos["frac"] *= (1 - partial); pos["banked"] = True; pos["sl"] = pos["entry"]
                if pos is not None and pos["banked"]:
                    ha_weak = bool(row["ha_red"]) if long else bool(row["ha_green"])
                    if ha_weak:
                        book(close_leg(pos, pos["frac"], price)); pos = None

        if pos is None:
            atr_i = float(row["atr"]); prev = df.iloc[i - 1]
            for lv in levels:
                if lv.confirmed_at > i or i > lv.expires_at:
                    continue
                if i - lv.last_trade_bar < cooldown:
                    continue
                lp = lv.price_at(i)
                if lp <= 0:
                    continue
                band = lp * zone_frac
                touched = lo <= lp + band and hi >= lp - band
                if not touched:
                    continue
                ha_flip_up = bool(prev["ha_red"] and row["ha_green"])
                ha_flip_dn = bool(prev["ha_green"] and row["ha_red"])
                go_long = allow_long and lv.is_support and ha_flip_up
                go_short = allow_short and (not lv.is_support) and ha_flip_dn
                if not (go_long or go_short):
                    continue
                entry = float(df.iloc[i + 1]["open"])
                if go_long:
                    sl = lp - band - sl_buffer_atr * atr_i
                    if sl >= entry:
                        continue
                    tp = entry + rr * (entry - sl)
                    fill = entry * (1 + SLIP_PCT); fee = cash * FEE_PCT
                    qty = (cash - fee) / fill
                    pos = {"side": "long", "entry": fill, "sl": sl, "tp": tp, "qty": qty,
                           "cost": cash, "frac": 1.0, "banked": False}
                    cash = 0.0
                else:
                    sl = lp + band + sl_buffer_atr * atr_i
                    if sl <= entry:
                        continue
                    tp = entry - rr * (sl - entry)
                    fill = entry * (1 - SLIP_PCT); qty = cash / fill
                    pos = {"side": "short", "entry": fill, "sl": sl, "tp": tp, "qty": qty,
                           "cost": cash, "frac": 1.0, "banked": False}
                lv.last_trade_bar = i
                break

        if pos is None:
            mark = cash
        elif pos["side"] == "long":
            mark = cash + pos["qty"] * pos["frac"] * price
        else:
            mark = cash + pos["qty"] * pos["frac"] * (pos["entry"] - price)
        eq.append(mark)

    e = pd.Series(eq)
    dd = e / e.cummax() - 1.0
    res.net_pct = (e.iloc[-1] - 1.0) * 100.0 if len(e) else 0.0
    res.pf = res.gross_profit / res.gross_loss if res.gross_loss > 0 else float("inf")
    res.max_dd_pct = float(dd.min() * 100.0) if len(e) else 0.0
    return res


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--bars", type=int, default=12000)
    p.add_argument("--interval", default="15", help="Bybit interval: 5, 15, 60 ...")
    p.add_argument("--symbol", default=PAIR)
    p.add_argument("--left", type=int, default=4)
    p.add_argument("--right", type=int, default=4)
    p.add_argument("--cluster-frac", type=float, default=0.006)
    p.add_argument("--min-touches", type=int, default=2)
    p.add_argument("--zone-frac", type=float, default=0.0015)
    p.add_argument("--cooldown", type=int, default=8)
    p.add_argument("--no-trendlines", action="store_true")
    args = p.parse_args()

    full = add_ha(fetch_bybit(args.symbol, args.interval, args.bars))
    days = (full.timestamp.iloc[-1] - full.timestamp.iloc[0]).total_seconds() / 86400
    print(f"symbol={args.symbol} interval={args.interval}m bars={len(full)} "
          f"(~{days:.0f} days) from={full.timestamp.iloc[0]} to={full.timestamp.iloc[-1]}")

    def build_levels(df):
        highs, lows = detect_pivots(df, args.left, args.right)
        lv = build_horizontal(df, highs, lows, args.right, args.cluster_frac, args.min_touches)
        nh = len(lv)
        nt = 0
        if not args.no_trendlines:
            tl = build_trendlines(df, highs, lows, args.right, min_slope_gap=args.right * 2,
                                  max_slope_gap=200, touch_frac=0.004, max_extend=400)
            lv += tl; nt = len(tl)
        return lv, nh, nt

    def sweep(df, levels):
        rows = []
        for sl in (0.5, 1.0):
            for rr in (1.0, 1.5, 2.0, 3.0):
                for mode, runner in (("flat", False), ("runr", True)):
                    for side, al, ash in (("L", True, False), ("S", False, True), ("LS", True, True)):
                        r = backtest(df, levels, allow_long=al, allow_short=ash,
                                     zone_frac=args.zone_frac, sl_buffer_atr=sl, rr=rr,
                                     cooldown=args.cooldown, runner=runner)
                        wr = r.wins / r.trades * 100 if r.trades else 0.0
                        rows.append({"net": r.net_pct, "pf": r.pf, "wr": wr, "tr": r.trades,
                                     "dd": r.max_dd_pct, "sl": sl, "rr": rr, "runner": runner,
                                     "al": al, "ash": ash,
                                     "tag": f"{mode}_{side}_sl{sl}_rr{rr}"})
        return rows

    # ── In-sample (first 60%) optimize, out-of-sample (last 40%) validate ──
    split = int(len(full) * 0.6)
    is_df = full.iloc[:split].reset_index(drop=True)
    oos_df = full.iloc[split:].reset_index(drop=True)
    is_levels, nh, nt = build_levels(is_df)
    oos_levels, _, _ = build_levels(oos_df)
    print(f"IS bars={len(is_df)} (to {is_df.timestamp.iloc[-1]}) | "
          f"OOS bars={len(oos_df)} (from {oos_df.timestamp.iloc[0]})")
    print(f"IS levels: horizontal={nh} trendlines={nt}")

    rows = sweep(is_df, is_levels)
    rows.sort(key=lambda r: r["net"], reverse=True)
    print("\nIN-SAMPLE TOP 10 by net_pct:")
    print("net_pct,pf,win_rate,trades,max_dd,sl,rr,tag")
    for r in rows[:10]:
        print(f"{r['net']:.2f},{r['pf']:.3f},{r['wr']:.1f},{r['tr']},{r['dd']:.2f},"
              f"{r['sl']},{r['rr']},{r['tag']}")

    # pick best IS config with a real sample, evaluate UNTOUCHED on OOS
    cand = [r for r in rows if r["tr"] >= 15]
    if not cand:
        print("\nno IS config with >=15 trades; nothing robust to validate"); return
    best = max(cand, key=lambda r: r["pf"])
    oos = backtest(oos_df, oos_levels, allow_long=best["al"], allow_short=best["ash"],
                   zone_frac=args.zone_frac, sl_buffer_atr=best["sl"], rr=best["rr"],
                   cooldown=args.cooldown, runner=best["runner"])
    owr = oos.wins / oos.trades * 100 if oos.trades else 0.0
    print(f"\nBEST IS CONFIG (by PF, >=15 trades): {best['tag']}")
    print(f"  IN-SAMPLE : net={best['net']:.2f}% pf={best['pf']:.3f} "
          f"win={best['wr']:.1f}% trades={best['tr']} dd={best['dd']:.2f}%")
    print(f"  OUT-SAMPLE: net={oos.net_pct:.2f}% pf={oos.pf:.3f} "
          f"win={owr:.1f}% trades={oos.trades} dd={oos.max_dd_pct:.2f}%")
    verdict = "HOLDS UP" if (oos.pf > 1.05 and oos.net_pct > 0 and oos.trades >= 10) else "FALLS APART (overfit)"
    print(f"  VERDICT: {verdict}")


if __name__ == "__main__":
    main()
