"""qfl_base_scan.py — honest backtest of a QFL/Crypto-Base-Scanner strategy.

User request 2026-06-12: build a "crypto base scanner". Before trusting its
alerts, test the underlying trade honestly: BASES = support levels where
price pivoted and bounced; CRACK = price dropping x% below a base; classic
play = buy the crack with a resting limit, sell the bounce back at the base.

PROTOCOL (pre-registered, FINDINGS.md checklist):
  - 1h closed bars, BTC/ETH/SOL/BNB (Bybit linear history in data/cache).
  - Base: pivot low at bar i (low[i] = min of lows in i-W..i+W, W=12) that
    BOUNCED >= 3% within 24 bars after the pivot. The base only becomes
    tradeable after bar i+W+1 (pivot needs W future bars to confirm — no
    lookahead). Base level = the pivot low. Newest base supersedes older
    ones; a base is retired after one trade on it or if price closes 25%
    below it (dead support).
  - Entry: resting limit at base*(1-crack). Fills when bar low <= limit
    (resting limit fills AT the limit price). Entry bar's own TP touch is
    DEFERRED to later bars (no wick-order lookahead).
  - Exit: TP = resting limit back at the base level (maker). SL = stop at
    entry*(1-sl): trigger on low, fill at worse(stop, open) + slip (taker).
    TP+SL same bar -> SL. Time stop: market-out at close after MAX_HOLD (taker).
  - Fees: maker 0.02% on limit fills (entry + TP), taker 0.055% + 0.02% slip
    on stop/time exits. ZERO-cost gross also reported.
  - 1 concurrent position per asset, spot semantics (1x, no liquidation).
  - IS = ..2023-12 (selection), OOS = 2024-01.. (report only).
GRID (fixed a priori): crack in {3, 5, 8}%, SL in {8, 15}%, MAX_HOLD 336 (14d).
"""
import numpy as np
import pandas as pd

ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
CACHE = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/{}_1h.csv"
W = 12                 # pivot half-window (hours)
BOUNCE_MIN = 0.03      # pivot must bounce 3% within 24 bars to count as a base
BOUNCE_WIN = 24
DEAD_BELOW = 0.25      # base retired if close < base*(1-25%)
MAX_HOLD = 336         # 14 days
MAKER, TAKER, SLIP = 0.0002, 0.00055, 0.0002
OOS_START = pd.Timestamp("2024-01-01")
CRACKS = [0.03, 0.05, 0.08]
SLS = [0.08, 0.15]


def load(sym):
    df = pd.read_csv(CACHE.format(sym), parse_dates=["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


def find_bases(df):
    """Return array: base_confirmed_at_bar -> base level. A pivot low at i is
    confirmed at i+W (window closed) AND requires a >=3% bounce within 24 bars
    of the pivot — so tradeable only from max(i+W, i+bounce_bar)+1 onward."""
    lo, hi = df["low"].values, df["high"].values
    n = len(df)
    events = []   # (first_tradeable_bar, level)
    for i in range(W, n - W):
        seg = lo[i - W:i + W + 1]
        if lo[i] != seg.min():
            continue
        if np.argmin(seg) != W:        # unique leftmost min must be the center
            continue
        # bounce check within BOUNCE_WIN bars after pivot
        end = min(i + BOUNCE_WIN, n - 1)
        bounce_at = None
        for j in range(i + 1, end + 1):
            if hi[j] >= lo[i] * (1 + BOUNCE_MIN):
                bounce_at = j
                break
        if bounce_at is None:
            continue
        first_ok = max(i + W, bounce_at) + 1
        if first_ok < n:
            events.append((first_ok, lo[i]))
    return events


def run_asset(df, crack, sl):
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    ts = df["timestamp"].values
    n = len(df)
    events = find_bases(df)
    ev_idx = 0
    active = []            # base levels, newest last
    pos = None
    trades = []
    for i in range(n):
        # activate newly confirmed bases
        while ev_idx < len(events) and events[ev_idx][0] == i:
            active.append(events[ev_idx][1])
            ev_idx += 1
            if len(active) > 10:
                active.pop(0)
        # retire dead bases
        active = [b for b in active if c[i] > b * (1 - DEAD_BELOW)]

        if pos is None:
            if not active:
                continue
            base = active[-1]                  # newest base
            limit_px = base * (1 - crack)
            if l[i] <= limit_px:
                pos = {"entry": limit_px, "base": base, "bar": i, "ts": ts[i]}
                active.pop()                   # one trade per base
            continue

        # manage open position (entry bar exits deferred: i > pos["bar"])
        if i == pos["bar"]:
            continue
        e = pos["entry"]
        tp_px = pos["base"]
        sl_px = e * (1 - sl)
        out = None
        sl_hit = l[i] <= sl_px
        tp_hit = h[i] >= tp_px
        if sl_hit:                              # pessimistic when both
            fill = min(sl_px, o[i]) * (1 - SLIP)
            out = ((fill / e - 1), "SL", TAKER)
        elif tp_hit:
            out = ((tp_px / e - 1), "TP", MAKER)
        elif i - pos["bar"] >= MAX_HOLD:
            fill = c[i] * (1 - SLIP)
            out = ((fill / e - 1), "TIME", TAKER)
        if out:
            gross, reason, fee_out = out
            net = gross - MAKER - fee_out       # entry maker + exit fee
            trades.append({"ts": pd.Timestamp(ts[i]), "gross": gross * 100,
                           "net": net * 100, "reason": reason,
                           "hold_h": i - pos["bar"]})
            pos = None
    return trades


def seg(trades, lo_t, hi_t):
    s = [t for t in trades if lo_t <= t["ts"] < hi_t]
    if not s:
        return None
    nt = len(s)
    wr = sum(1 for t in s if t["net"] > 0) / nt * 100
    g = sum(t["gross"] for t in s) / nt
    net = sum(t["net"] for t in s) / nt
    eq = 1.0
    peak = 1.0
    dd = 0.0
    for t in s:
        eq *= 1 + t["net"] / 100
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak)
    return nt, wr, g, net, (eq - 1) * 100, dd * 100


def main():
    data = {s: load(s) for s in ASSETS}
    t_lo = pd.Timestamp("2000-01-01")
    t_hi = pd.Timestamp("2100-01-01")
    print(f"{'asset':<9}{'crack':>6}{'SL':>5} | {'N':>5}{'WR%':>6}{'net/tr%':>9}"
          f"{'compound%':>10}{'maxDD%':>8} | {'IS net':>8}{'OOS N':>6}{'OOS net':>9}"
          f"{'OOS cmp%':>9}  verdict")
    port = {}
    for crack in CRACKS:
        for sl in SLS:
            all_tr = []
            for sym in ASSETS:
                tr = run_asset(data[sym], crack, sl)
                all_tr.extend(tr)
                full = seg(tr, t_lo, t_hi)
                if full is None:
                    continue
                nt, wr, g, net, cmp_, dd = full
                is_s = seg(tr, t_lo, OOS_START)
                oos = seg(tr, OOS_START, t_hi)
                isn = is_s[3] if is_s else float("nan")
                oosn = oos[3] if oos else float("nan")
                oosN = oos[0] if oos else 0
                ooscmp = oos[4] if oos else float("nan")
                verdict = ("POSITIVE" if net > 0 and (oos and oos[3] > 0) else
                           "IS-only" if net > 0 else "dead")
                print(f"{sym:<9}{crack*100:>5.0f}%{sl*100:>4.0f}% | {nt:>5}{wr:>6.1f}"
                      f"{net:>+9.3f}{cmp_:>+10.1f}{dd:>8.1f} | {isn:>+8.3f}{oosN:>6}"
                      f"{oosn:>+9.3f}{ooscmp:>+9.1f}  {verdict}")
            all_tr.sort(key=lambda t: t["ts"])
            p = seg(all_tr, t_lo, t_hi)
            po = seg(all_tr, OOS_START, t_hi)
            if p:
                print(f"{'PORT':<9}{crack*100:>5.0f}%{sl*100:>4.0f}% | {p[0]:>5}{p[1]:>6.1f}"
                      f"{p[3]:>+9.3f}{'':>10}{'':>8} | {'':>8}{po[0] if po else 0:>6}"
                      f"{po[3] if po else float('nan'):>+9.3f}{'':>9}  "
                      f"{'POSITIVE' if p[3] > 0 and po and po[3] > 0 else 'mixed'}")
            print()


if __name__ == "__main__":
    main()
