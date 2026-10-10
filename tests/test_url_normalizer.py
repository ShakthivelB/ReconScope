import pytest
from services.url_normalizer import normalize_url


def test_scheme_and_hostname_lowercasing():
    assert normalize_url("HTTPS://EXAMPLE.COM/Path") == "https://example.com/Path"
    assert normalize_url("http://Sub.Example.COM") == "http://sub.example.com/"


def test_default_port_removal():
    assert normalize_url("http://example.com:80/index") == "http://example.com/index"
    assert normalize_url("https://example.com:443/login") == "https://example.com/login"
    assert normalize_url("http://example.com:8080/api") == "http://example.com:8080/api"


def test_trailing_slash_and_empty_path():
    assert normalize_url("https://example.com") == "https://example.com/"
    assert normalize_url("https://example.com/") == "https://example.com/"


def test_query_parameter_sorting_and_preservation():
    url1 = "https://example.com/api?b=2&a=1"
    url2 = "https://example.com/api?a=1&b=2"
    assert normalize_url(url1) == "https://example.com/api?a=1&b=2"
    assert normalize_url(url1) == normalize_url(url2)


def test_fragment_removal():
    assert normalize_url("https://example.com/page#section1") == "https://example.com/page"


def test_missing_scheme():
    assert normalize_url("api.example.com/v1") == "http://api.example.com/v1"
