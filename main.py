from __future__ import annotations

import logging

from bot.application import ALLOWED_UPDATES, build_application
from bot.config import ConfigError, load_settings
from bot.logging_config import setup_logging

logger = logging.getLogger(__name__)


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    setup_logging(settings.log_level)
    logger.info("Starting bot in %s mode", settings.mode)

    application = build_application(settings)

    if settings.mode == "webhook":
        logger.info(
            "Listening for webhooks on %s:%s",
            settings.webhook_listen,
            settings.webhook_port,
        )
        application.run_webhook(
            listen=settings.webhook_listen,
            port=settings.webhook_port,
            url_path=settings.webhook_path.lstrip("/"),
            webhook_url=settings.webhook_url,
            secret_token=settings.webhook_secret,
            cert=settings.webhook_cert,
            key=settings.webhook_key,
            drop_pending_updates=True,
            allowed_updates=ALLOWED_UPDATES,
        )
    else:
        logger.info("Polling Telegram for updates")
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=ALLOWED_UPDATES,
        )


if __name__ == "__main__":
    main()
