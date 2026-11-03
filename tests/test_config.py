from pathlib import Path

import pytest

from bot.config import ConfigError, load_settings


def test_token_is_required():
    with pytest.raises(ConfigError):
        load_settings({})


def test_defaults():
    settings = load_settings({"BOT_TOKEN": "123:abc"})
    assert settings.mode == "polling"
    assert settings.default_action == "delete"
    assert settings.db_path == Path("data/bot.db")
    assert settings.webhook_port == 8443


def test_booleans_are_parsed():
    settings = load_settings(
        {
            "BOT_TOKEN": "123:abc",
            "CAPTCHA_ENABLED": "yes",
            "CAPTCHA_KICK_ON_TIMEOUT": "off",
        }
    )
    assert settings.captcha_enabled is True
    assert settings.captcha_kick_on_timeout is False


def test_invalid_boolean_is_rejected():
    with pytest.raises(ConfigError):
        load_settings({"BOT_TOKEN": "123:abc", "CAPTCHA_ENABLED": "maybe"})


def test_invalid_action_is_rejected():
    with pytest.raises(ConfigError):
        load_settings({"BOT_TOKEN": "123:abc", "DEFAULT_ACTION": "nuke"})


def test_invalid_mode_is_rejected():
    with pytest.raises(ConfigError):
        load_settings({"BOT_TOKEN": "123:abc", "MODE": "carrier-pigeon"})


def test_webhook_requires_https_url():
    with pytest.raises(ConfigError):
        load_settings({"BOT_TOKEN": "123:abc", "MODE": "webhook"})
    with pytest.raises(ConfigError):
        load_settings(
            {
                "BOT_TOKEN": "123:abc",
                "MODE": "webhook",
                "WEBHOOK_URL": "http://example.com",
            }
        )


def test_webhook_config_is_accepted():
    settings = load_settings(
        {
            "BOT_TOKEN": "123:abc",
            "MODE": "webhook",
            "WEBHOOK_URL": "https://example.com/telegram",
            "WEBHOOK_SECRET": "super-secret",
        }
    )
    assert settings.mode == "webhook"
    assert settings.webhook_url == "https://example.com/telegram"
    assert settings.webhook_secret == "super-secret"
