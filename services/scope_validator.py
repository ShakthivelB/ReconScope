import fnmatch
import ipaddress
import socket
import urllib.parse
from typing import List, Optional, Tuple

BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    # IPv6
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::ffff:0:0/96"),  # IPv4-mapped IPv6
]


def is_safe_ip(ip_obj: ipaddress._BaseAddress) -> bool:
    """Check if an IP address is public and safe to request."""
    # Check IPv4-mapped IPv6 explicitly
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped:
        ip_obj = ip_obj.ipv4_mapped

    if (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    ):
        return False

    for net in BLOCKED_NETWORKS:
        try:
            if ip_obj in net:
                return False
        except TypeError:
            continue

    return True


def resolve_and_check_ips(hostname: str) -> Tuple[bool, str, List[str]]:
    """
    Resolves hostname to IP addresses and verifies that none belong
    to blocked internal, private, loopback, or metadata ranges.
    """
    clean_host = hostname.strip().lower()
    if not clean_host:
        return False, "Empty hostname", []

    # Block common local names immediately
    if clean_host in ("localhost", "localhost.localdomain") or clean_host.endswith(".localhost"):
        return False, f"Loopback hostname blocked: {clean_host}", []

    # Check if host is already an IP address
    try:
        ip_obj = ipaddress.ip_address(clean_host)
        if not is_safe_ip(ip_obj):
            return False, f"Private or blocked IP address: {clean_host}", [clean_host]
        return True, "", [clean_host]
    except ValueError:
        pass

    # Resolve via DNS
    try:
        addr_info = socket.getaddrinfo(clean_host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        return False, f"DNS resolution failed: {e}", []
    except Exception as e:
        return False, f"DNS error: {e}", []

    resolved_ips: List[str] = []
    for entry in addr_info:
        ip_str = entry[4][0]
        if ip_str not in resolved_ips:
            resolved_ips.append(ip_str)

    if not resolved_ips:
        return False, "No IP addresses resolved", []

    for ip_str in resolved_ips:
        try:
            ip_obj = ipaddress.ip_address(ip_str)
            if not is_safe_ip(ip_obj):
                return False, f"Resolved to blocked IP {ip_str} (SSRF protection)", resolved_ips
        except ValueError:
            return False, f"Invalid resolved IP: {ip_str}", resolved_ips

    return True, "", resolved_ips


def is_in_scope(url_or_host: str, root_domain: str, scope_rules: str = "") -> bool:
    """
    Verifies if a URL or hostname is within the configured target scope.
    Supports wildcards (*.example.com), exact domain matching, and exclusions (!bad.example.com).
    """
    target_host = url_or_host.strip().lower()
    if "://" in target_host:
        try:
            parsed = urllib.parse.urlsplit(target_host)
            target_host = parsed.hostname or ""
        except Exception:
            return False

    if not target_host:
        return False

    clean_root = root_domain.strip().lower()

    # Parse scope rules into include patterns and exclude patterns
    includes = []
    excludes = []

    if scope_rules:
        # Split by comma or newline
        lines = [r.strip().lower() for r in scope_rules.replace(",", "\n").splitlines() if r.strip()]
        for line in lines:
            if line.startswith("!"):
                excludes.append(line[1:].strip())
            else:
                includes.append(line)

    # If no explicit includes, default to root_domain and *.root_domain
    if not includes and clean_root:
        includes = [clean_root, f"*.{clean_root}"]

    # First check excludes
    for pattern in excludes:
        if fnmatch.fnmatch(target_host, pattern):
            return False

    # Check includes
    for pattern in includes:
        if fnmatch.fnmatch(target_host, pattern):
            return True
        # Handle cases like pattern="example.com" matching subdomain if intended, or wildcard
        if pattern.startswith("*.") and target_host == pattern[2:]:
            return True

    return False


def validate_target_url(
    url: str, root_domain: str, scope_rules: str = ""
) -> Tuple[bool, str]:
    """
    Validates both scope compliance and SSRF safety before an HTTP request is made.
    """
    url_str = url.strip()
    if not (url_str.startswith("http://") or url_str.startswith("https://")):
        return False, "Invalid URL scheme; must be http or https"

    try:
        parsed = urllib.parse.urlsplit(url_str)
        hostname = parsed.hostname
    except Exception as e:
        return False, f"Malformed URL: {e}"

    if not hostname:
        return False, "URL has no hostname"

    # Check scope
    if root_domain and not is_in_scope(hostname, root_domain, scope_rules):
        return False, f"Host '{hostname}' is out of scope for target '{root_domain}'"

    # Check SSRF & DNS resolution
    is_safe, reason, _ = resolve_and_check_ips(hostname)
    if not is_safe:
        return False, reason

    return True, ""
