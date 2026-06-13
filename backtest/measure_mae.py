"""measure_mae.py — where can the SL sit WITHOUT blocking winners? (user 2026-06-12)

Idea: keep a stop (catastrophe protection) but place it beyond where winning
trades normally dip against us, so it cuts losers without killing winners.
Tool = Maximum Adverse Excursion (MAE): for each trade, the worst % the price
went against entry before exit. Compare winners' MAE vs losers' MAE.

Faithful v2.2 entry (uses fresh_honest's exact rsi/gap/atr/trend, gap filter ON,
counter-trend), single position, NO tight SL while measuring (wide 5%
catastrophe + 4h timeout) so we see winners' NATURAL adverse dip. TP 0.5%.
Then a small SL sweep with honest fees to confirm whether a winner-respecting
SL reaches profit.
"""
import numpy as np
import fresh_honest as fh

bt = fh.prep()
O, H, L = bt["open"].values, bt["high"].values, bt["low"].values
RSI, ATR, GAP, TR = bt["rsi"].values, bt["atr_pct"].values, bt["gap"].values, bt["trend"].values
n = len(bt)
FEE, SLIP = 0.00055, 0.0002
RT = 2*(FEE+SLIP)


def sim(tp, sl, record_mae=False):
    """single position, v2.2 entry. returns (rets, outcomes, maes)."""
    rets, outc, maes = [], [], []
    i = 1
    while i < n-1:
        # entry gate at closed bar i (counter-trend: pure RSI + gap + ATR)
        if np.isnan(RSI[i]) or np.isnan(ATR[i]) or np.isnan(GAP[i]) or np.isnan(TR[i]):
            i += 1; continue
        if ATR[i] > 0.80 or GAP[i] < 0.0020:
            i += 1; continue
        side = 1 if RSI[i] <= 35 else (-1 if RSI[i] >= 65 else 0)
        if side == 0:
            i += 1; continue
        entry = O[i+1]
        tp_px = entry*(1+tp*side)
        sl_px = entry*(1-sl*side)
        mae = 0.0; ret = None; won = False; j = i+1
        timeout = j + 48
        while j < n:
            adv = (L[j] if side == 1 else H[j])           # worst price this bar
            cur_mae = (entry-adv)/entry if side == 1 else (adv-entry)/entry
            mae = max(mae, cur_mae)
            # pessimistic: stop before TP within the bar
            if (side == 1 and L[j] <= sl_px) or (side == -1 and H[j] >= sl_px):
                ret = -sl - RT; won = False; break
            if (side == 1 and H[j] >= tp_px) or (side == -1 and L[j] <= tp_px):
                ret = tp - RT; won = True; break
            if j >= timeout:                               # 4h time exit at close
                c = bt["close"].values[j]
                ret = ((c-entry)/entry*side) - RT; won = ret > 0; break
            j += 1
        if ret is None:
            break
        rets.append(ret); outc.append(won); maes.append(mae*100)
        i = j+1
    return np.array(rets), np.array(outc), np.array(maes)


if __name__ == "__main__":
    # Measure MAE with a WIDE stop so winners show their natural dip
    rets, won, mae = sim(tp=0.005, sl=0.05, record_mae=True)
    wmae, lmae = mae[won], mae[~won]
    print(f"v2.2 entries, TP 0.5%, wide 5% stop. trades={len(mae):,}  winners={won.sum():,}\n")
    print("Maximum Adverse Excursion (how far price went AGAINST us before exit):")
    print(f"  {'percentile':>12} {'WINNERS':>9} {'LOSERS':>9}")
    for p in (50, 75, 90, 95, 99):
        print(f"  {p:>11}% {np.percentile(wmae,p):>8.2f}% {np.percentile(lmae,p):>8.2f}%")
    print(f"\n  winners' MEAN MAE {wmae.mean():.2f}%   losers' MEAN MAE {lmae.mean():.2f}%")
    print("\nShare of WINNERS that would be KILLED by a stop at each level:")
    for s in (0.3, 0.4, 0.5, 0.6, 0.8, 1.0):
        killed = (wmae > s).mean()*100
        print(f"  SL {s:.1f}%  ->  kills {killed:4.1f}% of winners")

    print("\nNet expectancy at each SL (honest fees) — does a winner-safe SL win?")
    print(f"  {'SL':>5} {'trades':>7} {'WR':>6} {'net%/trade':>11}")
    for s in (0.3, 0.5, 0.6, 0.8, 1.0, 1.5):
        r, w, _ = sim(tp=0.005, sl=s/100)
        print(f"  {s:>4.1f}% {len(r):>7} {w.mean()*100:>5.1f}% {r.mean()*100:>+10.4f}%")
