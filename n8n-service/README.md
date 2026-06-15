# n8n Service

Docker Compose deployment for n8n.

## Files

| File | Commit to GitHub | Purpose |
| --- | --- | --- |
| `docker-compose.yml` | Yes | Runs the n8n container. |
| `.env.example` | Yes | Documents required env variables without secrets. |
| `.env` | No | Real VM-only runtime secrets. |
| `n8n-data/` | No | Runtime n8n data volume folder. |

## Important Runtime Fix

The deployed VM currently needs this host override because the VM resolver was sending `supabase.alphaagentx.com` to the wrong public IP.

```yaml
extra_hosts:
  - "supabase.alphaagentx.com:35.236.134.87"
```

This makes n8n resolve Supabase to the same VM where Supabase is running.

## Deploy

Run on the VM:

```bash
cd /home/contact/n8n-service
docker compose up -d
```

## Verify Supabase From n8n

Run on the VM:

```bash
docker exec n8n-server node -e "require('dns').lookup('supabase.alphaagentx.com',{all:true},(e,a)=>console.log(e||a))"
```

Expected result:

```text
35.236.134.87
```

## GitHub Cleanup

Delete `n8n-service/.env` from GitHub if it exists. Real values belong only on the VM.
