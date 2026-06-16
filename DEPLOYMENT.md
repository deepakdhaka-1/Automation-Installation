# AI Agent Deployment Guide

This guide is for an AI agent deploying or repairing the VM-hosted automation stack from this repository.

The operator should provide:

```text
Repo URL
VM IP address
SSH username
SSH password or SSH key
n8n public subdomain
Supabase public subdomain
Supabase Postgres password
n8n login username/password
Supabase Studio username/password
Crawl4AI Gemini API token, if Crawl4AI should be deployed or recreated
Optional Certbot email
```

Example inputs from the current live deployment:

```text
VM IP: 35.236.134.87
n8n domain: development.alphaagentx.com
Supabase domain: supabase.alphaagentx.com
SSH user: contact
```

Do not assume these example domains will be reused. Use the domains the operator gives for the new deployment.

## Goal

Deploy two services on one VM:

| Service | Runtime | Public URL | Internal Port |
| --- | --- | --- | --- |
| n8n | Docker Compose | operator-provided n8n domain | `5678` |
| Supabase | Docker Compose | operator-provided Supabase domain | `8000` through Supabase Kong |
| Crawl4AI | Docker container or Docker Compose | direct VM port unless a domain is requested | `11235` |

nginx terminates HTTPS and reverse proxies to the containers.

## Important Safety Rules

Do not delete or recreate unrelated Docker containers.

Do not run destructive cleanup commands such as:

```bash
docker system prune -a
docker volume prune
rm -rf /home/contact/*
```

Be especially careful with the existing `crawl4ai` container. If it exists but is stopped, prefer `docker start crawl4ai` over deleting/recreating it.

Do not commit real `.env` files, passwords, JWT secrets, database data, TLS private keys, or Docker volumes.

Before changing any existing service file, create a backup:

```bash
cp docker-compose.yml docker-compose.yml.backup.$(date +%Y%m%d%H%M%S)
sudo cp /etc/nginx/sites-available/n8n /etc/nginx/sites-available/n8n.backup.$(date +%Y%m%d%H%M%S)
sudo cp /etc/nginx/sites-available/supabase /etc/nginx/sites-available/supabase.backup.$(date +%Y%m%d%H%M%S)
```

## DNS Requirements

Before Certbot can issue TLS certificates, the operator must create DNS A records:

```text
A <n8n-hostname>      <VM-IP>
A <supabase-hostname> <VM-IP>
```

Example:

```text
A development 35.236.134.87
A supabase    35.236.134.87
```

Check DNS from the VM:

```bash
getent ahostsv4 <n8n-domain>
getent ahostsv4 <supabase-domain>
```

Expected result: both domains resolve to the VM IP.

If a domain resolves to another IP from inside the VM, nginx/Certbot and service-to-service calls may fail. Remove conflicting DNS records, especially wrong `AAAA` records, or add a narrow Docker `extra_hosts` override for container-to-container access when needed.

## VM Prerequisites

Check the VM has enough resources:

```bash
free -h
nproc
df -h /
```

Recommended minimum:

```text
RAM: 6 GB
CPU: 2 cores
Disk: 80 GB SSD
```

Install required packages if missing:

```bash
sudo apt update
sudo apt install -y ca-certificates curl git nginx certbot python3-certbot-nginx
```

Install Docker and Docker Compose if missing. Prefer Docker's official install instructions for the target Ubuntu version.

Verify:

```bash
docker --version
docker compose version
nginx -v
certbot --version
```

## Repo Placement On VM

Clone this repository:

```bash
cd /home/contact
git clone <repo-url> Automation-Installation
```

Use this repo as deployment source and documentation.

Suggested service runtime paths:

```text
/home/contact/n8n-service
/home/contact/supabase-project/supabase/docker
```

## Deploy n8n

Create the n8n runtime folder:

```bash
mkdir -p /home/contact/n8n-service
cp /home/contact/Automation-Installation/n8n-service/docker-compose.yml /home/contact/n8n-service/docker-compose.yml
cp /home/contact/Automation-Installation/n8n-service/.env.example /home/contact/n8n-service/.env
cd /home/contact/n8n-service
```

Edit `.env` using the operator-provided values:

```text
N8N_BASIC_AUTH_USER=<operator-value>
N8N_BASIC_AUTH_PASSWORD=<operator-value>
N8N_HOST=<n8n-domain>
N8N_PROTOCOL=https
WEBHOOK_URL=https://<n8n-domain>/
N8N_PORT=5678
```

If Supabase runs on the same VM and n8n must call it by public hostname, set this in `docker-compose.yml`:

```yaml
extra_hosts:
  - "<supabase-domain>:<VM-IP>"
```

Deploy:

```bash
docker compose up -d
docker compose ps
```

Expected container:

```text
n8n-server
```

## Deploy Or Recover Crawl4AI

First check whether an existing container already exists:

```bash
docker ps -a --filter name=crawl4ai --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
```

If the container exists but is stopped, recover it:

```bash
docker update --restart unless-stopped crawl4ai
docker start crawl4ai
```

Verify:

```bash
docker ps --filter name=crawl4ai
curl -sS http://127.0.0.1:11235/health
```

Expected: the container is up and `/health` returns JSON with `"status":"ok"`.

If no container exists, deploy from this repo:

```bash
mkdir -p /home/contact/crawl4ai-service
cp /home/contact/Automation-Installation/crawl4ai-service/docker-compose.yml /home/contact/crawl4ai-service/docker-compose.yml
cp /home/contact/Automation-Installation/crawl4ai-service/.env.example /home/contact/crawl4ai-service/.env
cd /home/contact/crawl4ai-service
nano .env
docker compose up -d
```

Set `GEMINI_API_TOKEN` in `.env` if Crawl4AI workflows need Gemini-backed features.

Current expected local endpoints:

```text
http://127.0.0.1:11235/health
http://127.0.0.1:11235/docs
http://127.0.0.1:11235/openapi.json
```

## Deploy Supabase

Self-hosted Supabase should be deployed from Supabase's official Docker files.

Clone the official Supabase repo:

```bash
cd /home/contact
git clone --depth 1 https://github.com/supabase/supabase supabase-project
cd /home/contact/supabase-project/supabase/docker
cp .env.example .env
```

Edit `.env` using the operator-provided values:

```text
POSTGRES_PASSWORD=<operator-value>
DASHBOARD_USERNAME=<operator-value>
DASHBOARD_PASSWORD=<operator-value>
SUPABASE_PUBLIC_URL=https://<supabase-domain>
API_EXTERNAL_URL=https://<supabase-domain>
SITE_URL=https://<supabase-domain>
KONG_HTTP_PORT=8000
KONG_HTTPS_PORT=8443
```

Make sure these keys exist in `.env`:

```text
ANON_KEY
SERVICE_ROLE_KEY
SUPABASE_PUBLISHABLE_KEY
SUPABASE_SECRET_KEY
JWT_SECRET
```

For self-hosted Supabase, `SUPABASE_PUBLISHABLE_KEY` can mirror the anon/publishable JWT and `SUPABASE_SECRET_KEY` can mirror the service-role JWT when using the JWT-based key setup.

Deploy:

```bash
docker compose pull
docker compose up -d --wait
docker compose ps
```

Expected result: all Supabase containers are healthy.

## Install nginx Sites

Create nginx files from the templates in `nginx-sites/`.

Replace these values:

```text
development.alphaagentx.com -> <n8n-domain>
supabase.alphaagentx.com    -> <supabase-domain>
```

Copy files:

```bash
sudo cp /home/contact/Automation-Installation/nginx-sites/n8n.conf /etc/nginx/sites-available/n8n
sudo cp /home/contact/Automation-Installation/nginx-sites/supabase.conf /etc/nginx/sites-available/supabase
sudo ln -sfn /etc/nginx/sites-available/n8n /etc/nginx/sites-enabled/n8n
sudo ln -sfn /etc/nginx/sites-available/supabase /etc/nginx/sites-enabled/supabase
sudo nginx -t
sudo systemctl reload nginx
```

Before Certbot, verify plain HTTP routing:

```bash
curl -I http://<n8n-domain>
curl -I http://<supabase-domain>
curl -I http://127.0.0.1:5678
curl -I http://127.0.0.1:8000
```

## Issue HTTPS Certificates

Run Certbot only after DNS resolves to the VM:

```bash
sudo certbot --nginx -d <n8n-domain>
sudo certbot --nginx -d <supabase-domain>
sudo nginx -t
sudo systemctl reload nginx
```

If Certbot fails with Python OpenSSL errors, check whether user-local Python packages are shadowing system packages:

```bash
python3 -m pip show cryptography pyOpenSSL certbot
```

Try:

```bash
sudo env PYTHONNOUSERSITE=1 certbot --nginx -d <domain>
```

## Verify Public Services

Verify n8n:

```bash
curl -I https://<n8n-domain>
docker ps --filter name=n8n-server
```

Verify Crawl4AI:

```bash
docker ps --filter name=crawl4ai
curl -sS http://127.0.0.1:11235/health
```

Verify Supabase:

```bash
curl -I https://<supabase-domain>
cd /home/contact/supabase-project/supabase/docker
docker compose ps
```

Supabase Studio may require Basic Auth using `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD`.

## Verify Supabase API With Service Role

From the VM:

```bash
SERVICE_ROLE_KEY='<service-role-jwt>'
curl -sS -o /tmp/supabase-api-test.txt -w 'status=%{http_code} type=%{content_type}\n' \
  -H "apikey: ${SERVICE_ROLE_KEY}" \
  -H "Authorization: Bearer ${SERVICE_ROLE_KEY}" \
  "https://<supabase-domain>/rest/v1/"
head -c 300 /tmp/supabase-api-test.txt
```

Expected:

```text
status=200 type=application/openapi+json
```

## Verify n8n Can Reach Supabase

From inside the n8n container:

```bash
docker exec n8n-server node -e "require('dns').lookup('<supabase-domain>',{all:true},(e,a)=>console.log(e||a))"
```

Expected: the Supabase domain resolves to the VM IP.

Then test the API:

```bash
docker exec -e KEY='<service-role-jwt>' n8n-server node -e "fetch('https://<supabase-domain>/rest/v1/',{headers:{apikey:process.env.KEY,authorization:'Bearer '+process.env.KEY}}).then(async r=>{console.log(r.status,r.headers.get('content-type'));console.log((await r.text()).slice(0,160))}).catch(e=>{console.error(e);process.exit(1)})"
```

Expected:

```text
200 application/openapi+json
```

## n8n Credential Values

In n8n's Supabase credential form:

```text
Host: https://<supabase-domain>
Service Role Secret: <service-role-jwt>
```

Do not use the Supabase Studio password as the service role secret.

Do not append `/rest/v1` to the host unless n8n explicitly changes its credential format.

## Troubleshooting

If n8n credential test fails but Supabase opens in the browser:

1. Test `/rest/v1/` with the service-role key from the VM.
2. Test DNS resolution from inside `n8n-server`.
3. If DNS resolves to the wrong IP, add or update `extra_hosts` in `n8n-service/docker-compose.yml`.
4. Recreate only n8n:

```bash
cd /home/contact/n8n-service
docker compose up -d n8n
```

If Supabase Studio shows no API keys:

1. Check `.env` contains `SUPABASE_PUBLISHABLE_KEY` and `SUPABASE_SECRET_KEY`.
2. Recreate relevant Supabase services:

```bash
cd /home/contact/supabase-project/supabase/docker
docker compose up -d --wait --force-recreate studio kong auth
```

If nginx breaks:

```bash
sudo nginx -t
sudo journalctl -u nginx --no-pager -n 100
```

If a service is unhealthy:

```bash
docker ps
docker logs --tail 200 <container-name>
docker inspect <container-name> --format '{{json .State.Health}}'
```

If Crawl4AI is offline:

```bash
docker ps -a --filter name=crawl4ai
docker logs --tail 120 crawl4ai
docker update --restart unless-stopped crawl4ai
docker start crawl4ai
curl -sS http://127.0.0.1:11235/health
```

## Final Report Expected From Agent

At the end of deployment, report:

```text
n8n URL
Supabase URL
Container status summary
nginx config test result
Certbot certificate status
n8n-to-Supabase API test result
Crawl4AI health status
Any credentials the operator must rotate or preserve
```
