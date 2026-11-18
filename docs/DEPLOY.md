# Deploying LeadForge (VPS + Docker)

## Quick path (leadforge.aspiredigital.pk, from GitHub)
1. DNS: A record `leadforge` -> server IP (at the aspiredigital.pk DNS provider).
2. On the server:
   ```bash
   apt update && apt -y install git
   git clone https://github.com/Osama369/leadforge.git /opt/leadforge
   cd /opt/leadforge && sh deploy/install.sh leadforge.aspiredigital.pk
   docker compose exec app python -m leadforge create-user --email you@aspiredigital.pk --name "Your Name" --role admin
   ```
3. Open https://leadforge.aspiredigital.pk -> Settings -> paste API keys.

Update later: `cd /opt/leadforge && git pull && docker compose --env-file .env.deploy up -d --build`

The detailed manual steps follow.

One server, one workspace for your team. HTTPS is automatic (Caddy + Let's Encrypt).

## What you need
- A VPS with Ubuntu 22.04/24.04, **2 GB RAM** is enough (Hetzner CX22 / DigitalOcean / Contabo, ~$5–12/month).
- A domain or subdomain, e.g. `leads.youragency.com`.
- Your API keys (Google Places, optional Serper / Anthropic) and the Google service-account JSON (for Sheets).

## 1. DNS
At your domain provider add an **A record**: `leads` → your server's IP. Wait until `ping leads.youragency.com` shows the IP.

## 2. Server setup (once)
```bash
ssh root@YOUR_SERVER_IP
apt update && apt -y upgrade
curl -fsSL https://get.docker.com | sh
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw --force enable
```

## 3. Copy the project
From your PC (Git Bash / PowerShell), without `.venv`, `data`, `.env`:
```bash
ssh root@YOUR_SERVER_IP "mkdir -p /opt/leadforge"
scp -r D:/leadforge/leadforge D:/leadforge/config D:/leadforge/deploy D:/leadforge/Dockerfile D:/leadforge/docker-compose.yml D:/leadforge/requirements.txt D:/leadforge/.dockerignore root@YOUR_SERVER_IP:/opt/leadforge/
```
(Or push the project to a **private** GitHub repo and `git clone` it into `/opt/leadforge`.)

## 4. Configure
```bash
cd /opt/leadforge
echo "DOMAIN=leads.youragency.com" > .env.deploy
mkdir -p data && chown 1000:1000 data
```
Optional, for Google Sheets: copy the service-account JSON to `data/service-account.json`
and later set its path in Settings to `/app/data/service-account.json`.

## 5. Start
```bash
docker compose --env-file .env.deploy up -d --build
docker compose ps                     # app should become "healthy"
```

## 6. Create the first admin
```bash
docker compose exec app python -m leadforge create-user --email you@youragency.com --name "Your Name" --role admin
```
(The "create admin" screen is disabled on the server for safety.)

Open `https://leads.youragency.com`, log in, then:
1. **Settings** → paste API keys → Save (stored in `data/.env` on the server).
2. **Team** → add your sales reps as *Member*.
3. Google Cloud → your API key → *Application restrictions* → **IP addresses** → add the server IP.

## 7. Backups
```bash
crontab -e
# add this line: daily backup at 03:00, keeps 14
0 3 * * * cd /opt/leadforge && sh deploy/backup.sh >> data/backup.log 2>&1
```
Backups land in `data/backups/`. Copy them off the server now and then (e.g. `scp`).

## Updating to a new version
```bash
cd /opt/leadforge        # copy the new files first (step 3)
docker compose --env-file .env.deploy up -d --build
```
Data in `data/` is kept. Wait for a running campaign to finish first (it runs inside the app process).

## Useful commands
```bash
docker compose logs -f app                                               # live logs
docker compose exec app python -m leadforge reset-password --email x@y.com
docker compose exec app python -m leadforge doctor                       # which keys work
docker compose restart app
```

## Security checklist
- [x] HTTPS only, HSTS (Caddy)
- [x] Login required for every page and API call; passwords hashed (scrypt); sessions in HttpOnly cookies
- [x] Admin / Member roles; brute-force limit on login (8 tries / 15 min)
- [x] CSRF header check, CSP and other security headers, API docs off in production
- [x] App runs as non-root, only reachable through Caddy
- [ ] Restrict the Google API key to the server IP (step 6.3)
- [ ] Off-server copies of `data/backups`
