#!/bin/bash
# ═══════════════════════════════════════════════════════════
# ProtonVPN WireGuard Setup for Polymarket Trading Bot
#
# Usage:
#   ./setup-vpn.sh
#
# This script:
#   1. Prompts for your ProtonVPN WireGuard config values
#   2. Writes them to .env
#   3. Tests the gluetun container
#   4. Verifies your IP is masked
# ═══════════════════════════════════════════════════════════

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  ProtonVPN WireGuard Setup${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════${NC}"
echo ""
echo "You need a WireGuard config from ProtonVPN."
echo "Generate one at: https://account.protonvpn.com/downloads#wireguard-configuration"
echo ""
echo "From your .conf file, I need these values:"
echo ""

# Collect values
read -p "PrivateKey (from [Interface] section): " WG_PRIVATE_KEY
read -p "Address (e.g. 10.2.0.2/32): " WG_ADDRESS
WG_ADDRESS=${WG_ADDRESS:-10.2.0.2/32}
read -p "DNS (e.g. 10.2.0.1): " WG_DNS
WG_DNS=${WG_DNS:-10.2.0.1}
read -p "PublicKey (from [Peer] section): " WG_PUBLIC_KEY
read -p "Endpoint IP (e.g. 5.157.13.2): " WG_ENDPOINT_IP
read -p "Endpoint Port (e.g. 51820): " WG_ENDPOINT_PORT
WG_ENDPOINT_PORT=${WG_ENDPOINT_PORT:-51820}

echo ""
echo -e "${CYAN}Writing VPN config to .env...${NC}"

# Check if .env exists
ENV_FILE="$(dirname "$0")/.env"
if [ ! -f "$ENV_FILE" ]; then
    cp "$(dirname "$0")/.env.example" "$ENV_FILE" 2>/dev/null || touch "$ENV_FILE"
fi

# Remove old VPN entries if they exist
sed -i '/^WIREGUARD_PRIVATE_KEY=/d' "$ENV_FILE"
sed -i '/^WIREGUARD_PUBLIC_KEY=/d' "$ENV_FILE"
sed -i '/^WIREGUARD_ADDRESSES=/d' "$ENV_FILE"
sed -i '/^VPN_ENDPOINT_IP=/d' "$ENV_FILE"
sed -i '/^VPN_ENDPOINT_PORT=/d' "$ENV_FILE"
sed -i '/^DNS_ADDRESS=/d' "$ENV_FILE"
sed -i '/^VPN_PROVIDER=/d' "$ENV_FILE"
sed -i '/^VPN_TYPE=/d' "$ENV_FILE"
sed -i '/^VPN_REQUIRED=/d' "$ENV_FILE"
sed -i '/^PROXY_URL=/d' "$ENV_FILE"

# Append VPN config
cat >> "$ENV_FILE" << EOF

# ProtonVPN WireGuard (configured by setup-vpn.sh)
VPN_PROVIDER=custom
VPN_TYPE=wireguard
VPN_REQUIRED=true
PROXY_URL=socks5://127.0.0.1:1080
WIREGUARD_PRIVATE_KEY=${WG_PRIVATE_KEY}
WIREGUARD_PUBLIC_KEY=${WG_PUBLIC_KEY}
WIREGUARD_ADDRESSES=${WG_ADDRESS}
VPN_ENDPOINT_IP=${WG_ENDPOINT_IP}
VPN_ENDPOINT_PORT=${WG_ENDPOINT_PORT}
DNS_ADDRESS=${WG_DNS}
EOF

echo -e "${GREEN}VPN config written to .env${NC}"
echo ""

# Test with docker
echo -e "${CYAN}Testing gluetun container...${NC}"
echo "(This may take 30-60 seconds on first run)"
echo ""

docker compose up -d gluetun 2>/dev/null || docker-compose up -d gluetun

echo "Waiting for VPN to connect..."
sleep 15

# Check VPN status
echo ""
echo -e "${CYAN}Checking VPN status...${NC}"
VPS_IP=$(curl -s --max-time 5 https://ipinfo.io/ip || echo "unknown")
VPN_RESULT=$(curl -s --max-time 10 --socks5 127.0.0.1:1080 https://ipinfo.io/json 2>/dev/null || echo '{"error":"failed"}')

VPN_IP=$(echo "$VPN_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ip','failed'))" 2>/dev/null || echo "failed")
VPN_COUNTRY=$(echo "$VPN_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('country','unknown'))" 2>/dev/null || echo "unknown")

echo ""
echo "  VPS real IP:    ${VPS_IP}"
echo "  VPN masked IP:  ${VPN_IP}"
echo "  VPN country:    ${VPN_COUNTRY}"
echo ""

if [ "$VPN_IP" = "failed" ] || [ "$VPN_IP" = "$VPS_IP" ]; then
    echo -e "${RED}VPN CHECK FAILED${NC}"
    echo "The SOCKS5 proxy isn't working. Check:"
    echo "  docker logs gluetun"
    exit 1
fi

if [ "$VPN_COUNTRY" = "US" ]; then
    echo -e "${RED}WARNING: VPN is routing to US — Polymarket will block trades.${NC}"
    echo "Choose a non-US server in your ProtonVPN config."
    exit 1
fi

echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  VPN SETUP COMPLETE${NC}"
echo -e "${GREEN}  Masked IP: ${VPN_IP} (${VPN_COUNTRY})${NC}"
echo -e "${GREEN}  All trading traffic will route through ProtonVPN${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo ""
echo "Next: run ./deploy.sh to start the full system with VPN"
