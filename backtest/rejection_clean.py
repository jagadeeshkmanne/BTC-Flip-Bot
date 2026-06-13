"""rejection_clean.py — lookahead-FREE rejection entry, settling the edge.

Earlier (rejection_confirm_short.py) the z=0 'fade the high' showed gross
+0.08%/t — but its entry decided 're-arm vs enter' using bar j's HIGH and then
entered at bar j's OPEN: a 1-bar lookahead. This re-tests with strictly
closed-bar decisions:

  naive : swing high at bar i (N-bar max, known at close i) -> SHORT at open[i+1].
  stall : swing high at i AND bar i+1 did NOT exceed it (known at close i+1)
          -> SHORT at open[i+2].   (a real 'the high was rejected' confirmation)

Both decisions use only already-closed bars; entry is the NEXT open. Mirror for
longs at swing lows. Clean accounting: gross = pure price return (no slip),
net = gross - RT (RT = 2*(0.055% taker + 0.02% slip)). Exits: fixed TP/SL, and
a trailing stop on the stall entry (does letting winners run help?).
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
FEE, SLIP = 0.00055, 0.0002
RT = 2 * (FEE + SLIP)
N = 24

df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
O, H, L = df["open"].values, df["high"].values, df["low"].values
n = len(O)
roll_hi = pd.Series(H).rolling(N).max().shift(1).values   # N-bar max of bars BEFORE i
roll_lo = pd.Series(L).rolling(N).min().shift(1).values


def gen_entries(mode):
    """yield (entry_bar, side) using only closed-bar info. entry filled at open[entry_bar]."""
    out = []
    i = N
    while i < n - 2:
        new_hi = H[i] > roll_hi[i]      # bar i is a fresh N-bar high (vs prior N)
        new_lo = L[i] < roll_lo[i]
        if mode == "naive":
            if new_hi:   out.append((i + 1, -1))
            elif new_lo: out.append((i + 1, +1))
        else:  # stall: require the NEXT bar to fail to extend, enter the bar after
            if new_hi and H[i + 1] <= H[i]:   out.append((i + 2, -1))
            elif new_lo and L[i + 1] >= L[i]: out.append((i + 2, +1))
        i += 1
    return out


def manage(entries, cfg):
    rets, holds = [], []
    last_exit = -1
    for eb, side in entries:
        if eb <= last_exit or eb >= n:
            continue                       # one position at a time
        entry = O[eb]
        hard_sl = entry * (1 + cfg["sl"]) if side == -1 else entry * (1 - cfg["sl"])
        tp_px = None
        if cfg.get("tp"):
            tp_px = entry * (1 - cfg["tp"]) if side == -1 else entry * (1 + cfg["tp"])
        trailing = cfg["type"] == "trail"
        A, T = cfg.get("act", 0.0), cfg.get("trail", 0.0)
        best = entry; activated = False; eff = hard_sl
        ret = None; j = eb
        while j < n:
            bh, bl, bo = H[j], L[j], O[j]
            if side == -1:
                if bh >= eff:
                    ret = 1 - max(bo, eff) / entry; break
                if tp_px is not None and bl <= tp_px:
                    ret = 1 - tp_px / entry; break
            else:
                if bl <= eff:
                    ret = max(bo, eff) / entry - 1 if False else min(bo, eff) / entry - 1; break
                if tp_px is not None and bh >= tp_px:
                    ret = tp_px / entry - 1; break
            if trailing:
                if side == -1:
                    best = min(best, bl)
                    if best <= entry * (1 - A): activated = True
                    if activated: eff = min(eff, best * (1 + T))
                else:
                    best = max(best, bh)
                    if best >= entry * (1 + A): activated = True
                    if activated: eff = max(eff, best * (1 - T))
            j += 1
        if ret is None:
            break
        rets.append(ret); holds.append(j - eb); last_exit = j
    return np.array(rets), np.array(holds)


def show(entries, cfg, label):
    r, hold = manage(entries, cfg)
    if len(r) == 0:
        print(f"  {label:30s} no trades"); return
    print(f"  {label:30s} N={len(r):5d} WR={(r>0).mean()*100:4.1f}% "
          f"medHold={np.median(hold):3.0f}  gross={r.mean()*100:+.4f}%  net={(r.mean()-RT)*100:+.4f}%/t")


if __name__ == "__main__":
    print(f"Lookahead-free rejection, N={N}, both sides, {df['timestamp'].iloc[0].date()}"
          f"->{df['timestamp'].iloc[-1].date()}  taker RT {RT*100:.2f}%\n")
    for mode in ("naive", "stall"):
        ent = gen_entries(mode)
        print(f"===== entry = {mode}  ({len(ent):,} raw signals) =====")
        for tp, sl in [(0.005, 0.006), (0.010, 0.010), (0.020, 0.010)]:
            show(ent, {"type": "fixed", "tp": tp, "sl": sl}, f"fixed TP{tp*100:.1f}/SL{sl*100:.1f}")
        if mode == "stall":
            for A in (0.002, 0.003):
                for T in (0.002, 0.003, 0.005):
                    show(ent, {"type": "trail", "sl": 0.006, "act": A, "trail": T},
                         f"trail act{A*100:.1f}/trail{T*100:.1f}")
        print()
