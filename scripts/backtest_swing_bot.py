#!/usr/bin/env python3
"""backtest_swing_bot.py — Honest backtest of the Itsme23476/btc-trading-bot strategy core.

Faithful replication of its technical engine (the part that can be backtested):
  REGIME (ADX + EMA):  TREND_UP = ADX>trend & EMA_fast>EMA_slow ; RANGE = ADX<range_th
  SWING entry (TREND_UP, long): price pulls back into the EMA_fast zone
      (ema_fast <= price <= ema_fast*(1+pullback)), RSI recovering from oversold,
      MACD bullish cross within lookback. Stop = min(swingLow(N), entry-2.5*ATR).
  RANGE entry (long): RSI<30 and price <= lower Bollinger*1.01. Same stop rule.
  FILTERS: skip if ATR% > vol_mult * median ATR% (vol filter); min reward:risk.
  EXITS (priority): hard stop -> take 50% at +1.5R -> trail rest at HH - 3*ATR.
  RISK: 1% of equity per trade (size = risk/stop_distance), spot long-only, no leverage.

NOT modeled: the Claude news-bias filter (no historical bias data) -> this is the
technical engine ALONE. Decisions on CLOSED bars; entries fill next bar open; stops/
targets/trail checked on REAL intrabar high/low (SL first on straddle); fee+slippage.
"""
from __future__ import annotations

import argparse
import time

import pandas as pd
import requests

PAIR = "BTCUSDT"
BYBIT_BASE = "https://api.bybit.com"
FEE_PCT = 0.00055
SLIP_PCT = 0.0005
RISK_PER_TRADE = 0.01


def fetch_bybit(symbol, interval, bars):
    rows, end_ms = [], None
    while len(rows) < bars:
        params = {"category": "linear", "symbol": symbol, "interval": interval,
                  "limit": min(1000, bars - len(rows))}
        if end_ms is not None:
            params["end"] = end_ms
        r = requests.get(f"{BYBIT_BASE}/v5/market/kline", params=params, timeout=20)
        r.raise_for_status()
        body = r.json()
        if body.get("retCode") != 0:
            raise RuntimeError(body.get("retMsg"))
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


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0.0); dn = (-d).clip(lower=0.0)
    ru = up.ewm(alpha=1/n, adjust=False).mean()
    rd = dn.ewm(alpha=1/n, adjust=False).mean()
    rs = ru / rd.replace(0, 1e-9)
    return 100 - 100/(1+rs)


def macd(s, f=12, sl=26, sig=9):
    ml = ema(s, f) - ema(s, sl)
    sigl = ema(ml, sig)
    return ml, sigl


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"]-df["low"], (df["high"]-pc).abs(), (df["low"]-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def adx(df, n=14):
    up = df["high"].diff(); dn = -df["low"].diff()
    pdm = ((up > dn) & (up > 0)) * up.clip(lower=0)
    ndm = ((dn > up) & (dn > 0)) * dn.clip(lower=0)
    a = atr(df, n)
    pdi = 100*ema(pdm, n)/a; ndi = 100*ema(ndm, n)/a
    dx = 100*(pdi-ndi).abs()/(pdi+ndi).replace(0, 1e-9)
    return ema(dx, n)


def add_indicators(df, ema_fast, ema_slow):
    out = df.copy()
    out["ema_f"] = ema(out["close"], ema_fast)
    out["ema_s"] = ema(out["close"], ema_slow)
    out["rsi"] = rsi(out["close"], 14)
    ml, sg = macd(out["close"])
    out["macd"] = ml; out["macd_sig"] = sg
    out["macd_cross"] = (ml > sg) & (ml.shift(1) <= sg.shift(1))
    out["atr"] = atr(out, 14)
    out["atr_pct"] = out["atr"] / out["close"]
    mid = out["close"].rolling(20).mean()
    sd = out["close"].rolling(20).std()
    out["bb_low"] = mid - 2.0*sd
    out["adx"] = adx(out, 14)
    out["swing_low"] = out["low"].rolling(20).min()
    out["atrpct_med"] = out["atr_pct"].rolling(200, min_periods=50).median()
    return out


def metrics(curve):
    e = pd.Series(curve)
    if len(e) < 2:
        return 0.0, 0.0
    dd = e/e.cummax() - 1.0
    return (e.iloc[-1]-1.0)*100.0, float(dd.min()*100.0)


def buy_hold(df):
    p0 = float(df.iloc[0]["close"])
    return metrics([float(df.iloc[i]["close"])/p0 for i in range(len(df))])


def trend_follow(df):
    c = df["close"]; ef, es, eg = ema(c, 13), ema(c, 20), ema(c, 200)
    long = (ef > es) & (c > eg)
    cash, qty = 1.0, 0.0; eq = []
    for i in range(200, len(df)-1):
        px = float(df.iloc[i+1]["open"])
        if qty == 0 and bool(long.iloc[i]):
            fill = px*(1+SLIP_PCT); cash -= cash*FEE_PCT; qty = cash/fill; cash = 0.0
        elif qty > 0 and not bool(long.iloc[i]):
            fill = px*(1-SLIP_PCT); proc = qty*fill; cash = proc-proc*FEE_PCT; qty = 0.0
        eq.append(cash + qty*float(df.iloc[i+1]["close"]))
    if qty > 0:
        eq.append(cash + qty*float(df.iloc[-1]["close"]))
    return metrics(eq)


def backtest(df, *, adx_trend, adx_range, pullback, vol_mult, use_range, use_trend):
    cash = 1.0
    pos = None
    eq = []
    trades = wins = 0
    gp = gl = 0.0

    for i in range(210, len(df)-1):
        row = df.iloc[i]
        c = float(row["close"])

        # ---- manage open position on REAL intrabar high/low of bar i ----
        if pos is not None:
            hi, lo = float(row["high"]), float(row["low"])
            pos["hh"] = max(pos["hh"], hi)
            exit_px = None; qty_frac = 1.0
            if lo <= pos["stop"]:                          # hard stop first
                exit_px = pos["stop"]
            elif not pos["partial"] and hi >= pos["t1"]:   # take 50% at +1.5R
                fpx = pos["t1"]*(1-SLIP_PCT)
                proceeds = pos["qty"]*0.5*fpx; proceeds -= proceeds*FEE_PCT
                cost = pos["cost"]*0.5
                cash += proceeds; pnl = proceeds - cost
                gp += max(pnl, 0); gl += max(-pnl, 0); trades += 1; wins += int(pnl > 0)
                pos["qty"] *= 0.5; pos["cost"] *= 0.5; pos["partial"] = True
            if pos is not None and pos["partial"]:         # trail remainder
                trail = pos["hh"] - 3.0*float(row["atr"])
                pos["stop"] = max(pos["stop"], trail)
                if lo <= pos["stop"] and exit_px is None:
                    exit_px = pos["stop"]
            if exit_px is not None:
                fpx = exit_px*(1-SLIP_PCT)
                proceeds = pos["qty"]*fpx; proceeds -= proceeds*FEE_PCT
                cash += proceeds; pnl = proceeds - pos["cost"]
                gp += max(pnl, 0); gl += max(-pnl, 0); trades += 1; wins += int(pnl > 0)
                pos = None

        # ---- entries (closed-bar signal, fill next open) ----
        if pos is None:
            ef, es = float(row["ema_f"]), float(row["ema_s"])
            adx_i = float(row["adx"]); rsi_i = float(row["rsi"])
            rsi_prev = float(df.iloc[i-1]["rsi"]); rsi_min3 = float(df["rsi"].iloc[i-3:i+1].min())
            atrp = float(row["atr_pct"]); med = float(row["atrpct_med"]) if pd.notna(row["atrpct_med"]) else atrp
            macd_recent = bool(df["macd_cross"].iloc[i-4:i+1].any())
            vol_ok = atrp <= vol_mult*med
            trend_up = adx_i > adx_trend and ef > es
            ranging = adx_i < adx_range
            entry = float(df.iloc[i+1]["open"])
            sig = None

            macd_bull = float(row["macd"]) > float(row["macd_sig"])
            if use_trend and trend_up and vol_ok:
                in_pullback = abs(c - ef)/ef <= pullback              # price near EMA_fast
                rsi_bounce = rsi_min3 < 45 and rsi_i > rsi_prev        # recovering momentum
                if in_pullback and rsi_bounce and (macd_bull or macd_recent):
                    sig = "swing"
            if sig is None and use_range and ranging and vol_ok:
                if rsi_i < 38 and c <= float(row["bb_low"])*1.02:
                    sig = "range"

            if sig is not None:
                swing_low = float(row["swing_low"])
                stop = min(swing_low, entry - 2.5*float(row["atr"]))
                if stop < entry:
                    risk_frac = (entry - stop)/entry
                    notional = min(RISK_PER_TRADE/risk_frac, 1.0) * cash
                    if notional > 0.01*cash:
                        fill = entry*(1+SLIP_PCT)
                        fee = notional*FEE_PCT
                        qty = notional/fill
                        cash -= notional + fee
                        risk_amt = qty*(fill - stop)
                        pos = {"entry": fill, "stop": stop, "qty": qty, "cost": notional+fee,
                               "t1": fill + 1.5*(fill-stop), "partial": False, "hh": fill}

        eq.append(cash + (pos["qty"]*c if pos else 0.0))

    net, dd = metrics(eq)
    pf = gp/gl if gl > 0 else float("inf")
    wr = wins/trades*100 if trades else 0.0
    return {"net": net, "dd": dd, "pf": pf, "trades": trades, "wr": wr, "eq": eq,
            "ts": list(df["timestamp"].iloc[210:len(df)-1])}


def monthly(ts, eq):
    s = pd.Series(eq, index=pd.to_datetime(ts))
    m = s.resample("ME").last(); r = m.pct_change().dropna()*100
    if len(r) == 0:
        return [], 0.0, 0.0
    return [(i.strftime("%Y-%m"), float(v)) for i, v in r.items()], float(r.mean()), float((r > 0).mean()*100)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=PAIR)
    p.add_argument("--bars", type=int, default=6000)
    p.add_argument("--interval", default="240")
    p.add_argument("--ema-fast", type=int, default=50)
    p.add_argument("--ema-slow", type=int, default=200)
    args = p.parse_args()

    df = add_indicators(fetch_bybit(args.symbol, args.interval, args.bars), args.ema_fast, args.ema_slow)
    days = (df.timestamp.iloc[-1]-df.timestamp.iloc[0]).total_seconds()/86400
    print(f"{args.symbol} {args.interval} bars={len(df)} (~{days:.0f}d) "
          f"EMA{args.ema_fast}/{args.ema_slow} | {df.timestamp.iloc[0]} -> {df.timestamp.iloc[-1]}")

    split = int(len(df)*0.6)
    is_df = df.iloc[:split].reset_index(drop=True)
    oos_df = df.iloc[split:].reset_index(drop=True)
    print(f"IS {is_df.timestamp.iloc[0].date()}->{is_df.timestamp.iloc[-1].date()} | "
          f"OOS {oos_df.timestamp.iloc[0].date()}->{oos_df.timestamp.iloc[-1].date()}")

    print("\nFULL-PERIOD (sanity):")
    print("variant,net_pct,pf,win_rate,trades,max_dd")
    variants = {
        "trend+range": dict(use_trend=True, use_range=True),
        "trend_only":  dict(use_trend=True, use_range=False),
        "range_only":  dict(use_trend=False, use_range=True),
    }
    cfg = dict(adx_trend=25, adx_range=20, pullback=0.01, vol_mult=2.0)
    for name, v in variants.items():
        r = backtest(df, **cfg, **v)
        print(f"{name},{r['net']:.2f},{r['pf']:.3f},{r['wr']:.1f},{r['trades']},{r['dd']:.2f}")

    # IS sweep -> OOS validate (faithful trend+range engine)
    print("\nIN-SAMPLE sweep:")
    print("adx_trend,adx_range,pullback,net,pf,wr,trades,dd")
    best = None
    for at in (20, 25, 30):
        for ar in (18, 22):
            for pb in (0.008, 0.015, 0.025):
                r = backtest(is_df, adx_trend=at, adx_range=ar, pullback=pb, vol_mult=2.0,
                             use_trend=True, use_range=True)
                print(f"{at},{ar},{pb},{r['net']:.2f},{r['pf']:.3f},{r['wr']:.1f},{r['trades']},{r['dd']:.2f}")
                score = r["pf"] if r["trades"] >= 10 else -1
                if best is None or score > best[0]:
                    best = (score, (at, ar, pb))
    _, (at, ar, pb) = best
    ris = backtest(is_df, adx_trend=at, adx_range=ar, pullback=pb, vol_mult=2.0, use_trend=True, use_range=True)
    roos = backtest(oos_df, adx_trend=at, adx_range=ar, pullback=pb, vol_mult=2.0, use_trend=True, use_range=True)
    bh = buy_hold(oos_df); tf = trend_follow(oos_df)
    print(f"\nBEST IS config: adx_trend={at} adx_range={ar} pullback={pb}")
    print(f"  IN-SAMPLE : net={ris['net']:.2f}% pf={ris['pf']:.3f} wr={ris['wr']:.1f}% trades={ris['trades']} dd={ris['dd']:.2f}%")
    print(f"  OUT-SAMPLE: net={roos['net']:.2f}% pf={roos['pf']:.3f} wr={roos['wr']:.1f}% trades={roos['trades']} dd={roos['dd']:.2f}%")
    print(f"  vs buy_hold: net={bh[0]:.2f}% dd={bh[1]:.2f}% | trend_bot: net={tf[0]:.2f}% dd={tf[1]:.2f}%")
    rows, mean, pos = monthly(roos["ts"], roos["eq"])
    print(f"\nOOS MONTHLY: avg={mean:+.2f}%/mo positive={pos:.0f}%")
    for ym, v in rows:
        print(f"  {ym}: {v:+6.2f}%  {'#'*int(abs(v)*4)}")
    print(f"\nVERDICT: {'HOLDS UP' if roos['net'] > 0 and roos['pf'] > 1.05 else 'FALLS APART'}")


if __name__ == "__main__":
    main()
