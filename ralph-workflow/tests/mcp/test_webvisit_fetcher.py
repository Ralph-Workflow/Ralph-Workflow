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


#: URLs that clear or crash the stdlib policy parser and are rejected by
#: httpx's stricter one. Each used to escape ``fetch_url`` as an
#: exception from a function documented never to raise.
_MALFORMED_URLS = (
    ("http://[::1", "unbalanced bracket: stdlib urlparse raises ValueError first"),
    ("http://999.1.1.1/", "stdlib accepts the hostname; httpx rejects the IPv4 address"),
    ("http://exam]ple.com", "stray bracket"),
    ("http://exa\tmple.com/", "non-printable ASCII"),
    ("http://" + "a" * 70000 + ".com/", "over-long label: IDNA raises UnicodeEncodeError"),
)


def test_malformed_urls_are_an_outcome_not_an_exception() -> None:
    """``fetch_url`` documents that it never raises; these all made it raise.

    ``httpx.InvalidURL`` is declared straight off ``Exception``, so the
    guard's ``(ConnectError, RemoteProtocolError, HTTPError)`` tuple
    could not catch it, and the stdlib ``ValueError`` fires before httpx
    is reached at all. Both escaped to the MCP dispatcher as a RETRYABLE
    ``-32603``, so an agent could re-issue the identical failing call for
    hours, while the dedicated ``invalid_url`` status was unreachable for
    this entire input class.

    No network is touched: every URL here is rejected before a request
    is issued.
    """
    for url, why in _MALFORMED_URLS:
        outcome = fetcher.fetch_url(
            url,
            timeout_ms=1000,
            max_bytes=1024,
            user_agent="ralph-test",
            allow_private_networks=False,
        )
        assert outcome.status == "invalid_url", f"{why}: got {outcome.status}"
        assert outcome.error


def test_a_malformed_redirect_target_is_an_outcome_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``Location`` header is remote input and must not be able to raise.

    Ralph sets ``follow_redirects=False`` and hand-rolls the redirect
    loop, which opts out of the one place httpx converts ``InvalidURL``
    into a catchable ``RemoteProtocolError`` -- so a hostile or broken
    server's ``Location`` reached ``urljoin``/``client.stream`` raw.
    """
    _patch_client(
        monkeypatch,
        {
            "https://example.com/": _ResponseSpec(
                url="https://example.com/",
                status_code=302,
                headers={"location": "http://[::1", "content-type": "text/html"},
            )
        },
    )

    outcome = fetcher.fetch_url(
        "https://example.com/",
        timeout_ms=1000,
        max_bytes=1024,
        user_agent="ralph-test",
        allow_private_networks=False,
    )

    assert outcome.status == "invalid_url"
