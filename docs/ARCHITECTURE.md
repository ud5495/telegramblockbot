# Architecture

## Module map

| Module | Responsibility |
| --- | --- |
| `main.py` | Loads settings, builds the application, runs polling **or** webhook |
| `bot/config.py` | Reads and validates environment variables into `Settings` |
| `bot/logging_config.py` | Logging setup, noisy library suppression |
| `bot/application.py` | Builds `Application`, registers handlers and jobs |
| `bot/db.py` | Async SQLite storage (chats, whitelist, violations, stats, captcha) |
| `bot/cache.py` | TTL cache for chat administrator ids |
| `bot/detectors.py` | Link/@mention/invite detection from entities + regex |
| `bot/moderation.py` | Violation escalation, mute/ban/kick, anti-flood tracker |
| `bot/keyboards.py` | Inline keyboards (settings, captcha) |
| `bot/messages.py` | English user-facing strings |
| `bot/handlers/messages.py` | New/edited message moderation |
| `bot/handlers/commands.py` | `/settings`, `/toggle`, `/whitelist`, `/stats`, ... |
| `bot/handlers/members.py` | New-member captcha + admin cache invalidation |
| `bot/handlers/common.py` | Admin guard, audit logging, accessors |
| `bot/handlers/errors.py` | Global error handler |

## Message flow

```
Update ──▶ MessageHandler
              │
              ▼
        effective chat/user checks
              │
              ▼
        ensure chat row in SQLite
              │
              ▼
   feature enabled for this chat? ── no ──▶ stop
              │ yes
              ▼
        sender is admin? ── yes ──▶ stop
              │ no
              ▼
        sender whitelisted? ── yes ──▶ stop
              │ no
              ▼
        anti-flood / duplicate tracker ── hit ──▶ delete (+ mute) ──▶ audit
              │ clean
              ▼
        detectors.detect_message()
              │ no findings ──▶ stop
              ▼ yes
        delete message
              │
              ▼
        record violation (with decay)
              │
              ▼
        choose_action(action, count, warn_limit)
              │
     ┌────────┼─────────┬────────┐
   warn     delete     mute     ban
     │        │          │        │
  reply    stat++     restrict  ban
  +job                  user
              │
              ▼
        audit channel
```

Edited messages go through the same pipeline; the original is no longer
available, so the edited message is deleted on violation.

## Detection strategy

Two layers, because users hide links in different ways:

1. **Message entities** (authoritative): `url`, `text_link`, `mention`,
   `text_mention`. Catches inline links whose display text is innocuous.
2. **Regex fallback** over `text or caption`: `https?://`, `www.`, `t.me/`,
   `telegram.me/`, `tg://`, and `@username`. Catches plain text that Telegram
   did not turn into entities.

Whitelists are applied per item: a finding is kept only when neither its domain
nor its username is whitelisted. If all findings are whitelisted, the message is
clean.

## Enforcement

`choose_action(base_action, count, warn_limit)`:

| Base action | Result |
| --- | --- |
| `delete` | delete only |
| `warn` | warn; escalate to **mute** at `count >= warn_limit` |
| `mute` | delete + mute for `MUTE_SECONDS` |
| `ban` | delete + ban |

Violations decay after `VIOLATION_DECAY_SECONDS`; a user who behaves starts
from strike 1 again. Message deletion and subsequent enforcement are attempted
independently because Telegram permissions can differ. Statistics and audit
messages record the actual deletion and enforcement outcomes separately.

## Admin cache

`AdminCache` stores `chat_id -> (expires_at, admin_ids)`. It avoids a
`get_chat_administrators` call per message and is invalidated by
`ChatMemberHandler` whenever someone is promoted or demoted.

## Captcha

When enabled per chat:

1. A unique attempt is persisted before the new member is restricted. The
   restriction has no automatic Telegram expiry; it ends only after verification
   or timeout enforcement.
2. The bot sends an attempt- and message-bound “I'm human” button. Missing
   prompts are retried by the sweeper before `CAPTCHA_TIMEOUT` expires.
3. Pressing the button leases the attempt, restores the chat's default member
   permissions, and records a persistent post-action state before statistics
   are finalized. Failed operations remain retryable without reporting success.
4. Expired attempts are kicked (ban + unban) when
   `CAPTCHA_KICK_ON_TIMEOUT=true`, or explicitly unrestricted otherwise.
5. Atomic leases prevent callback/sweeper duplication, while persistent states
   and legacy-schema upgrades allow recovery after restarts. Successful Telegram
   actions are not repeated when only database finalization needs a retry.

## SQLite schema

- `chats` — per-chat `enabled`, `action`, `captcha_enabled`.
- `whitelist` — `(chat_id, kind, value)` where kind is `domain` or `user`.
- `violations` — strike count, last violation timestamp, mute expiry.
- `stats` — counters for deleted/warned/muted/banned/captcha.
- `pending_captcha` — attempt identity, prompt, expiry, lifecycle state and
  retry lease for incomplete verifications.

The database lives at `DB_PATH` (default `data/bot.db`). In Docker, `/app/data`
is backed by the `bot_data` named volume, which is initialized from an image
directory owned by the non-root bot user (UID 10001). Older `./data` bind-mount
contents require the documented one-time migration; they are not imported
automatically.

## Modes

`Settings.mode` selects the runner in `main.py`:

- **polling** — `Application.run_polling(...)`; no infrastructure.
- **webhook** — `Application.run_webhook(...)`; requires an HTTPS `WEBHOOK_URL`
  with a hostname, no query/fragment, and a public port in `443, 80, 88, 8443`
  (omitted means 443), plus a `WEBHOOK_PATH` exactly matching its path.
  `WEBHOOK_SECRET` must be 32–256 Telegram-approved characters. The local
  `WEBHOOK_PORT` may be 1–65535. `WEBHOOK_CERT`/`WEBHOOK_KEY` remain optional
  but must be provided together.

Both use the same handlers, database and job queue, so behaviour is identical.

## Extending

- **New detection rule**: add a regex or entity type in `detectors.py`; add a
  test in `tests/test_detectors.py`.
- **New command**: add a handler in `handlers/commands.py` and register it in
  `application.py`; add its `BotCommand` to `BOT_COMMANDS`.
- **New setting**: add a column to `chats` in `db.py`, expose it via
  `/settings`, and read it in the message handler.
- **New storage**: replace the `Database` class; handlers only use its methods.
