import urllib.parse
from typing import List, Tuple

INTERESTING_PATHS = {
    "/admin": "Admin endpoint",
    "/admin/": "Admin endpoint",
    "/administrator": "Admin endpoint",
    "/login": "Authentication / Login",
    "/signin": "Authentication / Signin",
    "/auth": "Authentication endpoint",
    "/oauth": "OAuth endpoint",
    "/api": "API endpoint",
    "/api/v1": "API v1 endpoint",
    "/api/v2": "API v2 endpoint",
    "/api/v3": "API v3 endpoint",
    "/graphql": "GraphQL endpoint",
    "/graphiql": "GraphQL interface",
    "/swagger": "Swagger documentation",
    "/swagger-ui": "Swagger UI",
    "/swagger.json": "Swagger definition",
    "/openapi.json": "OpenAPI definition",
    "/openapi.yaml": "OpenAPI definition",
    "/debug": "Debug endpoint",
    "/actuator": "Spring Boot Actuator",
    "/actuator/health": "Actuator Health",
    "/actuator/env": "Actuator Environment",
    "/console": "Console interface",
    "/internal": "Internal endpoint",
    "/dev": "Development path",
    "/staging": "Staging path",
    "/test": "Test path",
    "/metrics": "Prometheus/Metrics endpoint",
    "/phpinfo.php": "PHP Info",
    "/.env": "Sensitive environment file",
    "/.git": "Git repository path",
    "/wp-admin": "WordPress Admin",
    "/wp-json": "WordPress REST API",
}

INTERESTING_PARAMS = {
    "id": "Identifier parameter (id)",
    "user": "User parameter",
    "username": "Username parameter",
    "uid": "User ID parameter",
    "redirect": "Redirect parameter",
    "url": "URL destination parameter",
    "next": "Next destination parameter",
    "dest": "Destination parameter",
    "callback": "Callback parameter",
    "return": "Return URL parameter",
    "returnurl": "Return URL parameter",
    "file": "File reference parameter",
    "path": "Path traversal parameter",
    "page": "Pagination or page load parameter",
    "query": "Query parameter",
    "q": "Search query parameter",
    "search": "Search parameter",
    "token": "Security / Token parameter",
    "auth": "Auth parameter",
    "key": "API Key / Key parameter",
    "secret": "Secret parameter",
    "cmd": "Command parameter",
    "exec": "Execution parameter",
}

SENSITIVE_EXTENSIONS = {
    ".json": "JSON data endpoint",
    ".xml": "XML data endpoint",
    ".bak": "Backup file (.bak)",
    ".old": "Old backup file (.old)",
    ".swp": "Swap file (.swp)",
    ".sql": "SQL database dump (.sql)",
    ".db": "Database file (.db)",
    ".sqlite": "SQLite file (.sqlite)",
    ".env": "Environment file (.env)",
    ".yml": "YAML configuration (.yml)",
    ".yaml": "YAML configuration (.yaml)",
    ".config": "Config file (.config)",
    ".conf": "Configuration file (.conf)",
    ".log": "Log file (.log)",
    ".tar": "Archive (.tar)",
    ".gz": "Archive (.gz)",
    ".zip": "Archive (.zip)",
}


def detect_interesting_url(url_or_path: str, query_str: str = "") -> Tuple[bool, List[str]]:
    """
    Evaluates a URL or path and query string for interesting triage patterns.
    Returns (is_interesting: bool, reasons: list of human-readable reason strings).
    Note: For triage purposes only, NEVER labels items as vulnerable.
    """
    reasons = []

    path = url_or_path
    if "://" in url_or_path:
        try:
            parsed = urllib.parse.urlsplit(url_or_path)
            path = parsed.path
            if not query_str:
                query_str = parsed.query
        except Exception:
            pass

    clean_path = path.lower().rstrip("/")
    if not clean_path:
        clean_path = "/"

    # Check exact and prefix path matches
    for p_pat, desc in INTERESTING_PATHS.items():
        clean_pat = p_pat.lower().rstrip("/")
        if not clean_pat:
            continue
        if clean_path == clean_pat or clean_path.startswith(clean_pat + "/"):
            reasons.append(desc)
            break

    # Check interesting file extensions
    for ext, desc in SENSITIVE_EXTENSIONS.items():
        if clean_path.endswith(ext):
            reasons.append(desc)
            break

    # Check query parameters
    if query_str:
        try:
            qsl = urllib.parse.parse_qsl(query_str, keep_blank_values=True)
            found_params = set()
            for key, _ in qsl:
                param_lower = key.lower()
                if param_lower in INTERESTING_PARAMS and param_lower not in found_params:
                    found_params.add(param_lower)
                    reasons.append(INTERESTING_PARAMS[param_lower])
        except Exception:
            pass

    # Deduplicate while preserving order
    unique_reasons = []
    for r in reasons:
        if r not in unique_reasons:
            unique_reasons.append(r)

    return len(unique_reasons) > 0, unique_reasons
