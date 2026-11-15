"""
Tests for outbound-webhook SSRF protection (services/url_safety.py + routes/webhooks.py).

What is covered:
- which IP addresses count as public (private / loopback / link-local / metadata / reserved /
  IPv4-in-IPv6 tricks are all refused)
- save-time URL validation: https by default, no credentials, no internal host names,
  numeric-IP obfuscation (decimal / hex / octal), hosts that RESOLVE to private addresses
- DNS rebinding: a host that looks public at save time but resolves to a private address at
  send time is blocked; the connection goes to the exact IP that was validated (no 2nd lookup)
- redirects are never followed (a 302 to the cloud-metadata address is not chased)
- a valid public endpoint still works end to end (signature header, body, 2xx -> success)
- the create/update routes return 400 for unsafe URLs, and a webhook whose stored URL is unsafe
  (e.g. saved before these rules existed) is refused at send time

No test here touches the real network: DNS answers are faked, and the "public" server the
delivery connects to is a throwaway HTTP server on 127.0.0.1 reached through the module's `_connect`
seam (the address that was VALIDATED is a documentation-style public IP, the socket goes to the
local server).
"""

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.services import url_safety
from app.services.url_safety import (
    BLOCKED_ADDRESS, BLOCKED_HOST, BLOCKED_SCHEME, DNS_FAILURE, INVALID_URL, REDIRECT_NOT_FOLLOWED,
    UnsafeUrlError, is_public_ip, parse_webhook_url, resolve_public_addresses, safe_post, validate_webhook_url,
)

PUBLIC_IP = "93.184.216.34"      # a normal, globally routable address
PRIVATE_IP = "10.0.0.5"


@pytest.fixture(autouse=True)
def _clean_webhook_env(monkeypatch):
    """Every test starts from the production defaults: https only, no private targets, no proxy."""
    for name in ("WEBHOOK_ALLOW_INSECURE_HTTP", "WEBHOOK_ALLOW_PRIVATE_TARGETS", "WEBHOOK_EGRESS_PROXY",
                 "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)


def _fake_dns(monkeypatch, *answers_per_call):
    """Replaces getaddrinfo. Each call returns the next list of IPs (the last one repeats). Returns a
    list that records every hostname looked up, so tests can count resolutions."""
    calls = []

    def fake_getaddrinfo(host, port, *args, **kwargs):
        calls.append(host)
        ips = answers_per_call[min(len(calls), len(answers_per_call)) - 1]
        return [
            (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port) if ":" not in ip else (ip, port, 0, 0))
            for ip in ips
        ]

    monkeypatch.setattr(url_safety.socket, "getaddrinfo", fake_getaddrinfo)
    return calls


# --------------------------------------------------------------------------------------
# Which addresses are public
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("address", [
    "127.0.0.1", "127.1.2.3", "10.0.0.1", "172.16.0.1", "172.31.255.255", "192.168.1.1",
    "169.254.169.254",            # cloud metadata
    "169.254.0.1", "100.64.0.1", "0.0.0.0", "0.1.2.3", "224.0.0.1", "240.0.0.1", "255.255.255.255",
    "198.18.0.1", "192.0.2.1",
    "::1", "::", "fe80::1", "fc00::1", "fd00:ec2::254", "ff02::1", "2001:db8::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",   # IPv4 hidden in IPv6
    "64:ff9b::7f00:1",                                                   # NAT64 -> 127.0.0.1
    "2002:7f00:1::",                                                     # 6to4 -> 127.0.0.1
])
def test_private_loopback_linklocal_and_reserved_addresses_are_not_public(address):
    assert is_public_ip(address) is False


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", PUBLIC_IP, "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_public_addresses_are_public(address):
    assert is_public_ip(address) is True


# --------------------------------------------------------------------------------------
# Save-time URL validation
# --------------------------------------------------------------------------------------

def test_https_is_required_by_default():
    with pytest.raises(UnsafeUrlError) as exc:
        parse_webhook_url("http://example.com/hook")
    assert exc.value.code == BLOCKED_SCHEME


def test_http_allowed_only_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("WEBHOOK_ALLOW_INSECURE_HTTP", "1")
    assert parse_webhook_url("http://example.com/hook").scheme == "http"


def test_insecure_http_flag_does_not_unlock_private_targets(monkeypatch):
    monkeypatch.setenv("WEBHOOK_ALLOW_INSECURE_HTTP", "1")
    with pytest.raises(UnsafeUrlError) as exc:
        parse_webhook_url("http://127.0.0.1:8000/admin")
    assert exc.value.code == BLOCKED_ADDRESS


@pytest.mark.parametrize("url, code", [
    ("ftp://example.com/x", BLOCKED_SCHEME),
    ("javascript:alert(1)", BLOCKED_SCHEME),
    ("file:///etc/passwd", BLOCKED_SCHEME),
    ("not-a-url", BLOCKED_SCHEME),
    ("", INVALID_URL),
    ("https://", INVALID_URL),
    ("https://user:pass@example.com/hook", INVALID_URL),
    ("https://example.com/hook\r\nX-Evil: 1", INVALID_URL),
    ("https://exa mple.com/", INVALID_URL),
    ("https://example.com:99999/", INVALID_URL),
    ("https://example.com/" + "a" * 3000, INVALID_URL),
    ("https://localhost/hook", BLOCKED_HOST),
    ("https://LOCALHOST./hook", BLOCKED_HOST),
    ("https://app.localhost/hook", BLOCKED_HOST),
    ("https://metadata.google.internal/computeMetadata/v1/", BLOCKED_HOST),
    ("https://db.internal/", BLOCKED_HOST),
    ("https://printer.local/", BLOCKED_HOST),
    ("https://127.0.0.1/", BLOCKED_ADDRESS),
    ("https://10.1.2.3/", BLOCKED_ADDRESS),
    ("https://192.168.0.10:8443/", BLOCKED_ADDRESS),
    ("https://169.254.169.254/latest/meta-data/iam/security-credentials/", BLOCKED_ADDRESS),
    ("https://[::1]/", BLOCKED_ADDRESS),
    ("https://[::ffff:7f00:1]/", BLOCKED_ADDRESS),
    ("https://[fd00:ec2::254]/latest/meta-data/", BLOCKED_ADDRESS),
])
def test_unsafe_urls_are_refused(url, code):
    with pytest.raises(UnsafeUrlError) as exc:
        validate_webhook_url(url)
    assert exc.value.code == code


@pytest.mark.parametrize("obfuscated", ["2130706433", "0x7f000001", "0x7f.1", "017700000001", "127.1"])
def test_numeric_ip_obfuscation_is_caught_by_resolution(obfuscated):
    """Decimal / hex / octal spellings of 127.0.0.1 aren't parsed as IPs by `ipaddress`, but the OS
    resolver turns them into 127.0.0.1 — which is why the check runs on RESOLVED addresses."""
    with pytest.raises(UnsafeUrlError) as exc:
        validate_webhook_url(f"https://{obfuscated}/hook")
    assert exc.value.code == BLOCKED_ADDRESS


def test_hostname_resolving_to_private_address_is_refused_at_save_time(monkeypatch):
    _fake_dns(monkeypatch, [PRIVATE_IP])
    with pytest.raises(UnsafeUrlError) as exc:
        validate_webhook_url("https://innocent-looking.example.org/hook")
    assert exc.value.code == BLOCKED_ADDRESS
    assert PRIVATE_IP in str(exc.value)


def test_one_private_answer_among_public_ones_rejects_the_host(monkeypatch):
    """Mixed answers are what a rebinding attacker serves: the whole host is refused."""
    _fake_dns(monkeypatch, [PUBLIC_IP, "127.0.0.1"])
    with pytest.raises(UnsafeUrlError) as exc:
        resolve_public_addresses("mixed.example.org", 443)
    assert exc.value.code == BLOCKED_ADDRESS


def test_public_hostname_is_accepted(monkeypatch):
    _fake_dns(monkeypatch, [PUBLIC_IP])
    assert validate_webhook_url("https://hooks.example.org/in") == "https://hooks.example.org/in"


def test_unresolvable_host_is_accepted_at_save_time(monkeypatch):
    """Not deployed yet isn't an attack; the check runs again when a delivery is attempted."""
    def boom(*a, **k):
        raise socket.gaierror(-2, "Name or service not known")
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", boom)
    assert validate_webhook_url("https://not-deployed-yet.example.org/hook")


def test_private_target_override_is_ignored_in_production(monkeypatch):
    monkeypatch.setenv("WEBHOOK_ALLOW_PRIVATE_TARGETS", "1")
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(UnsafeUrlError):
        validate_webhook_url("https://127.0.0.1/hook")


def test_private_target_override_works_in_development_only(monkeypatch):
    monkeypatch.setenv("WEBHOOK_ALLOW_PRIVATE_TARGETS", "1")
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert validate_webhook_url("https://127.0.0.1/hook")


# --------------------------------------------------------------------------------------
# Delivery time: a throwaway local server stands in for "the public internet"
# --------------------------------------------------------------------------------------

class _Receiver:
    """Local HTTP server that records every request it gets and answers with a scripted response."""

    def __init__(self, status=200, location=None):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": self.rfile.read(length).decode()})
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def receiver_factory():
    created = []

    def make(**kwargs):
        receiver = _Receiver(**kwargs)
        created.append(receiver)
        return receiver

    yield make
    for receiver in created:
        receiver.close()


def _route_connections_to(monkeypatch, port):
    """DNS says the host is PUBLIC_IP (so validation passes); the actual socket goes to the local
    server. Returns the list of addresses the application asked to connect to."""
    connected_to = []
    real_connect = url_safety._connect

    def fake_connect(family, socktype, proto, sockaddr, timeout, source_address=None, socket_options=None):
        connected_to.append(sockaddr[0])
        return real_connect(socket.AF_INET, socket.SOCK_STREAM, 0, ("127.0.0.1", port), timeout, source_address, socket_options)

    monkeypatch.setattr(url_safety, "_connect", fake_connect)
    return connected_to


HEADERS = {"Content-Type": "application/json", "X-Webhook-Signature": "sha256=abc", "X-Webhook-Event": "order.paid"}


def test_valid_public_endpoint_still_receives_the_delivery(monkeypatch, receiver_factory):
    receiver = receiver_factory(status=200)
    monkeypatch.setenv("WEBHOOK_ALLOW_INSECURE_HTTP", "1")  # the local test server speaks plain http
    _fake_dns(monkeypatch, [PUBLIC_IP])
    connected_to = _route_connections_to(monkeypatch, receiver.port)

    result = safe_post("http://hooks.example.org/in", json.dumps({"event": "order.paid"}), HEADERS, timeout=5)

    assert result.ok and result.status_code == 200 and result.error_code is None
    assert connected_to == [PUBLIC_IP]  # the connection used the validated address
    assert len(receiver.requests) == 1
    assert receiver.requests[0]["path"] == "/in"
    assert receiver.requests[0]["headers"]["X-Webhook-Signature"] == "sha256=abc"
    assert json.loads(receiver.requests[0]["body"]) == {"event": "order.paid"}


def test_server_error_is_a_retryable_failure_not_a_block(monkeypatch, receiver_factory):
    receiver = receiver_factory(status=503)
    monkeypatch.setenv("WEBHOOK_ALLOW_INSECURE_HTTP", "1")
    _fake_dns(monkeypatch, [PUBLIC_IP])
    _route_connections_to(monkeypatch, receiver.port)

    result = safe_post("http://hooks.example.org/in", "{}", HEADERS, timeout=5)
    assert not result.ok and result.status_code == 503
    assert result.permanent is False


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirects_are_never_followed(monkeypatch, receiver_factory, status):
    """A public host redirecting to the metadata address is the classic SSRF bypass."""
    receiver = receiver_factory(status=status, location="http://169.254.169.254/latest/meta-data/")
    monkeypatch.setenv("WEBHOOK_ALLOW_INSECURE_HTTP", "1")
    _fake_dns(monkeypatch, [PUBLIC_IP])
    connected_to = _route_connections_to(monkeypatch, receiver.port)

    result = safe_post("http://hooks.example.org/in", "{}", HEADERS, timeout=5)

    assert result.error_code == REDIRECT_NOT_FOLLOWED
    assert result.status_code == status
    assert result.permanent is True
    assert len(receiver.requests) == 1            # the server was asked once; the Location was not chased
    assert connected_to == [PUBLIC_IP]            # no connection to 169.254.169.254 was ever attempted


def test_private_destination_is_blocked_and_nothing_is_sent(monkeypatch):
    _fake_dns(monkeypatch, [PRIVATE_IP])
    attempts = []
    monkeypatch.setattr(url_safety, "_connect", lambda *a, **k: attempts.append(a))

    result = safe_post("https://sneaky.example.org/hook", "{}", HEADERS, timeout=5)

    assert result.error_code == BLOCKED_ADDRESS and result.permanent is True
    assert attempts == []   # no socket was even opened


def test_dns_rebinding_is_blocked_at_send_time(monkeypatch):
    """Save time: the name resolves to a public IP -> accepted. Send time: it now resolves to
    127.0.0.1 -> blocked, and no connection is attempted."""
    calls = _fake_dns(monkeypatch, [PUBLIC_IP], ["127.0.0.1"])
    attempts = []
    monkeypatch.setattr(url_safety, "_connect", lambda *a, **k: attempts.append(a))

    assert validate_webhook_url("https://rebind.example.org/hook")        # 1st lookup: public
    result = safe_post("https://rebind.example.org/hook", "{}", HEADERS, timeout=5)  # 2nd lookup: loopback

    assert len(calls) == 2
    assert result.error_code == BLOCKED_ADDRESS and result.permanent is True
    assert attempts == []


def test_connection_is_pinned_to_the_validated_ip_with_no_second_lookup(monkeypatch):
    """Within ONE delivery the name is resolved exactly once and the socket goes to that same IP, so
    there is no window in which a second DNS answer could swap the destination."""
    calls = _fake_dns(monkeypatch, [PUBLIC_IP], ["127.0.0.1"])  # a 2nd lookup, if one happened, would be loopback
    connected_to = []

    def fake_connect(family, socktype, proto, sockaddr, *a, **k):
        connected_to.append(sockaddr[0])
        raise ConnectionRefusedError("test: nothing listening")

    monkeypatch.setattr(url_safety, "_connect", fake_connect)

    result = safe_post("https://pinned.example.org/hook", "{}", HEADERS, timeout=5)

    assert calls == ["pinned.example.org"]       # looked up once
    assert connected_to == [PUBLIC_IP]           # connected to exactly what was validated
    assert result.error_code == "network_error" and result.permanent is False


def test_environment_proxy_variables_are_ignored(monkeypatch, receiver_factory):
    """A proxy would resolve names itself and bypass the pinning, so HTTP(S)_PROXY must not be used."""
    receiver = receiver_factory(status=200)
    monkeypatch.setenv("WEBHOOK_ALLOW_INSECURE_HTTP", "1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")   # a dead proxy: using it would fail the delivery
    _fake_dns(monkeypatch, [PUBLIC_IP])
    _route_connections_to(monkeypatch, receiver.port)

    assert safe_post("http://hooks.example.org/in", "{}", HEADERS, timeout=5).ok


def test_unresolvable_host_is_a_retryable_failure(monkeypatch):
    def boom(*a, **k):
        raise socket.gaierror(-2, "Name or service not known")
    monkeypatch.setattr(url_safety.socket, "getaddrinfo", boom)

    result = safe_post("https://this-host-does-not-resolve.invalid/hook", "{}", HEADERS, timeout=5)
    assert result.error_code == DNS_FAILURE and result.permanent is False


def test_stored_url_that_is_now_unsafe_is_refused_at_send_time():
    """e.g. a webhook saved before these rules existed."""
    result = safe_post("http://169.254.169.254/latest/meta-data/", "{}", HEADERS, timeout=5)
    assert result.permanent is True and result.error_code in (BLOCKED_SCHEME, BLOCKED_ADDRESS)


# --------------------------------------------------------------------------------------
# Routes + delivery service (full app)
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://127.0.0.1/hook",
    "https://localhost:8000/admin",
    "http://example.com/hook",
    "https://169.254.169.254/latest/meta-data/",
    "https://10.0.0.5/hook",
    "https://user:pw@example.com/hook",
    "https://metadata.google.internal/",
])
def test_create_webhook_rejects_unsafe_urls(client, auth_headers, url):
    resp = client.post("/admin/webhooks", json={"url": url, "subscribed_events": ["order.paid"]}, headers=auth_headers)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]


def test_create_webhook_accepts_a_public_https_url(client, auth_headers):
    resp = client.post("/admin/webhooks", json={"url": "https://example.com/hook", "subscribed_events": ["order.paid"]}, headers=auth_headers)
    assert resp.status_code == 200, resp.text


def test_update_webhook_cannot_repoint_to_a_private_address(client, auth_headers):
    created = client.post("/admin/webhooks", json={"url": "https://example.com/hook", "subscribed_events": ["order.paid"]}, headers=auth_headers).json()
    resp = client.patch(f"/admin/webhooks/{created['id']}", json={"url": "https://192.168.1.10/hook"}, headers=auth_headers)
    assert resp.status_code == 400
    still = [w for w in client.get("/admin/webhooks", headers=auth_headers).json() if w["id"] == created["id"]][0]
    assert still["url"] == "https://example.com/hook"


def test_delivery_to_a_stored_private_url_is_blocked_marked_failed_and_explained(client, db_engine, auth_headers, customer_auth_headers):
    """A row that predates the rules (or was edited in the DB) is still refused at send time, is NOT
    retried five times, and shows the admin why."""
    from sqlalchemy.orm import sessionmaker
    from app.models.webhook import WebhookDB

    created = client.post("/admin/webhooks", json={"url": "https://example.com/hook", "subscribed_events": ["order.paid"]}, headers=auth_headers).json()
    session = sessionmaker(bind=db_engine)()
    try:
        row = session.query(WebhookDB).filter(WebhookDB.id == created["id"]).one()
        row.url = "https://169.254.169.254/latest/meta-data/"
        session.commit()
    finally:
        session.close()

    product = client.post("/admin/products/", json={"name": "SSRF Item", "price": 75.0, "is_active": True}, headers=auth_headers).json()
    resp = client.post("/customer/cart/", json={"product_id": product["id"], "quantity": 1}, headers=customer_auth_headers)
    assert resp.status_code == 200, resp.text
    resp = client.post("/customer/checkout", json={"address_line": "1 Test St", "phone": "9999999999", "payment_method": "cod"}, headers=customer_auth_headers)
    assert resp.status_code == 200, resp.text
    resp = client.post("/customer/checkout/verify", json={"order_id": resp.json()["order_id"]}, headers=customer_auth_headers)
    assert resp.status_code == 200, resp.text

    deliveries = client.get(f"/admin/webhooks/{created['id']}/deliveries", headers=auth_headers).json()
    target = [d for d in deliveries if d["event_type"] == "order.paid"][0]
    replayed = client.post(f"/admin/webhooks/deliveries/{target['id']}/replay", headers=auth_headers).json()

    assert replayed["status"] == "failed"               # permanent: no retry schedule
    assert replayed["next_retry_at"] is None
    assert replayed["attempt_count"] == 1
    assert replayed["failure_code"] in (BLOCKED_ADDRESS, BLOCKED_HOST, BLOCKED_SCHEME)
    assert replayed["last_error"]
