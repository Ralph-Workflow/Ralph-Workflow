"""Bounded HTTP transport for the universal upstream skill updater.

This module is the ONLY place where the updater touches the network, and it
is BOUNDED by construction:

* a redirect is REFUSED, never followed -- the upstream origin is the only
  origin the updater talks to, so a ``Location`` header is never honoured;
* a response that declares a ``Content-Length`` above the cap of its URL is
  refused BEFORE the body is read;
* a body that grows past its cap WHILE IT STREAMS aborts the transfer;
* the timeout covers the connect AND the read phase.

The byte cap of a URL is resolved by the CALLER through the
``cap_for_url`` callback, so this module owns no origin and no cap of its
own: the caller keeps the single source of truth for both, and a caller that
lowers a cap lowers it for the transfer as well.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

#: The lowest HTTP status that is a redirect.
HTTP_REDIRECT_MIN = 300

#: The highest HTTP status that is a redirect.
HTTP_REDIRECT_MAX = 399


class _TransportError(Exception):
    """Base class of the bounded transport failures."""


class _ResponseTooLargeError(_TransportError):
    """The upstream response body exceeded the cap that applies to its URL."""


class _RedirectRefusedError(_TransportError):
    """The upstream answered with a redirect; the transport never follows one."""


def streaming_client(
    cap_for_url: Callable[[str], int],
) -> Callable[[str, float], tuple[int, bytes]]:
    """Return the bounded streaming client for the upstream seam.

    Args:
        cap_for_url: Resolves the byte cap that applies to a URL. It is
            called for every request, so the caller can change a cap
            between two requests.

    Returns:
        The HTTP seam: a callable that fetches a URL within a timeout and
        answers with a ``(status_code, body)`` pair. A transport failure
        propagates to the caller, which reports a failed outcome.
    """

    def _get(url: str, timeout: float) -> tuple[int, bytes]:
        cap = cap_for_url(url)
        with (
            httpx.Client(
                follow_redirects=False,
                timeout=httpx.Timeout(timeout, connect=timeout, read=timeout),
            ) as client,
            client.stream("GET", url) as response,
        ):
            if HTTP_REDIRECT_MIN <= response.status_code <= HTTP_REDIRECT_MAX:
                raise _RedirectRefusedError(url)
            declared = _declared_length(response)
            if declared is not None and declared > cap:
                raise _ResponseTooLargeError(url)
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > cap:
                    raise _ResponseTooLargeError(url)
            return (response.status_code, bytes(body))

    return _get


def _declared_length(response: httpx.Response) -> int | None:
    """Return the declared content length of ``response``, or None."""
    raw: str | None = response.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


__all__ = [
    "HTTP_REDIRECT_MAX",
    "HTTP_REDIRECT_MIN",
]
