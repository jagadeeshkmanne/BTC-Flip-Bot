"""htf_trend_gate_v22.py — does gating v2.2's 5m entries by a higher-TF trend help?

User question (2026-06-12): keep v2.2's 5m RSI mean-reversion entries, but only
open LONG when the 1h / 4h / 1d trend is UP, and SHORT when it's DOWN. Good or not?

Honest single-position test (DCA/BE-trail removed — that's the booking artifact):
  - Signal: Wilder RSI9 on closed 5m bar, 35/65 (v2.2).
  - Direction modes:
      counter (v2.2 actual): long on oversold / short on overbought, no HTF gate
      with-1h / with-4h / with-1d: take the trade ONLY if HTF EMA20>EMA50 (long)
                                   or EMA20<EMA50 (short). HTF trend uses only
                                   CLOSED HTF bars (shifted) — no lookahead.
  - Enter next 5m bar open. Honest fills (favorable gap->open, adverse gap->open,
    same-bar TP+SL -> SL first). Fees: 0.055%/side taker + 0.02%/side slip.
  - A few TP/SL geometries around v2.2's scale.
  - Diagnostic: barrier WR* = SL/(TP+SL). If observed WR ~= WR* and gross ~= 0,
    the gate added no information.
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
RSI_P, OS, OB = 9, 35, 65
FEE, SLIP = 0.00055, 0.0002
RT = 2 * (FEE + SLIP)
GRID = [(0.005, 0.006), (0.010, 0.006), (0.005, 0.005), (0.010, 0.010)]


def htf_trend_on_5m(df5_index, c5_ts, rule):
    """EMA20>EMA50 on the HTF close, forward-filled to 5m, using only closed bars."""
    s = pd.Series(np.nan)
    g = pd.DataFrame(index=df5_index)
    return None  # placeholder (unused)


def load():
    df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    base = pd.DataFrame({"open": df["open"], "high": df["high"], "low": df["low"], "close": df["close"]})
    c = base["close"]
    d = c.diff()
    ag = d.clip(lower=0).ewm(alpha=1/RSI_P, min_periods=RSI_P, adjust=False).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1/RSI_P, min_periods=RSI_P, adjust=False).mean()
    base["rsi"] = 100 - 100/(1 + ag/al.replace(0, np.nan))
    # HTF trend (EMA20>EMA50 on closed HTF bars), as -1/+1 mapped back to 5m
    for tf in ("1h", "4h", "1D"):
        hc = df["close"].resample(tf, label="right", closed="right").last().dropna()
        e20 = hc.ewm(span=20, adjust=False, min_periods=20).mean()
        e50 = hc.ewm(span=50, adjust=False, min_periods=50).mean()
        tr = pd.Series(np.where(e20 > e50, 1.0, -1.0), index=hc.index)
        tr[e20.isna() | e50.isna()] = np.nan
        # the HTF bar labelled at its close-time is known only AT/after that time
        base[f"trend_{tf}"] = tr.reindex(base.index, method="ffill")
    return base.dropna(subset=["rsi"]).reset_index()


def run(o, h, lo, rsi, trend, mode, tp, sl):
    """mode: 'counter' or 'with'. trend: array of -1/+1/nan aligned to bars (or None)."""
    n = len(o); i = 0; rg = []; rn = []
    while i < n - 1:
        side = None
        if rsi[i] <= OS:
            side = "L"
        elif rsi[i] >= OB:
            side = "S"
        if side is not None and mode == "with":
            t = trend[i]
            if np.isnan(t):
                side = None
            elif side == "L" and t < 0:
                side = None
            elif side == "S" and t > 0:
                side = None
        if side is None:
            i += 1; continue
        entry = o[i+1]
        if side == "L":
            tp_px, sl_px = entry*(1+tp), entry*(1-sl)
        else:
            tp_px, sl_px = entry*(1-tp), entry*(1+sl)
        ret = None; j = i + 1
        while j < n:
            bo, bh, bl = o[j], h[j], lo[j]
            if side == "L":
                hsl, htp = bl <= sl_px, bh >= tp_px
                if hsl:
                    ret = (bo if bo <= sl_px else sl_px)/entry - 1; break
                if htp:
                    ret = (bo if bo >= tp_px else tp_px)/entry - 1; break
            else:
                hsl, htp = bh >= sl_px, bl <= tp_px
                if hsl:
                    ret = 1 - (bo if bo >= sl_px else sl_px)/entry; break
                if htp:
                    ret = 1 - (bo if bo <= tp_px else tp_px)/entry; break
            j += 1
        if ret is None:
            break
        rg.append(ret); rn.append(ret - RT)
        i = j + 1
    return np.array(rg), np.array(rn)


if __name__ == "__main__":
    b = load()
    o = b["open"].values; h = b["high"].values; lo = b["low"].values; rsi = b["rsi"].values
    trends = {"with-1h": b["trend_1h"].values, "with-4h": b["trend_4h"].values, "with-1d": b["trend_1D"].values}
    print(f"5m bars {b['timestamp'].iloc[0].date()}->{b['timestamp'].iloc[-1].date()}  N={len(b)}")
    print(f"fees: round-trip {RT*100:.2f}% price\n")
    for tp, sl in GRID:
        wstar = sl/(tp+sl)*100
        print(f"TP {tp*100:.2f}% / SL {sl*100:.2f}%   barrier WR* = {wstar:.1f}%")
        modes = [("counter (no gate)", "counter", None)] + [(k, "with", trends[k]) for k in trends]
        for label, mode, tr in modes:
            rg, rn = run(o, h, lo, rsi, tr, mode, tp, sl)
            if len(rg) == 0:
                print(f"   {label:18s}  no trades"); continue
            wr = (rg > 0).mean()*100
            print(f"   {label:18s}  N={len(rg):6d}  WR={wr:4.1f}%  "
                  f"gross={rg.mean()*100:+.4f}%/t  net={rn.mean()*100:+.4f}%/t")
        print()
