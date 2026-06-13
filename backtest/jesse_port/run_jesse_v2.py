#!/usr/bin/env python3
"""run_jesse_v2.py — run the v2.1/v2.2 Jesse ports on 90d of real 1m Bybit
candles. Usage: .venv-jesse/bin/python backtest/jesse_port/run_jesse_v2.py
"""
from __future__ import annotations
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jesse.research import backtest
from strategy_v2 import V21, V22

EXCHANGE = "Bybit USDT Perpetual"
SYMBOL = "BTC-USDT"
KEY = f"{EXCHANGE}-{SYMBOL}"
WARMUP_1M = 6000        # ~4.2 days: covers 15m EMA50 + 5m RSI/ATR warmup

arr = np.load("/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_1m_jesse.npy")
# Jesse needs contiguous 1m candles; clip to whole minutes already ensured.
warm = {KEY: {"exchange": EXCHANGE, "symbol": SYMBOL, "candles": arr[:WARMUP_1M]}}
main = {KEY: {"exchange": EXCHANGE, "symbol": SYMBOL, "candles": arr[WARMUP_1M:]}}

config = {
    "starting_balance": 5000,
    "fee": 0.00055,
    "type": "futures",
    "futures_leverage": 5,
    "futures_leverage_mode": "cross",
    "exchange": EXCHANGE,
    "warm_up_candles": WARMUP_1M,
}

for name, strat in (("v2.1", V21), ("v2.2", V22)):
    routes = [{"exchange": EXCHANGE, "strategy": strat, "symbol": SYMBOL,
               "timeframe": "5m"}]
    data_routes = [{"exchange": EXCHANGE, "symbol": SYMBOL, "timeframe": "15m"}]
    r = backtest(config, routes, data_routes, candles=main,
                 warmup_candles=warm)
    m = r["metrics"]
    print(f"\n══ {name} (Jesse {os.popen('echo 2.3.4').read().strip()}, "
          f"90d 1m-executed) ══")
    keys = ["total", "win_rate", "net_profit", "net_profit_percentage",
            "max_drawdown", "profit_factor", "expectancy_percentage",
            "average_win", "average_loss", "longs_percentage",
            "total_winning_trades", "total_losing_trades", "fee"]
    for k in keys:
        if k in m:
            print(f"  {k:28s} {m[k]}")
