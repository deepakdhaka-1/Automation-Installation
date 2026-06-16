# Automation Installation

Deployment repository for the VM-hosted AlphaAgentX automation services.

This repo focuses on the services running on the VM:

| Public URL | Service | Runtime | Internal Target |
| --- | --- | --- | --- |
| `https://development.alphaagentx.com` | n8n | Docker Compose | `http://127.0.0.1:5678` |
| `https://supabase.alphaagentx.com` | Supabase | Docker Compose | `http://127.0.0.1:8000` |
| direct VM port `11235` | Crawl4AI | Docker container / Compose | `http://127.0.0.1:11235` |

The VM public IP is:

```text
35.236.134.87
```

## Repository Layout

```text
n8n-service/
  docker-compose.yml
  .env.example
  README.md

supabase-service/
  .env.example
  README.md

crawl4ai-service/
  docker-compose.yml
  .env.example
  README.md

nginx-sites/
  n8n.conf
  supabase.conf
  README.md
```

## DNS Required

Create these DNS records before issuing HTTPS certificates:

```text
A development 35.236.134.87
A supabase    35.236.134.87
```

The root domain `alphaagentx.com` can point somewhere else. The service subdomains above must point to the VM.

Avoid conflicting `AAAA` records for these service hostnames unless the VM has a working public IPv6 address.

## Deploy Order

1. Configure DNS records.
2. Deploy n8n from `n8n-service/`.
3. Deploy Supabase from `supabase-service/`.
4. Deploy or recover Crawl4AI from `crawl4ai-service/`.
5. Install nginx configs from `nginx-sites/`.
6. Run Certbot for both hostnames.
7. Verify n8n can connect to Supabase and Crawl4AI is healthy.

## VM Runtime Paths

| VM Path | Purpose |
| --- | --- |
| `/home/contact/n8n-service` | n8n Docker Compose project |
| `/home/contact/supabase-project/supabase/docker` | Supabase Docker Compose project |
| `/home/contact/crawl4ai-service` | Crawl4AI Docker Compose project |
| `/etc/nginx/sites-available/n8n` | n8n nginx server block |
| `/etc/nginx/sites-available/supabase` | Supabase nginx server block |

## Secrets

Do not commit real `.env` files. Commit only `.env.example` templates.

Real runtime secrets live on the VM only.

## Health Checks

Run on the VM:

```bash
docker ps
cd /home/contact/n8n-service && docker compose ps
cd /home/contact/supabase-project/supabase/docker && docker compose ps
docker ps --filter name=crawl4ai
curl -sS http://127.0.0.1:11235/health
sudo nginx -t
```

Expected result: n8n is running, Supabase services are healthy, Crawl4AI health returns `ok`, and nginx config validates.
