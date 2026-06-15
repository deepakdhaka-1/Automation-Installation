# n8n Service

Docker Compose deployment for n8n at:

```text
https://development.alphaagentx.com
```

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.yml` | Runs the `n8n-server` container. |
| `.env.example` | Safe template for required n8n variables. |
| `.env` | VM-only runtime secrets. Do not commit. |
| `n8n-data/` | VM-only persistent n8n data. Do not commit. |

## Deploy On VM

```bash
mkdir -p /home/contact/n8n-service
cd /home/contact/n8n-service
cp .env.example .env
nano .env
docker compose up -d
```

The live deployment uses:

```text
container: n8n-server
public URL: https://development.alphaagentx.com
internal port: 5678
data directory: /home/contact/n8n-service/n8n-data
```

## nginx Route

nginx terminates HTTPS for `development.alphaagentx.com` and proxies traffic to:

```text
http://localhost:5678
```

Use `../nginx-sites/n8n.conf`.

## Supabase Connection

n8n connects to the self-hosted Supabase API using:

```text
Host: https://supabase.alphaagentx.com
Service Role Secret: Supabase service_role JWT
```

The compose file includes this required host override:

```yaml
extra_hosts:
  - "supabase.alphaagentx.com:35.236.134.87"
```

This keeps the n8n container pointed at the VM-hosted Supabase service even if external DNS resolves the hostname incorrectly from inside Docker.

## Verify

```bash
cd /home/contact/n8n-service
docker compose ps
docker exec n8n-server node -e "require('dns').lookup('supabase.alphaagentx.com',{all:true},(e,a)=>console.log(e||a))"
curl -I https://development.alphaagentx.com
```

Expected DNS result inside the n8n container:

```text
35.236.134.87
```
