"""btc_base_sweep.py — find the BEST base-crack strategy + TP on BTC history.

User request 2026-06-12 (follow-up to qfl_base_scan.py): sweep the full
design space on BTC 1h, 2019-2026, and report the best cell HONESTLY:
  - pivot window W in {6, 12, 24} bars each side (+3% bounce confirm, 24 bars)
  - crack depth in {1, 2, 3, 5, 8}% below base (resting limit buy)
  - take profit: fixed {0.5, 1, 2, 3, 5}% above entry, or BASE (full revert)
  - stop loss in {4, 8, 15}% below entry; time stop 14d (taker)
Protocol identical to qfl_base_scan.py (no-lookahead base confirm, limit
fills at limit price, entry-bar exits deferred, TP+SL same bar -> SL, maker
0.02% limits / taker 0.055%+0.02% slip market, spot 1x).

Selection discipline: rank by IS (..2023) net/trade with N_IS >= 40, then
report the TOP-10 IS cells WITH their OOS (2024..) results — the OOS column
of the IS-winner is the only number that counts. Also: best-by-OOS shown for
reference (that one is cherry-picked, labeled as such), and zero-fee gross
for the IS winner to show whether ANY gross edge exists.
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_1h.csv"
BOUNCE_MIN = 0.03
BOUNCE_WIN = 24
DEAD_BELOW = 0.25
MAX_HOLD = 336
MAKER, TAKER, SLIP = 0.0002, 0.00055, 0.0002
OOS_START = pd.Timestamp("2024-01-01")

WS = [6, 12, 24]
CRACKS = [0.01, 0.02, 0.03, 0.05, 0.08]
TPS = [0.005, 0.01, 0.02, 0.03, 0.05, None]   # None = full revert to base
SLS = [0.04, 0.08, 0.15]


def load():
    df = pd.read_csv(CSV, parse_dates=["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


def find_bases(df, w):
    lo, hi = df["low"].values, df["high"].values
    n = len(df)
    events = []
    for i in range(w, n - w):
        seg = lo[i - w:i + w + 1]
        if lo[i] != seg.min() or np.argmin(seg) != w:
            continue
        end = min(i + BOUNCE_WIN, n - 1)
        bounce_at = None
        for j in range(i + 1, end + 1):
            if hi[j] >= lo[i] * (1 + BOUNCE_MIN):
                bounce_at = j
                break
        if bounce_at is None:
            continue
        first_ok = max(i + w, bounce_at) + 1
        if first_ok < n:
            events.append((first_ok, lo[i]))
    return events


def run(df, events, crack, tp, sl):
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    ts = df["timestamp"].values
    n = len(df)
    ev_idx = 0
    active = []
    pos = None
    trades = []
    for i in range(n):
        while ev_idx < len(events) and events[ev_idx][0] <= i:
            if events[ev_idx][0] == i:
                active.append(events[ev_idx][1])
                if len(active) > 10:
                    active.pop(0)
            ev_idx += 1
        active = [b for b in active if c[i] > b * (1 - DEAD_BELOW)]

        if pos is None:
            if not active:
                continue
            base = active[-1]
            limit_px = base * (1 - crack)
            if l[i] <= limit_px:
                pos = {"entry": limit_px, "base": base, "bar": i}
                active.pop()
            continue

        if i == pos["bar"]:
            continue
        e = pos["entry"]
        tp_px = pos["base"] if tp is None else e * (1 + tp)
        sl_px = e * (1 - sl)
        out = None
        if l[i] <= sl_px:
            fill = min(sl_px, o[i]) * (1 - SLIP)
            out = ((fill / e - 1), TAKER)
        elif h[i] >= tp_px:
            out = ((tp_px / e - 1), MAKER)
        elif i - pos["bar"] >= MAX_HOLD:
            fill = c[i] * (1 - SLIP)
            out = ((fill / e - 1), TAKER)
        if out:
            gross, fee_out = out
            trades.append({"ts": pd.Timestamp(ts[i]), "gross": gross * 100,
                           "net": (gross - MAKER - fee_out) * 100})
            pos = None
    return trades


def stats(trades, lo_t, hi_t):
    s = [t for t in trades if lo_t <= t["ts"] < hi_t]
    if not s:
        return None
    nt = len(s)
    wr = sum(1 for t in s if t["net"] > 0) / nt * 100
    net = sum(t["net"] for t in s) / nt
    gross = sum(t["gross"] for t in s) / nt
    eq = 1.0
    for t in s:
        eq *= 1 + t["net"] / 100
    return {"n": nt, "wr": wr, "net": net, "gross": gross, "cmp": (eq - 1) * 100}


def main():
    df = load()
    t_lo, t_hi = pd.Timestamp("2000-01-01"), pd.Timestamp("2100-01-01")
    print(f"BTC 1h: {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]} ({len(df):,} bars)")
    base_cache = {w: find_bases(df, w) for w in WS}
    for w in WS:
        print(f"  W={w}: {len(base_cache[w])} confirmed bases")
    rows = []
    for w in WS:
        for crack in CRACKS:
            for tp in TPS:
                for sl in SLS:
                    if tp is not None and tp >= sl:   # need TP < SL? no — allow all
                        pass
                    tr = run(df, base_cache[w], crack, tp, sl)
                    full = stats(tr, t_lo, t_hi)
                    is_s = stats(tr, t_lo, OOS_START)
                    oos = stats(tr, OOS_START, t_hi)
                    if full is None or is_s is None or is_s["n"] < 40:
                        continue
                    rows.append({"w": w, "crack": crack, "tp": tp, "sl": sl,
                                 "full": full, "is": is_s, "oos": oos})
    def tp_lbl(tp):
        return "BASE" if tp is None else f"{tp*100:.1f}%"
    def fmt(r):
        o = r["oos"]
        return (f"W{r['w']:>3} crack {r['crack']*100:>3.0f}% TP {tp_lbl(r['tp']):>5} "
                f"SL {r['sl']*100:>3.0f}% | IS N={r['is']['n']:>4} WR {r['is']['wr']:>5.1f}% "
                f"net {r['is']['net']:>+7.3f}%/tr cmp {r['is']['cmp']:>+8.1f}% | "
                f"OOS N={o['n'] if o else 0:>4} net {o['net'] if o else float('nan'):>+7.3f}%/tr "
                f"cmp {o['cmp'] if o else float('nan'):>+7.1f}% gross {o['gross'] if o else float('nan'):>+7.3f}%")
    rows_is = sorted(rows, key=lambda r: -r["is"]["net"])
    print(f"\n── TOP 10 by IS net/trade (selection set) — OOS column is the verdict ──")
    for r in rows_is[:10]:
        print(fmt(r))
    n_pos_is = sum(1 for r in rows if r["is"]["net"] > 0)
    n_pos_oos = sum(1 for r in rows if r["oos"] and r["oos"]["net"] > 0)
    n_pos_both = sum(1 for r in rows if r["is"]["net"] > 0 and r["oos"] and r["oos"]["net"] > 0)
    print(f"\nCells: {len(rows)} | IS-positive: {n_pos_is} | OOS-positive: {n_pos_oos} "
          f"| positive BOTH: {n_pos_both}")
    rows_oos = sorted([r for r in rows if r["oos"]], key=lambda r: -r["oos"]["net"])
    print(f"\n── best 5 by OOS (cherry-picked AFTER seeing OOS — reference only) ──")
    for r in rows_oos[:5]:
        print(fmt(r))
    if rows_is:
        b = rows_is[0]
        zf = b["full"]
        print(f"\nIS-winner zero-fee gross full-sample: {zf['gross']:+.3f}%/trade "
              f"(net {zf['net']:+.3f}%) — fees {'are NOT' if zf['gross'] < 0 else 'may be'} the binding issue.")


if __name__ == "__main__":
    main()
