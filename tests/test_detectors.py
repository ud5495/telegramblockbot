from types import SimpleNamespace

from bot.detectors import detect_content, detect_message


def _kinds(findings):
    return [finding.kind for finding in findings]


def test_clean_text_has_no_findings():
    assert detect_content("hello there, how are you?") == []


def test_plain_url_is_detected():
    findings = detect_content("visit https://evil.example/path now")
    assert [f.kind for f in findings] == ["url"]
    assert "https://evil.example/path" in findings[0].value


def test_www_url_is_detected():
    findings = detect_content("see www.example.com")
    assert findings and findings[0].kind == "url"


def test_telegram_invite_is_detected():
    findings = detect_content("join t.me/+abcdef")
    assert any(f.kind in {"invite", "url"} for f in findings)


def test_mention_is_detected():
    findings = detect_content("thanks @somechannel")
    assert findings == [f for f in findings if f.kind == "mention"]
    assert findings[0].value == "@somechannel"


def test_email_is_not_a_mention():
    assert detect_content("write to mail@example.com") == []


def test_whitelisted_domain_is_allowed():
    findings = detect_content(
        "watch https://youtube.com/watch?v=1", allowed_domains=["youtube.com"]
    )
    assert findings == []


def test_subdomain_of_whitelisted_domain_is_allowed():
    findings = detect_content(
        "https://music.youtube.com/foo", allowed_domains=["youtube.com"]
    )
    assert findings == []


def test_whitelisted_username_is_allowed():
    findings = detect_content("hi @friend", allowed_usernames=["@friend"])
    assert findings == []


def test_text_link_entity_is_detected():
    entity = SimpleNamespace(
        type="text_link", url="https://evil.example", offset=0, length=10
    )
    findings = detect_content("click here", [entity])
    assert findings and findings[0].kind == "url"
    assert findings[0].value == "https://evil.example"


def test_url_entity_with_whitelisted_target_is_allowed():
    entity = SimpleNamespace(
        type="text_link", url="https://youtube.com/x", offset=0, length=10
    )
    assert detect_content("click here", [entity], allowed_domains=["youtube.com"]) == []


def test_detect_message_uses_caption():
    message = SimpleNamespace(
        text=None,
        caption="join https://evil.example",
        entities=None,
        caption_entities=[],
    )
    findings = detect_message(message)
    assert findings and findings[0].kind == "url"
