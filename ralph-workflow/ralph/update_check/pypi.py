"""Best-effort PyPI lookup for the latest ``ralph-workflow`` release.

Every failure path is swallowed and returns ``None``: a version check must never
delay or break a user's run. The HTTP client is injectable so unit tests never
touch the network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import httpx

if TYPE_CHECKING:
    from types import TracebackType

PYPI_JSON_URL = "https://pypi.org/pypi/ralph-workflow/json"
DEFAULT_TIMEOUT_SECONDS = 2.0


class _HttpResponse(Protocol):
    def raise_for_status(self) -> object: ...
    def json(self) -> object: ...


class _HttpClient(Protocol):
    def get(self, url: str) -> _HttpResponse: ...
    def __enter__(self) -> _HttpClient: ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> object: ...


def latest_version_from_payload(payload: object) -> str | None:
    """Extract ``info.version`` from a decoded PyPI JSON payload."""
    if not isinstance(payload, dict):
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    version = info.get("version")
    if isinstance(version, str) and version.strip():
        return version.strip()
    return None


def fetch_latest_version(client: _HttpClient, *, url: str = PYPI_JSON_URL) -> str | None:
    """Fetch the latest published version via ``client``, or ``None`` on any error."""
    try:
        response = client.get(url)
        response.raise_for_status()
        return latest_version_from_payload(response.json())
    except Exception:
        return None


def fetch_latest_version_over_network(
    *, url: str = PYPI_JSON_URL, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> str | None:
    """Production fetch: build a short-lived, time-boxed httpx client and query PyPI."""
    try:
        with httpx.Client(timeout=timeout) as client:
            return fetch_latest_version(client, url=url)
    except Exception:
        return None
