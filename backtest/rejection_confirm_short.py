"""rejection_confirm_short.py — does WAITING FOR CONFIRMATION help? (user 2026-06-12)

User point: v2.2 shorts the instant RSI hits 65 — no confirmation the top is in.
Their idea (a "swing-failure / rejection" entry): wait for price to make a high,
then enter SHORT only once it has actually turned back down (e.g. 64.0k -> 63.8k).

Test: short after an N-bar swing high, requiring a pullback of Z% from the high
to CONFIRM the rejection before entry. Z=0 = NO confirmation (fade the high
immediately, v2.2-style). If confirmation helps, Z>0 should beat Z=0.

  - Detect N-bar high on a CLOSED bar (no lookahead). Arm.
  - From the next bar, watch for price to trade Z% below the high -> SHORT
    (honest fill at the trigger, or the open if it gaps through). If a higher
    high forms first, the high moves up (re-arm). Abandon if no pullback in
    MAXWAIT bars.
  - Mirror LONG at swing lows (bounce-confirm) for completeness.
  - Exit TP/SL, honest fills, real taker fee + slip. 5m BTC, 2019-2026.
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
FEE, SLIP = 0.00055, 0.0002
RT = 2 * (FEE + SLIP)
N = 24          # swing-high lookback (2h on 5m)
MAXWAIT = 24    # give up if no confirming pullback within 2h
Z_GRID = [0.0, 0.001, 0.002, 0.003, 0.005]   # confirmation pullback %
TPSL = [(0.005, 0.006), (0.010, 0.010)]


def load():
    df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    return df


def run(o, h, l, direction, z, tp, sl):
    """direction=-1 short@highs, +1 long@lows. z=confirmation pullback fraction."""
    n = len(o)
    rg = []
    i = N
    while i < n - 1:
        # swing extreme over the closed window ending at i
        if direction == -1:
            ext = h[i-N+1:i+1].max()
            is_new = h[i] >= ext
        else:
            ext = l[i-N+1:i+1].min()
            is_new = l[i] <= ext
        if not is_new:
            i += 1; continue
        # armed at extreme E; wait up to MAXWAIT bars for a z% pullback to confirm
        E = ext
        entered = None
        j = i + 1
        while j < n and (j - i) <= MAXWAIT:
            if direction == -1:
                if h[j] > E:                 # higher high -> move the high up, re-arm
                    E = h[j]; i = j; break
                trig = E * (1 - z)
                if l[j] <= trig:
                    entered = min(o[j], trig) if o[j] <= trig else trig
                    break
            else:
                if l[j] < E:
                    E = l[j]; i = j; break
                trig = E * (1 + z)
                if h[j] >= trig:
                    entered = max(o[j], trig) if o[j] >= trig else trig
                    break
            j += 1
        else:
            i = i + 1; continue
        if entered is None:
            i = j + 1 if j < n else n
            continue
        # manage TP/SL from bar j onward, honest fills
        entry = entered
        if direction == -1:
            tp_px, sl_px = entry*(1-tp), entry*(1+sl)
        else:
            tp_px, sl_px = entry*(1+tp), entry*(1-sl)
        ret = None; k = j
        while k < n:
            bo, bh, bl = o[k], h[k], l[k]
            if direction == -1:
                if bh >= sl_px:
                    ret = 1 - (bo if bo >= sl_px else sl_px)/entry; break
                if bl <= tp_px:
                    ret = 1 - (bo if bo <= tp_px else tp_px)/entry; break
            else:
                if bl <= sl_px:
                    ret = (bo if bo <= sl_px else sl_px)/entry - 1; break
                if bh >= tp_px:
                    ret = (bo if bo >= tp_px else tp_px)/entry - 1; break
            k += 1
        if ret is None:
            break
        rg.append(ret)
        i = k + 1
    return np.array(rg)


if __name__ == "__main__":
    df = load()
    o, h, l = df["open"].values, df["high"].values, df["low"].values
    print(f"5m BTC {df['timestamp'].iloc[0].date()}->{df['timestamp'].iloc[-1].date()}  "
          f"swing-high lookback {N} bars, fees RT {RT*100:.2f}%\n")
    for dirn, dlabel in [(-1, "SHORT @ highs (your idea)"), (1, "LONG @ lows (mirror)")]:
        print(f"===== {dlabel} =====")
        for tp, sl in TPSL:
            wstar = sl/(tp+sl)*100
            print(f"  TP {tp*100:.1f}% / SL {sl*100:.1f}%  (barrier WR* {wstar:.0f}%)")
            for z in Z_GRID:
                r = run(o, h, l, dirn, z, tp, sl)
                conf = "NO confirm" if z == 0 else f"confirm {z*100:.1f}% pullback"
                if len(r) == 0:
                    print(f"    {conf:22s} no trades"); continue
                wr = (r > 0).mean()*100
                print(f"    {conf:22s} N={len(r):6d}  WR={wr:4.1f}%  "
                      f"gross={r.mean()*100:+.4f}%/t  net={(r.mean()-RT)*100:+.4f}%/t")
            print()
