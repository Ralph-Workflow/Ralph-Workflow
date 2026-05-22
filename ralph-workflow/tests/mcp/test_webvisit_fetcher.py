from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING

from ralph.mcp.webvisit import fetcher

if TYPE_CHECKING:
    from collections.abc import Iterator

    import pytest


@dataclass(frozen=True)
class _ResponseSpec:
    url: str
    status_code: int
    headers: dict[str, str]
    body: bytes = b""


def _make_response(spec: _ResponseSpec) -> SimpleNamespace:
    def iter_bytes() -> Iterator[bytes]:
        yield spec.body

    return SimpleNamespace(
        url=spec.url,
        status_code=spec.status_code,
        headers=spec.headers,
        iter_bytes=iter_bytes,
    )


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, _ResponseSpec],
) -> tuple[list[str], list[dict[str, object]]]:
    requested_urls: list[str] = []
    created_kwargs: list[dict[str, object]] = []

    class _FakeResponseStream:
        def __init__(self, response: SimpleNamespace) -> None:
            self._response = response

        def __enter__(self) -> SimpleNamespace:
            return self._response

        def __exit__(
            self,
            exc_type: object | None,
            exc: object | None,
            tb: object | None,
        ) -> bool:
            return False

    class _FakeClient:
        def __init__(self, **kwargs: object) -> None:
            self.created_kwargs = kwargs

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(
            self,
            exc_type: object | None,
            exc: object | None,
            tb: object | None,
        ) -> bool:
            return False

        def stream(
            self,
            method: str,
            url: str,
            headers: dict[str, str],
        ) -> _FakeResponseStream:
            assert method == "GET"
            requested_urls.append(url)
            try:
                response = _make_response(responses[url])
            except KeyError as exc:  # pragma: no cover - defensive test helper guard
                raise AssertionError(f"unexpected request for {url!r}") from exc
            return _FakeResponseStream(response)

    def factory(**kwargs: object) -> _FakeClient:
        created_kwargs.append(kwargs)
        return _FakeClient(**kwargs)

    monkeypatch.setattr(fetcher.httpx, "Client", factory)
    return requested_urls, created_kwargs


def test_fetch_url_blocks_private_redirect_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_url = "https://example.com/start"
    private_url = "http://127.0.0.1/secret"
    requested_urls, created_kwargs = _patch_client(
        monkeypatch,
        {
            start_url: _ResponseSpec(
                url=start_url,
                status_code=302,
                headers={"location": private_url},
            )
        },
    )

    outcome = fetcher.fetch_url(
        start_url,
        timeout_ms=1000,
        max_bytes=1024,
        user_agent="RalphWorkflow/1.0",
        allow_private_networks=False,
    )

    assert outcome.status == "blocked_by_policy"
    assert outcome.effective_url == private_url
    assert requested_urls == [start_url]
    assert created_kwargs == [{"follow_redirects": False, "timeout": 1.0}]


def test_fetch_url_follows_public_redirect_then_reads_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_url = "https://example.com/start"
    final_url = "https://example.com/final"
    requested_urls, created_kwargs = _patch_client(
        monkeypatch,
        {
            start_url: _ResponseSpec(
                url=start_url,
                status_code=302,
                headers={"location": final_url},
            ),
            final_url: _ResponseSpec(
                url=final_url,
                status_code=200,
                headers={"content-type": "text/html; charset=utf-8"},
                body=b"<html><body><p>hello</p></body></html>",
            ),
        },
    )

    outcome = fetcher.fetch_url(
        start_url,
        timeout_ms=1000,
        max_bytes=1024,
        user_agent="RalphWorkflow/1.0",
        allow_private_networks=False,
    )

    assert outcome.status == "ok"
    assert outcome.effective_url == final_url
    assert outcome.body == b"<html><body><p>hello</p></body></html>"
    assert requested_urls == [start_url, final_url]
    assert created_kwargs == [{"follow_redirects": False, "timeout": 1.0}]
