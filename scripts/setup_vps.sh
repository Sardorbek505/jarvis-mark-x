#!/bin/bash
# setup_vps.sh — One-command VPS setup for JARVIS (Ubuntu/Debian)
# Usage: bash scripts/setup_vps.sh your-domain.com
set -e

DOMAIN=${1:-""}
if [ -z "$DOMAIN" ]; then
    echo "Usage: bash scripts/setup_vps.sh your-domain.com"
    exit 1
fi

echo "=== JARVIS VPS Setup ==="
echo "Domain: $DOMAIN"
echo ""

# 1. Install Docker
if ! command -v docker &>/dev/null; then
    echo "[1/4] Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    usermod -aG docker "$USER"
else
    echo "[1/4] Docker already installed ✓"
fi

# 2. Install Docker Compose v2
if ! docker compose version &>/dev/null; then
    echo "[2/4] Installing Docker Compose..."
    apt-get install -y docker-compose-plugin
else
    echo "[2/4] Docker Compose already installed ✓"
fi

# 3. Set DOMAIN in Caddyfile
echo "[3/4] Configuring domain: $DOMAIN"
sed -i "s|jarvis.yourdomain.com|$DOMAIN|g" Caddyfile

# 4. Update api_keys.json with miniapp_url
echo "[4/4] Setting miniapp_url..."
if [ -f config/api_keys.json ]; then
    python3 -c "
import json, sys
with open('config/api_keys.json') as f:
    d = json.load(f)
d['miniapp_url'] = 'https://$DOMAIN'
d['miniapp_port'] = 8000
with open('config/api_keys.json', 'w') as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
print('api_keys.json updated')
"
fi

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Start JARVIS:"
echo "  docker compose up -d"
echo ""
echo "View logs:"
echo "  docker compose logs -f bot"
echo "  docker compose logs -f miniapp"
echo ""
echo "Your Mini App URL (set this in BotFather):"
echo "  https://$DOMAIN"
