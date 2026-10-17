import asyncio
import json
import logging
import re
import ssl
import socket
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy.orm import Session

from database.database import SessionLocal
from database.models import (
    HttpResult,
    ScanJob,
    ScanResult,
    Technology,
    Url,
    UrlTechnology,
)
from .scope_validator import validate_target_url
from .technology_detector import detect_technologies

logger = logging.getLogger("reconscope.probe")

TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
LANG_RE = re.compile(r"<html[^>]+\blang\s*=\s*['\"]?([^\s'\">]+)", re.IGNORECASE)
DESCRIPTION_RE = re.compile(
    r"<meta\b(?=[^>]*(?:name|property)\s*=\s*['\"](?:description|og:description)['\"])(?=[^>]*content\s*=\s*['\"]([^'\"]*)['\"])[^>]*>",
    re.IGNORECASE,
)

# Active scan cancellation & pause signals
ACTIVE_SCANS: Dict[int, Dict[str, Any]] = {}


def extract_title(html_text: str) -> Optional[str]:
    """Extracts page title safely from HTML string."""
    if not html_text:
        return None
    match = TITLE_TAG_RE.search(html_text[:65536])
    if match:
        raw_title = match.group(1).strip()
        clean_title = re.sub(r"\s+", " ", raw_title)
        return clean_title[:255]
    return None


async def probe_url(
    url: str,
    root_domain: str = "",
    scope_rules: str = "",
    enforce_scope: bool = False,
    timeout: float = 10.0,
    max_redirects: int = 5,
    max_response_size: int = 5 * 1024 * 1024,
) -> Dict[str, Any]:
    """
    Probes an individual URL with manual redirect validation,
    and metadata extraction. Explicitly distinguishes status codes from network failures.
    """
    current_url = url
    redirect_chain: List[Dict[str, Any]] = []

    start_time = time.monotonic()
    total_time_ms = 0.0

    client_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ReconScope/1.0",
        "Accept": "*/*",
    }

    async with httpx.AsyncClient(
        verify=False,
        follow_redirects=False,
        timeout=httpx.Timeout(timeout, connect=5.0),
        headers=client_headers,
    ) as client:
        redirects_count = 0

        while True:
            # Apply network safety on every request and scope only when configured.
            is_valid, reason = validate_target_url(
                current_url, root_domain, scope_rules, enforce_scope
            )
            if not is_valid:
                return {
                    "success": False,
                    "error": (
                        f"Scope blocked: {reason}" if "out of scope" in reason.lower()
                        else f"Network safety blocked: {reason}"
                    ),
                    "status_code": None,
                    "response_time_ms": round((time.monotonic() - start_time) * 1000, 2),
                    "response_size_bytes": 0,
                    "final_url": current_url,
                    "redirect_chain": redirect_chain,
                    "title": None,
                    "content_type": None,
                    "server": None,
                    "http_version": None,
                    "headers": {},
                    "technologies": [],
                }

            hop_start = time.monotonic()
            try:
                # Stream response to guard against oversized payloads
                async with client.stream("GET", current_url) as resp:
                    hop_time = (time.monotonic() - hop_start) * 1000
                    status_code = resp.status_code
                    http_version = str(resp.http_version)
                    headers_dict = dict(resp.headers)
                    content_type = headers_dict.get("content-type", "")
                    server = headers_dict.get("server")

                    # Check for redirect status
                    if resp.is_redirect and "location" in resp.headers and redirects_count < max_redirects:
                        loc = resp.headers["location"]
                        next_url = urllib.parse.urljoin(current_url, loc)
                        redirect_chain.append({
                            "status": status_code,
                            "url": current_url,
                            "location": next_url,
                            "time_ms": round(hop_time, 2),
                        })
                        redirects_count += 1
                        current_url = next_url
                        continue

                    # Read body with max_response_size limit
                    body_bytes = bytearray()
                    async for chunk in resp.aiter_bytes():
                        body_bytes.extend(chunk)
                        if len(body_bytes) > max_response_size:
                            break

                    total_time_ms = (time.monotonic() - start_time) * 1000
                    body_text = ""
                    try:
                        body_text = body_bytes[:262144].decode("utf-8", errors="replace")
                    except Exception:
                        pass

                    title = None
                    language = headers_dict.get("content-language")
                    description = None
                    if "html" in content_type.lower():
                        title = extract_title(body_text)
                        lang_match = LANG_RE.search(body_text[:65536])
                        language = language or (lang_match.group(1) if lang_match else None)
                        description_match = DESCRIPTION_RE.search(body_text[:65536])
                        description = description_match.group(1).strip()[:1000] if description_match else None

                    # Detect technologies
                    techs = detect_technologies(headers_dict, body_text)

                    return {
                        "success": True,
                        "error": None,
                        "status_code": status_code,
                        "response_time_ms": round(total_time_ms, 2),
                        "response_size_bytes": len(body_bytes),
                        "content_type": content_type[:128] if content_type else None,
                        "server": server[:128] if server else None,
                        "http_version": http_version,
                        "final_url": str(resp.url),
                        "redirect_chain": redirect_chain,
                        "title": title,
                        "language": language[:64] if language else None,
                        "description": description,
                        "powered_by": headers_dict.get("x-powered-by", "")[:128] or None,
                        "headers": headers_dict,
                        "technologies": techs,
                    }

            except httpx.TimeoutException:
                return {
                    "success": False,
                    "error": "Timeout",
                    "status_code": None,
                    "response_time_ms": round((time.monotonic() - start_time) * 1000, 2),
                    "response_size_bytes": 0,
                    "final_url": current_url,
                    "redirect_chain": redirect_chain,
                    "title": None,
                    "content_type": None,
                    "server": None,
                    "http_version": None,
                    "headers": {},
                    "technologies": [],
                }
            except (ssl.SSLError, httpx.ConnectError) as e:
                err_str = str(e).lower()
                cause = e.__cause__
                dns_failure = isinstance(cause, socket.gaierror)
                while cause and not dns_failure:
                    dns_failure = isinstance(cause, socket.gaierror)
                    cause = cause.__cause__
                error_type = "Connection error"
                if dns_failure or any(marker in err_str for marker in (
                    "getaddrinfo", "name or service not known", "temporary failure in name resolution",
                )):
                    error_type = "DNS error"
                elif "ssl" in err_str or "certificate" in err_str or "tls" in err_str:
                    error_type = "TLS error"

                return {
                    "success": False,
                    "error": error_type,
                    "status_code": None,
                    "response_time_ms": round((time.monotonic() - start_time) * 1000, 2),
                    "response_size_bytes": 0,
                    "final_url": current_url,
                    "redirect_chain": redirect_chain,
                    "title": None,
                    "content_type": None,
                    "server": None,
                    "http_version": None,
                    "headers": {},
                    "technologies": [],
                }
            except Exception as e:
                return {
                    "success": False,
                    "error": f"Connection error: {str(e)[:100]}",
                    "status_code": None,
                    "response_time_ms": round((time.monotonic() - start_time) * 1000, 2),
                    "response_size_bytes": 0,
                    "final_url": current_url,
                    "redirect_chain": redirect_chain,
                    "title": None,
                    "content_type": None,
                    "server": None,
                    "http_version": None,
                    "headers": {},
                    "technologies": [],
                }


async def run_scan_job(
    scan_job_id: int,
    concurrency: int = 15,
    timeout: float = 10.0,
):
    """
    Executes a scan job using an asynchronous worker queue, scaling cleanly
    even across 10,000+ URLs with live progress updates and pause/cancel handling.
    """
    db: Session = SessionLocal()
    try:
        scan_job = db.query(ScanJob).filter(ScanJob.id == scan_job_id).first()
        if not scan_job:
            logger.error(f"ScanJob {scan_job_id} not found.")
            return

        query = db.query(Url.id, Url.normalized_url)
        if scan_job.target_id is not None:
            query = query.filter(Url.target_id == scan_job.target_id)
        url_records = query.all()
        total_urls = len(url_records)
        scan_job.total_urls = total_urls
        scan_job.status = "running"
        scan_job.started_at = datetime.now(timezone.utc)
        db.commit()
        ACTIVE_SCANS[scan_job_id] = {"status": "running"}

        # Populate async queue
        queue = asyncio.Queue()
        for record in url_records:
            queue.put_nowait(record)

        db_lock = asyncio.Lock()
        completed_counter = 0

        async def worker():
            nonlocal completed_counter
            while not queue.empty():
                state = ACTIVE_SCANS.get(scan_job_id, {}).get("status", "running")
                while state == "paused":
                    await asyncio.sleep(1)
                    state = ACTIVE_SCANS.get(scan_job_id, {}).get("status", "running")

                if state == "cancelled":
                    break

                try:
                    u_id, u_norm = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                # Probe URL
                try:
                    res = await probe_url(
                        url=u_norm,
                        # Target associations remain for legacy data organization.
                        # They do not restrict the new all-URL scan workflow.
                        enforce_scope=False,
                        timeout=timeout,
                    )
                except Exception as exc:
                    # Keep a single unexpected URL failure from terminating this worker.
                    logger.exception("Unexpected probe failure for URL %s", u_id)
                    res = {
                        "success": False,
                        "error": f"Probe error: {exc}",
                        "status_code": None,
                        "response_time_ms": None,
                        "response_size_bytes": 0,
                        "content_type": None,
                        "server": None,
                        "http_version": None,
                        "final_url": u_norm,
                        "redirect_chain": [],
                        "title": None,
                        "language": None,
                        "description": None,
                        "powered_by": None,
                        "headers": {},
                        "technologies": [],
                    }

                # Persist result with thread-safe session
                async with db_lock:
                    job_db = SessionLocal()
                    try:
                        job = job_db.query(ScanJob).filter(ScanJob.id == scan_job_id).first()
                        if not job:
                            continue

                        completed_counter += 1
                        job.completed_urls = completed_counter
                        status_code = res.get("status_code")
                        error_msg = res.get("error")

                        if status_code is not None:
                            if 200 <= status_code < 300:
                                job.status_2xx += 1
                            elif 300 <= status_code < 400:
                                job.status_3xx += 1
                            elif status_code == 401:
                                job.status_401 += 1
                            elif status_code == 403:
                                job.status_403 += 1
                            elif status_code == 404:
                                job.status_404 += 1
                            elif 500 <= status_code < 600:
                                job.status_5xx += 1
                        else:
                            if error_msg and "timeout" in error_msg.lower():
                                job.timeouts += 1
                            else:
                                job.errors += 1

                        # Store the current result and retain a per-scan history row.
                        existing_http = (
                            job_db.query(HttpResult)
                            .filter(HttpResult.url_id == u_id)
                            .order_by(HttpResult.id.desc())
                            .first()
                        )
                        if not existing_http:
                            existing_http = HttpResult(url_id=u_id)
                            job_db.add(existing_http)

                        job_db.add(ScanResult(
                            scan_job_id=scan_job_id,
                            url_id=u_id,
                            status_code=status_code,
                            response_time=res.get("response_time_ms"),
                            error_message=error_msg,
                            scanned_at=datetime.now(timezone.utc),
                        ))

                        existing_http.status_code = status_code
                        existing_http.error_message = error_msg
                        existing_http.response_time_ms = res.get("response_time_ms")
                        existing_http.response_size_bytes = res.get("response_size_bytes")
                        existing_http.content_type = res.get("content_type")
                        existing_http.server = res.get("server")
                        existing_http.http_version = res.get("http_version")
                        existing_http.final_url = res.get("final_url")
                        existing_http.redirect_chain = json.dumps(res.get("redirect_chain", []))
                        existing_http.title = res.get("title")
                        existing_http.language = res.get("language")
                        existing_http.description = res.get("description")
                        existing_http.powered_by = res.get("powered_by")
                        existing_http.headers_json = json.dumps(res.get("headers", {}))
                        existing_http.probed_at = datetime.now(timezone.utc)

                        # Update technologies if any detected.
                        for tech_result in res.get("technologies", []):
                            tech = job_db.query(Technology).filter(Technology.name == tech_result["name"]).first()
                            if not tech:
                                tech = Technology(
                                    name=tech_result["name"],
                                    category=tech_result.get("category", "General"),
                                )
                                job_db.add(tech)
                                job_db.flush()

                            url_tech = job_db.query(UrlTechnology).filter(
                                UrlTechnology.url_id == u_id,
                                UrlTechnology.technology_id == tech.id,
                            ).first()
                            if not url_tech:
                                job_db.add(UrlTechnology(
                                    url_id=u_id,
                                    technology_id=tech.id,
                                    confidence=tech_result.get("confidence", "medium"),
                                ))

                        job_db.commit()
                    except Exception as ex:
                        logger.error(f"Error saving probe result for URL {u_id}: {ex}")
                        job_db.rollback()
                    finally:
                        job_db.close()
                        queue.task_done()

        # Start concurrent workers
        worker_count = min(concurrency, total_urls) if total_urls > 0 else 1
        workers = [asyncio.create_task(worker()) for _ in range(worker_count)]
        await asyncio.gather(*workers, return_exceptions=True)

        # Finalize job status
        final_state = ACTIVE_SCANS.get(scan_job_id, {}).get("status", "completed")
        scan_job = db.query(ScanJob).filter(ScanJob.id == scan_job_id).first()
        if scan_job:
            scan_job.status = "cancelled" if final_state == "cancelled" else "completed"
            scan_job.finished_at = datetime.now(timezone.utc)
            db.commit()

    except Exception as e:
        logger.error(f"Scan job {scan_job_id} encountered unexpected error: {e}", exc_info=True)
        if scan_job:
            scan_job.status = "failed"
            scan_job.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        ACTIVE_SCANS.pop(scan_job_id, None)
        db.close()
