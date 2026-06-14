#!/usr/bin/env python3
"""run_v23_sweep.py — find the BEST achievable v2.3 regime-router config.
Routes v2.1 (with-trend) in trends + v2.2 (counter-trend) in ranges by 1h ADX.
90d real 1m Bybit candles, Jesse. Also answers: does 15m+1h dual confirm help?
"""
from __future__ import annotations
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jesse.research import backtest
from strategy_v2 import (V21, V22, V23, V23_NoDeadzone, V23_Dual,
                         V23_TrendOnly, V23_Wide)

EX, SYM = "Bybit USDT Perpetual", "BTC-USDT"
KEY = f"{EX}-{SYM}"
WARM = 6000
arr = np.load("/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_1m_jesse.npy")
warm = {KEY: {"exchange": EX, "symbol": SYM, "candles": arr[:WARM]}}
main = {KEY: {"exchange": EX, "symbol": SYM, "candles": arr[WARM:]}}
cfg = {"starting_balance": 5000, "fee": 0.00055, "type": "futures",
       "futures_leverage": 5, "futures_leverage_mode": "cross",
       "exchange": EX, "warm_up_candles": WARM}

variants = [
    ("v2.1 standalone (ref)",            V21),
    ("v2.2 standalone (ref)",            V22),
    ("v2.3 router 1h (trend>25/rng<20)", V23),
    ("v2.3 no-deadzone (split@20)",      V23_NoDeadzone),
    ("v2.3 wide (trend>28/rng<18)",      V23_Wide),
    ("v2.3 DUAL 15m+1h confirm",         V23_Dual),
    ("v2.3 TREND-LEG ONLY",              V23_TrendOnly),
]
print(f"{'config':<35}{'trades':>7}{'win%':>6}{'PF':>6}{'net%':>8}{'maxDD':>8}{'fees$':>8}")
print("-" * 78)
for label, strat in variants:
    routes = [{"exchange": EX, "strategy": strat, "symbol": SYM, "timeframe": "5m"}]
    data_routes = [{"exchange": EX, "symbol": SYM, "timeframe": "15m"},
                   {"exchange": EX, "symbol": SYM, "timeframe": "1h"}]
    r = backtest(cfg, routes, data_routes, candles=main, warmup_candles=warm)
    m = r["metrics"]
    if not m or m.get("total", 0) == 0:
        print(f"{label:<35}{'0':>7}  (no trades)")
        continue
    pf = m["total_winning_trades"] * m["average_win"] / max(
        abs(m["total_losing_trades"] * m["average_loss"]), 1e-9)
    print(f"{label:<35}{m['total']:>7}{m['win_rate']*100:>6.0f}{pf:>6.2f}"
          f"{m['net_profit_percentage']:>+8.1f}{m['max_drawdown']:>+8.1f}{m['fee']:>8.0f}")
