import ipaddress

from services.scope_validator import (
    is_in_scope,
    is_safe_ip,
    resolve_and_check_ips,
    validate_target_url,
)


def test_target_scope_is_optional_and_respects_rules():
    assert is_in_scope("api.example.com", "example.com")
    assert not is_in_scope("elsewhere.example.net", "example.com")
    assert is_in_scope("elsewhere.example.net", "example.com", "*.example.net")


def test_mapped_private_ipv6_is_blocked():
    assert not is_safe_ip(ipaddress.ip_address("::ffff:127.0.0.1"))
    assert not is_safe_ip(ipaddress.ip_address("::ffff:192.168.1.5"))


def test_private_destinations_and_localhost_are_blocked():
    for url in (
        "http://127.0.0.1/",
        "http://192.168.1.10/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://localhost/",
    ):
        valid, reason = validate_target_url(url)
        assert not valid
        assert reason


def test_public_cross_domain_url_allowed_when_scope_not_enforced(monkeypatch):
    monkeypatch.setattr(
        "services.scope_validator.resolve_and_check_ips",
        lambda _host: (True, "", ["93.184.216.34"]),
    )
    assert validate_target_url(
        "https://api.example.org/v1", "example.com", enforce_scope=False
    ) == (True, "")
    allowed, reason = validate_target_url(
        "https://api.example.org/v1", "example.com", enforce_scope=True
    )
    assert not allowed
    assert "out of scope" in reason


def test_unsupported_scheme_is_rejected():
    valid, reason = validate_target_url("file:///etc/passwd")
    assert not valid
    assert "scheme" in reason.lower()


def test_dns_answers_with_any_prohibited_address_are_rejected(monkeypatch):
    monkeypatch.setattr(
        "services.scope_validator.socket.getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 0)),
            (2, 1, 6, "", ("127.0.0.1", 0)),
        ],
    )
    valid, reason = validate_target_url("https://rebound.example/")
    assert not valid
    assert "127.0.0.1" in reason
