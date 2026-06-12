#!/bin/bash
# v3 — 4h trend portfolio paper bot (long/flat, 2x perp, 4 pairs, honest accounting).
#   LONG per coin when EMA30>EMA150 AND px>EMA50 AND ADX14>20 (alts need BTC confirm).
#   Catastrophe SL 8% from entry (live-price, any tick). No shorts, no DCA, no grid.
#   Validated 2026-06-12 (backtest/v3_param_plateau.py): OOS 2023-26 +262%@1x,
#   Sharpe 1.82, maxDD -14% (param plateau: 54/54 cells OOS-positive). @2x: -28% DD.
#   Funding charged at each 8h event; taker fee 0.055% + slip 0.02% per side.
#
# Schedule via crontab (every minute: SL responsiveness; trades only on closed 4h bars):
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_v3.sh >> /home/jags/BTC-Flip-Bot/data/v3_trend/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
V3_DATA_DIR=v3_trend \
  /usr/bin/flock -n /tmp/v3_trend.lock \
  /usr/bin/python3 bot/bot_v3_trend.py 2>&1 || exit 0
