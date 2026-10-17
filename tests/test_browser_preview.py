import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.responses import Response
from playwright.async_api import Error as PlaywrightError

from database.database import Base
from database.models import HttpResult, Target, Url
from services import browser_preview


class FakeRequest:
    def __init__(self, url, frame):
        self.url = url
        self.frame = frame

    def is_navigation_request(self):
        return True


class FakeRoute:
    def __init__(self, request):
        self.request = request
        self.was_aborted = False

    async def continue_(self):
        return None

    async def abort(self, _reason):
        self.was_aborted = True


class FakeResponse:
    def __init__(self, request, status, headers):
        self.request = request
        self.status = status
        self.url = request.url
        self.headers = headers


class FakePage:
    def __init__(self, start_url, final_url, statuses=None, content_types=None):
        self.url = "about:blank"
        self.main_frame = object()
        self.start_url = start_url
        self.final_url = final_url
        self.statuses = statuses or [302, 200]
        self.content_types = content_types or ["text/html", "text/html; charset=utf-8"]
        self.route_handler = None
        self.response_handler = None
        self.navigation_handler = None
        self.routes_seen = []

    def set_default_navigation_timeout(self, _timeout):
        pass

    def on(self, event, callback):
        if event == "response":
            self.response_handler = callback
        elif event == "framenavigated":
            self.navigation_handler = callback

    async def route(self, _pattern, callback):
        self.route_handler = callback

    async def goto(self, _url, wait_until, timeout):
        assert wait_until == "domcontentloaded"
        assert timeout > 0
        urls = [self.start_url] if self.start_url == self.final_url else [self.start_url, self.final_url]
        final_response = None
        for index, url in enumerate(urls):
            request = FakeRequest(url, self.main_frame)
            route = FakeRoute(request)
            await self.route_handler(route)
            self.routes_seen.append(route)
            if route.was_aborted:
                raise PlaywrightError("navigation blocked")
            self.url = url
            status = self.statuses[index]
            headers = {
                "content-type": self.content_types[index],
                "server": "fixture-server",
                "set-cookie": "must-not-be-exposed=secret",
            }
            if status in (301, 302, 303, 307, 308) and index + 1 < len(urls):
                headers["location"] = urls[index + 1]
            final_response = FakeResponse(request, status, headers)
            self.response_handler(final_response)
            if self.navigation_handler:
                self.navigation_handler(self.main_frame)
        return final_response

    async def wait_for_load_state(self, _state, timeout):
        assert timeout > 0

    async def evaluate(self, _expression):
        return {
            "url": self.url,
            "title": "Register as an employer",
            "ready": "complete",
            "text": "Register as an employer Email address Password Success! Register employer account Already have an account? Sign in",
            "controls": 4,
            "busy": False,
        }

    async def title(self):
        return "Register as an employer"

    async def screenshot(self, **kwargs):
        assert kwargs["type"] == "jpeg"
        return b"in-memory-jpeg"

    async def close(self):
        pass


class FakeContext:
    def __init__(self, page):
        self.page = page

    async def new_page(self):
        return self.page

    async def close(self):
        pass


class FakeBrowser:
    version = "test-chromium"

    def __init__(self, context):
        self.context = context

    async def new_context(self, **kwargs):
        assert kwargs["java_script_enabled"] is True
        return self.context


@pytest.mark.asyncio
async def test_preview_waits_for_redirected_render_and_returns_final_metadata(monkeypatch):
    initial = "https://sg.employer.seek.com/oauth/register?tracking=test"
    final = "https://accounts.external-example.net/login?state=fixture"
    page = FakePage(initial, final)
    browser = FakeBrowser(FakeContext(page))
    checked_hosts = []

    def allow_public_url(url, *_args):
        checked_hosts.append(url)
        return True, ""

    async def fake_get_browser():
        return browser

    async def inline_to_thread(function, *args):
        return function(*args)

    monkeypatch.setattr(browser_preview, "validate_target_url", allow_public_url)
    monkeypatch.setattr(browser_preview, "get_browser", fake_get_browser)
    monkeypatch.setattr(browser_preview.asyncio, "to_thread", inline_to_thread)

    capture = await browser_preview.capture_preview(
        initial,
        timeout_ms=18000,
        settle_timeout_ms=1800,
    )

    assert capture.image_bytes == b"in-memory-jpeg"
    assert capture.final_url == final
    assert capture.title == "Register as an employer"
    assert capture.status_code == 200
    assert capture.requested_url == initial
    assert capture.content_type == "text/html; charset=utf-8"
    assert capture.response_headers == {
        "content-type": "text/html; charset=utf-8",
        "server": "fixture-server",
    }
    assert capture.redirect_chain == [{
        "status": 302, "url": initial, "location": final,
        "response_headers": {"content-type": "text/html", "server": "fixture-server"},
    }]
    assert capture.main_frame_navigated is True
    assert capture.browser_version == "test-chromium"
    assert capture.settled is True
    assert checked_hosts == [initial, initial, final]
    assert all(not route.was_aborted for route in page.routes_seen)


@pytest.mark.asyncio
async def test_preview_allows_public_cross_domain_top_level_redirect(monkeypatch):
    initial = "https://sg.employer.seek.com/oauth/register"
    final = "https://accounts.external-example.net/login"
    page = FakePage(initial, final)
    browser = FakeBrowser(FakeContext(page))

    def validate(_url, *_args):
        return True, ""

    async def fake_get_browser():
        return browser

    async def inline_to_thread(function, *args):
        return function(*args)

    monkeypatch.setattr(browser_preview, "validate_target_url", validate)
    monkeypatch.setattr(browser_preview, "get_browser", fake_get_browser)
    monkeypatch.setattr(browser_preview.asyncio, "to_thread", inline_to_thread)

    capture = await browser_preview.capture_preview(initial, timeout_ms=8000)
    assert capture.final_url == final


@pytest.mark.asyncio
async def test_preview_blocks_redirect_to_prohibited_destination(monkeypatch):
    initial = "https://public.example/"
    final = "http://127.0.0.1/admin"
    page = FakePage(initial, final)
    browser = FakeBrowser(FakeContext(page))

    def validate(url, *_args):
        if "127.0.0.1" in url:
            return False, "Private or blocked IP address: 127.0.0.1"
        return True, ""

    async def fake_get_browser():
        return browser

    async def inline_to_thread(function, *args):
        return function(*args)

    monkeypatch.setattr(browser_preview, "validate_target_url", validate)
    monkeypatch.setattr(browser_preview, "get_browser", fake_get_browser)
    monkeypatch.setattr(browser_preview.asyncio, "to_thread", inline_to_thread)

    with pytest.raises(ValueError, match="Redirect / navigation blocked"):
        await browser_preview.capture_preview(initial, timeout_ms=8000)


@pytest.mark.asyncio
async def test_preview_captures_best_available_page_when_it_never_settles(monkeypatch):
    class LoadingPage(FakePage):
        async def evaluate(self, _expression):
            return {
                "url": self.url,
                "title": "Loading",
                "ready": "complete",
                "text": "Loading...",
                "controls": 0,
                "busy": True,
            }

    initial = "https://sg.employer.seek.com/oauth/register"
    page = LoadingPage(initial, initial)
    browser = FakeBrowser(FakeContext(page))

    async def fake_get_browser():
        return browser

    async def inline_to_thread(function, *args):
        return function(*args)

    monkeypatch.setattr(browser_preview, "validate_target_url", lambda *_args: (True, ""))
    monkeypatch.setattr(browser_preview, "get_browser", fake_get_browser)
    monkeypatch.setattr(browser_preview.asyncio, "to_thread", inline_to_thread)

    capture = await browser_preview.capture_preview(
        initial,
        timeout_ms=8000,
        settle_timeout_ms=600,
    )

    assert capture.image_bytes == b"in-memory-jpeg"
    assert capture.settled is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "content_type"),
    [
        (200, "text/html"),
        (200, "application/json"),
        (403, "text/html"),
        (404, "text/html"),
        (500, "text/html"),
    ],
)
async def test_http_error_pages_and_json_are_valid_in_memory_previews(monkeypatch, status, content_type):
    url = "https://meesho.example/api/navigation-tree"
    page = FakePage(url, url, statuses=[status], content_types=[content_type])
    browser = FakeBrowser(FakeContext(page))

    async def fake_get_browser():
        return browser

    async def inline_to_thread(function, *args):
        return function(*args)

    monkeypatch.setattr(browser_preview, "validate_target_url", lambda *_args: (True, ""))
    monkeypatch.setattr(browser_preview, "get_browser", fake_get_browser)
    monkeypatch.setattr(browser_preview.asyncio, "to_thread", inline_to_thread)

    capture = await browser_preview.capture_preview(url, timeout_ms=8000)

    assert capture.image_bytes == b"in-memory-jpeg"
    assert capture.requested_url == url
    assert capture.final_url == url
    assert capture.status_code == status
    assert capture.content_type == content_type
    assert capture.title == "Register as an employer"
    assert capture.response_headers == {
        "content-type": content_type,
        "server": "fixture-server",
    }
    assert capture.redirect_chain == []
    assert capture.main_frame_navigated is True


def test_preview_metadata_endpoint_headers(monkeypatch):
    from api import previews
    from services.browser_preview import PreviewCapture

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    target = Target(name="Seek Test", root_domain="seek.com")
    db.add(target)
    db.flush()
    url = Url(
        target_id=target.id,
        original_url="https://sg.employer.seek.com/oauth/register",
        normalized_url="https://sg.employer.seek.com/oauth/register",
        scheme="https",
        hostname="sg.employer.seek.com",
        path="/oauth/register",
    )
    db.add(url)
    db.flush()
    db.add(HttpResult(url_id=url.id, status_code=200, content_type="application/json"))
    db.commit()

    async def fake_capture(**_kwargs):
        return PreviewCapture(
            image_bytes=b"jpeg",
            requested_url=url.normalized_url,
            final_url="https://authenticate.seek.com/login?state=fixture",
            title="Register as an employer",
            status_code=403,
            content_type="text/html",
            response_headers={"content-type": "text/html", "server": "AkamaiGHost"},
            redirect_chain=[{
                "status": 302,
                "url": url.normalized_url,
                "location": "https://authenticate.seek.com/login?state=fixture",
                "response_headers": {"content-type": "text/html", "server": "fixture-server"},
            }],
            browser_version="test-chromium",
            settled=True,
            main_frame_navigated=True,
        )

    monkeypatch.setattr(previews, "capture_preview", fake_capture)
    response = asyncio.run(previews.get_url_preview(url.id, db))

    assert isinstance(response, Response)
    assert response.body == b"jpeg"
    assert response.headers["cache-control"].startswith("no-store")
    assert response.headers["x-preview-final-url"].endswith("state%3Dfixture")
    assert response.headers["x-preview-title"] == "Register%20as%20an%20employer"
    assert response.headers["x-preview-status"] == "403"
    assert response.headers["x-preview-content-type"] == "text%2Fhtml"
    assert response.headers["x-preview-response-headers"] == (
        "%7B%22content-type%22%3A%22text%2Fhtml%22%2C%22server%22%3A%22AkamaiGHost%22%7D"
    )
    assert response.headers["x-preview-requested-url"].startswith("https%3A%2F%2F")
    assert response.headers["x-preview-redirect-count"] == "1"
    assert response.headers["x-preview-redirect-chain"]
    assert response.headers["x-preview-main-frame-navigated"] == "true"
    assert response.headers["x-preview-settled"] == "true"
    stored_http_result = db.query(HttpResult).filter(HttpResult.url_id == url.id).one()
    assert stored_http_result.status_code == 200
    assert stored_http_result.content_type == "application/json"
    db.close()
    engine.dispose()


def test_preview_endpoint_accepts_url_without_target(monkeypatch):
    from api import previews
    from services.browser_preview import PreviewCapture

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    url = Url(
        target_id=None,
        original_url="https://cdn.example.net/app.js",
        normalized_url="https://cdn.example.net/app.js",
        scheme="https",
        hostname="cdn.example.net",
        path="/app.js",
    )
    db.add(url)
    db.commit()
    received = {}

    async def fake_capture(**kwargs):
        received.update(kwargs)
        return PreviewCapture(
            image_bytes=b"jpeg",
            requested_url=url.normalized_url,
            final_url=url.normalized_url,
            title="Asset preview",
            status_code=200,
            content_type="text/html",
            response_headers={},
            redirect_chain=[],
            browser_version="test-chromium",
            settled=True,
            main_frame_navigated=True,
        )

    monkeypatch.setattr(previews, "capture_preview", fake_capture)
    response = asyncio.run(previews.get_url_preview(url.id, db))
    assert isinstance(response, Response)
    assert response.body == b"jpeg"
    assert received["enforce_scope"] is False
    db.close()
    engine.dispose()


def test_preview_timeout_returns_gateway_timeout_without_changing_http_result(monkeypatch):
    from api import previews

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    target = Target(name="Timeout Test", root_domain="example.com")
    db.add(target)
    db.flush()
    url = Url(
        target_id=target.id,
        original_url="https://example.com/slow",
        normalized_url="https://example.com/slow",
        scheme="https",
        hostname="example.com",
        path="/slow",
    )
    db.add(url)
    db.flush()
    db.add(HttpResult(url_id=url.id, status_code=200, content_type="application/json"))
    db.commit()

    async def timeout_capture(**_kwargs):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(previews, "capture_preview", timeout_capture)
    response = asyncio.run(previews.get_url_preview(url.id, db))
    assert response.status_code == 504
    stored_http_result = db.query(HttpResult).filter(HttpResult.url_id == url.id).one()
    assert stored_http_result.status_code == 200
    db.close()
    engine.dispose()
