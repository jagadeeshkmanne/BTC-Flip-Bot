"""rejection_trailing.py — rejection entry + trailing exits, honest. (user 2026-06-12)

Entry: NO-confirmation fade of an N-bar swing extreme (the best variant from
rejection_confirm_short.py): short the bar after a new N-bar high, long after a
new N-bar low. One position at a time.

Exits compared (let-winners-run via trailing):
  fixed TP/SL          — baseline (what v2.2 does)
  trailing stop        — hard initial SL; once price moves A% in favor, a stop
                         trails the best price by T% and ratchets (no TP cap)
  trail + TP cap       — same, but also bank at a TP ceiling

Honest fills: pessimistic intrabar (adverse/stop checked before favorable update;
gap through stop fills at the open). Entry+exit slip; stop/trailing exits are
MARKET (taker). Reported gross (0 fee) and net (taker 0.055%/side + 0.02% slip).
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
# precompute rolling N-bar extremes ending at each bar (closed-bar, no lookahead)
roll_hi = pd.Series(H).rolling(N).max().values
roll_lo = pd.Series(L).rolling(N).min().values


def simulate(cfg):
    rets, holds = [], []
    i = N
    while i < n - 1:
        side = 0
        if H[i] >= roll_hi[i]:      side = -1     # new N-bar high -> short
        elif L[i] <= roll_lo[i]:    side = 1      # new N-bar low  -> long
        if side == 0:
            i += 1; continue
        entry = O[i+1] * (1 - SLIP) if side == -1 else O[i+1] * (1 + SLIP)
        sl_init = cfg.get("sl", cfg.get("sl_init"))
        hard_sl = entry * (1 + sl_init) if side == -1 else entry * (1 - sl_init)
        tp_px = None
        if cfg.get("tp"):
            tp_px = entry * (1 - cfg["tp"]) if side == -1 else entry * (1 + cfg["tp"])
        trailing = cfg["type"] in ("trail", "trail_tp")
        A = cfg.get("act", 0.0); T = cfg.get("trail", 0.0)
        best = entry                      # best favorable price
        activated = False
        eff_stop = hard_sl
        ret = None; j = i + 1
        while j < n:
            bo, bh, bl = O[j], H[j], L[j]
            # 1) adverse / stop check first (pessimistic)
            if side == -1:
                if bh >= eff_stop:
                    fill = max(bo, eff_stop)              # gap up through stop -> open
                    ret = 1 - fill * (1 + SLIP) / entry; break
                if tp_px is not None and bl <= tp_px:
                    ret = 1 - tp_px / entry; break        # limit TP (favorable)
            else:
                if bl <= eff_stop:
                    fill = min(bo, eff_stop)
                    ret = fill * (1 - SLIP) / entry - 1; break
                if tp_px is not None and bh >= tp_px:
                    ret = tp_px / entry - 1; break
            # 2) update favorable peak + ratchet trailing stop
            if trailing:
                if side == -1:
                    best = min(best, bl)
                    if not activated and best <= entry * (1 - A):
                        activated = True
                    if activated:
                        eff_stop = min(eff_stop, best * (1 + T))   # ratchet down
                else:
                    best = max(best, bh)
                    if not activated and best >= entry * (1 + A):
                        activated = True
                    if activated:
                        eff_stop = max(eff_stop, best * (1 - T))
            j += 1
        if ret is None:
            break
        rets.append(ret); holds.append(j - i)
        i = j + 1
    return np.array(rets), np.array(holds)


def report(cfg, label):
    r, hold = simulate(cfg)
    if len(r) == 0:
        print(f"  {label:34s} no trades"); return
    wr = (r > 0).mean() * 100
    net = r - RT
    eqg = np.prod(1 + r); eqn = np.prod(1 + net)      # spot 1x compounded
    wins = r[r > 0].mean() * 100 if (r > 0).any() else 0
    loss = r[r <= 0].mean() * 100 if (r <= 0).any() else 0
    print(f"  {label:34s} N={len(r):5d} WR={wr:4.1f}% medHold={np.median(hold):3.0f} "
          f"avgW={wins:+.2f}% avgL={loss:+.2f}%  gross={r.mean()*100:+.4f}%  net={net.mean()*100:+.4f}%/t")


if __name__ == "__main__":
    print(f"Rejection entry (no-confirm, N={N}), both sides. {df['timestamp'].iloc[0].date()}"
          f"->{df['timestamp'].iloc[-1].date()}  taker RT {RT*100:.2f}%\n")
    print("— FIXED TP/SL (baseline: take the small profit) —")
    for tp, sl in [(0.005, 0.006), (0.010, 0.006), (0.010, 0.010), (0.020, 0.010)]:
        report({"type": "fixed", "tp": tp, "sl": sl}, f"fixed TP {tp*100:.1f}% / SL {sl*100:.1f}%")
    print("\n— TRAILING STOP (let winners run, no TP cap) —")
    for A in (0.002, 0.003, 0.005):
        for Tr in (0.002, 0.003, 0.005):
            report({"type": "trail", "sl_init": 0.006, "act": A, "trail": Tr},
                   f"trail: activate {A*100:.1f}% / trail {Tr*100:.1f}%")
    print("\n— TRAIL + TP CAP (run, but bank by 2%) —")
    for Tr in (0.003, 0.005):
        report({"type": "trail_tp", "sl_init": 0.006, "act": 0.003, "trail": Tr, "tp": 0.020},
               f"trail {Tr*100:.1f}% + TP cap 2.0%")
