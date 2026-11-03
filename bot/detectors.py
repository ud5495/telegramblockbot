from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]{}]+", re.IGNORECASE)
TELEGRAM_RE = re.compile(r"(?:t(?:elegram)?\.me/|tg://)[^\s<>()\[\]{}]+", re.IGNORECASE)
MENTION_RE = re.compile(r"(?<![\w@/])@[A-Za-z0-9_]{3,}")

_URL_ENTITY_TYPES = {"url", "text_link"}
_MENTION_ENTITY_TYPES = {"mention"}
_TEXT_MENTION_ENTITY_TYPES = {"text_mention"}


@dataclass(frozen=True)
class Finding:
    kind: str
    value: str

    def as_reason(self) -> str:
        return f"{self.kind}:{self.value}"


def _normalize_domain(domain: str) -> str:
    domain = domain.strip().lower()
    if "://" in domain:
        domain = urlsplit(domain).hostname or domain
    return domain.removeprefix("www.").strip(".")


def _normalize_username(username: str) -> str:
    return username.strip().lstrip("@").lower()


def domain_allowed(url: str, allowed_domains: Sequence[str]) -> bool:
    if not allowed_domains:
        return False
    candidate = url if "://" in url else f"http://{url}"
    host = urlsplit(candidate).hostname
    if not host:
        return False
    host = host.lower().rstrip(".")
    for allowed in allowed_domains:
        allowed = _normalize_domain(allowed)
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def username_allowed(username: str, allowed_usernames: Sequence[str]) -> bool:
    if not allowed_usernames:
        return False
    candidate = _normalize_username(username)
    return any(
        candidate == _normalize_username(allowed) for allowed in allowed_usernames
    )


def _slice_entity(text: str, entity: Any) -> str:
    offset = getattr(entity, "offset", 0)
    length = getattr(entity, "length", 0)
    return text[offset : offset + length]


def _dedupe(findings: Iterable[Finding]) -> list[Finding]:
    seen: set[tuple[str, str]] = set()
    result: list[Finding] = []
    for finding in findings:
        key = (finding.kind, finding.value.lower())
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result


def detect_content(
    text: str | None = None,
    entities: Sequence[Any] = (),
    allowed_domains: Sequence[str] = (),
    allowed_usernames: Sequence[str] = (),
) -> list[Finding]:
    """Return disallowed links/mentions found in ``text`` and ``entities``.

    A message is considered clean when every finding is whitelisted.
    """
    text = text or ""
    findings: list[Finding] = []

    for entity in entities or ():
        entity_type = str(getattr(entity, "type", "")).lower()
        if entity_type in _URL_ENTITY_TYPES:
            url = getattr(entity, "url", None) or _slice_entity(text, entity)
            if url and not domain_allowed(url, allowed_domains):
                findings.append(Finding("url", url))
        elif entity_type in _MENTION_ENTITY_TYPES:
            mention = _slice_entity(text, entity) or getattr(entity, "url", "") or ""
            if mention and not username_allowed(mention, allowed_usernames):
                findings.append(Finding("mention", mention))
        elif entity_type in _TEXT_MENTION_ENTITY_TYPES:
            user = getattr(entity, "user", None)
            username = getattr(user, "username", None) if user else None
            user_id = str(getattr(user, "id", "")) if user else ""
            if user_id and user_id in set(allowed_usernames):
                continue
            if username:
                if not username_allowed(username, allowed_usernames):
                    findings.append(Finding("mention", f"@{username}"))
            else:
                findings.append(Finding("mention", f"user:{user_id}"))

    for match in URL_RE.finditer(text):
        value = match.group(0)
        if not domain_allowed(value, allowed_domains):
            findings.append(Finding("url", value))

    for match in TELEGRAM_RE.finditer(text):
        value = match.group(0)
        if not domain_allowed(value, allowed_domains):
            findings.append(Finding("invite", value))

    for match in MENTION_RE.finditer(text):
        value = match.group(0)
        if not username_allowed(value, allowed_usernames):
            findings.append(Finding("mention", value))

    return _dedupe(findings)


def detect_message(
    message: Any,
    allowed_domains: Sequence[str] = (),
    allowed_usernames: Sequence[str] = (),
) -> list[Finding]:
    """Convenience wrapper for a :class:`telegram.Message`."""
    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    entities = (
        getattr(message, "entities", None)
        or getattr(message, "caption_entities", None)
        or ()
    )
    return detect_content(text, entities, allowed_domains, allowed_usernames)
