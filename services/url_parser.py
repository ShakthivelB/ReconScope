import urllib.parse
from typing import Dict, Any, Optional


def parse_url(raw_url: str) -> Dict[str, Any]:
    """
    Parses a raw URL string safely. If scheme is missing, defaults to 'http'.
    Returns a dictionary of URL components.
    """
    url_str = raw_url.strip()
    if not url_str:
        raise ValueError("URL cannot be empty")

    # If scheme is omitted (e.g. 'sub.example.com/path'), prepend 'http://'
    if not (url_str.startswith("http://") or url_str.startswith("https://")):
        url_str = "http://" + url_str

    parsed = urllib.parse.urlsplit(url_str)
    scheme = parsed.scheme.lower() if parsed.scheme else "http"
    hostname = parsed.hostname.lower() if parsed.hostname else ""
    port = parsed.port

    path = parsed.path
    if not path:
        path = "/"

    query = parsed.query

    return {
        "scheme": scheme,
        "hostname": hostname,
        "port": port,
        "path": path,
        "query": query,
        "fragment": parsed.fragment,
        "netloc": parsed.netloc.lower(),
        "parsed_url": url_str,
    }
