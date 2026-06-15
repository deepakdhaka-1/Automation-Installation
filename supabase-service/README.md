# Supabase Service

Self-hosted Supabase Docker deployment at:

```text
https://supabase.alphaagentx.com
```

Self-hosted Supabase behaves like one Supabase project per Docker stack.

## VM Paths

```text
/home/contact/supabase-project/supabase/docker
/home/contact/supabase-project/supabase/docker/.env
/home/contact/supabase-project/supabase/docker/docker-compose.yml
/home/contact/supabase-project/supabase/docker/volumes
```

Do not commit the live `.env` file or `volumes/` data.

## Deploy On VM

The live server follows Supabase's official Docker self-hosting setup.

```bash
cd /home/contact
git clone --depth 1 https://github.com/supabase/supabase supabase-project
cd /home/contact/supabase-project/supabase/docker
cp .env.example .env
nano .env
docker compose pull
docker compose up -d --wait
```

Set these public URL values in the VM `.env`:

```text
SUPABASE_PUBLIC_URL=https://supabase.alphaagentx.com
API_EXTERNAL_URL=https://supabase.alphaagentx.com
SITE_URL=https://supabase.alphaagentx.com
KONG_HTTP_PORT=8000
KONG_HTTPS_PORT=8443
```

## Required Keys

Self-hosted Supabase uses env-based API keys.

| Variable | Purpose |
| --- | --- |
| `ANON_KEY` | Public anon JWT used by Supabase services. |
| `SERVICE_ROLE_KEY` | Privileged service-role JWT. |
| `SUPABASE_PUBLISHABLE_KEY` | Publishable key shown in Studio. |
| `SUPABASE_SECRET_KEY` | Secret/service-role key shown in Studio. |

For n8n credentials use:

```text
Host: https://supabase.alphaagentx.com
Service Role Secret: service_role JWT
```

## nginx Route

nginx terminates HTTPS for `supabase.alphaagentx.com` and proxies traffic to:

```text
http://127.0.0.1:8000
```

Use `../nginx-sites/supabase.conf`.

## Verify

```bash
cd /home/contact/supabase-project/supabase/docker
docker compose ps
curl -I http://127.0.0.1:8000
curl -I https://supabase.alphaagentx.com
```

All Supabase containers should be healthy.

To verify API access, call `/rest/v1/` with both headers:

```text
apikey: <service_role_jwt>
Authorization: Bearer <service_role_jwt>
```

