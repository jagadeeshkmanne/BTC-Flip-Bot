#!/bin/bash
# Divergence-Flip Pro paper-trading bot.
# Adds EMA200 trend filter + ATR volatility guard on top of Sharp config.
# Mainnet PUBLIC kline data + virtual fills + virtual $5K balance.
# State/log in data/paper_divflip_pro/.
cd /home/jags/BTC-Flip-Bot
/usr/bin/python3 strategies/day/bot_divflip_pro.py 2>&1
