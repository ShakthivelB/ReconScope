import fnmatch
import ipaddress
import socket
import urllib.parse
from typing import List, Tuple


BLOCKED_NETWORKS = [
    ipaddress.ip_network(network)
    for network in (
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
        "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
        "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24",
        "224.0.0.0/4", "240.0.0.0/4", "255.255.255.255/32", "::/128", "::1/128",
        "::ffff:0:0/96", "64:ff9b::/96", "100::/64", "2001:db8::/32", "2001::/23",
        "fc00::/7", "fe80::/10", "ff00::/8",
    )
]


def is_safe_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if not address.is_global:
        return False
    return not any(address.version == net.version and address in net for net in BLOCKED_NETWORKS)


def resolve_and_check_ips(hostname: str) -> Tuple[bool, str, List[str]]:
    host = hostname.strip().lower().rstrip(".")
    if not host:
        return False, "Empty hostname", []
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return False, f"Local hostname blocked: {host}", []
    try:
        address = ipaddress.ip_address(host)
        if not is_safe_ip(address):
            return False, f"Private or blocked IP address: {host}", [str(address)]
        return True, "", [str(address)]
    except ValueError:
        pass

    try:
        answers = socket.getaddrinfo(host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return False, f"DNS resolution failed: {exc}", []
    except Exception as exc:
        return False, f"DNS error: {exc}", []

    addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses:
        return False, "No IP addresses resolved", []
    for raw_address in addresses:
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError:
            return False, f"Invalid resolved IP: {raw_address}", addresses
        if not is_safe_ip(address):
            return False, f"DNS resolved to prohibited address {address} (network safety)", addresses
    return True, "", addresses


def is_in_scope(url_or_host: str, root_domain: str, scope_rules: str = "") -> bool:
    host = url_or_host.strip().lower()
    if "://" in host:
        try:
            host = urllib.parse.urlsplit(host).hostname or ""
        except ValueError:
            return False
    if not host:
        return False

    root = root_domain.strip().lower().rstrip(".")
    include_rules, exclude_rules = [], []
    for rule in (scope_rules or "").replace(",", "\n").splitlines():
        rule = rule.strip().lower()
        if not rule:
            continue
        (exclude_rules if rule.startswith("!") else include_rules).append(
            rule[1:].strip() if rule.startswith("!") else rule
        )
    if not include_rules and root:
        include_rules = [root, f"*.{root}"]
    if any(fnmatch.fnmatchcase(host, rule) for rule in exclude_rules):
        return False
    return any(
        fnmatch.fnmatchcase(host, rule) or (rule.startswith("*.") and host == rule[2:])
        for rule in include_rules
    )


def validate_target_url(
    url: str,
    root_domain: str = "",
    scope_rules: str = "",
    enforce_scope: bool = False,
) -> Tuple[bool, str]:
    value = url.strip()
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        return False, f"Malformed URL: {exc}"
    if parsed.scheme.lower() not in ("http", "https"):
        return False, "Invalid URL scheme; must be http or https"
    host = parsed.hostname
    if not host:
        return False, "URL has no hostname"

    # Always perform DNS and destination safety checks, independent of scope mode.
    safe, reason, _addresses = resolve_and_check_ips(host)
    if not safe:
        return False, reason
    if enforce_scope and root_domain and not is_in_scope(host, root_domain, scope_rules):
        return False, f"Host '{host}' is out of scope for target '{root_domain}'"
    return True, ""
