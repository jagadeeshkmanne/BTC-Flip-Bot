"""trend_pullback.py — honest backtest of the user's trend-pullback spec.

Spec (fixed, no tuning): EMA20>50>200 stack + ADX14>20 + vol>SMA20 + RSI9
pullback cross (40 long / 60 short) + bullish/bearish close. Enter next bar
open. SL = 1.2xATR14. TP1 at 1R (close 50%, stop->BE). TP2 at 2R. After 1R,
trail 1.5xATR on close-based peak. Time exit: <0.5R max-fav after 12 candles.
Risk 0.5%/trade sized from stop distance, notional capped at 3x. Daily loss
limit 2% (UTC). One position.

Honesty rules: closed candles only; same-bar TP/SL conflict -> SL first;
stop fills at worse of (stop, bar open); trail peak from CLOSES not wicks;
maker 0.02% on TP limits, taker 0.055% + 0.02% slip on entries/stops/time.
Same engine run on 5m (spec) and 15m/1h/4h for a no-tuning robustness check.
"""
import pandas as pd
import numpy as np

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv"
START_BAL = 5000.0
RISK = 0.005
LEV_CAP = 3.0
ATR_SL = 1.2
TRAIL_ATR = 1.5
TIME_BARS = 12
TIME_MIN_R = 0.5
DAILY_STOP = 0.02
MAKER = 0.0002
TAKER = 0.00055
SLIP = 0.0002


def wilder(s, n):
    return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def indicators(df):
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    out = pd.DataFrame(index=df.index)
    for n in (20, 50, 200):
        out[f"ema{n}"] = c.ewm(span=n, adjust=False, min_periods=n).mean()
    d = c.diff()
    out["rsi"] = 100 - 100 / (1 + wilder(d.clip(lower=0), 9) / wilder(-d.clip(upper=0), 9))
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    out["atr"] = wilder(tr, 14)
    up, dn = h.diff(), -l.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    atr_w = wilder(tr, 14)
    pdi = 100 * wilder(pdm, 14) / atr_w
    ndi = 100 * wilder(ndm, 14) / atr_w
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi)
    out["adx"] = wilder(dx, 14)
    out["volsma"] = v.rolling(20).mean()
    return out


def run(df, label):
    ind = indicators(df)
    o = df["open"].values; h = df["high"].values; l = df["low"].values
    c = df["close"].values; v = df["volume"].values
    e20, e50, e200 = ind["ema20"].values, ind["ema50"].values, ind["ema200"].values
    rsi, atr, adx, vs = ind["rsi"].values, ind["atr"].values, ind["adx"].values, ind["volsma"].values
    ts = df.index

    bal = START_BAL
    pos = None
    pending = None
    daily_pnl = {}
    trades = []
    eq_low_track = []   # (date, equity_mtm_low)
    daily_eq = {}
    fees_R = []

    def book(net, ts_i, tag):
        nonlocal bal
        bal += net
        d = ts_i.date()
        daily_pnl[d] = daily_pnl.get(d, 0.0) + net

    for i in range(1, len(df)):
        t = ts[i]
        d = t.date()

        # fill pending entry at this bar's open
        if pos is None and pending is not None:
            side, sd = pending
            pending = None
            if daily_pnl.get(d, 0.0) > -DAILY_STOP * bal:
                fill = o[i] * (1 + SLIP) if side == "L" else o[i] * (1 - SLIP)
                qty = (RISK * bal) / sd
                qty = min(qty, LEV_CAP * bal / fill)
                fee_in = fill * qty * TAKER
                pos = {"side": side, "e": fill, "q": qty, "q0": qty, "sd": sd,
                       "stop": fill - sd if side == "L" else fill + sd,
                       "tp1": fill + sd if side == "L" else fill - sd,
                       "tp2": fill + 2 * sd if side == "L" else fill - 2 * sd,
                       "tp1_done": False, "bar0": i, "peak": fill,
                       "maxfavR": 0.0, "fees": fee_in, "real": 0.0}

        if pos is not None:
            side, e, q, sd = pos["side"], pos["e"], pos["q"], pos["sd"]
            sgn = 1 if side == "L" else -1
            exited = False
            # stop check (pessimistic: before TP on same bar), fill worse of stop/open
            sl = pos["stop"]
            hit = l[i] <= sl if side == "L" else h[i] >= sl
            if hit:
                fill = min(sl, o[i]) if side == "L" else max(sl, o[i])
                fill = fill * (1 - SLIP) if side == "L" else fill * (1 + SLIP)
                gross = sgn * (fill - e) * q
                fee = fill * q * TAKER
                net = pos["real"] + gross - fee - pos["fees"]
                book(net, t, "SL"); trades.append((net, t, pos)); exited = True
            if not exited:
                # TP1 partial (maker limit)
                if not pos["tp1_done"]:
                    tp1hit = h[i] >= pos["tp1"] if side == "L" else l[i] <= pos["tp1"]
                    if tp1hit:
                        half = q / 2
                        gross = sgn * (pos["tp1"] - e) * half
                        fee = pos["tp1"] * half * MAKER
                        pos["real"] += gross - fee
                        pos["q"] = q = q - half
                        pos["tp1_done"] = True
                        pos["stop"] = e  # breakeven
                # TP2 (maker limit) on remainder
                if pos["tp1_done"]:
                    tp2hit = h[i] >= pos["tp2"] if side == "L" else l[i] <= pos["tp2"]
                    if tp2hit:
                        gross = sgn * (pos["tp2"] - e) * q
                        fee = pos["tp2"] * q * MAKER
                        net = pos["real"] + gross - fee - pos["fees"]
                        book(net, t, "TP2"); trades.append((net, t, pos)); exited = True
            if not exited:
                # excursion tracking on closes (live-pollable), MTM on wicks
                favR = sgn * (c[i] - e) / sd
                pos["maxfavR"] = max(pos["maxfavR"], favR)
                if side == "L":
                    pos["peak"] = max(pos["peak"], c[i])
                else:
                    pos["peak"] = min(pos["peak"], c[i])
                # trail after TP1
                if pos["tp1_done"]:
                    tr_stop = (pos["peak"] - TRAIL_ATR * atr[i] if side == "L"
                               else pos["peak"] + TRAIL_ATR * atr[i])
                    pos["stop"] = max(pos["stop"], tr_stop) if side == "L" else min(pos["stop"], tr_stop)
                # time exit
                if (i - pos["bar0"]) >= TIME_BARS and pos["maxfavR"] < TIME_MIN_R:
                    fill = c[i] * (1 - SLIP) if side == "L" else c[i] * (1 + SLIP)
                    gross = sgn * (fill - e) * q
                    fee = fill * q * TAKER
                    net = pos["real"] + gross - fee - pos["fees"]
                    book(net, t, "TIME"); trades.append((net, t, pos)); exited = True
            if exited:
                fees_R.append((pos["fees"] + 0.0) / (pos["q0"] * sd))
                pos = None
            else:
                adv = l[i] if side == "L" else h[i]
                eq_low_track.append((d, bal + sgn * (adv - e) * pos["q"] + pos["real"]))

        daily_eq[d] = bal
        # signal on this CLOSED bar -> entry next bar
        if pos is None and pending is None:
            if np.isnan(e200[i]) or np.isnan(adx[i]) or np.isnan(vs[i]) or np.isnan(rsi[i - 1]):
                continue
            if daily_pnl.get(d, 0.0) <= -DAILY_STOP * bal:
                continue
            vol_ok = v[i] > vs[i]
            sd = ATR_SL * atr[i]
            if sd <= 0:
                continue
            if (e20[i] > e50[i] > e200[i] and adx[i] > 20 and vol_ok
                    and rsi[i - 1] < 40 <= rsi[i] and c[i] > o[i]):
                pending = ("L", sd)
            elif (e20[i] < e50[i] < e200[i] and adx[i] > 20 and vol_ok
                    and rsi[i - 1] > 60 >= rsi[i] and c[i] < o[i]):
                pending = ("S", sd)

    # ---- metrics ----
    nets = np.array([x[0] for x in trades])
    when = [x[1] for x in trades]
    n = len(nets)
    if n == 0:
        print(f"{label}: no trades"); return
    wins = nets[nets > 0]; losses = nets[nets < 0]
    wr = len(wins) / n * 100
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf")
    exp = nets.mean()
    eq = pd.Series(daily_eq).sort_index()
    dr = eq.pct_change().dropna()
    sharpe = dr.mean() / dr.std() * np.sqrt(365) if dr.std() > 0 else 0
    dn = dr[dr < 0]
    sortino = dr.mean() / dn.std() * np.sqrt(365) if len(dn) > 1 and dn.std() > 0 else 0
    # MTM max DD
    full = pd.concat([eq, pd.Series({d: v for d, v in eq_low_track})]).sort_index()
    mtm = pd.Series([min(x) if hasattr(x, "__len__") else x for x in
                     pd.concat([eq, pd.DataFrame(eq_low_track).set_index(0)[1]
                               .groupby(level=0).min()], axis=1).min(axis=1)],
                    index=eq.index)
    peak = mtm.cummax()
    maxdd = ((peak - mtm) / peak).max() * 100
    months = (eq.index[-1] - eq.index[0]).days / 30.44
    print(f"\n══ {label} ══")
    print(f"  Net: ${bal - START_BAL:+,.0f} ({(bal/START_BAL-1)*100:+.1f}%)  trades: {n} "
          f"({n/months:.0f}/mo)  WR: {wr:.1f}%  PF: {pf:.2f}")
    print(f"  Expectancy: ${exp:+.2f}/trade  avgW ${wins.mean() if len(wins) else 0:+.2f}  "
          f"avgL ${losses.mean() if len(losses) else 0:+.2f}")
    print(f"  Sharpe: {sharpe:.2f}  Sortino: {sortino:.2f}  maxDD(MTM): {maxdd:.1f}%")
    print(f"  avg fees per trade: {np.mean(fees_R):.2f}R  (1R = planned risk of a full stop)")
    yr = pd.Series(nets, index=pd.DatetimeIndex(when)).groupby(lambda x: x.year).sum()
    print("  Yearly: " + "  ".join(f"{y}: ${v:+,.0f}" for y, v in yr.items()))
    if label.startswith("5m"):
        m = pd.Series(nets, index=pd.DatetimeIndex(when))
        mt = m.groupby([m.index.year, m.index.month]).sum().unstack(fill_value=0)
        print("  Monthly P&L ($):")
        print(mt.round(0).to_string())


def main():
    df5 = pd.read_csv(CSV, parse_dates=["timestamp"]).set_index("timestamp")
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    for label, frame in [("5m (spec)", df5),
                         ("15m same spec", df5.resample("15min").agg(agg).dropna()),
                         ("1h same spec", df5.resample("1h").agg(agg).dropna()),
                         ("4h same spec", df5.resample("4h").agg(agg).dropna())]:
        run(frame, label)


if __name__ == "__main__":
    main()
