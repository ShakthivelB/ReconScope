import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Dict, Optional
from urllib.parse import quote, urljoin

from playwright.async_api import (
    Browser,
    Error as PlaywrightError,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from .scope_validator import validate_target_url

logger = logging.getLogger("reconscope.preview")

_playwright_instance: Optional[Playwright] = None
_browser_instance: Optional[Browser] = None
_browser_lock = asyncio.Lock()
_preview_semaphore = asyncio.Semaphore(2)

_LOADING_TEXT_RE = re.compile(
    r"^(?:loading(?:\.\.\.)?|please wait(?:\.\.\.)?|redirecting(?:\.\.\.)?|one moment(?:\.\.\.)?)$",
    re.IGNORECASE,
)
_SAFE_RESPONSE_HEADER_NAMES = ("content-type", "server", "via", "x-cache")


def _safe_response_headers(headers) -> Dict[str, str]:
    """Keep only low-risk response diagnostics; never return cookies or tokens."""
    return {
        name: str(headers[name])[:500]
        for name in _SAFE_RESPONSE_HEADER_NAMES
        if headers.get(name)
    }


@dataclass
class PreviewCapture:
    """Ephemeral screenshot plus metadata from the rendered top-level page."""

    image_bytes: bytes
    final_url: str
    title: str
    status_code: Optional[int]
    browser_version: str
    settled: bool
    requested_url: str = ""
    content_type: Optional[str] = None
    response_headers: Dict[str, str] = field(default_factory=dict)
    redirect_chain: list = field(default_factory=list)
    main_frame_navigated: bool = False


async def get_browser() -> Browser:
    """Returns the shared Chromium process; each preview gets a fresh context."""
    global _playwright_instance, _browser_instance
    async with _browser_lock:
        if _browser_instance is None or not _browser_instance.is_connected():
            if _playwright_instance is None:
                _playwright_instance = await async_playwright().start()
            _browser_instance = await _playwright_instance.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
        return _browser_instance


async def close_browser():
    """Gracefully shuts down Playwright browser."""
    global _playwright_instance, _browser_instance
    async with _browser_lock:
        if _browser_instance:
            try:
                await _browser_instance.close()
            except Exception:
                logger.debug("Error closing preview browser", exc_info=True)
            _browser_instance = None
        if _playwright_instance:
            try:
                await _playwright_instance.stop()
            except Exception:
                logger.debug("Error stopping Playwright", exc_info=True)
            _playwright_instance = None


async def _wait_for_rendered_page(page, settle_timeout_ms: int) -> bool:
    """Wait for useful content and a stable URL/DOM without requiring network idle."""
    started = time.monotonic()
    deadline = started + settle_timeout_ms / 1000
    previous_signature = None
    stable_since = None
    settled = False

    while time.monotonic() < deadline:
        try:
            state = await page.evaluate("""() => {
              const body = document.body;
              const text = (body?.innerText || '').replace(/\\s+/g, ' ').trim();
              const controls = [...document.querySelectorAll('input, button, select, textarea, [role="button"]')]
                .filter((el) => {
                  const rect = el.getBoundingClientRect();
                  const style = getComputedStyle(el);
                  return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                }).length;
              const busy = [...document.querySelectorAll('[aria-busy="true"], [role="progressbar"]')]
                .some((el) => {
                  const rect = el.getBoundingClientRect();
                  const style = getComputedStyle(el);
                  return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
                });
              return {
                url: location.href,
                title: document.title || '',
                ready: document.readyState,
                text,
                controls,
                busy,
              };
            }""")
        except PlaywrightError:
            await asyncio.sleep(0.25)
            continue

        text = state["text"]
        text_is_loading = bool(_LOADING_TEXT_RE.fullmatch(text[:100].strip()))
        meaningful = len(text) >= 100 or (len(text) >= 28 and state["controls"] > 0)
        meaningful = meaningful and not (state["busy"] and len(text) < 220) and not text_is_loading
        signature = (state["url"], state["title"], text[:1200], state["controls"], state["busy"])
        now = time.monotonic()

        if meaningful and signature == previous_signature:
            stable_since = stable_since or now
            if now - started >= 1.25 and now - stable_since >= 0.9:
                settled = True
                break
        else:
            stable_since = now if meaningful else None

        previous_signature = signature
        await asyncio.sleep(0.25)

    return settled


async def capture_preview(
    url: str,
    root_domain: str,
    scope_rules: str = "",
    timeout_ms: int = 10000,
    settle_timeout_ms: int = 3000,
) -> PreviewCapture:
    """Render a page in memory and return its final URL and browser metadata.

    The preview is isolated from the user's Chrome profile. No cookies, storage,
    screenshot files, caches, or database records are shared or persisted.
    """
    total_timeout_ms = max(5000, timeout_ms)
    # Reserve time for content settling, screenshot capture, and cleanup so
    # the endpoint returns promptly even when a site keeps loading forever.
    navigation_timeout_ms = min(
        7000,
        max(2500, total_timeout_ms - settle_timeout_ms - 600 - 1200 - 800 - 1000),
    )

    async def validate(url_to_check: str):
        return await asyncio.to_thread(
            validate_target_url,
            url_to_check,
            root_domain,
            scope_rules,
        )

    async with asyncio.timeout(total_timeout_ms / 1000):
        is_valid, reason = await validate(url)
        if not is_valid:
            raise ValueError(f"SSRF / Scope protection blocked preview: {reason}")

        async with _preview_semaphore:
            browser = await get_browser()
            context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                ignore_https_errors=True,
                java_script_enabled=True,
            )
            page = await context.new_page()
            page.set_default_navigation_timeout(navigation_timeout_ms)

            blocked_reason = None
            main_status = None
            main_content_type = None
            main_response_headers = {}
            main_request_url = None
            redirect_chain = []
            main_frame_navigated = False

            async def validate_main_frame_navigation(route):
                nonlocal blocked_reason, main_request_url
                request = route.request
                try:
                    is_main_frame = request.is_navigation_request() and request.frame == page.main_frame
                except PlaywrightError:
                    is_main_frame = False

                if not is_main_frame:
                    await route.continue_()
                    return

                if main_request_url is None:
                    main_request_url = request.url

                valid, why = await validate(request.url)
                if not valid:
                    blocked_reason = f"Redirect / navigation blocked: {why}"
                    await route.abort("blockedbyclient")
                    return
                await route.continue_()

            def record_main_response(response):
                nonlocal main_status, main_content_type, main_response_headers
                try:
                    if response.request.is_navigation_request() and response.request.frame == page.main_frame:
                        main_status = response.status
                        response_headers = response.headers
                        main_content_type = response_headers.get("content-type")
                        main_response_headers = _safe_response_headers(response_headers)
                        location = response_headers.get("location")
                        if 300 <= response.status < 400 and location and len(redirect_chain) < 20:
                            redirect_chain.append({
                                "status": response.status,
                                "url": response.url,
                                "location": urljoin(response.url, location),
                                "response_headers": _safe_response_headers(response_headers),
                            })
                except PlaywrightError:
                    return

            def record_main_frame_navigation(frame):
                nonlocal main_frame_navigated
                if frame == page.main_frame:
                    main_frame_navigated = True

            page.on("response", record_main_response)
            page.on("framenavigated", record_main_frame_navigation)
            await page.route("**/*", validate_main_frame_navigation)

            try:
                try:
                    navigation_response = await page.goto(
                        url, wait_until="domcontentloaded", timeout=navigation_timeout_ms
                    )
                    if navigation_response is not None and main_status is None:
                        record_main_response(navigation_response)
                except PlaywrightTimeoutError:
                    # Some sites keep the initial document busy. Continue with a
                    # bounded render wait and capture the best page available.
                    logger.info("Preview navigation timed out before DOMContentLoaded")
                except PlaywrightError:
                    if blocked_reason:
                        raise ValueError(blocked_reason)
                    raise

                if blocked_reason:
                    raise ValueError(blocked_reason)

                # `load` is useful when available, but it is optional and bounded;
                # networkidle is intentionally avoided for long-lived connections.
                try:
                    await page.wait_for_load_state("load", timeout=min(600, navigation_timeout_ms))
                except PlaywrightTimeoutError:
                    pass

                settled = await _wait_for_rendered_page(page, settle_timeout_ms)
                if blocked_reason:
                    raise ValueError(blocked_reason)

                try:
                    title = await page.title()
                except PlaywrightError:
                    title = ""
                screenshot = await page.screenshot(type="jpeg", quality=75, timeout=1200)
                return PreviewCapture(
                    image_bytes=screenshot,
                    final_url=page.url,
                    title=title[:500],
                    status_code=main_status,
                    browser_version=browser.version,
                    settled=settled,
                    requested_url=main_request_url or url,
                    content_type=main_content_type,
                    response_headers=main_response_headers,
                    redirect_chain=redirect_chain,
                    main_frame_navigated=main_frame_navigated,
                )
            finally:
                # Dispose the isolated context promptly, even after timeout/errors.
                try:
                    # Closing the context also closes its page and is bounded.
                    await asyncio.wait_for(context.close(), timeout=0.8)
                except Exception:
                    pass


def encode_preview_header(value: str) -> str:
    """Encode metadata for safe transport in an HTTP response header."""
    return quote(value or "", safe="")


def encode_preview_redirect_chain(redirect_chain) -> str:
    """Encode only safe redirect metadata; request and response headers are never included."""
    return encode_preview_header(json.dumps(redirect_chain or [], separators=(",", ":")))
