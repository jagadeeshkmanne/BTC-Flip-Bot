#!/bin/bash
# ─── Run this ON the GCP VM after the code is uploaded ───
# Installs Python deps and a systemd timer that runs the Bybit divflip v1
# live bot every 1 minute. Idempotent — safe to re-run after a redeploy.
set -e

BOT_DIR="$HOME/BTC-Flip-Bot-Bybit"
cd "$BOT_DIR"

echo "=== Installing Python dependencies ==="
pip3 install -r requirements.txt 2>/dev/null \
  || pip3 install --break-system-packages -r requirements.txt

echo ""
echo "=== Creating data directory ==="
mkdir -p data

if [ ! -f .env ]; then
  echo ""
  echo "!!  WARNING: .env not found. The bot cannot place orders without it."
  echo "!!  Create it on the VM:  cp .env.example .env  && nano .env"
fi

echo ""
echo "=== Installing systemd service + timer (runs every 1 min) ==="
sudo tee /etc/systemd/system/bybit-divflip.service > /dev/null << EOF
[Unit]
Description=Bybit Divflip v1 LIVE bot
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$BOT_DIR
ExecStart=/usr/bin/python3 $BOT_DIR/bot_divflip_bybit.py
StandardOutput=append:$BOT_DIR/data/bot.log
StandardError=append:$BOT_DIR/data/bot.log
EOF

sudo tee /etc/systemd/system/bybit-divflip.timer > /dev/null << EOF
[Unit]
Description=Run Bybit Divflip bot every 1 min

[Timer]
OnBootSec=60
OnUnitActiveSec=1min
Persistent=true

[Install]
WantedBy=timers.target
EOF

echo ""
echo "=== Enabling timer ==="
sudo systemctl daemon-reload
sudo systemctl enable --now bybit-divflip.timer

echo ""
echo "=== Status ==="
sudo systemctl status bybit-divflip.timer --no-pager || true

echo ""
echo "================================================================"
echo "  DONE — Bybit divflip bot installed."
echo ""
echo "  IMPORTANT: trading_enabled is true in config/bybit_live.json."
echo "  The bot places REAL orders once .env has valid API keys."
echo "  To run monitor-only: set \"trading_enabled\": false and redeploy."
echo ""
echo "  Logs:    tail -f $BOT_DIR/data/bot.log"
echo "  Timer:   sudo systemctl list-timers | grep bybit"
echo "  Stop:    sudo systemctl disable --now bybit-divflip.timer"
echo "================================================================"
