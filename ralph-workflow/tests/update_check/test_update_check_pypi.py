"""Best-effort PyPI fetch for the update nagger."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.update_check.pypi import (
    fetch_latest_version,
    latest_version_from_payload,
)

if TYPE_CHECKING:
    from types import TracebackType


class _FakeResponse:
    def __init__(self, payload: object, *, error: Exception | None = None) -> None:
        self._payload = payload
        self._error = error

    def raise_for_status(self) -> object:
        if self._error is not None:
            raise self._error
        return None

    def json(self) -> object:
        return self._payload


class _FakeClient:
    def __init__(self, response: _FakeResponse | None = None, *, get_error: Exception | None = None) -> None:
        self._response = response
        self._get_error = get_error

    def get(self, url: str) -> _FakeResponse:
        _ = url
        if self._get_error is not None:
            raise self._get_error
        assert self._response is not None
        return self._response

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> object:
        return None


def test_extracts_version_from_payload() -> None:
    assert latest_version_from_payload({"info": {"version": "1.2.3"}}) == "1.2.3"


def test_payload_missing_info_returns_none() -> None:
    assert latest_version_from_payload({"other": {}}) is None
    assert latest_version_from_payload("not-a-dict") is None
    assert latest_version_from_payload({"info": {"version": ""}}) is None


def test_fetch_returns_version_on_success() -> None:
    client = _FakeClient(_FakeResponse({"info": {"version": "0.9.1"}}))
    assert fetch_latest_version(client) == "0.9.1"


def test_fetch_returns_none_on_http_error() -> None:
    client = _FakeClient(_FakeResponse(None, error=RuntimeError("500")))
    assert fetch_latest_version(client) is None


def test_fetch_returns_none_on_transport_error() -> None:
    client = _FakeClient(get_error=OSError("connection refused"))
    assert fetch_latest_version(client) is None


def test_fetch_returns_none_on_bad_json() -> None:
    client = _FakeClient(_FakeResponse({"unexpected": True}))
    assert fetch_latest_version(client) is None
