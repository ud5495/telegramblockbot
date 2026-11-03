# Setup — Polling

Polling is the simplest way to run the bot: it asks Telegram for new updates
continuously. No domain, public IP, TLS certificate or reverse proxy is needed.
Use it for development, small groups, and quick deployments.

## 1. Requirements

- Python 3.10–3.13 (3.14 works but is less battle-tested; Docker uses 3.13)
- A Telegram account
- The bot added to a group where you are an administrator

## 2. Create the bot and get a token

1. Open Telegram and talk to [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, choose a name and a username ending in `bot`.
3. Copy the token (looks like `123456789:AA...`).

> If the token was ever committed to git, revoke it in BotFather with
> `/revoke` and use the new one.

## 3. Install

```bash
git clone <your-repo-url> telegramblockbot
cd telegramblockbot

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If `python3 -m venv` fails on Debian/Ubuntu, install `python3-venv` (or use
Python 3.13 and Docker).

## 4. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
BOT_TOKEN=123456789:AA...
MODE=polling
```

Everything else has sensible defaults. See the environment table in the main
[README](../README.md#configuration).

## 5. Add the bot to a group

1. Add the bot to your group.
2. Promote it to **administrator** and enable:
   - **Delete messages**
   - **Restrict members** (needed for mute/captcha)
   - **Ban users** (needed if `DEFAULT_ACTION=ban`)
3. Send `/start` in the group to verify it responds.

Without these permissions the bot cannot delete or mute, and moderation actions
will fail silently in the logs.

## 6. Run

```bash
source .venv/bin/activate
python main.py
```

Expected log lines:

```
INFO - Starting bot in polling mode
INFO - Polling Telegram for updates
```

Send a message with a link as a non-admin member. It should be removed.

Useful commands in the group (admins only):

```
/settings
/toggle
/whitelist add domain youtube.com
/stats
```

## 7. Run as a systemd service

Create `/etc/systemd/system/telegramblockbot.service`:

```ini
[Unit]
Description=Telegram Link Guard Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=botuser
WorkingDirectory=/opt/telegramblockbot
EnvironmentFile=/opt/telegramblockbot/.env
ExecStart=/opt/telegramblockbot/.venv/bin/python /opt/telegramblockbot/main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now telegramblockbot
sudo systemctl status telegramblockbot
journalctl -u telegramblockbot -f
```

## 8. Run with Docker

```bash
cp .env.example .env   # set BOT_TOKEN and MODE=polling
docker compose up -d --build
docker compose logs -f bot
```

The SQLite database is persisted in `./data`. Caddy is not started for polling
because it lives behind the `webhook` profile.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Configuration error: BOT_TOKEN is required` | `.env` missing or not in the working directory |
| Bot is silent | Check it is an admin with the required permissions |
| Messages are not deleted | Verify `messages` update is delivered; check logs for `Forbidden` |
| `Conflict: terminated by other getUpdates request` | Another instance is polling with the same token; stop it, or use webhook |
| Rate-limit / `RetryAfter` errors | The admin cache should prevent most calls; ensure only one instance runs |

## Switching to webhook

See [SETUP_WEBHOOK.md](SETUP_WEBHOOK.md). Polling and webhook are mutually
exclusive; setting `MODE=webhook` stops polling automatically.
