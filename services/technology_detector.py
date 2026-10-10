import re
from typing import Dict, List, Tuple


def detect_technologies(
    headers: Dict[str, str],
    body_text: str = "",
) -> List[Dict[str, str]]:
    """
    Detects technologies based on response headers and HTML content signatures.
    Returns a list of dicts: [{"name": "React", "category": "Frontend Framework", "confidence": "high"}]
    """
    detected: Dict[str, Dict[str, str]] = {}

    headers_lower = {k.lower(): str(v).lower() for k, v in headers.items()}
    server_hdr = headers_lower.get("server", "")
    powered_by = headers_lower.get("x-powered-by", "")
    all_headers_str = " ".join(f"{k}:{v}" for k, v in headers_lower.items())

    # Server checks
    if "nginx" in server_hdr:
        detected["Nginx"] = {"category": "Web Server", "confidence": "high"}
    elif "apache" in server_hdr:
        detected["Apache"] = {"category": "Web Server", "confidence": "high"}
    elif "cloudflare" in server_hdr or "cf-ray" in headers_lower or "cf-cache-status" in headers_lower:
        detected["Cloudflare"] = {"category": "CDN / WAF", "confidence": "high"}
    elif "caddy" in server_hdr:
        detected["Caddy"] = {"category": "Web Server", "confidence": "high"}
    elif "microsoft-iis" in server_hdr:
        detected["Microsoft IIS"] = {"category": "Web Server", "confidence": "high"}
    elif "envoy" in server_hdr:
        detected["Envoy"] = {"category": "Proxy", "confidence": "high"}

    # X-Powered-By / Framework headers
    if "express" in powered_by:
        detected["Express"] = {"category": "Backend Framework", "confidence": "high"}
    if "php" in powered_by or "phpsessid" in all_headers_str:
        detected["PHP"] = {"category": "Programming Language", "confidence": "high"}
    if "next.js" in powered_by:
        detected["Next.js"] = {"category": "Frontend Framework", "confidence": "high"}
    if "asp.net" in powered_by or "x-aspnet-version" in headers_lower:
        detected["ASP.NET"] = {"category": "Backend Framework", "confidence": "high"}
    if "laravel_session" in all_headers_str or "x-laravel-cache" in headers_lower:
        detected["Laravel"] = {"category": "Backend Framework", "confidence": "high"}
    if "drupal" in all_headers_str:
        detected["Drupal"] = {"category": "CMS", "confidence": "high"}

    # HTML / Body checks (limit search to first 250KB for speed and resource limits)
    sample_body = body_text[:262144].lower() if body_text else ""
    if sample_body:
        # Next.js
        if "__next_data__" in sample_body or "/_next/static/" in sample_body:
            detected["Next.js"] = {"category": "Frontend Framework", "confidence": "high"}
            detected["React"] = {"category": "Frontend Framework", "confidence": "high"}

        # React
        if "data-reactroot" in sample_body or "react.production.min.js" in sample_body or "_reactroot" in sample_body:
            detected["React"] = {"category": "Frontend Framework", "confidence": "high"}

        # Vue.js
        if "data-v-" in sample_body or "vue.js" in sample_body or "vue.min.js" in sample_body or "v-cloak" in sample_body:
            detected["Vue.js"] = {"category": "Frontend Framework", "confidence": "high"}

        # Angular
        if "ng-version" in sample_body or "ng-app" in sample_body or "ng-binding" in sample_body:
            detected["Angular"] = {"category": "Frontend Framework", "confidence": "high"}

        # WordPress
        if "wp-content/" in sample_body or "wp-includes/" in sample_body or 'generator" content="wordpress' in sample_body:
            detected["WordPress"] = {"category": "CMS", "confidence": "high"}

        # Django
        if "csrfmiddlewaretoken" in sample_body or "__admin__" in sample_body:
            detected["Django"] = {"category": "Backend Framework", "confidence": "medium"}

        # Bootstrap
        if "bootstrap.min.css" in sample_body or "bootstrap.css" in sample_body:
            detected["Bootstrap"] = {"category": "CSS Framework", "confidence": "medium"}

        # Tailwind
        if "tailwind" in sample_body:
            detected["Tailwind CSS"] = {"category": "CSS Framework", "confidence": "medium"}

        # Swagger / OpenAPI
        if "swagger-ui" in sample_body or "swagger-ui.css" in sample_body:
            detected["Swagger UI"] = {"category": "API Documentation", "confidence": "high"}

    return [
        {"name": name, "category": meta["category"], "confidence": meta["confidence"]}
        for name, meta in detected.items()
    ]
