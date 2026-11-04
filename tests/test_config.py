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


VALID_WEBHOOK_SECRET = "A" * 28 + "_b-9"


def valid_webhook_env(**overrides: str) -> dict[str, str]:
    env = {
        "BOT_TOKEN": "123:abc",
        "MODE": "webhook",
        "WEBHOOK_URL": "https://example.com/telegram",
        "WEBHOOK_PATH": "/telegram",
        "WEBHOOK_SECRET": VALID_WEBHOOK_SECRET,
    }
    env.update(overrides)
    return env


def test_webhook_requires_https_url():
    with pytest.raises(ConfigError, match="WEBHOOK_URL"):
        load_settings({"BOT_TOKEN": "123:abc", "MODE": "webhook"})
    with pytest.raises(ConfigError, match="WEBHOOK_URL"):
        load_settings(
            valid_webhook_env(WEBHOOK_URL="http://example.com/telegram")
        )


@pytest.mark.parametrize(
    "url",
    [
        "https:///telegram",
        "https://example.com/telegram?source=telegram",
        "https://example.com/telegram#updates",
        "https://example.com/telegram?",
        "https://example.com:invalid/telegram",
    ],
)
def test_invalid_webhook_urls_are_rejected(url):
    with pytest.raises(ConfigError, match="WEBHOOK_URL"):
        load_settings(valid_webhook_env(WEBHOOK_URL=url))


@pytest.mark.parametrize("port", [None, 80, 88, 443, 8443])
def test_supported_public_webhook_ports_are_accepted(port):
    host = "example.com" if port is None else f"example.com:{port}"
    settings = load_settings(
        valid_webhook_env(WEBHOOK_URL=f"https://{host}/telegram")
    )
    assert settings.webhook_url == f"https://{host}/telegram"


@pytest.mark.parametrize("port", [0, 123, 444, 8444, 65535])
def test_unsupported_public_webhook_ports_are_rejected(port):
    with pytest.raises(ConfigError, match="WEBHOOK_URL port"):
        load_settings(
            valid_webhook_env(
                WEBHOOK_URL=f"https://example.com:{port}/telegram"
            )
        )


def test_webhook_requires_secret():
    env = valid_webhook_env()
    del env["WEBHOOK_SECRET"]
    with pytest.raises(ConfigError, match="WEBHOOK_SECRET is required"):
        load_settings(env)


@pytest.mark.parametrize(
    "secret",
    [
        "a" * 31,
        "a" * 31 + "!",
        "é" * 32,
        "a" * 16 + " " + "b" * 16,
    ],
)
def test_weak_or_invalid_webhook_secrets_are_rejected(secret):
    with pytest.raises(ConfigError, match="WEBHOOK_SECRET"):
        load_settings(valid_webhook_env(WEBHOOK_SECRET=secret))


def test_webhook_secret_256_characters_is_accepted():
    secret = "a" * 256
    settings = load_settings(valid_webhook_env(WEBHOOK_SECRET=secret))
    assert settings.webhook_secret == secret


def test_webhook_secret_257_characters_is_rejected():
    with pytest.raises(ConfigError, match="between 32 and 256"):
        load_settings(valid_webhook_env(WEBHOOK_SECRET="a" * 257))


@pytest.mark.parametrize("path", ["telegram", "/other", "/telegram/"])
def test_webhook_path_must_match_url_path_exactly(path):
    with pytest.raises(ConfigError, match="WEBHOOK_PATH"):
        load_settings(valid_webhook_env(WEBHOOK_PATH=path))


@pytest.mark.parametrize("port", ["0", "-1", "65536", "invalid"])
def test_invalid_webhook_ports_are_rejected(port):
    with pytest.raises(ConfigError, match="WEBHOOK_PORT"):
        load_settings(valid_webhook_env(WEBHOOK_PORT=port))


@pytest.mark.parametrize("port", [1, 65535])
def test_webhook_port_boundaries_are_accepted(port):
    settings = load_settings(valid_webhook_env(WEBHOOK_PORT=str(port)))
    assert settings.webhook_port == port


@pytest.mark.parametrize("key", ["WEBHOOK_CERT", "WEBHOOK_KEY"])
def test_webhook_cert_and_key_must_be_set_together(key):
    with pytest.raises(ConfigError, match="must be set together"):
        load_settings(valid_webhook_env(**{key: "/tmp/webhook.pem"}))


def test_webhook_config_is_accepted():
    settings = load_settings(valid_webhook_env())
    assert settings.mode == "webhook"
    assert settings.webhook_url == "https://example.com/telegram"
    assert settings.webhook_path == "/telegram"
    assert settings.webhook_secret == VALID_WEBHOOK_SECRET
