# nginx Sites

Reverse proxy configuration for the VM services.

## Files

| Repo File | VM Path | Route |
| --- | --- | --- |
| `n8n.conf` | `/etc/nginx/sites-available/n8n` | `development.alphaagentx.com` to `localhost:5678` |
| `supabase.conf` | `/etc/nginx/sites-available/supabase` | `supabase.alphaagentx.com` to `127.0.0.1:8000` |

## Install On VM

```bash
sudo cp n8n.conf /etc/nginx/sites-available/n8n
sudo cp supabase.conf /etc/nginx/sites-available/supabase
sudo ln -sfn /etc/nginx/sites-available/n8n /etc/nginx/sites-enabled/n8n
sudo ln -sfn /etc/nginx/sites-available/supabase /etc/nginx/sites-enabled/supabase
sudo nginx -t
sudo systemctl reload nginx
```

## TLS With Certbot

DNS must point to the VM before running Certbot.

```bash
sudo certbot --nginx -d development.alphaagentx.com
sudo certbot --nginx -d supabase.alphaagentx.com
sudo nginx -t
sudo systemctl reload nginx
```

## Verify

```bash
curl -I https://development.alphaagentx.com
curl -I https://supabase.alphaagentx.com
```

