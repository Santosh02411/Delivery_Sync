"""
Outbound-request safety (SSRF protection) for webhook delivery.

THE PROBLEM
-----------
A webhook URL is chosen by an organization admin, and the server makes the
HTTP request. Checking that the URL "starts with http(s)://" says nothing
about WHERE it points. Without more, an admin could register
`http://169.254.169.254/latest/meta-data/` (cloud credentials),
`http://localhost:8000/admin/...`, `http://db:5432`, or a hostname whose DNS
points at any of those, and use this server as a proxy into the private
network. In a multi-tenant platform one tenant must never be able to do that.

WHAT THIS MODULE DOES (defence in depth, application layer)
-----------------------------------------------------------
1. validate_webhook_url()   — save time. HTTPS required by default, no
   credentials in the URL, no obviously internal hostnames, and if the host
   resolves right now, every address must be public. Gives the admin an
   immediate, readable error instead of a webhook that silently never fires.
   (A hostname that doesn't resolve yet is allowed: it may simply not be
   deployed yet, and the real check happens again at connect time.)

2. safe_post()              — delivery time. This is the check that actually
   matters, because DNS can change between "save" and "send" (DNS rebinding:
   an attacker's DNS answers with a public IP when you validate and with
   127.0.0.1 when you connect). So the connection itself is made through a
   custom urllib3 connection class that resolves the hostname ONCE, rejects
   the host if ANY answer is non-public, and then connects to that exact,
   already-validated IP address. There is no second lookup to poison.
   TLS still verifies the certificate against the ORIGINAL hostname (SNI and
   certificate checks use the hostname, only the TCP connect uses the IP).

3. Redirects are never followed. A 3xx from a public host to
   http://169.254.169.254/ is the classic bypass. A redirect is reported as a
   permanent failure with a readable reason instead.

4. Proxy environment variables are ignored (a proxy would do its own
   resolution and bypass the pinning) unless an explicit egress proxy is
   configured with WEBHOOK_EGRESS_PROXY (see docs/WEBHOOK_EGRESS_HARDENING.md).

This is the FIRST layer. The second layer is network policy outside the
application (egress firewall / dedicated egress proxy) — see
docs/WEBHOOK_EGRESS_HARDENING.md. Neither layer replaces the other.

CONFIGURATION (environment variables)
-------------------------------------
WEBHOOK_ALLOW_INSECURE_HTTP=1   also allow http:// (still subject to every IP check)
WEBHOOK_ALLOW_PRIVATE_TARGETS=1 allow private/loopback targets — ONLY honoured when
                                ENVIRONMENT is not "production" (lets a developer point
                                a webhook at a receiver on localhost). Ignored in production.
WEBHOOK_EGRESS_PROXY=<url>      send deliveries through this dedicated egress proxy.
"""

import ipaddress
import logging
import os
import socket
from dataclasses import dataclass
from typing import List, Optional, Tuple
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

logger = logging.getLogger(__name__)

MAX_URL_LENGTH = 2048

# Machine-readable reasons (stored on a failed delivery, returned to the admin).
BLOCKED_SCHEME = "blocked_scheme"
BLOCKED_HOST = "blocked_host"
BLOCKED_ADDRESS = "blocked_address"
INVALID_URL = "invalid_url"
REDIRECT_NOT_FOLLOWED = "redirect_not_followed"
DNS_FAILURE = "dns_failure"

_TRUTHY = {"1", "true", "yes", "on"}

# Hostnames that are internal by definition, whatever DNS says. (The IP check below is the real
# defence; this list just gives a fast, clear error and covers names a resolver might special-case.)
_BLOCKED_HOSTNAMES = {
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
    "metadata", "metadata.google.internal", "instance-data", "instance-data.ec2.internal",
    "kubernetes", "kubernetes.default", "kubernetes.default.svc",
}
_BLOCKED_SUFFIXES = (
    ".localhost", ".local", ".localdomain", ".internal", ".intranet", ".corp", ".lan",
    ".home", ".home.arpa", ".svc", ".cluster.local",
)

# Ranges `ipaddress.is_global` does not reliably cover on every Python version. Everything here
# is refused in addition to the stdlib's own is_private / is_loopback / is_link_local / ... checks.
_EXTRA_BLOCKED_NETWORKS = [
    ipaddress.ip_network(n) for n in (
        "0.0.0.0/8",         # "this network"
        "100.64.0.0/10",     # carrier-grade NAT (also used for some internal cloud services)
        "169.254.0.0/16",    # link-local, includes the cloud metadata endpoint 169.254.169.254
        "192.0.0.0/24",      # IETF protocol assignments
        "192.0.2.0/24",      # TEST-NET-1
        "198.18.0.0/15",     # benchmarking
        "198.51.100.0/24",   # TEST-NET-2
        "203.0.113.0/24",    # TEST-NET-3
        "224.0.0.0/4",       # multicast
        "240.0.0.0/4",       # reserved (includes 255.255.255.255 broadcast)
        "::/128",            # unspecified
        "::1/128",           # loopback
        "fc00::/7",          # unique local (includes fd00:ec2::254, AWS IPv6 metadata)
        "fe80::/10",         # link-local
        "ff00::/8",          # multicast
        "2001:db8::/32",     # documentation
    )
]
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class UnsafeUrlError(ValueError):
    """A webhook URL (or the address it resolves to) is not allowed. `code` is machine-readable."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def allow_insecure_http() -> bool:
    return _flag("WEBHOOK_ALLOW_INSECURE_HTTP")


def allow_private_targets() -> bool:
    """Developer escape hatch. NEVER honoured in production, whatever the variable says."""
    if not _flag("WEBHOOK_ALLOW_PRIVATE_TARGETS"):
        return False
    if os.environ.get("ENVIRONMENT", "development").strip().lower() == "production":
        logger.warning("WEBHOOK_ALLOW_PRIVATE_TARGETS is ignored because ENVIRONMENT=production.")
        return False
    return True


def egress_proxy() -> Optional[str]:
    return os.environ.get("WEBHOOK_EGRESS_PROXY", "").strip() or None


# --------------------------------------------------------------------------------------
# IP classification
# --------------------------------------------------------------------------------------

def _embedded_ipv4(ip: ipaddress.IPv6Address) -> Optional[ipaddress.IPv4Address]:
    """IPv4 address hidden inside an IPv6 one (::ffff:a.b.c.d, 6to4, Teredo, NAT64), if any.

    These are how `::ffff:127.0.0.1` or `64:ff9b::7f00:1` sneak a private IPv4 target past a check
    that only looks at the IPv6 form, so the embedded address is judged on its own."""
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip.teredo is not None:
        return ip.teredo[1]  # the client address (the server half is public infrastructure)
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def is_public_ip(value) -> bool:
    """True only for a globally routable unicast address. Anything private, loopback, link-local,
    multicast, reserved, unspecified, shared, or documentation-only is False."""
    ip = ipaddress.ip_address(value) if not isinstance(value, (ipaddress.IPv4Address, ipaddress.IPv6Address)) else value
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return is_public_ip(ip.ipv4_mapped)  # ::ffff:a.b.c.d is just a.b.c.d — judge it as IPv4
        embedded = _embedded_ipv4(ip)
        if embedded is not None and not is_public_ip(embedded):
            return False
        if ip.scope_id:  # fe80::1%eth0 style
            return False
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified or not ip.is_global):
        return False
    return not any(ip in net for net in _EXTRA_BLOCKED_NETWORKS if net.version == ip.version)


# --------------------------------------------------------------------------------------
# Hostname / URL validation
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ParsedTarget:
    scheme: str
    host: str          # lower-case, IDNA (ASCII) form, no trailing dot, no brackets
    port: int
    url: str


def _normalize_host(raw_host: str) -> str:
    host = raw_host.strip().rstrip(".").lower()
    if not host:
        raise UnsafeUrlError("The URL has no host name.", INVALID_URL)
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        raise UnsafeUrlError("The host name is not valid.", INVALID_URL)


def _check_hostname_policy(host: str) -> None:
    """Rejects names that are internal by definition. IP literals are judged by is_public_ip."""
    if host in _BLOCKED_HOSTNAMES or host.endswith(_BLOCKED_SUFFIXES):
        raise UnsafeUrlError(
            "That host name points at an internal service, which webhooks are not allowed to reach.",
            BLOCKED_HOST,
        )


def _ip_literal(host: str):
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def parse_webhook_url(url: str) -> ParsedTarget:
    """Syntax and policy checks that need no network. Raises UnsafeUrlError."""
    if not isinstance(url, str) or not url.strip():
        raise UnsafeUrlError("url is required.", INVALID_URL)
    url = url.strip()
    if len(url) > MAX_URL_LENGTH:
        raise UnsafeUrlError(f"url is too long (max {MAX_URL_LENGTH} characters).", INVALID_URL)
    if any(ord(c) < 33 or ord(c) == 127 for c in url):  # whitespace / control characters, incl. CRLF
        raise UnsafeUrlError("url must not contain spaces or control characters.", INVALID_URL)

    try:
        parts = urlsplit(url)
        port = parts.port
        hostname = parts.hostname
    except ValueError:
        raise UnsafeUrlError("url is not a valid URL.", INVALID_URL)

    scheme = (parts.scheme or "").lower()
    allowed = {"https", "http"} if allow_insecure_http() else {"https"}
    if scheme not in {"http", "https"}:
        raise UnsafeUrlError("url must be a valid https:// URL.", BLOCKED_SCHEME)
    if scheme not in allowed:
        raise UnsafeUrlError("Webhook URLs must use https:// (plain http:// is not allowed).", BLOCKED_SCHEME)
    if parts.username is not None or parts.password is not None:
        raise UnsafeUrlError("url must not contain a username or password.", INVALID_URL)
    if not hostname:
        raise UnsafeUrlError("url must include a host name.", INVALID_URL)

    host = _normalize_host(hostname)
    literal = _ip_literal(host)
    if literal is not None:
        if not allow_private_targets() and not is_public_ip(literal):
            raise UnsafeUrlError("That address is private or reserved, so webhooks are not allowed to reach it.", BLOCKED_ADDRESS)
    elif not allow_private_targets():
        _check_hostname_policy(host)

    if port is None:
        port = 443 if scheme == "https" else 80
    if not (1 <= port <= 65535):
        raise UnsafeUrlError("url has an invalid port.", INVALID_URL)
    return ParsedTarget(scheme=scheme, host=host, port=port, url=url)


def resolve_public_addresses(host: str, port: int) -> List[Tuple[int, int, int, tuple]]:
    """Resolves `host` ONCE and returns its (family, type, proto, sockaddr) answers — but only if
    EVERY answer is public. One private answer among public ones rejects the whole host: mixed
    answers are exactly what a rebinding attacker serves. Raises UnsafeUrlError."""
    literal = _ip_literal(host)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise UnsafeUrlError(f"The host name could not be resolved ({error}).", DNS_FAILURE)
    if not infos:
        raise UnsafeUrlError("The host name did not resolve to any address.", DNS_FAILURE)

    if not allow_private_targets():
        for _family, _type, _proto, _canon, sockaddr in infos:
            address = sockaddr[0].split("%")[0]
            if not is_public_ip(address):
                where = "an IP address literal" if literal is not None else "its DNS records"
                raise UnsafeUrlError(
                    f"{host} resolves (via {where}) to {address}, which is a private or reserved address. "
                    "Webhooks may only target public internet addresses.",
                    BLOCKED_ADDRESS,
                )
    return [(family, type_, proto, sockaddr) for family, type_, proto, _canon, sockaddr in infos]


def validate_webhook_url(url: str) -> str:
    """Save-time validation used by the create/update routes. Returns the cleaned URL or raises
    UnsafeUrlError. A host that does not resolve is accepted (see module docstring); a host that
    DOES resolve to anything non-public is refused."""
    target = parse_webhook_url(url)
    if _ip_literal(target.host) is None:
        try:
            resolve_public_addresses(target.host, target.port)
        except UnsafeUrlError as error:
            if error.code != DNS_FAILURE:
                raise
    return target.url


# --------------------------------------------------------------------------------------
# Connection-time enforcement
# --------------------------------------------------------------------------------------

def _connect(family: int, socktype: int, proto: int, sockaddr: tuple, timeout, source_address=None, socket_options=None) -> socket.socket:
    """Opens one TCP connection to an ALREADY-VALIDATED address. Separate function so tests can
    observe exactly which address the application actually connected to."""
    sock = socket.socket(family, socktype, proto)
    try:
        for level, option, value in (socket_options or []):
            sock.setsockopt(level, option, value)
        if timeout is not None and timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:  # type: ignore[attr-defined]
            sock.settimeout(timeout)
        if source_address:
            sock.bind(source_address)
        sock.connect(sockaddr)
        return sock
    except Exception:
        sock.close()
        raise


def _open_validated_socket(host: str, port: int, timeout, source_address=None, socket_options=None) -> socket.socket:
    """Resolve once, validate every answer, connect to a validated IP. Used by both connection classes."""
    answers = resolve_public_addresses(host, port)
    last_error: Optional[Exception] = None
    for family, socktype, proto, sockaddr in answers:
        try:
            return _connect(family, socktype, proto, sockaddr, timeout, source_address, socket_options)
        except OSError as error:
            last_error = error
    raise last_error or OSError("could not connect")


class _GuardedHTTPConnection(HTTPConnection):
    def _new_conn(self):
        return _open_validated_socket(self._dns_host, self.port, self.timeout, self.source_address, self.socket_options)


class _GuardedHTTPSConnection(HTTPSConnection):
    # TLS (SNI + certificate verification) still uses self.host — the original hostname — because
    # only the underlying TCP socket is replaced.
    def _new_conn(self):
        return _open_validated_socket(self._dns_host, self.port, self.timeout, self.source_address, self.socket_options)


class _GuardedHTTPPool(HTTPConnectionPool):
    ConnectionCls = _GuardedHTTPConnection


class _GuardedHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _GuardedHTTPSConnection


class GuardedAdapter(HTTPAdapter):
    """requests adapter whose every connection goes through the address check above."""

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {"http": _GuardedHTTPPool, "https": _GuardedHTTPSPool}


def build_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False  # ignore HTTP(S)_PROXY / NO_PROXY / netrc from the environment
    proxy = egress_proxy()
    if proxy:
        # A dedicated egress proxy performs its own (infrastructure-level) filtering and DNS, so
        # the connection-level pinning cannot apply; the save-time and pre-flight checks still do.
        session.proxies = {"http": proxy, "https": proxy}
    else:
        adapter = GuardedAdapter(max_retries=0)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
    return session


@dataclass
class SafePostResult:
    status_code: Optional[int] = None
    error_code: Optional[str] = None      # one of the constants above, or "network_error"
    error_message: Optional[str] = None
    permanent: bool = False               # True: retrying cannot help (blocked / redirect)

    @property
    def ok(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 300


def safe_post(url: str, data: str, headers: dict, timeout: float) -> SafePostResult:
    """POSTs `data` to a webhook URL with every SSRF protection applied. Never raises for
    network/policy problems — returns a SafePostResult describing what happened."""
    try:
        target = parse_webhook_url(url)  # re-validated at send time: the stored URL may predate these rules
        if egress_proxy() and _ip_literal(target.host) is None:
            resolve_public_addresses(target.host, target.port)  # pre-flight only (no pinning behind a proxy)
    except UnsafeUrlError as error:
        permanent = error.code != DNS_FAILURE
        return SafePostResult(error_code=error.code, error_message=str(error), permanent=permanent)

    session = build_session()
    try:
        response = session.post(target.url, data=data, headers=headers, timeout=timeout,
                                allow_redirects=False, stream=True)
        try:
            status = response.status_code
        finally:
            response.close()  # never download the receiver's response body
        if 300 <= status < 400:
            return SafePostResult(
                status_code=status, error_code=REDIRECT_NOT_FOLLOWED, permanent=True,
                error_message=f"The endpoint answered with a redirect (HTTP {status}). Redirects are not followed; "
                              "update the webhook to the final URL.",
            )
        return SafePostResult(status_code=status)
    except UnsafeUrlError as error:  # raised from inside the connection by the resolver check
        return SafePostResult(error_code=error.code, error_message=str(error), permanent=error.code != DNS_FAILURE)
    except requests.RequestException as error:
        # urllib3 wraps exceptions raised while connecting; unwrap ours so a blocked target is
        # reported as blocked (and not retried) instead of as a generic network error.
        cause = error
        for _ in range(8):  # bounded walk down the exception chain
            if cause is None:
                break
            if isinstance(cause, UnsafeUrlError):
                return SafePostResult(error_code=cause.code, error_message=str(cause), permanent=cause.code != DNS_FAILURE)
            cause = cause.__cause__ or cause.__context__ or (cause.args[0] if cause.args and isinstance(cause.args[0], BaseException) else None)
        return SafePostResult(error_code="network_error", error_message=str(error)[:300])
    finally:
        session.close()
