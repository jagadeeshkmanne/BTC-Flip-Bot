#!/usr/bin/env python3
"""Fine-tune TP, SL, leverage, pyramid, and timeline for repaired MTF engulfing strategy.

This intentionally starts from the safer repaired family, not the advertised 2x SL-flip
version that produced extreme drawdowns.
"""
from __future__ import annotations

from itertools import product

from backtest_mtf_engulfing_flip_pyramid import Config, ROOT, add_features, load_1h, metrics, run


def eval_cfg(feature_cache: dict, split: int, cfg: Config):
    key = (cfg.sl_max_pct, cfg.sl_buf_pct, cfg.vol_spike_ratio)
    df, ins_df, oos_df = feature_cache[key]
    eq, tr = run(df, cfg)
    full = metrics(eq, tr)
    ins_eq, ins_tr = run(ins_df, cfg)
    oos_eq, oos_tr = run(oos_df, cfg)
    ins = metrics(ins_eq, ins_tr)
    oos = metrics(oos_eq, oos_tr)
    return full, ins, oos


def score(full: dict, ins: dict, oos: dict) -> float:
    if full["trades"] < 35 or ins["trades"] < 15 or oos["trades"] < 12:
        return -1e9
    if min(full["pf"], ins["pf"], oos["pf"]) < 1.35:
        return -1e9
    cal = full["cagr"] / abs(full["dd"]) if full["dd"] else 0.0
    ocal = oos["cagr"] / abs(oos["dd"]) if oos["dd"] else 0.0
    stability = min(full["cagr"], ins["cagr"], oos["cagr"])
    dd_penalty = max(0.0, abs(full["dd"]) - 35.0) * 2.0 + max(0.0, abs(oos["dd"]) - 25.0) * 2.0
    return min(full["pf"], ins["pf"], oos["pf"]) * 100 + cal * 35 + ocal * 35 + stability - dd_penalty


def cfg_label(cfg: Config) -> str:
    hold = "none" if cfg.max_hold_hours == 0 else f"{cfg.max_hold_hours // 24}d"
    pyr = "pyr" if cfg.use_pyramid else "nop"
    return (
        f"lev={cfg.leverage:g} sl={cfg.sl_max_pct*100:g}% tpR={cfg.partial_tp_r:g} "
        f"tp%={cfg.partial_frac*100:g} be={cfg.partial_be_buf_pct*100:g}% "
        f"{pyr}@{cfg.pyramid_r:g} hold={hold} vol={cfg.vol_spike_ratio:g}"
    )


def main() -> None:
    df = load_1h(ROOT / "data/cache/BTCUSDT_1h_binance_volume.csv")
    split = int(len(df) * 0.60)
    print(f"Fine-tune repaired MTF engulfing risk/exits on BTC 1H: {df.timestamp.iloc[0]} -> {df.timestamp.iloc[-1]}")
    print("Base: no SL-flip, RSI21/ATR20, RSI zones 55/45, real volume filter, honest next-open fills.\n")

    feature_cache = {}
    rows = []
    grid = product(
        [0.5],                         # leverage
        [0.02, 0.025, 0.035],          # max SL cap
        [4.0, 6.0, 8.0],               # partial TP trigger R
        [0.15, 0.25],                  # partial close fraction
        [0.0, 0.001],                  # BE buffer after partial
        [False, True],                 # pyramid
        [3.0, 4.0],                    # pyramid trigger
        [0, 720],                      # max hold hours: none, 30d
        [1.25],                        # volume spike ratio
    )
    for lev, sl, tp_r, tp_frac, be_buf, use_pyr, pyr_r, max_hold, vol_ratio in grid:
        if tp_frac == 0.0 and be_buf > 0:
            continue
        if not use_pyr and pyr_r != 3.0:
            continue
        cfg = Config(
            use_volume=True,
            leverage=lev,
            use_sl_flip=False,
            use_pyramid=use_pyr,
            rsi_len=21,
            atr_len=20,
            rsi_long_min=55,
            rsi_short_max=45,
            vol_spike_ratio=vol_ratio,
            sl_max_pct=sl,
            partial_tp_r=tp_r,
            partial_frac=tp_frac,
            partial_be_buf_pct=be_buf,
            pyramid_r=pyr_r,
            max_hold_hours=max_hold,
        )
        key = (cfg.sl_max_pct, cfg.sl_buf_pct, cfg.vol_spike_ratio)
        if key not in feature_cache:
            feature_cache[key] = (
                add_features(df, cfg),
                add_features(df.iloc[:split].reset_index(drop=True), cfg),
                add_features(df.iloc[split:].reset_index(drop=True), cfg),
            )
        full, ins, oos = eval_cfg(feature_cache, split, cfg)
        sc = score(full, ins, oos)
        if sc > -1e8:
            rows.append((sc, cfg, full, ins, oos))

    rows.sort(key=lambda x: x[0], reverse=True)
    print(
        f"{'rank':>4} {'score':>7} {'config':<72} | "
        f"{'FULL':>6} {'PF':>5} {'DD':>6} {'tr':>4} | "
        f"{'IS':>6} {'PF':>5} {'DD':>6} | "
        f"{'OOS':>6} {'PF':>5} {'DD':>6} {'tr':>4}"
    )
    for rank, (sc, cfg, full, ins, oos) in enumerate(rows[:40], 1):
        print(
            f"{rank:4d} {sc:7.1f} {cfg_label(cfg):<72} | "
            f"{full['cagr']:6.1f} {full['pf']:5.2f} {full['dd']:6.1f} {full['trades']:4.0f} | "
            f"{ins['cagr']:6.1f} {ins['pf']:5.2f} {ins['dd']:6.1f} | "
            f"{oos['cagr']:6.1f} {oos['pf']:5.2f} {oos['dd']:6.1f} {oos['trades']:4.0f}"
        )


if __name__ == "__main__":
    main()
