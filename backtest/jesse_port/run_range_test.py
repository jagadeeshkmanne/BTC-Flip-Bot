#!/usr/bin/env python3
"""run_range_test.py — the "what about range?" test (v2.3 question):
does gating v2.2's counter-trend leg to confirmed 1h ranges (ADX<20) flip its
expectancy positive, or just bleed slower? 90d real 1m Bybit candles, Jesse.
"""
from __future__ import annotations
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jesse.research import backtest
from strategy_v2 import V22, V22_R20, V22_R25, V22_R20_GAP, V22_R20_3070

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
    ("v2.2 baseline (gap0.20, no ADX)", V22),
    ("range ADX<20 (gap off)",          V22_R20),
    ("range ADX<25 (gap off)",          V22_R25),
    ("range ADX<20 + gap0.20",          V22_R20_GAP),
    ("range ADX<20 + RSI30/70",         V22_R20_3070),
]
print(f"{'variant':<34}{'trades':>7}{'win%':>6}{'PF':>6}{'net%':>8}{'maxDD':>8}{'fees$':>8}")
print("-" * 77)
for label, strat in variants:
    routes = [{"exchange": EX, "strategy": strat, "symbol": SYM, "timeframe": "5m"}]
    data_routes = [{"exchange": EX, "symbol": SYM, "timeframe": "15m"},
                   {"exchange": EX, "symbol": SYM, "timeframe": "1h"}]
    r = backtest(cfg, routes, data_routes, candles=main, warmup_candles=warm)
    m = r["metrics"]
    if not m or m.get("total", 0) == 0:
        print(f"{label:<34}{'0':>7}  (no trades)")
        continue
    print(f"{label:<34}{m['total']:>7}{m['win_rate']*100:>6.0f}"
          f"{m.get('ratio_avg_win_loss', 0) and m['total_winning_trades']*m['average_win']/max(abs(m['total_losing_trades']*m['average_loss']),1e-9):>6.2f}"
          f"{m['net_profit_percentage']:>+8.1f}{m['max_drawdown']:>+8.1f}{m['fee']:>8.0f}")
