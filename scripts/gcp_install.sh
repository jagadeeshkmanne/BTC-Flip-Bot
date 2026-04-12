#!/bin/bash
# ─── Run this ON the GCP VM after uploading bot code ───
set -e

BOT_DIR="$HOME/BTC-Flip-Bot"
cd "$BOT_DIR"

echo "=== Installing Python dependencies ==="
pip3 install numpy pandas requests 2>/dev/null || pip3 install --break-system-packages numpy pandas requests

echo ""
echo "=== Creating data directories ==="
mkdir -p data/testnet data/production

echo ""
echo "=== Installing systemd services ==="
# Bot timer (runs every 15 min) — Testnet
sudo tee /etc/systemd/system/btc-bot-testnet.service > /dev/null << EOF
[Unit]
Description=BTC Flip Bot - Testnet
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$BOT_DIR
ExecStart=/usr/bin/python3 $BOT_DIR/bot.py --env testnet
StandardOutput=append:$BOT_DIR/data/testnet/bot.log
StandardError=append:$BOT_DIR/data/testnet/bot.log
EOF

sudo tee /etc/systemd/system/btc-bot-testnet.timer > /dev/null << EOF
[Unit]
Description=Run BTC Bot Testnet every 15 min

[Timer]
OnBootSec=60
OnUnitActiveSec=15min
Persistent=true

[Install]
WantedBy=timers.target
EOF

# Bot timer — Production
sudo tee /etc/systemd/system/btc-bot-production.service > /dev/null << EOF
[Unit]
Description=BTC Flip Bot - Production
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$BOT_DIR
ExecStart=/usr/bin/python3 $BOT_DIR/bot.py --env production
StandardOutput=append:$BOT_DIR/data/production/bot.log
StandardError=append:$BOT_DIR/data/production/bot.log
EOF

sudo tee /etc/systemd/system/btc-bot-production.timer > /dev/null << EOF
[Unit]
Description=Run BTC Bot Production every 15 min

[Timer]
OnBootSec=90
OnUnitActiveSec=15min
Persistent=true

[Install]
WantedBy=timers.target
EOF

# Dashboard server (always running)
sudo tee /etc/systemd/system/btc-bot-server.service > /dev/null << EOF
[Unit]
Description=BTC Bot Dashboard Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$BOT_DIR
ExecStart=/usr/bin/python3 $BOT_DIR/server.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

echo ""
echo "=== Enabling services ==="
sudo systemctl daemon-reload

# Enable and start both bot timers
sudo systemctl enable --now btc-bot-testnet.timer
sudo systemctl enable --now btc-bot-production.timer

# Enable and start dashboard server
sudo systemctl enable --now btc-bot-server.service

echo ""
echo "=== Status ==="
echo "Testnet timer:"
sudo systemctl status btc-bot-testnet.timer --no-pager
echo ""
echo "Production timer:"
sudo systemctl status btc-bot-production.timer --no-pager
echo ""
echo "Dashboard server:"
sudo systemctl status btc-bot-server.service --no-pager

echo ""
echo "================================================"
echo "  DONE! Bot is running on GCP."
echo ""
echo "  Dashboard: http://$(curl -s ifconfig.me):8888"
echo "  Settings:  http://$(curl -s ifconfig.me):8888/settings.html"
echo ""
echo "  Use the toggle switches on the Settings page"
echo "  to enable/disable testnet and production."
echo ""
echo "  Useful commands:"
echo "    sudo systemctl status btc-bot-testnet.timer"
echo "    sudo journalctl -u btc-bot-testnet -f"
echo "    tail -f ~/BTC-Flip-Bot/data/testnet/bot.log"
echo "================================================"
