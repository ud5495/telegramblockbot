from __future__ import annotations

HELP_TEXT = (
    "🛡️ <b>Link Guard Bot</b>\n\n"
    "I remove links, @mentions and invite links from members who are not admins.\n\n"
    "<b>Commands</b>\n"
    "/settings — show and change this chat's settings\n"
    "/toggle — enable or disable the bot here\n"
    "/whitelist add|remove|list domain|user &lt;value&gt; — manage exceptions\n"
    "/stats — moderation counters\n"
    "/rules — show the rules\n"
    "/help — this message\n\n"
    "Add me as an administrator with <i>delete messages</i> and "
    "<i>restrict members</i> permissions."
)

RULES_TEXT = (
    "📋 <b>Chat rules</b>\n"
    "• No links, invite links or advertising\n"
    "• No @mentions of channels or users for promotion\n"
    "• No spam or flooding\n"
    "• Respect other members\n\n"
    "Violations are removed automatically. Repeat violations lead to a mute or ban."
)

GROUP_ONLY = "This command only works in groups."
ADMIN_ONLY = "Only group administrators can use this command."
BOT_MISSING_PERMISSIONS = (
    "⚠️ I need administrator rights with <i>delete messages</i> and "
    "<i>restrict members</i> to moderate this chat."
)
ENABLED = "✅ Moderation enabled in this chat."
DISABLED = "⏸️ Moderation disabled in this chat."
SETTINGS_UPDATED = "Settings updated."
WHITELIST_USAGE = (
    "Usage:\n"
    "/whitelist add domain example.com\n"
    "/whitelist remove domain example.com\n"
    "/whitelist add user @username\n"
    "/whitelist add user 123456789\n"
    "/whitelist list [domain|user]"
)
WHITELIST_EMPTY = "The whitelist is empty."
WHITELIST_ADDED = "✅ Added {value} to the {kind} whitelist."
WHITELIST_REMOVED = "🗑️ Removed {value} from the {kind} whitelist."
WHITELIST_NOT_FOUND = "{value} is not in the {kind} whitelist."
WHITELIST_INVALID = "Kind must be 'domain' or 'user'."
STATS_TEXT = (
    "📊 <b>Moderation stats</b>\n"
    "Deleted: {deleted}\n"
    "Warned: {warned}\n"
    "Muted: {muted}\n"
    "Banned: {banned}\n"
    "Captcha passed: {captcha_passed}\n"
    "Captcha failed: {captcha_failed}"
)
SETTINGS_TEXT = (
    "⚙️ <b>Settings for this chat</b>\n"
    "Enabled: {enabled}\n"
    "Action: <code>{action}</code>\n"
    "Captcha for new members: {captcha}\n"
    "Warn limit: {warn_limit}\n"
    "Whitelisted domains: {domains}\n"
    "Whitelisted users: {users}"
)
WARNING = (
    "⚠️ {name}, your message was removed. Links, mentions and invite links "
    "are not allowed here.\nStrike: {count}"
)
CAPTCHA_PROMPT = "👋 Welcome {name}! Please tap the button below within {timeout} seconds to prove you are human."
CAPTCHA_BUTTON = "✅ I'm human"
CAPTCHA_PASSED = "✅ Verified. Welcome to the chat!"
CAPTCHA_EXPIRED = "This verification expired. Please contact an administrator."
CAPTCHA_NOT_YOURS = "This verification is not for you."
CAPTCHA_TIMEOUT_KICK = "⌛ {name} did not verify in time and was removed."
HEALTH_TEXT = "OK"
