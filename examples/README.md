# Examples

Two minimal, self-contained bots that show the two ways of receiving updates
from Telegram. They do not import the `bot/` package on purpose, so you can read
them end to end.

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
export WEBHOOK_SECRET="a-long-random-string"
export WEBHOOK_PORT=8443
export WEBHOOK_PATH=/telegram
python examples/webhook.py
```

Telegram must be able to reach `WEBHOOK_URL` over HTTPS on port 443, 80, 88 or
8443. Use Caddy/nginx as a reverse proxy (see `docs/SETUP_WEBHOOK.md`) or pass
`WEBHOOK_CERT`/`WEBHOOK_KEY` to let the bot serve TLS itself.

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
