# Telegram Link Guard Bot

A Telegram group moderation bot that removes **links, @mentions and invite
links** posted by non-admins, with per-chat settings, whitelists, progressive
enforcement, anti-flood, new-member captcha and an optional audit channel.

It runs in **polling** mode (no infrastructure) or **webhook** mode (production
grade) from the same codebase — pick with a single environment variable.

## Features

- Detects links via Telegram **entities** *and* regex, so hidden links
  (`text_link`), captions, edited messages and forwarded posts are covered.
- Admin detection with a TTL cache (no API call per message).
- Per-chat settings stored in SQLite; admins manage them with commands.
- Domain and user whitelists to avoid false positives.
- Progressive enforcement: `delete`, `warn` (escalates to mute), `mute`, `ban`.
- Anti-flood and duplicate-message detection.
- Optional captcha for new members (tap to verify, auto-kick on timeout).
- Optional audit channel for every moderation action.
- Runs with **polling** or **webhook**; Docker + Caddy included.

## Requirements

- Python 3.10–3.13 (Docker uses 3.13)
- A bot token from [@BotFather](https://t.me/BotFather)
- The bot added to a group as **administrator** with *Delete messages*,
  *Restrict members* and (for bans) *Ban users*

## Quick start (polling)

```bash
git clone <your-repo-url> telegramblockbot
cd telegramblockbot

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: set BOT_TOKEN and keep MODE=polling

python main.py
```

If `python3 -m venv` is unavailable on Debian/Ubuntu, install `python3-venv`
or use Docker (below).

Add the bot to your group, promote it to administrator, then send `/start`.
Full walkthrough: [docs/SETUP_POLLING.md](docs/SETUP_POLLING.md).

## Webhook mode

Webhook requires a public HTTPS endpoint. The quickest path is Caddy, which
obtains TLS automatically:

```bash
# .env
MODE=webhook
WEBHOOK_URL=https://bot.example.com/telegram
WEBHOOK_LISTEN=0.0.0.0
WEBHOOK_PORT=8443
WEBHOOK_PATH=/telegram
WEBHOOK_SECRET=replace-with-32-or-more-random-characters
WEBHOOK_DOMAIN=bot.example.com   # used by Caddy in Docker
```

```bash
docker compose --profile webhook up -d --build
curl "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo"
```

Full walkthrough (DNS, TLS, reverse proxy, self-hosted certs, managed hosts,
local tunnels, security): [docs/SETUP_WEBHOOK.md](docs/SETUP_WEBHOOK.md).
Minimal runnable examples: [`examples/`](examples/README.md).

> Polling and webhook are mutually exclusive. Before switching back to polling,
> call `deleteWebhook` (see the webhook guide).

## Configuration

All settings come from environment variables (or `.env`). See `.env.example`.

| Variable | Default | Description |
| --- | --- | --- |
| `BOT_TOKEN` | — | **Required.** Bot token from BotFather |
| `MODE` | `polling` | `polling` or `webhook` |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `DB_PATH` | `data/bot.db` | SQLite database path |
| `ADMIN_CACHE_TTL` | `300` | Seconds to cache chat admins |
| `DEFAULT_ACTION` | `delete` | `delete`, `warn`, `mute` or `ban` for new chats |
| `WARN_LIMIT` | `3` | Strikes before `warn` escalates to mute |
| `MUTE_SECONDS` | `600` | Mute duration |
| `VIOLATION_DECAY_SECONDS` | `86400` | Time after which strikes reset |
| `FLOOD_MAX_MESSAGES` | `5` | Messages allowed per window |
| `FLOOD_WINDOW_SECONDS` | `10` | Anti-flood window |
| `CAPTCHA_ENABLED` | `false` | Default captcha for new chats |
| `CAPTCHA_TIMEOUT` | `120` | Seconds to solve the captcha |
| `CAPTCHA_KICK_ON_TIMEOUT` | `true` | Kick users who fail the captcha |
| `AUDIT_CHAT_ID` | — | Chat id that receives audit messages |
| `WEBHOOK_URL` | — | HTTPS URL with hostname; ports: `443` (default), `80`, `88`, `8443`; no query/fragment |
| `WEBHOOK_LISTEN` | `0.0.0.0` | Webhook bind address; must be `0.0.0.0` for Compose + Caddy |
| `WEBHOOK_PORT` | `8443` | Local webhook bind port (`1`–`65535`) |
| `WEBHOOK_PATH` | `/telegram` | Must exactly match the public `WEBHOOK_URL` path |
| `WEBHOOK_SECRET` | — | Required for webhooks: 32–256 ASCII letters/digits/`_`/`-` |
| `WEBHOOK_CERT` | — | TLS certificate (self-managed TLS) |
| `WEBHOOK_KEY` | — | TLS private key (self-managed TLS) |

Per-chat defaults are applied when the bot first sees a chat; admins can change
them later with `/settings`. `DEFAULT_ACTION` and `CAPTCHA_ENABLED` only affect
newly seen chats.

## Commands

| Command | Who | Description |
| --- | --- | --- |
| `/start`, `/help` | everyone | Show help |
| `/rules` | everyone | Show the rules |
| `/settings` | admins | Show/edit enabled, action and captcha (inline buttons) |
| `/toggle` | admins | Enable or disable moderation in this chat |
| `/whitelist add domain <domain>` | admins | Allow a domain (e.g. `youtube.com`) |
| `/whitelist add user <@name\|id>` | admins | Allow a user |
| `/whitelist remove domain\|user <value>` | admins | Remove an exception |
| `/whitelist list [domain\|user]` | admins | List exceptions |
| `/stats` | admins | Show moderation counters |
| `/health` | everyone | Health check (`OK`) |

## How detection works

1. **Entities** — `url`, `text_link`, `mention`, `text_mention`. This catches
   links hidden behind innocent display text.
2. **Regex** over `text or caption` — `https?://`, `www.`, `t.me/`,
   `telegram.me/`, `tg://`, `@username`.
3. **Whitelist** — a finding is ignored when its domain or username is
   whitelisted. If everything is whitelisted, the message is left alone.

Admins, anonymous senders, bots and whitelisted users are never moderated.
Edited messages are re-checked; captions are handled like normal text.

## Project structure

```
main.py                 # entrypoint: polling or webhook
bot/
  config.py             # env configuration
  application.py        # application + handler wiring
  db.py                 # SQLite storage
  cache.py              # admin TTL cache
  detectors.py          # link/mention detection
  moderation.py         # enforcement helpers + anti-flood
  messages.py           # English strings
  keyboards.py          # inline keyboards
  handlers/             # message, command, member and error handlers
examples/               # minimal polling and webhook bots
docs/                   # setup guides and architecture
tests/                  # pytest suite
Dockerfile
docker-compose.yml
Caddyfile
```

More detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Deployment

### Docker (polling)

```bash
cp .env.example .env    # BOT_TOKEN=..., MODE=polling
docker compose up -d --build
docker compose logs -f bot
```

### Docker (webhook + Caddy)

```bash
# .env: MODE=webhook, WEBHOOK_URL, WEBHOOK_SECRET, WEBHOOK_DOMAIN
docker compose --profile webhook up -d --build
```

The SQLite database is persisted in the `bot_data` Docker named volume, mounted
at `/app/data` and initialized with ownership for the non-root bot user.

### One-time volume migration

Existing `./data` bind-mount contents are **not imported automatically**. Stop
the old bot and back up its database before migrating. To import
`./data/bot.db` without overwriting a database already in `bot_data`, and then
fix the volume ownership, run:

```bash
docker compose stop bot
docker compose run --rm --user root \
  --volume "$PWD/data:/migration:ro" \
  --entrypoint sh bot \
  -c 'cp -n /migration/bot.db /app/data/bot.db &&
      chown -R 10001:10001 /app/data'
docker compose up -d bot
```

With the bot stopped, repair ownership of an existing `bot_data` volume without
importing host data:

```bash
docker compose run --rm --user root --entrypoint sh bot \
  -c 'chown -R 10001:10001 /app/data'
```

### systemd

See [docs/SETUP_POLLING.md](docs/SETUP_POLLING.md#7-run-as-a-systemd-service).

## Testing

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## Security

- **Rotate the bot token** if it was ever committed. The original version of
  this repository contained a hardcoded token; revoke it in BotFather.
- Never commit `.env`; it is ignored by `.gitignore`.
- In webhook mode use a random `WEBHOOK_SECRET` of 32–256 permitted characters
  and an unguessable `WEBHOOK_PATH` that exactly matches the URL.
- Run the bot as an unprivileged user (the Docker image does).
- Optional: purge the leaked token from git history with `git filter-repo` or
  BFG.
