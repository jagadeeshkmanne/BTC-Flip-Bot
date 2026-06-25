#!/usr/bin/env python3
"""backtest_demand_zone.py — BoS + demand/supply-zone retest, LONG and SHORT.

Faithful supply/demand 'sniper entry' core, both directions (perp, shorts allowed):
  LONG  : bullish BoS (swing high > prior swing high) -> buy the higher-low demand zone on
          retest; stop below zone; target the broken swing high.
  SHORT : bearish BoS (swing low < prior swing low) -> sell the lower-high supply zone on
          retest; stop above zone; target the broken swing low.
  zone_frac = the multi-timeframe 'refinement' lever (1.0 full zone; lower = deeper/better RR,
  more misses). min_rr filters trades by geometry.

HONESTY: pivots confirmed with a lag (no lookahead); fills/stops/targets on REAL intrabar
high/low (stop-first on straddle); fee+slippage round-trip; all-in sizing; IS/OOS 60/40.
"""
from __future__ import annotations
import argparse, time
import pandas as pd
import requests

BYBIT_BASE = "https://api.bybit.com"; FEE_PCT = 0.00055; SLIP_PCT = 0.0005


def fetch(symbol, interval, bars):
    rows, end_ms = [], None
    while len(rows) < bars:
        p = {"category": "linear", "symbol": symbol, "interval": interval, "limit": min(1000, bars-len(rows))}
        if end_ms is not None:
            p["end"] = end_ms
        b = requests.get(f"{BYBIT_BASE}/v5/market/kline", params=p, timeout=20).json()
        batch = b.get("result", {}).get("list", [])
        if not batch:
            break
        rows.extend(batch); end_ms = min(int(x[0]) for x in batch)-1; time.sleep(0.05)
    rows = sorted({int(x[0]): x for x in rows}.values(), key=lambda x: int(x[0]))
    return pd.DataFrame({"timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
                         "open": [float(x[1]) for x in rows], "high": [float(x[2]) for x in rows],
                         "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows]}).reset_index(drop=True)


def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"]-df["low"], (df["high"]-pc).abs(), (df["low"]-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def metrics(eq):
    e = pd.Series(eq)
    if len(e) < 2:
        return 0.0, 0.0
    dd = e/e.cummax()-1
    return (e.iloc[-1]-1)*100, float(dd.min()*100)


def buy_hold(df):
    p0 = float(df.iloc[0]["close"]); return metrics([float(df.iloc[i]["close"])/p0 for i in range(len(df))])


def pivots(df, left, right):
    h, l = df["high"].values, df["low"].values
    hi, lo = [], []
    for i in range(left, len(df)-right):
        wh, wl = h[i-left:i+right+1], l[i-left:i+right+1]
        if h[i] == wh.max() and (wh == h[i]).sum() == 1:
            hi.append((i+right, i, float(h[i])))
        if l[i] == wl.min() and (wl == l[i]).sum() == 1:
            lo.append((i+right, i, float(l[i])))
    return hi, lo


def backtest(df, *, left, right, zone_frac, stop_buf_atr, min_rr, allow_long, allow_short):
    a = atr(df, 14).values
    hi, lo = pivots(df, left, right)
    ev_at = {}
    for c, pi, pr in hi:
        ev_at.setdefault(c, []).append(("H", pi, pr))
    for c, pi, pr in lo:
        ev_at.setdefault(c, []).append(("L", pi, pr))

    bal = 1.0
    pos = None          # {side, entry, stop, tp, rr}
    setup_l = setup_s = None
    eq = []
    trades = wins = 0
    long_pnl = short_pnl = 0.0
    prev_sh = prev_sl = None
    EXP = right*20

    for i in range(len(df)-1):
        o = float(df.iloc[i]["open"]); c = float(df.iloc[i]["close"])
        hgh = float(df.iloc[i]["high"]); low = float(df.iloc[i]["low"])
        for t, pi, pr in ev_at.get(i, []):
            if t == "H":
                prev_sh = (pi, pr)
            else:
                prev_sl = (pi, pr)

        # ---- manage open position (real intrabar, stop-first) ----
        if pos is not None:
            if pos["side"] == "long":
                ex = pos["stop"] if low <= pos["stop"] else (pos["tp"] if hgh >= pos["tp"] else None)
                if ex is not None:
                    fill = ex*(1-SLIP_PCT); factor = fill/pos["entry"]
                    new = bal*factor*(1-2*FEE_PCT); pnl = new-bal
                    bal = new; trades += 1; wins += int(pnl > 0); long_pnl += pnl; pos = None
            else:
                ex = pos["stop"] if hgh >= pos["stop"] else (pos["tp"] if low <= pos["tp"] else None)
                if ex is not None:
                    fill = ex*(1+SLIP_PCT); factor = (2*pos["entry"]-fill)/pos["entry"]
                    new = bal*factor*(1-2*FEE_PCT); pnl = new-bal
                    bal = new; trades += 1; wins += int(pnl > 0); short_pnl += pnl; pos = None

        # ---- detect BoS + build pending zone ----
        if pos is None and prev_sh and prev_sl:
            # bullish BoS: higher-low after a swing high, break above that swing high
            if allow_long and setup_l is None and prev_sl[0] > prev_sh[0] and c > prev_sh[1]:
                zi = prev_sl[0]; z_lo = float(df.iloc[zi]["low"])
                z_hi = max(float(df.iloc[zi]["open"]), float(df.iloc[zi]["close"]))
                ztop = z_lo + zone_frac*(z_hi-z_lo); stop = z_lo - stop_buf_atr*float(a[i])
                target = max(prev_sh[1], hgh)
                if ztop > stop:
                    rr = (target-ztop)/(ztop-stop)
                    if rr >= min_rr:
                        setup_l = {"lvl": ztop, "stop": stop, "tp": target, "rr": rr, "exp": i+EXP}
            # bearish BoS: lower-high after a swing low, break below that swing low
            if allow_short and setup_s is None and prev_sh[0] > prev_sl[0] and c < prev_sl[1]:
                zi = prev_sh[0]; z_hi = float(df.iloc[zi]["high"])
                z_lo = min(float(df.iloc[zi]["open"]), float(df.iloc[zi]["close"]))
                zbot = z_hi - zone_frac*(z_hi-z_lo); stop = z_hi + stop_buf_atr*float(a[i])
                target = min(prev_sl[1], low)
                if zbot < stop:
                    rr = (zbot-target)/(stop-zbot)
                    if rr >= min_rr:
                        setup_s = {"lvl": zbot, "stop": stop, "tp": target, "rr": rr, "exp": i+EXP}

        # ---- entries on retest ----
        if pos is None and setup_l is not None:
            if i > setup_l["exp"] or c > setup_l["tp"]:
                setup_l = None
            elif low <= setup_l["lvl"]:
                entry = setup_l["lvl"]*(1+SLIP_PCT)
                if entry > setup_l["stop"]:
                    pos = {"side": "long", "entry": entry, "stop": setup_l["stop"], "tp": setup_l["tp"], "rr": setup_l["rr"]}
                setup_l = None
        if pos is None and setup_s is not None:
            if i > setup_s["exp"] or c < setup_s["tp"]:
                setup_s = None
            elif hgh >= setup_s["lvl"]:
                entry = setup_s["lvl"]*(1-SLIP_PCT)
                if entry < setup_s["stop"]:
                    pos = {"side": "short", "entry": entry, "stop": setup_s["stop"], "tp": setup_s["tp"], "rr": setup_s["rr"]}
                setup_s = None

        # ---- mark equity ----
        nc = float(df.iloc[i+1]["close"])
        if pos is None:
            eq.append(bal)
        elif pos["side"] == "long":
            eq.append(bal*nc/pos["entry"])
        else:
            eq.append(bal*(2*pos["entry"]-nc)/pos["entry"])

    net, dd = metrics(eq)
    wr = wins/trades*100 if trades else 0.0
    return {"net": net, "dd": dd, "trades": trades, "wr": wr,
            "long_pnl": long_pnl*100, "short_pnl": short_pnl*100}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--bars", type=int, default=6000)
    ap.add_argument("--interval", default="240"); a = ap.parse_args()
    df = fetch("BTCUSDT", a.interval, a.bars)
    days = (df.timestamp.iloc[-1]-df.timestamp.iloc[0]).days
    split = int(len(df)*0.6); oos = df.iloc[split:].reset_index(drop=True)
    print(f"BTCUSDT {a.interval} bars={len(df)} (~{days}d) {df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()}")
    bh = buy_hold(df); bho = buy_hold(oos)
    print(f"buy&hold: FULL {bh[0]:.1f}% | OOS {bho[0]:.1f}%\n")
    print("cfg = zone_frac0.6, min_rr1.5, stop_buf0.5ATR")
    print(f"{'side':12s} {'FULL net%':>9} {'dd%':>6} {'tr':>4} {'wr%':>5} {'Lpnl%':>7} {'Spnl%':>7} | {'OOS net%':>8} {'dd%':>6} {'tr':>4} {'wr%':>5}")
    for name, al, ash in (("long_only", True, False), ("short_only", False, True), ("long+short", True, True)):
        rf = backtest(df, left=3, right=3, zone_frac=0.6, stop_buf_atr=0.5, min_rr=1.5, allow_long=al, allow_short=ash)
        ro = backtest(oos, left=3, right=3, zone_frac=0.6, stop_buf_atr=0.5, min_rr=1.5, allow_long=al, allow_short=ash)
        print(f"{name:12s} {rf['net']:9.1f} {rf['dd']:6.1f} {rf['trades']:4d} {rf['wr']:5.0f} "
              f"{rf['long_pnl']:7.1f} {rf['short_pnl']:7.1f} | {ro['net']:8.1f} {ro['dd']:6.1f} {ro['trades']:4d} {ro['wr']:5.0f}")


if __name__ == "__main__":
    main()
