from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable

HTTP_REDIRECT_MIN = 300

HTTP_REDIRECT_MAX = 399


class _TransportError(Exception):
    pass


class _ResponseTooLargeError(_TransportError):
    pass


class _RedirectRefusedError(_TransportError):
    pass


def streaming_client(
    cap_for_url: Callable[[str], int],
) -> Callable[[str, float], tuple[int, bytes]]:
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
