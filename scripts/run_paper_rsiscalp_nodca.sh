#!/bin/bash
# RSI-Scalp NO-DCA experiment — single entry (no averaging down).
# Entry RSI 28/72, TP 0.25% from entry, SL 1.5% from entry (= the current bot's
# 0.5% spacing + 1% effective distance), breaker, 3x.
# ⚠️ Backtests NEGATIVE expectancy (-0.028%/trade) with a LARGER worst loss
# (-4.4%) than the DCA bot — deployed ONLY as a live paper experiment.
#
# Schedule via crontab:
#   * * * * * /home/jags/BTC-Flip-Bot/scripts/run_paper_rsiscalp_nodca.sh >> /home/jags/BTC-Flip-Bot/data/paper_rsiscalp_nodca/cron.log 2>&1
cd /home/jags/BTC-Flip-Bot
RSISCALP_DATA_DIR=paper_rsiscalp_nodca RSISCALP_NODCA=1 RSISCALP_OS=28 RSISCALP_OB=72 RSISCALP_SL=0.015 /usr/bin/python3 strategies/day/bot_rsiscalp.py 2>&1
