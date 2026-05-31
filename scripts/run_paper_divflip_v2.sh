#!/bin/bash
# Divergence-Flip v2 paper-trading bot — Option B: 1h trend filter (EMA50/200).
# Runs in parallel with v1 (data/paper_divflip_v2/ vs data/paper_divflip/).
# v1 is the proven config (RSI 40/70, 15m EMA20/50 trend filter).
# v2 tests whether slower trend filter materially improves signal rate.
#
# Schedule via crontab:
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_divflip_v2.sh >> /home/jags/BTC-Flip-Bot/data/paper_divflip_v2/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
/usr/bin/python3 strategies/day/bot_divflip_v2.py 2>&1
