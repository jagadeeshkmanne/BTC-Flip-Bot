"""What if stops/TPs are EXCHANGE-RESIDENT orders instead of poll+market?

Models: conditional stop-market resting at the exchange -> triggers on touch,
fills ~at the stop price (legacy fill) + slippage; TP as resting limit.
Run with REAL fees. This is the best-case for the strategy: it grants the
stop-price fill the paper bot books, but charges real fees + slip.
"""
import pandas as pd
import live_faithful as lf

df = pd.read_csv(lf.CSV_PATH, parse_dates=["timestamp"])
bt = lf.prep(df)
print(f"Data: {bt['timestamp'].iloc[0]} -> {bt['timestamp'].iloc[-1]}  ({len(bt):,} bars)")

RUNS = [
    # label, fee, slip, compound, stop_fill
    ("EXCH-RESTING taker-fees compounded", 0.00055, 0.0002, True,  "legacy"),
    ("EXCH-RESTING taker-fees fixed-$5K",  0.00055, 0.0002, False, "legacy"),
    # generous variant: maker fee (0.02%) on EVERY fill, zero slip —
    # unrealistically kind (stop exits are taker by definition), upper bound
    ("EXCH-RESTING all-maker no-slip $5K", 0.00020, 0.0,    False, "legacy"),
]

for name, cfg in lf.CONFIGS.items():
    for label, fee, slip, compound, sfill in RUNS:
        r = lf.run(bt, cfg["tp_dca"], cfg["time_sl_bars"], fee, slip,
                   compound=compound, tp_close_through=False, stop_fill=sfill)
        lf.report(name, label, r)
