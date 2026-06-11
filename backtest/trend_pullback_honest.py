"""User spec 2026-06-12: 5m trend-pullback bot (triple-EMA + ADX + vol + RSI
pullback, ATR stop, 1R/2R partial TP, BE move, ATR trail, 12-bar time exit,
0.5% risk sizing, 2% daily stop, 3x cap). HONEST backtest:

  - indicators on CLOSED bars only; signal at close of bar i, entry at open
    of bar i+1 (+slip, taker)
  - SL is a resting stop: fill = worse(stop, bar open), taker + slip
  - TP1/TP2 are resting limits: fill at limit price, maker fee, no slip
  - pessimistic same-bar conflicts: SL beats TP; trail-stop beats TP2
  - time exit at bar close (market, taker + slip)
  - sizing: qty = 0.5% * balance / stop_distance, notional capped at 3x
  - daily net PnL <= -2% balance -> no new entries until next UTC day
  - mark-to-market max DD, Sharpe/Sortino from daily closed-equity returns
"""
import pandas as pd
import numpy as np

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
INITIAL = 5000.0
LEV_CAP = 3.0
RISK = 0.005
ATR_SL_K = 1.2
TRAIL_K = 1.5
TIME_BARS = 12
DAILY_STOP = 0.02
FEE_T, FEE_M, SLIP = 0.00055, 0.0002, 0.0002

df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
# drop the synthetic flat warmup rows at the head (volume ~0, ohlc identical)
df = df[df["high"] > df["low"]].reset_index(drop=True)

c, o, h, l, v = df["close"], df["open"], df["high"], df["low"], df["volume"]
e20 = c.ewm(span=20, adjust=False).mean()
e50 = c.ewm(span=50, adjust=False).mean()
e200 = c.ewm(span=200, adjust=False).mean()

d = c.diff()
gain = d.clip(lower=0.0); loss = (-d).clip(lower=0.0)
rsi = 100 - 100 / (1 + gain.ewm(alpha=1/9, adjust=False, min_periods=9).mean()
                   / loss.ewm(alpha=1/9, adjust=False, min_periods=9).mean())

pc = c.shift(1)
tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
atr = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()

up = h.diff(); dn = -l.diff()
plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
atr_w = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
pdi = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1/14, adjust=False, min_periods=14).mean() / atr_w
mdi = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1/14, adjust=False, min_periods=14).mean() / atr_w
dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
adx = dx.ewm(alpha=1/14, adjust=False, min_periods=14).mean()

vsma = v.rolling(20).mean()

E20, E50, E200 = e20.values, e50.values, e200.values
RSI, ADX, ATR, VS = rsi.values, adx.values, atr.values, vsma.values
O, H, L, C, V = o.values, h.values, l.values, c.values, v.values
TS = df["timestamp"].values

bal = INITIAL
pos = None; pend = None
halt_day = None
daily_pnl = {}
trades = []
peak_mtm = INITIAL; max_dd = 0.0
day_bal = {}  # date -> closing balance for Sharpe

def book(ts, net, kind):
    global bal
    bal += net
    d_ = pd.Timestamp(ts).date()
    daily_pnl[d_] = daily_pnl.get(d_, 0.0) + net
    trades.append({"net": net, "kind": kind, "ts": pd.Timestamp(ts)})

n = len(df)
for i in range(220, n):
    ts = TS[i]
    d_ = pd.Timestamp(ts).date()
    day_bal[d_] = bal

    # ── pending entry fills at open ──
    if pos is None and pend is not None:
        side, slx, r1 = pend
        pend = None
        eff = O[i] * (1 + SLIP) if side == "L" else O[i] * (1 - SLIP)
        stop = eff - r1 if side == "L" else eff + r1
        qty = RISK * bal / r1
        if qty * eff > LEV_CAP * bal:
            qty = LEV_CAP * bal / eff
        fee_in = eff * qty * FEE_T
        bal -= fee_in
        pos = {"side": side, "qty": qty, "q1": qty / 2, "q2": qty / 2,
               "avg": eff, "stop": stop, "r": r1, "tp1_done": False,
               "bar": i, "fees_in": fee_in}

    if pos is not None:
        side, avg, r1 = pos["side"], pos["avg"], pos["r"]
        sgn = 1 if side == "L" else -1
        tp1 = avg + sgn * r1
        tp2 = avg + sgn * 2 * r1

        # update trail (only after TP1) using last closed ATR, ratcheting
        if pos["tp1_done"]:
            t_stop = C[i - 1] - sgn * TRAIL_K * ATR[i - 1]
            pos["stop"] = max(pos["stop"], t_stop) if side == "L" else min(pos["stop"], t_stop)

        stop = pos["stop"]
        sl_hit = (L[i] <= stop) if side == "L" else (H[i] >= stop)
        tp1_hit = (not pos["tp1_done"]) and ((H[i] >= tp1) if side == "L" else (L[i] <= tp1))
        tp2_hit = pos["tp1_done"] and ((H[i] >= tp2) if side == "L" else (L[i] <= tp2))

        if sl_hit:  # pessimistic: stop beats limits
            fill = min(stop, O[i]) if side == "L" else max(stop, O[i])
            eff = fill * (1 - SLIP) if side == "L" else fill * (1 + SLIP)
            qty = pos["q1"] + pos["q2"] if not pos["tp1_done"] else pos["q2"]
            gross = sgn * (eff - avg) * qty
            book(ts, gross - eff * qty * FEE_T - (0 if pos["tp1_done"] else pos["fees_in"]),
                 "SL" if not pos["tp1_done"] else ("BE" if abs(stop - avg) < 1e-9 else "TRAIL"))
            pos = None
        elif tp1_hit:
            eff = tp1
            gross = sgn * (eff - avg) * pos["q1"]
            book(ts, gross - eff * pos["q1"] * FEE_M - pos["fees_in"], "TP1")
            pos["q1"] = 0.0
            pos["tp1_done"] = True
            pos["stop"] = avg  # breakeven
        elif tp2_hit:
            eff = tp2
            gross = sgn * (eff - avg) * pos["q2"]
            book(ts, gross - eff * pos["q2"] * FEE_M, "TP2")
            pos = None

        # time exit: after 12 bars, if unrealized < 0.5R (on remaining), close
        if pos is not None and (i - pos["bar"]) >= TIME_BARS and not pos["tp1_done"]:
            unreal = sgn * (C[i] - avg)
            if unreal < 0.5 * r1:
                eff = C[i] * (1 - SLIP) if side == "L" else C[i] * (1 + SLIP)
                qty = pos["q1"] + pos["q2"]
                gross = sgn * (eff - avg) * qty
                book(ts, gross - eff * qty * FEE_T - pos["fees_in"], "TIME")
                pos = None

        if pos is not None:
            adv = L[i] if side == "L" else H[i]
            qty = (pos["q1"] + pos["q2"])
            eq = bal + sgn * (adv - avg) * qty
            fav = H[i] if side == "L" else L[i]
            peak_mtm = max(peak_mtm, bal + sgn * (fav - avg) * qty)
            max_dd = max(max_dd, (peak_mtm - eq) / peak_mtm)
        else:
            peak_mtm = max(peak_mtm, bal)
            max_dd = max(max_dd, (peak_mtm - bal) / peak_mtm)

    # ── signal at close ──
    if pos is None and pend is None:
        if np.isnan(E200[i]) or np.isnan(ADX[i]) or np.isnan(VS[i]) or np.isnan(ATR[i]):
            continue
        if daily_pnl.get(d_, 0.0) <= -DAILY_STOP * bal:
            continue
        long_trend = E20[i] > E50[i] > E200[i]
        short_trend = E20[i] < E50[i] < E200[i]
        if not (long_trend or short_trend) or ADX[i] <= 20 or V[i] <= VS[i]:
            continue
        if long_trend and RSI[i - 1] < 40 <= RSI[i] and C[i] > O[i]:
            pend = ("L", None, ATR_SL_K * ATR[i])
        elif short_trend and RSI[i - 1] > 60 >= RSI[i] and C[i] < O[i]:
            pend = ("S", None, ATR_SL_K * ATR[i])

# ── report ──
tr_ = pd.DataFrame(trades)
print(f"Data: {pd.Timestamp(TS[0])} -> {pd.Timestamp(TS[-1])}  ({n:,} bars)")
if tr_.empty:
    print("NO TRADES"); raise SystemExit
# a 'trade event' here is a booked fill; group TP1+TP2/SL of same position ≈ sequential
net_total = tr_["net"].sum()
wins = tr_[tr_["net"] > 0]["net"]; losses = tr_[tr_["net"] < 0]["net"]
pf = wins.sum() / abs(losses.sum()) if len(losses) else float("inf")
months = (pd.Timestamp(TS[-1]) - pd.Timestamp(TS[0])).days / 30.44
db = pd.Series(day_bal).sort_index()
dr = db.pct_change().dropna()
sharpe = dr.mean() / dr.std() * np.sqrt(365) if dr.std() > 0 else 0
neg = dr[dr < 0]
sortino = dr.mean() / neg.std() * np.sqrt(365) if len(neg) and neg.std() > 0 else 0
print(f"Final: ${bal:,.0f}  net ${net_total:+,.0f} ({net_total/INITIAL*100:+.1f}%)")
print(f"Fills: {len(tr_)}  ({len(tr_)/months:.0f}/month)   WR(fills): {len(wins)/len(tr_)*100:.1f}%   PF: {pf:.2f}")
print(f"Expectancy: ${tr_['net'].mean():+.2f}/fill   avg win ${wins.mean():+.2f}   avg loss ${losses.mean():+.2f}")
print(f"Sharpe: {sharpe:.2f}   Sortino: {sortino:.2f}   MaxDD (MTM): {max_dd*100:.1f}%")
print("\nExit mix:", tr_.groupby("kind")["net"].agg(["count", "sum"]).to_string())
tr_["yr"] = tr_["ts"].dt.year
print("\nYearly net $:")
print(tr_.groupby("yr")["net"].agg(["count", "sum"]).to_string())
mr = tr_.set_index("ts")["net"].resample("ME").sum()
print(f"\nMonths: {len(mr)}  positive: {(mr>0).sum()} ({(mr>0).mean()*100:.0f}%)  "
      f"mean ${mr.mean():+,.0f}  best ${mr.max():+,.0f}  worst ${mr.min():+,.0f}")
