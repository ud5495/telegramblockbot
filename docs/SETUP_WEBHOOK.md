# Setup — Webhook

With webhooks, Telegram pushes updates to your bot over HTTPS instead of the
bot polling. It is the better choice for production: lower latency and less
resource usage, at the cost of needing a public HTTPS endpoint.

## How it works

```
Telegram  ──HTTPS──▶  Caddy/nginx (:443)  ──HTTP──▶  bot (:8443)
```

Telegram only delivers webhooks on public ports **443, 80, 88 or 8443**. An
omitted `WEBHOOK_URL` port defaults to 443; other explicit public ports are
rejected. In practice you use 443 via a reverse proxy that handles TLS. The
local `WEBHOOK_PORT` is independent and may be any port from 1 to 65535.

## Requirements

- A server with a public IP (VPS, Fly.io, Railway, Render, etc.)
- A domain or subdomain, e.g. `bot.example.com`, with a DNS A/AAAA record
  pointing to the server
- A valid TLS certificate (Caddy does this automatically)
- The bot token and the `python-telegram-bot` webhook dependencies

## 1. Configure DNS and the server

Point `bot.example.com` at your server. Make sure ports 80 and 443 are open in
the firewall (80 is used for the ACME HTTP challenge by Caddy).

## 2. Configure the bot

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
BOT_TOKEN=123456789:AA...
MODE=webhook

WEBHOOK_URL=https://bot.example.com/telegram
WEBHOOK_LISTEN=127.0.0.1
WEBHOOK_PORT=8443
WEBHOOK_PATH=/telegram
WEBHOOK_SECRET=replace-with-32-or-more-random-characters
```

- `WEBHOOK_URL` is the public URL Telegram will call. It must use HTTPS, include
  a hostname, and have no query string or fragment.
- `WEBHOOK_PATH` must exactly match the path in `WEBHOOK_URL`.
- `WEBHOOK_LISTEN=127.0.0.1` keeps the bot reachable only from the local proxy.
  Use `0.0.0.0` if the bot itself must be reachable from outside (e.g. in a
  container); this is required for the Compose + Caddy deployment.
- `WEBHOOK_SECRET` is required and must be 32–256 characters long using only
  ASCII letters, digits, underscores or hyphens. Telegram sends it in the
  `X-Telegram-Bot-Api-Secret-Token` header and the library validates it.

Generate a secret:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

## 3. Option A — Caddy reverse proxy (recommended)

Install Caddy, then use the `Caddyfile` in this repository:

```
bot.example.com {
    encode gzip
    reverse_proxy 127.0.0.1:8443
}
```

```bash
caddy run --config Caddyfile
# or as a service:
sudo systemctl enable --now caddy
```

Caddy fetches and renews the certificate automatically.

### Docker variant

The repository ships a compose profile that runs the bot and Caddy together:

```bash
# .env
# MODE=webhook
# WEBHOOK_URL=https://bot.example.com/telegram
# WEBHOOK_LISTEN=0.0.0.0
# WEBHOOK_PORT=8443
# WEBHOOK_PATH=/telegram
# WEBHOOK_SECRET=replace-with-32-or-more-random-characters
# WEBHOOK_DOMAIN=bot.example.com   (read by Caddy)
docker compose --profile webhook up -d --build
docker compose logs -f bot caddy
```

The `Caddyfile` proxies `{$WEBHOOK_DOMAIN}` to the `bot` service on `8443`.
Existing `./data` bind-mount contents do not migrate automatically; see the
[one-time volume migration](../README.md#one-time-volume-migration) if upgrading.

## 4. Option B — the bot serves TLS itself

If you do not want a reverse proxy, let the bot terminate TLS. Telegram still
requires port 443/80/88/8443; the bot must be reachable directly on that port.

```dotenv
MODE=webhook
WEBHOOK_URL=https://bot.example.com:8443/telegram
WEBHOOK_LISTEN=0.0.0.0
WEBHOOK_PORT=8443
WEBHOOK_PATH=/telegram
WEBHOOK_SECRET=replace-with-32-or-more-random-characters
WEBHOOK_CERT=/etc/letsencrypt/live/bot.example.com/fullchain.pem
WEBHOOK_KEY=/etc/letsencrypt/live/bot.example.com/privkey.pem
```

Obtain the certificate with Certbot (standalone or DNS challenge) and make sure
the files are readable by the bot's user.

## 5. Option C — managed platform

Fly.io, Railway, Render and similar give you a public HTTPS URL with TLS
already configured:

1. Deploy the repository (they run `main.py` from the `Dockerfile`).
2. Set the environment variables above.
3. Set `WEBHOOK_URL` to the platform-provided URL plus a path, e.g.
   `https://mybot.fly.dev/telegram`.
4. Use an unguessable `WEBHOOK_PATH` and always set `WEBHOOK_SECRET`.

## 6. Local testing with a tunnel

No public IP? Use a tunnel:

```bash
# cloudflared
cloudflared tunnel --url http://localhost:8443

# or ngrok
ngrok http 8443
```

Set `WEBHOOK_URL` to the tunnel URL plus your path. The bot's `run_webhook`
registers the webhook on startup.

## 7. Verify registration

```bash
curl "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo"
```

A healthy response has:

- `"url": "https://bot.example.com/telegram"`
- `"pending_update_count": 0`
- no `last_error_message`

## 8. Security checklist

- [ ] `WEBHOOK_URL` uses HTTPS with a valid certificate and a supported public
  port (443 when omitted)
- [ ] `WEBHOOK_SECRET` is random, 32–256 characters long, and uses only ASCII
  letters, digits, `_` or `-`
- [ ] `WEBHOOK_PATH` is hard to guess (e.g. `/telegram/<random>`)
- [ ] Firewall: only 80/443 exposed; bot port 8443 bound to localhost
- [ ] Token rotated if it was ever committed
- [ ] `.env` is not in git
- [ ] Only one instance runs (webhook and polling are mutually exclusive)

## Switching back to polling

Remove the webhook first:

```bash
curl "https://api.telegram.org/bot${BOT_TOKEN}/deleteWebhook?drop_pending_updates=true"
```

Then set `MODE=polling` and restart.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `WEBHOOK_URL is required` | Set `MODE=webhook` **and** `WEBHOOK_URL` |
| `GET https://... 502` | Proxy is up but the bot is not listening; check `WEBHOOK_LISTEN`/`WEBHOOK_PORT` |
| TLS errors from Telegram | Certificate invalid/expired; check Caddy logs |
| `Wrong response from the webhook: 403` | Secret/path mismatch; check `WEBHOOK_SECRET` and `WEBHOOK_PATH` |
| Updates stop after redeploy | `drop_pending_updates=True` is on; check `getWebhookInfo` |
| Port not reachable | Telegram only uses 443/80/88/8443; expose one of these |
