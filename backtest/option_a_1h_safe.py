"""option_a_1h_safe.py — honest test of the user's "Option A: Safe RSI Scalper".

Finetune of v2.2's RSI logic with THREE changes (user req 2026-06-12):
  1. Timeframe 5m -> 1h   (filter noise)
  2. Disable counter-trend -> WITH-trend gate (no buying falling knives)
  3. TP_SINGLE 0.5% -> 2.0%

Faithful choices (documented):
  - Signal: Wilder RSI9 on closed 1h bar, 35/65 (v2.2 thresholds).
  - Trend gate: EMA20 vs EMA50 on the 1h close (the signal TF). with-trend =
    LONG only when EMA20>EMA50, SHORT only when EMA20<EMA50. A falling market
    (EMA20<EMA50) blocks longs -> "no buying aggressively falling markets".
  - Single position (NO DCA): DCA + BE-trail is the booking-artifact source
    (FINDINGS #1); the user's "TP must cover the inevitable losses" framing is
    a single-shot risk model. This isolates the entry+TP hypothesis cleanly,
    matching the rr_frontier / proposal_stack methodology FINDINGS already uses.
  - Dropped the 5m ATR(<=0.8%)/gap(0.20%) micro-gates: on 1h they block ~everything
    (1h ATR% >> 0.8%); the timeframe itself is the noise filter per the user's rationale.
  - Enter at NEXT bar open after signal. Exit checks start same entry bar.
  - HONEST fills: favorable gap fills at bar open; adverse gap fills at bar open;
    same-bar TP+SL conflict -> SL first (pessimistic).
  - Fees: gross = 0; net = 0.055%/side taker + 0.02%/side slip (round trip 0.15% price).
  - Leverage 5x for the equity curve (per-trade PRICE % is the clean metric).
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
RSI_P, OS, OB = 9, 35, 65
EMA_F, EMA_S = 20, 50
TP = 0.02
SL_GRID = [0.005, 0.010, 0.015, 0.020]
FEE, SLIP = 0.00055, 0.0002      # per side, price units
RT_COST = 2 * (FEE + SLIP)        # round-trip price cost = 0.0015
LEV = 5.0


def load_1h():
    df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    o = df["open"].resample("1h").first()
    h = df["high"].resample("1h").max()
    l = df["low"].resample("1h").min()
    c = df["close"].resample("1h").last()
    g = pd.DataFrame({"open": o, "high": h, "low": l, "close": c}).dropna()
    d = g["close"].diff()
    ag = d.clip(lower=0).ewm(alpha=1/RSI_P, min_periods=RSI_P, adjust=False).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1/RSI_P, min_periods=RSI_P, adjust=False).mean()
    g["rsi"] = 100 - 100 / (1 + ag / al.replace(0, np.nan))
    g["ema_f"] = g["close"].ewm(span=EMA_F, adjust=False, min_periods=EMA_F).mean()
    g["ema_s"] = g["close"].ewm(span=EMA_S, adjust=False, min_periods=EMA_S).mean()
    return g.dropna().reset_index()


def run(g, sl, fee_on):
    o = g["open"].values; h = g["high"].values; lo = g["low"].values
    rsi = g["rsi"].values; ef = g["ema_f"].values; es = g["ema_s"].values
    n = len(g); i = 0; trades = []
    cost = RT_COST if fee_on else 0.0
    while i < n - 1:
        up = ef[i] > es[i]
        side = None
        if rsi[i] <= OS and up:      side = "L"
        elif rsi[i] >= OB and not up: side = "S"
        if side is None:
            i += 1; continue
        entry = o[i + 1]
        if side == "L":
            tp_px, sl_px = entry * (1 + TP), entry * (1 - sl)
        else:
            tp_px, sl_px = entry * (1 - TP), entry * (1 + sl)
        ret = None; j = i + 1
        while j < n:
            bo, bh, bl = o[j], h[j], lo[j]
            if side == "L":
                hit_sl = bl <= sl_px; hit_tp = bh >= tp_px
                if hit_sl and hit_tp:                       # pessimistic: SL first
                    fill = min(bo, sl_px) if bo <= sl_px else sl_px
                    ret = fill / entry - 1; break
                if hit_sl:
                    fill = bo if bo <= sl_px else sl_px      # adverse gap -> open
                    ret = fill / entry - 1; break
                if hit_tp:
                    fill = bo if bo >= tp_px else tp_px      # favorable gap -> open
                    ret = fill / entry - 1; break
            else:
                hit_sl = bh >= sl_px; hit_tp = bl <= tp_px
                if hit_sl and hit_tp:
                    fill = max(bo, sl_px) if bo >= sl_px else sl_px
                    ret = 1 - fill / entry; break
                if hit_sl:
                    fill = bo if bo >= sl_px else sl_px
                    ret = 1 - fill / entry; break
                if hit_tp:
                    fill = bo if bo <= tp_px else tp_px
                    ret = 1 - fill / entry; break
            j += 1
        if ret is None:        # ran off the data while open
            break
        trades.append({"t": g["timestamp"].iloc[i + 1], "side": side,
                       "ret_gross": ret, "ret_net": ret - cost})
        i = j + 1              # no re-entry until the bar after exit
    return pd.DataFrame(trades)


def summarize(tr, label):
    if len(tr) == 0:
        print(f"  {label:26s}  no trades"); return
    g = tr["ret_gross"]; ntr = tr["ret_net"]
    wr = (g > 0).mean() * 100
    # compounded equity at LEV on net price returns
    eq = float(np.prod(1 + ntr.values * LEV))
    print(f"  {label:26s}  N={len(tr):5d}  WR={wr:4.1f}%  "
          f"gross={g.mean()*100:+.3f}%/t  net={ntr.mean()*100:+.3f}%/t  "
          f"net@5x x{eq:6.2f}")


if __name__ == "__main__":
    g = load_1h()
    mid = g["timestamp"].iloc[len(g) // 2]
    print(f"1h bars: {len(g)}  {g['timestamp'].iloc[0].date()} -> {g['timestamp'].iloc[-1].date()}"
          f"   IS/OOS split @ {mid.date()}\n")
    for sl in SL_GRID:
        print(f"TP {TP*100:.1f}%  /  SL {sl*100:.2f}%   (R:R {TP/sl:.2f})")
        for fee_on, tag in [(False, "GROSS (0 fees)"), (True, "NET (taker+slip)")]:
            tr = run(g, sl, fee_on)
            summarize(tr, f"  full  {tag}")
            is_ = tr[tr["t"] < mid]; oos = tr[tr["t"] >= mid]
            if fee_on:
                summarize(is_, f"  IS    {tag}")
                summarize(oos, f"  OOS   {tag}")
        print()
