#!/bin/bash
# RSI-Scalp +Trend v2 — sibling of rsiscalp_trend with ONE extra entry filter:
# 15m EMA20/EMA50 GAP must be >= 0.25% (firm trend required, not knife-edge).
#
# Backtest evidence: turns -75% (29mo OOS) baseline into +44% OOS (PF 1.33, MaxDD 21%).
# Walk-forward validated on 2025-2026 test set.
#
# Runs in parallel with v1 (paper_rsiscalp_trend) for live A/B comparison.
# Own state in data/paper_rsiscalp_trend_v2/.
#
# Schedule via crontab:
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_rsiscalp_trend_v2.sh >> /home/jags/BTC-Flip-Bot/data/paper_rsiscalp_trend_v2/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
RSISCALP_DATA_DIR=paper_rsiscalp_trend_v2 \
RSISCALP_TREND=1 \
RSISCALP_V2_GAP_MIN=0.0025 \
  /usr/bin/flock -n /tmp/rsiscalp_v2.lock /usr/bin/python3 strategies/day/bot_rsiscalp_v2.py 2>&1 || exit 0
