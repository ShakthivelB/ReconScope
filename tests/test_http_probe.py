import asyncio

from services import http_probe


class FakeResponse:
    status_code = 302
    http_version = "HTTP/1.1"
    headers = {"location": "http://127.0.0.1/admin"}
    is_redirect = True
    url = "https://public.example/"


class FakeStream:
    async def __aenter__(self):
        return FakeResponse()

    async def __aexit__(self, *_args):
        return False


class FakeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def stream(self, *_args, **_kwargs):
        return FakeStream()


def test_http_probe_revalidates_redirect_destination(monkeypatch):
    checked = []

    def validate(url, *_args):
        checked.append(url)
        if "127.0.0.1" in url:
            return False, "Private or blocked IP address: 127.0.0.1"
        return True, ""

    monkeypatch.setattr(http_probe, "validate_target_url", validate)
    monkeypatch.setattr(http_probe.httpx, "AsyncClient", lambda **_kwargs: FakeClient())

    result = asyncio.run(http_probe.probe_url("https://public.example/"))

    assert checked == ["https://public.example/", "http://127.0.0.1/admin"]
    assert result["status_code"] is None
    assert "Network safety blocked" in result["error"]
    assert len(result["redirect_chain"]) == 1
