"""l1_trail_sweep.py — does an L1 trailing stop (or no-DCA variant) rescue v2.2?

User idea 2026-06-12: 'so many L1 trades go positive — save that profit.'
Tests the deployed v2.2 config (honest fills, fixed-stake ledger) with an L1
trail that arms at +arm% (close-sampled peak, like the 1-min poller) and
trails buf% behind the peak. Side effect under test: an armed trail makes the
DCA unreachable, cutting off the martingale tail at the source.

Prediction on record (FINDINGS.md #2): entries carry no information, so any
barrier geometry lands at zero-fee PF 0.92-1.01 and loses after fees. If any
cell beats that band, walk-forward before believing it.
"""
import numpy as np
import fresh_honest as fh

FEES = {
    "0-fee": dict(fee=0.0, slip=0.0),
    "real ": dict(fee=0.00055, slip=0.0002),
}
VARIANTS = [
    ("baseline (as deployed)",      dict()),
    ("trail 0.15/0.05",             dict(l1_arm=0.15, l1_buf=0.05)),
    ("trail 0.20/0.05",             dict(l1_arm=0.20, l1_buf=0.05)),
    ("trail 0.20/0.10",             dict(l1_arm=0.20, l1_buf=0.10)),
    ("trail 0.30/0.05",             dict(l1_arm=0.30, l1_buf=0.05)),
    ("trail 0.30/0.10",             dict(l1_arm=0.30, l1_buf=0.10)),
    ("trail 0.40/0.10",             dict(l1_arm=0.40, l1_buf=0.10)),
    ("no-DCA",                      dict(use_dca=False)),
    ("no-DCA + trail 0.30/0.10",    dict(use_dca=False, l1_arm=0.30, l1_buf=0.10)),
]
SPLIT = np.datetime64("2023-01-01")


def main():
    bt = fh.prep()
    print(f"Data: {bt['timestamp'].iloc[0]} -> {bt['timestamp'].iloc[-1]}  ({len(bt):,} bars)")
    print(f"Config: v2.2 (TP_DCA 1.00%, time-SL 144) | honest fills | SL 0.6% | "
          f"fixed-stake ${fh.PER_LEG_NOTIONAL:,.0f}/leg ledger\n")
    print(f"{'variant':<28}{'fees':<7}{'trades':>7}{'WR%':>6}{'PF':>7}{'avg$':>8}"
          f"{'total$':>10}{'19-22$':>10}{'23-26$':>10}  exits")
    for vname, vkw in VARIANTS:
        for fname, fkw in FEES.items():
            r = fh.run(bt, tp_dca=0.0100, time_sl=144, fill="honest",
                       fee_maker=None, sl_l1=0.006, fixed_cap=True, **fkw, **vkw)
            nets = np.array([t[0] for t in r["trades"]])
            tss = np.array([np.datetime64(t[2]) for t in r["trades"]])
            w = int((nets > 1e-9).sum()); lo = int((nets < -1e-9).sum())
            gw, gl = nets[nets > 0].sum(), nets[nets < 0].sum()
            pf = abs(gw / gl) if gl < 0 else float("inf")
            h1 = nets[tss < SPLIT].sum(); h2 = nets[tss >= SPLIT].sum()
            ex = " ".join(f"{k}={v}" for k, v in r["exits"].items() if v)
            print(f"{vname:<28}{fname:<7}{len(nets):>7,}{w/max(w+lo,1)*100:>6.1f}{pf:>7.3f}"
                  f"{nets.mean() if len(nets) else 0:>8.2f}{nets.sum():>10,.0f}"
                  f"{h1:>10,.0f}{h2:>10,.0f}  {ex}")


if __name__ == "__main__":
    main()
