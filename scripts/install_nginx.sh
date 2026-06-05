#!/bin/bash
# Install + configure nginx in front of the Python bot server.
# Idempotent — safe to re-run. Includes rollback markers.
set -e

NGINX_SITE=/etc/nginx/sites-available/btc-bot
NGINX_ENABLED=/etc/nginx/sites-enabled/btc-bot
SRC_CONF=/home/jags/BTC-Flip-Bot/scripts/nginx-btc-bot.conf
SYSTEMD_UNIT=/etc/systemd/system/btc-bot-server.service

echo "═══ 1. Install nginx if missing ═══"
if ! command -v nginx >/dev/null 2>&1; then
    sudo apt-get update -qq
    sudo apt-get install -y nginx
fi
nginx -v 2>&1
echo

echo "═══ 2. Update systemd unit — pass BTC_BOT_PORT=8889 to Python ═══"
if [ -f "$SYSTEMD_UNIT" ]; then
    # Backup
    sudo cp "$SYSTEMD_UNIT" "${SYSTEMD_UNIT}.pre-nginx.bak"
    # Add/replace Environment= line for port
    if grep -q "^Environment=BTC_BOT_PORT=" "$SYSTEMD_UNIT"; then
        sudo sed -i 's|^Environment=BTC_BOT_PORT=.*|Environment=BTC_BOT_PORT=8889|' "$SYSTEMD_UNIT"
    else
        # Insert after [Service]
        sudo sed -i '/^\[Service\]/a Environment=BTC_BOT_PORT=8889\nEnvironment=BTC_BOT_BIND=127.0.0.1' "$SYSTEMD_UNIT"
    fi
    sudo systemctl daemon-reload
    echo "✓ systemd unit updated (Python will bind to 127.0.0.1:8889)"
else
    echo "  WARN: $SYSTEMD_UNIT not found — Python will use default 8888"
fi
echo

echo "═══ 3. Install nginx site config ═══"
sudo cp "$SRC_CONF" "$NGINX_SITE"
# Enable site, disable default
sudo ln -sf "$NGINX_SITE" "$NGINX_ENABLED"
sudo rm -f /etc/nginx/sites-enabled/default
# Cache dir for proxy_cache
sudo mkdir -p /var/cache/nginx/btc-bot
sudo chown -R www-data:www-data /var/cache/nginx/btc-bot
echo

echo "═══ 4. Validate nginx config ═══"
sudo nginx -t
echo

echo "═══ 5. Restart Python server (now on :8889) ═══"
sudo systemctl restart btc-bot-server
sleep 3
sudo systemctl is-active btc-bot-server
# Confirm python bound to 8889
sudo ss -tlnp | grep -E ":888[89] " || echo "  WARN: nothing on 8888/8889 yet"
echo

echo "═══ 6. Restart nginx to bind :8888 ═══"
sudo systemctl restart nginx
sudo systemctl enable nginx >/dev/null 2>&1
sleep 2
sudo systemctl is-active nginx
sudo ss -tlnp | grep ":8888 " || echo "  WARN: nginx not listening on 8888"
echo

echo "═══ 7. Smoke test ═══"
echo "── nginx → static asset ──"
curl -s -o /dev/null -w "  HTTP %{http_code} | size %{size_download}B | %{time_total}s\n" \
    -H 'Accept-Encoding: gzip' \
    http://localhost:8888/bots/assets/lightweight-charts-BNJuxvCB.js 2>&1
echo "── nginx → /bots/v2 (HTML) ──"
curl -s -o /dev/null -w "  HTTP %{http_code} | size %{size_download}B | %{time_total}s\n" \
    http://localhost:8888/bots/v2 2>&1
echo "── nginx → /api/bots/all (proxied to Python) ──"
curl -s -o /dev/null -w "  HTTP %{http_code} | size %{size_download}B | %{time_total}s\n" \
    http://localhost:8888/api/bots/all 2>&1
echo "── nginx → /api/ticker (cached) ──"
for i in 1 2 3; do
    curl -s -o /dev/null -w "  attempt $i: HTTP %{http_code} | %{time_total}s\n" \
        http://localhost:8888/api/ticker 2>&1
done

echo
echo "✓ nginx installed and routing /bots → static, /api → Python:8889"
echo "  Rollback: sudo cp ${SYSTEMD_UNIT}.pre-nginx.bak ${SYSTEMD_UNIT}"
echo "           sudo systemctl stop nginx && sudo systemctl disable nginx"
echo "           sudo sed -i 's/Environment=BTC_BOT_PORT=8889//' ${SYSTEMD_UNIT}"
echo "           sudo systemctl daemon-reload && sudo systemctl restart btc-bot-server"
