# Crawl4AI Service

Docker deployment for Crawl4AI.

The current live VM runs:

```text
container: crawl4ai
image: unclecode/crawl4ai:0.8.0
port: 11235
health endpoint: http://127.0.0.1:11235/health
restart policy: unless-stopped
```

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yml` | Runs the `crawl4ai` container. |
| `.env.example` | Safe template for required API tokens. |
| `.env` | VM-only runtime secrets. Do not commit. |

## Deploy On VM

```bash
mkdir -p /home/contact/crawl4ai-service
cd /home/contact/crawl4ai-service
cp /home/contact/Automation-Installation/crawl4ai-service/docker-compose.yml docker-compose.yml
cp /home/contact/Automation-Installation/crawl4ai-service/.env.example .env
nano .env
docker compose up -d
```

## Existing Container Recovery

If the existing container is stopped but still present, bring it back without recreating it:

```bash
docker update --restart unless-stopped crawl4ai
docker start crawl4ai
```

## Verify

```bash
docker ps --filter name=crawl4ai
curl -sS http://127.0.0.1:11235/health
curl -sS http://127.0.0.1:11235/openapi.json | head -c 300
```

Expected health response:

```json
{"status":"ok"}
```

The actual response also includes timestamp and version fields.

## Public Exposure

The current VM exposes port `11235` directly:

```text
0.0.0.0:11235->11235/tcp
```

If a public HTTPS domain is required later, add a dedicated nginx site and TLS certificate instead of reusing the n8n or Supabase domains.

