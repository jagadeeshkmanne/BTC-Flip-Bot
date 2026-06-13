"""bag_avoidance.py — can the martingale's open bag be avoided? (user 2026-06-12)

Levers tested on the BEST spot config from martingale_popular.py
(dev 1% / mult 2.0 / 7 SOs / TP 2%, 1x spot, full-ladder capitalized):
  - round SL: realize the bag at avg*(1-5%) or avg*(1-10%)  [bag -> losses]
  - trend gate: only START rounds while price > 200d SMA     [fewer bags]
  - entry: ALWAYS re-enter vs fresh RSI14<30 signal
Reference lines: BTC buy-hold; no-SL baseline with its end bag.
"""
import pandas as pd
import martingale_popular as mp

DEV, MULT, NSO, TP = 0.01, 2.0, 7, 0.02
OOS = pd.Timestamp("2024-01-01")


def main():
    df = mp.load()
    bh = mp.INITIAL * df["close"].iloc[-1] / df["close"].iloc[0]
    print(f"BTC 1h {df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()} | "
          f"buy-hold ${bh:,.0f} | config dev{DEV*100:.0f}% mult{MULT} SO{NSO} TP{TP*100:.0f}% spot 1x")
    print(f"\n{'entry':<7}{'trend':>6}{'SL':>6} | {'rounds':>7}{'WR%':>6}{'worst rd$':>10}"
          f"{'final$':>9}{'mtmDD%':>8}{'bag$':>9} | {'OOS pnl$':>9}")
    for rsi_gate in (False, True):
        for trend_gate in (False, True):
            for round_sl in (None, 0.05, 0.10):
                r = mp.run(df, DEV, MULT, NSO, TP, rsi_gate=rsi_gate, lev=1.0,
                           round_sl=round_sl, trend_gate=trend_gate)
                rds = r["rounds"]
                nt = len(rds)
                wr = (sum(1 for x in rds if x["pnl"] > 0) / nt * 100) if nt else 0
                worst = min((x["pnl"] for x in rds), default=0.0)
                closed = mp.INITIAL + sum(x["pnl"] for x in rds)
                bag = r["end"] - closed
                oos_pnl = sum(x["pnl"] for x in rds if x["ts"] >= OOS)
                print(f"{'RSI<30' if rsi_gate else 'ALWAYS':<7}"
                      f"{'>SMA' if trend_gate else '-':>6}"
                      f"{f'{round_sl*100:.0f}%' if round_sl else '-':>6} | "
                      f"{nt:>7}{wr:>6.1f}{worst:>+10.0f}{r['end']:>9,.0f}"
                      f"{r['max_dd']:>8.1f}{bag:>+9.0f} | {oos_pnl:>+9.0f}")


if __name__ == "__main__":
    main()
