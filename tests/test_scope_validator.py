import ipaddress
import pytest
from services.scope_validator import is_in_scope, is_safe_ip, resolve_and_check_ips, validate_target_url


def test_scope_matching_wildcard():
    rules = "*.example.com\nexample.com"
    assert is_in_scope("example.com", "example.com", rules) is True
    assert is_in_scope("api.example.com", "example.com", rules) is True
    assert is_in_scope("deep.sub.example.com", "example.com", rules) is True
    assert is_in_scope("notexample.com", "example.com", rules) is False
    assert is_in_scope("google.com", "example.com", rules) is False


def test_scope_exclusions():
    rules = "*.example.com\n!internal.example.com"
    assert is_in_scope("api.example.com", "example.com", rules) is True
    assert is_in_scope("internal.example.com", "example.com", rules) is False


def test_ssrf_blocked_ips():
    blocked = [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.100",
        "172.16.5.1",
        "169.254.169.254",  # AWS metadata
        "0.0.0.0",
        "::1",
        "::ffff:127.0.0.1",
    ]
    for ip_str in blocked:
        ip = ipaddress.ip_address(ip_str)
        assert is_safe_ip(ip) is False, f"IP {ip_str} should be blocked"


def test_ssrf_allowed_public_ips():
    public_ips = [
        "8.8.8.8",
        "1.1.1.1",
        "93.184.216.34",
    ]
    for ip_str in public_ips:
        ip = ipaddress.ip_address(ip_str)
        assert is_safe_ip(ip) is True, f"IP {ip_str} should be allowed"


def test_resolve_localhost_blocking():
    is_safe, reason, _ = resolve_and_check_ips("localhost")
    assert is_safe is False
    assert "blocked" in reason.lower() or "loopback" in reason.lower()


def test_validate_target_url_out_of_scope():
    is_valid, reason = validate_target_url("https://malicious.com/api", "example.com", "*.example.com")
    assert is_valid is False
    assert "out of scope" in reason.lower()
