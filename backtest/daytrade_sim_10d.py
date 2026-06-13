#!/usr/bin/env python3
"""daytrade_sim_10d.py — 10-day honest sanity sim of the day-trade screener
rules BEFORE paper-deploying them (bot/bot_daytrade.py).

Rules (identical to the bot):
  Universe: top 100 Bybit perps by 24h turnover, turnover >= $10M.
  Signal on closed 15m bars: LONG  EMA20>EMA50 & px>EMA20 & ADX14>20
                             SHORT EMA20<EMA50 & px<EMA20 & ADX14>20
  Entry only when signal is FRESH (on for <= 2 closed bars), |24h move| <= 15%
  (no pump chasing — FINDINGS #10), daily ATR14 3-25%.
  Exit: TP +2% / hard SL -1.5% (price move; intra-bar, SL-first pessimistic),
        15m close back through EMA20 (invalidation), max hold 96 bars (24h).
  Entry fill: next bar OPEN +/- slip. Costs 0.055%+0.02% per side. 2x lev,
  margin 10% of equity, max 5 concurrent. Funding NOT simmed (10 days; noted).

Limits (sanity check, not proof): 10.4 days of 15m data (Bybit 1000-bar cap),
today's top-100 universe applied backwards (mild survivorship), one regime.
"""
from __future__ import annotations
import sys, time
import numpy as np
import pandas as pd
import requests

BASE = "https://api.bybit.com"
FEE, SLIP = 0.00055, 0.0002
TP, SL = 0.02, 0.015
MAX_HOLD = 96
FRESH, ADX_MIN = 2, 20.0
MAX_POS, MARGIN_FRAC, LEV = 5, 0.10, 2.0
START = 5000.0
MIN_TURN = 10e6
MAX_24H = 0.15
ATR_LO, ATR_HI = 3.0, 25.0


def top100() -> list[str]:
    r = requests.get(f"{BASE}/v5/market/tickers", params={"category": "linear"},
                     timeout=15).json()["result"]["list"]
    rows = [t for t in r if t["symbol"].endswith("USDT") and "-" not in t["symbol"]
            and float(t.get("turnover24h") or 0) >= MIN_TURN]
    rows.sort(key=lambda t: -float(t["turnover24h"]))
    return [t["symbol"] for t in rows[:100]]


def klines(sym: str, interval: str, limit: int) -> pd.DataFrame | None:
    r = requests.get(f"{BASE}/v5/market/kline",
                     params={"category": "linear", "symbol": sym,
                             "interval": interval, "limit": limit}, timeout=15)
    rows = r.json().get("result", {}).get("list", [])
    if not rows:
        return None
    rows = list(reversed(rows))
    return pd.DataFrame([{"ts": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                          "low": float(k[3]), "close": float(k[4])} for k in rows])


def indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    df["e20"] = c.ewm(span=20, adjust=False, min_periods=20).mean()
    df["e50"] = c.ewm(span=50, adjust=False, min_periods=50).mean()
    up, dn = df["high"].diff(), -df["low"].diff()
    plus = pd.Series(((up > dn) & (up > 0)) * up).fillna(0.0)
    minus = pd.Series(((dn > up) & (dn > 0)) * dn).fillna(0.0)
    pc = c.shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    pdi = 100 * plus.ewm(alpha=1 / 14, adjust=False).mean() / atr
    mdi = 100 * minus.ewm(alpha=1 / 14, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    df["adx"] = dx.ewm(alpha=1 / 14, adjust=False).mean()
    df["long"] = (df["e20"] > df["e50"]) & (c > df["e20"]) & (df["adx"] > ADX_MIN)
    df["short"] = (df["e20"] < df["e50"]) & (c < df["e20"]) & (df["adx"] > ADX_MIN)
    return df


def main() -> None:
    htf_gate = "--htf" in sys.argv
    syms = top100()
    print(f"{len(syms)} symbols (top-100, turnover >= $10M)"
          f"{' + 4h-trend alignment gate' if htf_gate else ''}", file=sys.stderr)
    data, atrp, htf = {}, {}, {}
    for i, s in enumerate(syms):
        df = klines(s, "15", 1000)
        dd = klines(s, "D", 20)
        if df is None or dd is None or len(df) < 200:
            continue
        if htf_gate:
            h4 = klines(s, "240", 1000)
            if h4 is None or len(h4) < 160:
                continue
            c4 = h4["close"]
            e30 = c4.ewm(span=30, adjust=False, min_periods=30).mean()
            e150 = c4.ewm(span=150, adjust=False, min_periods=150).mean()
            # state of each CLOSED 4h bar, keyed by bar start ts
            htf[s] = {int(h4["ts"].iloc[k]): (1 if e30.iloc[k] > e150.iloc[k] else -1)
                      for k in range(len(h4) - 1) if pd.notna(e150.iloc[k])}
        pc = dd["close"].shift(1)
        tr = pd.concat([dd["high"] - dd["low"], (dd["high"] - pc).abs(),
                        (dd["low"] - pc).abs()], axis=1).max(axis=1)
        atrp[s] = float((tr.rolling(14).mean() / dd["close"]).iloc[-2] * 100)
        data[s] = indicators(df)
        if (i + 1) % 25 == 0:
            print(f"  …{i+1}/{len(syms)}", file=sys.stderr)
        time.sleep(0.05)

    # global 15m timeline
    all_ts = sorted(set(ts for df in data.values() for ts in df["ts"]))
    idx = {s: dict(zip(df["ts"], range(len(df)))) for s, df in data.items()}

    cash, positions = START, {}
    eq_curve, trades = [], []
    for t_i, ts in enumerate(all_ts[:-1]):           # last bar is forming
        # exits on this bar (entered earlier)
        for s in list(positions):
            pos = positions[s]
            j = idx[s].get(ts)
            if j is None or j >= len(data[s]) - 1:
                continue
            bar = data[s].iloc[j]
            side = pos["side"]
            tp_px = pos["entry"] * (1 + TP) if side == "L" else pos["entry"] * (1 - TP)
            sl_px = pos["entry"] * (1 - SL) if side == "L" else pos["entry"] * (1 + SL)
            hit_sl = bar["low"] <= sl_px if side == "L" else bar["high"] >= sl_px
            hit_tp = bar["high"] >= tp_px if side == "L" else bar["low"] <= tp_px
            pos["held"] += 1
            exit_px, why = None, None
            if hit_sl:                                # pessimistic: SL before TP
                exit_px, why = min(sl_px, bar["open"]) if side == "L" \
                    else max(sl_px, bar["open"]), "SL"
            elif hit_tp:
                exit_px, why = tp_px, "TP"            # resting limit at target
            elif (side == "L" and bar["close"] < bar["e20"]) or \
                 (side == "S" and bar["close"] > bar["e20"]):
                exit_px, why = bar["close"], "INVALID"
            elif pos["held"] >= MAX_HOLD:
                exit_px, why = bar["close"], "TIME"
            if exit_px is not None:
                fill = exit_px * (1 - SLIP) if side == "L" else exit_px * (1 + SLIP)
                pnl = ((fill - pos["entry"]) if side == "L"
                       else (pos["entry"] - fill)) * pos["qty"]
                pnl -= fill * pos["qty"] * FEE + pos["entry_fee"]
                cash += pos["margin"] + pnl
                trades.append({"s": s, "side": side, "why": why,
                               "pnl": pnl, "ret": pnl / pos["margin"]})
                positions.pop(s)
        # equity
        upnl = 0.0
        for s, pos in positions.items():
            j = idx[s].get(ts)
            px = data[s].iloc[j]["close"] if j is not None else pos["entry"]
            upnl += pos["margin"] + ((px - pos["entry"]) if pos["side"] == "L"
                                     else (pos["entry"] - px)) * pos["qty"]
        equity = cash + upnl
        eq_curve.append(equity)
        # entries: signal on bar ts (closed), fill next bar open
        for s, df in data.items():
            if s in positions or len(positions) >= MAX_POS:
                continue
            j = idx[s].get(ts)
            if j is None or j + 1 >= len(df) or j < 60:
                continue
            bar = df.iloc[j]
            side = None
            lb = sum(1 for k in range(max(0, j - 5), j + 1) if df["long"].iloc[k])
            sb = sum(1 for k in range(max(0, j - 5), j + 1) if df["short"].iloc[k])
            if bar["long"] and 0 < bars_on(df, j, "long") <= FRESH:
                side = "L"
            elif bar["short"] and 0 < bars_on(df, j, "short") <= FRESH:
                side = "S"
            if side is None or not (ATR_LO <= atrp.get(s, 0) <= ATR_HI):
                continue
            if htf_gate:
                # last closed 4h bar strictly before this 15m bar's close
                bar_close = ts + 900_000
                h_ts = [k for k in htf.get(s, {}) if k + 14_400_000 <= bar_close]
                if not h_ts:
                    continue
                st = htf[s][max(h_ts)]
                if (side == "L" and st != 1) or (side == "S" and st != -1):
                    continue
            d24 = abs(bar["close"] / df.iloc[max(0, j - 96)]["close"] - 1)
            if d24 > MAX_24H:
                continue
            margin = equity * MARGIN_FRAC
            if margin > cash or equity <= 0:
                continue
            op = df.iloc[j + 1]["open"]
            fill = op * (1 + SLIP) if side == "L" else op * (1 - SLIP)
            notional = margin * LEV
            fee = notional * FEE
            cash -= margin + fee
            positions[s] = {"side": side, "entry": fill, "qty": notional / fill,
                            "margin": margin, "entry_fee": fee, "held": 0}
    eq = np.array(eq_curve)
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    tr_df = pd.DataFrame(trades)
    print(f"\n10-day sim, {len(data)} symbols, {len(all_ts)} bars")
    print(f"final ${eq[-1]:,.2f}  ({(eq[-1]/START-1)*100:+.2f}%)  maxDD {dd*100:+.2f}%")
    if len(tr_df):
        print(f"trades {len(tr_df)}  win {(tr_df.pnl>0).mean()*100:.0f}%  "
              f"avg/trade {tr_df.ret.mean()*100:+.2f}% of margin")
        print("\nby exit reason:")
        print(tr_df.groupby("why").agg(n=("pnl", "size"), pnl=("pnl", "sum"),
                                       avg=("ret", "mean")).round(3))
        print("\nby side:")
        print(tr_df.groupby("side").agg(n=("pnl", "size"), pnl=("pnl", "sum"),
                                        avg=("ret", "mean")).round(3))
        print("\nNOTE: 10 days only, funding not simmed, today's universe — "
              "sanity check, not validation.")


def bars_on(df: pd.DataFrame, j: int, col: str) -> int:
    cnt = 0
    while j - cnt >= 0 and bool(df[col].iloc[j - cnt]):
        cnt += 1
    return cnt


if __name__ == "__main__":
    main()
