#!/bin/sh
# One-shot LeadForge install on a fresh Ubuntu VPS (run as root from /opt/leadforge):
#   sh deploy/install.sh leadforge.aspiredigital.pk
set -e
DOMAIN="${1:?Usage: sh deploy/install.sh your.domain.com}"
cd "$(dirname "$0")/.."

echo "==> Checking DNS for $DOMAIN"
SERVER_IP=$(curl -fsS https://api.ipify.org || true)
DNS_IP=$(getent hosts "$DOMAIN" | awk '{print $1}' | head -n1)
echo "    server IP: ${SERVER_IP:-?}   DNS says: ${DNS_IP:-nothing}"
if [ -n "$SERVER_IP" ] && [ "$SERVER_IP" != "$DNS_IP" ]; then
  echo "    WARNING: DNS does not point to this server yet. HTTPS will fail until it does."
  echo "    Add an A record: leadforge -> $SERVER_IP   (Ctrl+C to stop, or wait 10s to continue)"
  sleep 10
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker"
  curl -fsSL https://get.docker.com | sh
fi

if command -v ufw >/dev/null 2>&1; then
  echo "==> Firewall: SSH, 80, 443"
  ufw allow OpenSSH >/dev/null; ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
  ufw --force enable >/dev/null
fi

echo "DOMAIN=$DOMAIN" > .env.deploy
mkdir -p data/backups
chown -R 1000:1000 data

echo "==> Building and starting (first time takes 2-4 minutes)"
docker compose --env-file .env.deploy up -d --build

echo "==> Waiting for the app to become healthy"
i=0
until [ "$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q app)")" = "healthy" ]; do
  i=$((i+1)); [ $i -gt 30 ] && { echo "App not healthy, check: docker compose logs app"; exit 1; }
  sleep 3
done
echo "    app is healthy"

if ! crontab -l 2>/dev/null | grep -q "deploy/backup.sh"; then
  (crontab -l 2>/dev/null; echo "0 3 * * * cd $(pwd) && sh deploy/backup.sh >> data/backup.log 2>&1") | crontab -
  echo "==> Daily backup scheduled (03:00, keeps 14)"
fi

echo
echo "Done. Now create your admin login:"
echo "  docker compose exec app python -m leadforge create-user --email YOU@aspiredigital.pk --name \"Your Name\" --role admin"
echo "Then open https://$DOMAIN"
