#!/bin/bash
cd /home/jags/BTC-Flip-Bot
set -a && source .env && set +a
/usr/bin/python3 strategies/swing/bot.py --env testnet 2>&1
