import urllib.parse
import re
from typing import Tuple

UNRESERVED_PAT = re.compile(r"%([0-9a-fA-F]{2})")


def _unreserved_sub(match: re.Match) -> str:
    val = int(match.group(1), 16)
    # RFC 3986 unreserved characters: ALPHA / DIGIT / "-" / "." / "_" / "~"
    if (
        (65 <= val <= 90)
        or (97 <= val <= 122)
        or (48 <= val <= 57)
        or val in (45, 46, 95, 126)
    ):
        return chr(val)
    return f"%{match.group(1).upper()}"


def normalize_url(raw_url: str) -> str:
    """
    Deterministically normalizes a URL string:
    - Standardizes scheme (lowercase, default http if absent)
    - Lowercases hostname
    - Removes default ports (:80 for http, :443 for https)
    - Standardizes root path to '/' and normalizes trailing slashes on subpaths
    - Strips fragment
    - Sorts query parameters deterministically while keeping values intact
    """
    url_str = raw_url.strip()
    if not url_str:
        return ""

    url_lower = url_str.lower()
    if not (url_lower.startswith("http://") or url_lower.startswith("https://")):
        url_str = "http://" + url_str

    try:
        parsed = urllib.parse.urlsplit(url_str)
    except Exception:
        return url_str

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        scheme = "http"

    hostname = parsed.hostname.lower() if parsed.hostname else ""
    if not hostname:
        return url_str

    port = parsed.port
    # Remove default ports
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None

    netloc = hostname
    if port:
        netloc = f"{hostname}:{port}"

    # Normalize path
    path = parsed.path
    if not path or path == "/":
        path = "/"
    else:
        # Collapse multiple slashes (preserving leading /)
        path = re.sub(r"/{2,}", "/", path)
        # Normalize percent encoding
        path = UNRESERVED_PAT.sub(_unreserved_sub, path)
        # Strip trailing slash on non-root paths for deterministic equivalence
        if path.endswith("/"):
            path = path.rstrip("/")

    # Normalize query string
    query = ""
    if parsed.query:
        # Keep blank values, preserve parameter names and values, sort pairs for determinism
        qsl = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        qsl_sorted = sorted(qsl, key=lambda item: (item[0], item[1]))
        query = urllib.parse.urlencode(qsl_sorted, doseq=True)

    # Assemble normalized URL (omit fragment)
    normalized = urllib.parse.urlunsplit((scheme, netloc, path, query, ""))
    return normalized
