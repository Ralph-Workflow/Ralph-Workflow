"""HTTP fetch layer for the visit_url tool.

Performs a single HTTP GET with SSRF-guard, size cap, and timeout enforcement.
No network IO should escape this module in production code paths.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urljoin, urlparse

import httpx

FetchStatus = Literal[
    "ok",
    "timeout",
    "unreachable",
    "http_error",
    "unsupported_content",
    "too_large",
    "blocked_by_policy",
    "invalid_url",
]

_SUPPORTED_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "text/plain",
        "application/xhtml+xml",
        "application/xml",
        "text/xml",
    }
)

_PRIVATE_HOSTNAMES = frozenset({"localhost"})

_HTTP_SUCCESS_MIN = 200
_HTTP_SUCCESS_MAX = 300
_HTTP_REDIRECT_MIN = 300
_HTTP_REDIRECT_MAX = 400
_MAX_REDIRECTS = 10


@dataclass(frozen=True)
class FetchOutcome:
    """Result of a single HTTP fetch attempt."""

    status: FetchStatus
    effective_url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    body: bytes | None = None
    error: str | None = None


_DNS_SSRF_CHECK_TIMEOUT = 5.0  # seconds; prevents unbounded blocking on slow DNS


def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _is_private_address(host: str) -> bool:
    if host.lower() in _PRIVATE_HOSTNAMES:
        return True
    # Parse as a literal IP address first  no DNS needed and no blocking
    try:
        return _is_private_ip(ipaddress.ip_address(host))
    except ValueError:
        pass
    # Hostname: resolve with a bounded timeout so slow DNS cannot block the server
    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(_DNS_SSRF_CHECK_TIMEOUT)
        results = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    finally:
        socket.setdefaulttimeout(old_timeout)
    for _family, _type, _proto, _canonname, sockaddr in results:
        addr = sockaddr[0]
        try:
            if _is_private_ip(ipaddress.ip_address(addr)):
                return True
        except ValueError:
            continue
    return False


def _content_type_base(content_type_header: str | None) -> str | None:
    if not content_type_header:
        return None
    return content_type_header.split(";")[0].strip().lower()


def _check_url_policy(
    url: str,
    *,
    allow_private_networks: bool,
    error_url: str | None = None,
) -> FetchOutcome | None:
    """Validate URL scheme, hostname, and SSRF policy. Returns an error FetchOutcome or None."""
    parsed = urlparse(url)
    target_url = error_url or url
    if parsed.scheme not in {"http", "https"}:
        return FetchOutcome(
            status="invalid_url",
            effective_url=target_url,
            error=f"unsupported scheme: {parsed.scheme!r}",
        )
    if not parsed.hostname:
        return FetchOutcome(
            status="invalid_url",
            effective_url=target_url,
            error="missing hostname",
        )
    if not allow_private_networks and _is_private_address(parsed.hostname):
        return FetchOutcome(
            status="blocked_by_policy",
            effective_url=target_url,
            error=(
                "access to private/loopback networks is disabled by default; "
                "set allow_private_networks=true in [web_visit] config to enable"
            ),
        )
    return None


def _read_streaming_body(
    response: httpx.Response,
    *,
    max_bytes: int,
    effective_url: str,
    http_status: int,
    content_type_header: str | None,
) -> FetchOutcome:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            return FetchOutcome(
                status="too_large",
                effective_url=effective_url,
                http_status=http_status,
                content_type=content_type_header,
                error=f"response body exceeds {max_bytes} bytes",
            )
        chunks.append(chunk)
    return FetchOutcome(
        status="ok",
        effective_url=effective_url,
        http_status=http_status,
        content_type=content_type_header,
        body=b"".join(chunks),
    )


def fetch_url(
    url: str,
    *,
    timeout_ms: int,
    max_bytes: int,
    user_agent: str,
    allow_private_networks: bool,
) -> FetchOutcome:
    """Fetch a single URL and return a FetchOutcome.

    Never raises on network failures  always returns a FetchOutcome.
    """
    timeout = timeout_ms / 1000.0
    headers = {"User-Agent": user_agent}
    current_url = url
    result: FetchOutcome | None = None

    try:
        with httpx.Client(follow_redirects=False, timeout=timeout) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                policy_error = _check_url_policy(
                    current_url,
                    allow_private_networks=allow_private_networks,
                )
                if policy_error is not None:
                    result = policy_error
                    break

                with client.stream("GET", current_url, headers=headers) as response:
                    effective_url: str = str(response.url)
                    http_status: int = response.status_code
                    content_type_header: str | None = response.headers.get("content-type")
                    content_type_base = _content_type_base(content_type_header)

                    if _HTTP_REDIRECT_MIN <= http_status < _HTTP_REDIRECT_MAX:
                        location: str | None = response.headers.get("location")
                        if not location:
                            result = FetchOutcome(
                                status="http_error",
                                effective_url=effective_url,
                                http_status=http_status,
                                content_type=content_type_header,
                                error=f"HTTP {http_status}",
                            )
                            break
                        next_url = urljoin(effective_url, location)
                        redirect_policy_error = _check_url_policy(
                            next_url,
                            allow_private_networks=allow_private_networks,
                            error_url=next_url,
                        )
                        if redirect_policy_error is not None:
                            result = redirect_policy_error
                            break
                        current_url = next_url
                        continue

                    if http_status < _HTTP_SUCCESS_MIN or http_status >= _HTTP_SUCCESS_MAX:
                        result = FetchOutcome(
                            status="http_error",
                            effective_url=effective_url,
                            http_status=http_status,
                            content_type=content_type_header,
                            error=f"HTTP {http_status}",
                        )
                        break

                    if content_type_base not in _SUPPORTED_CONTENT_TYPES:
                        result = FetchOutcome(
                            status="unsupported_content",
                            effective_url=effective_url,
                            http_status=http_status,
                            content_type=content_type_header,
                            error=f"unsupported content type: {content_type_header!r}",
                        )
                        break

                    result = _read_streaming_body(
                        response,
                        max_bytes=max_bytes,
                        effective_url=effective_url,
                        http_status=http_status,
                        content_type_header=content_type_header,
                    )
                    break
            else:
                result = FetchOutcome(status="unreachable", error="too many redirects")

    except httpx.TimeoutException as exc:
        return FetchOutcome(status="timeout", error=str(exc))
    except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.HTTPError) as exc:
        return FetchOutcome(status="unreachable", error=str(exc))

    return result or FetchOutcome(status="unreachable", error="too many redirects")


__all__ = ["FetchOutcome", "FetchStatus", "fetch_url"]
