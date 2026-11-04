# Examples

Two minimal bots that show the two ways of receiving updates from Telegram.
The webhook example reuses the project's configuration validation; run it as a
module from the repository root so the `bot/` package is importable.

| File | Mode | Needs |
| --- | --- | --- |
| `polling.py` | Long-polling | Only `BOT_TOKEN` |
| `webhook.py` | Webhook | `BOT_TOKEN`, public HTTPS URL, TLS |

## Polling

```bash
export BOT_TOKEN="123456:your-token"
python examples/polling.py
```

The bot long-polls Telegram. Works behind NAT and needs no domain.

## Webhook

```bash
export BOT_TOKEN="123456:your-token"
export WEBHOOK_URL="https://bot.example.com/telegram"
export WEBHOOK_SECRET="replace-with-32-or-more-random-characters"
export WEBHOOK_PORT=8443
export WEBHOOK_PATH=/telegram
python -m examples.webhook
```

Telegram must be able to reach `WEBHOOK_URL` over HTTPS on public port 443, 80,
88 or 8443; an omitted port defaults to 443. `WEBHOOK_PATH` must exactly match
the URL path, and `WEBHOOK_SECRET` must be 32–256 characters using only ASCII
letters, digits, underscores and hyphens. Use Caddy/nginx as a reverse proxy
(see `docs/SETUP_WEBHOOK.md`) or pass `WEBHOOK_CERT`/`WEBHOOK_KEY` to let the bot
serve TLS itself.

## Switching modes

Polling and webhook are mutually exclusive. Before switching a bot back to
polling, remove the registered webhook:

```bash
curl "https://api.telegram.org/bot${BOT_TOKEN}/deleteWebhook?drop_pending_updates=true"
```

Check the current webhook:

```bash
curl "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo"
```
