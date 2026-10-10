import pytest
from services.interesting_detector import detect_interesting_url


def test_interesting_paths():
    paths = [
        "https://example.com/admin",
        "https://example.com/api/v1/users",
        "https://example.com/swagger-ui",
        "https://example.com/graphql",
        "https://example.com/actuator/health",
        "https://example.com/login",
    ]
    for p in paths:
        is_int, reasons = detect_interesting_url(p)
        assert is_int is True, f"Path {p} should be flagged as interesting"
        assert len(reasons) > 0
        # Invariant check: never claim vulnerability
        for r in reasons:
            assert "vulnerable" not in r.lower()
            assert "critical" not in r.lower()


def test_interesting_parameters():
    is_int, reasons = detect_interesting_url("https://example.com/page", "redirect=https://google.com&token=secret123")
    assert is_int is True
    reasons_str = " ".join(reasons).lower()
    assert "redirect" in reasons_str
    assert "token" in reasons_str


def test_benign_urls():
    is_int, reasons = detect_interesting_url("https://example.com/about-us")
    assert is_int is False
    assert len(reasons) == 0
