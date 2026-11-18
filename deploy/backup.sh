#!/bin/sh
# Daily database backup (keeps 14). Add to the server's crontab:
#   0 3 * * * cd /opt/leadforge && sh deploy/backup.sh >> data/backup.log 2>&1
docker compose -f "${COMPOSE_FILE:-docker-compose.yml}" exec -T app python -m leadforge backup --keep 14
