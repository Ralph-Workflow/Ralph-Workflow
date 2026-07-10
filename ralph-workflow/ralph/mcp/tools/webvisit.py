"""MCP tool handler for visit_url: fetch one URL and return readable text.

Exported surface:

- ``handle_visit_url`` — the public MCP tool handler. Requires the
  ``WebVisit`` capability on the session, fetches the URL through
  ``ralph.mcp.webvisit.fetcher.fetch_url`` (bounded ``timeout_ms``,
  ``max_bytes``, and an opt-in private-network toggle), and runs the
  response body through the readability-lxml / selectolax extractor.
  Returns a JSON payload with ``status``, ``title``,
  ``effective_url``, ``content_type``, the readable ``text`` (clamped
  to ``max_bytes // 4`` characters), and the extracted ``links`` when
  ``with_links`` is requested.
- ``handle_download_url`` — the public download handler. Requires the
  ``WebDownload`` capability, fetches the URL with the same bounded
  network contract, and writes the response body to a workspace
  ``output_path`` (UTF-8 with ``errors="replace"``). Returns a JSON
  payload with ``status``, ``effective_url``, ``content_type``,
  ``output_path``, and ``bytes_written``. A write failure is converted
  into a non-retryable ``is_error`` result so a model that sees the
  error does not loop re-issuing the call.
- ``_error_result`` / ``_MAX_TEXT_CHARS_DIVISOR`` — internal helper
  for the ``FetchOutcome`` -> ``ToolResult`` translation, and the
  factor used to clamp the extracted text length.
- ``WEB_VISIT_CAPABILITY`` / ``WEB_DOWNLOAD_CAPABILITY`` — the
  capability strings required by the two public handlers.

Trust boundary: every public handler is gated on a ``McpCapability``
declared by the agent session. The fetch is performed by
``ralph.mcp.webvisit.fetcher.fetch_url`` which carries the bounded
``timeout_ms`` and ``max_bytes`` from ``WebVisitConfig``; private
network ranges are opt-in via ``allow_private_networks``.

Side effects (network contract): ``handle_visit_url`` performs an
HTTP/HTTPS fetch bounded by ``WebVisitConfig.timeout_ms`` and
``max_bytes``. ``handle_download_url`` additionally writes the
downloaded body to a workspace path (any ``OSError`` is captured and
returned as a non-retryable ``is_error`` result rather than re-raised
as a -32603 protocol error). The extractor is best-effort: an
extraction exception is converted to an ``is_error`` JSON payload
with ``status="unsupported_content"`` and the exception text.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from loguru import logger

from ralph.config.mcp_models import WebVisitConfig
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
    ToolResult,
    require_capability,
)
from ralph.mcp.tools.workspace import required_string_param
from ralph.mcp.webvisit.extractor import extract_readable
from ralph.mcp.webvisit.fetcher import FetchOutcome, fetch_url

if TYPE_CHECKING:
    from ralph.workspace import Workspace

WEB_VISIT_CAPABILITY = "WebVisit"
WEB_DOWNLOAD_CAPABILITY = "WebDownload"

_MAX_TEXT_CHARS_DIVISOR = 4

# Phase 4: bounded head_preview for ``visit_url format='metadata'``.
# The text body is dropped from the inline payload in metadata mode;
# the head_preview lets the agent see the page summary without
# triggering a follow-up ``format='raw'`` call for routine inspection.
_VISIT_HEAD_PREVIEW_CHARS = 480

# Phase 4: bounded head_preview for ``download_url format='summary'``.
# The downloaded body is dropped from the inline payload in summary
# mode; only metadata + a bounded head_preview is returned. The
# download summary also exposes a ``sha256`` for replay verification.
_DOWNLOAD_HEAD_PREVIEW_BYTES = 240


def handle_visit_url(
    session: CoordinationSessionLike,
    _workspace: Workspace,
    params: dict[str, object],
    *,
    web_visit_config: WebVisitConfig | None = None,
) -> ToolResult:
    """Fetch a URL and return readable extracted text.

    Args:
        session: Agent session; must declare ``WebVisit``.
        _workspace: Unused; kept for tool-handler signature parity.
        params: Mapping with required ``url`` (string, http/https), optional
            ``with_links`` boolean (overrides ``WebVisitConfig.extract_links``),
            and optional ``format`` (``'raw'|'metadata'``, default ``'raw'``).
        web_visit_config: Optional injected ``WebVisitConfig`` providing
            ``timeout_ms``, ``max_bytes``, ``user_agent``, and
            ``allow_private_networks``. Defaults to ``WebVisitConfig()``.

    Returns:
        A ``ToolResult`` whose text content is a JSON payload with
        ``status``, ``title``, ``effective_url``, ``content_type``,
        ``text`` (clamped to ``max_bytes // 4`` chars), and the
        ``links`` array when ``with_links`` was requested
        (``format='raw'``, default). When ``format='metadata'`` the
        full text body is dropped from the inline payload and a
        bounded ``head_preview`` plus ``byte_count`` counters are
        returned instead. ``with_links=true`` with metadata mode
        attaches up to 10 outbound link cards after the metadata
        envelope.

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``WebVisit``. The handler enforces default-deny.
        InvalidParamsError: When ``params`` is missing ``url`` or
            carries an unknown ``format`` value.

    Side effects (network contract):
        Performs an HTTP/HTTPS fetch bounded by
        ``WebVisitConfig.timeout_ms`` and ``max_bytes``. Private network
        ranges are opt-in via ``allow_private_networks``. Readability
        extraction is best-effort; an extraction exception is converted
        to ``is_error=True`` with ``status="unsupported_content"``.
        No workspace writes.
    """
    try:
        require_capability(session, WEB_VISIT_CAPABILITY, "Visit URL")
        url = required_string_param(params, "url")
    except (CapabilityDeniedError, InvalidParamsError) as exc:
        return ToolResult(content=[ToolContent.text_content(str(exc))], is_error=True)

    format_value = params.get("format", "raw") if isinstance(params, dict) else "raw"
    if format_value not in ("raw", "metadata"):
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Invalid visit_url format: {format_value!r}; "
                    "expected 'raw' or 'metadata'"
                )
            ],
            is_error=True,
        )

    config = web_visit_config or WebVisitConfig()

    with_links_raw = params.get("with_links", config.extract_links)
    with_links = bool(with_links_raw) if isinstance(with_links_raw, bool) else config.extract_links

    outcome = fetch_url(
        url,
        timeout_ms=config.timeout_ms,
        max_bytes=config.max_bytes,
        user_agent=config.user_agent,
        allow_private_networks=config.allow_private_networks,
    )

    if outcome.status != "ok":
        logger.warning("visit_url fetch failed: status={s}", s=outcome.status)
        return _error_result(outcome)

    body_text = (outcome.body or b"").decode("utf-8", errors="replace")
    try:
        page = extract_readable(
            body_text,
            base_url=outcome.effective_url,
            with_links=with_links,
        )
    except Exception as exc:
        logger.warning("visit_url extraction failed: {exc}", exc=exc)
        err_payload: dict[str, object] = {"status": "unsupported_content", "error": str(exc)}
        return ToolResult(
            content=[ToolContent.text_content(json.dumps(err_payload))],
            is_error=True,
        )

    max_text_chars = config.max_bytes // _MAX_TEXT_CHARS_DIVISOR
    text = page.text[:max_text_chars]

    if format_value == "metadata":
        head_preview = text[:_VISIT_HEAD_PREVIEW_CHARS]
        truncated = len(text) > _VISIT_HEAD_PREVIEW_CHARS
        bytes_in = len(outcome.body or b"")
        metadata_payload: dict[str, object] = {
            "format": "metadata",
            "status": "ok",
            "title": page.title,
            "effective_url": outcome.effective_url,
            "content_type": outcome.content_type,
            "byte_count": len(text),
            "head_preview": head_preview,
            "bytes_in": bytes_in,
            "truncated": truncated,
        }
        # ``bytes_out`` is computed after optional ``links`` so the
        # counter reflects the finalized payload size.
        if with_links:
            metadata_payload["links"] = list(page.links)[:10]
        metadata_payload["bytes_out"] = len(
            json.dumps(metadata_payload).encode("utf-8")
        )
        return ToolResult(
            content=[ToolContent.text_content(json.dumps(metadata_payload))],
            is_error=False,
        )

    payload: dict[str, object] = {
        "status": "ok",
        "title": page.title,
        "effective_url": outcome.effective_url,
        "content_type": outcome.content_type,
        "text": text,
    }
    if with_links:
        payload["links"] = list(page.links)

    return ToolResult(
        content=[ToolContent.text_content(json.dumps(payload))],
        is_error=False,
    )


def _error_result(outcome: FetchOutcome) -> ToolResult:
    payload: dict[str, object] = {
        "status": outcome.status,
        "error": outcome.error,
        "effective_url": outcome.effective_url,
        "http_status": outcome.http_status,
    }
    return ToolResult(
        content=[ToolContent.text_content(json.dumps(payload))],
        is_error=True,
    )


def handle_download_url(
    session: CoordinationSessionLike,
    workspace: Workspace,
    params: dict[str, object],
    *,
    web_visit_config: WebVisitConfig | None = None,
) -> ToolResult:
    """Download a URL and save its content to a workspace file.

    Args:
        session: Agent session; must declare ``WebDownload``.
        workspace: Workspace surface whose root resolves ``output_path``.
        params: Mapping with required ``url`` (string), ``output_path``
            (relative path inside the workspace), and optional
            ``format`` (``'raw'|'summary'``, default ``'raw'``).
        web_visit_config: Optional injected ``WebVisitConfig`` providing
            ``timeout_ms``, ``max_bytes``, ``user_agent``, and
            ``allow_private_networks``. Defaults to ``WebVisitConfig()``.

    Returns:
        A ``ToolResult`` whose text content is a JSON payload with
        ``status``, ``effective_url``, ``content_type``,
        ``output_path``, and ``bytes_written`` when ``format='raw'``
        (default). When ``format='summary'`` the response adds a
        ``sha256`` fingerprint (first 16 hex chars) and a bounded
        ``head_preview`` (first 240 bytes rendered as base64-safe
        ASCII) but does NOT echo the downloaded body inline. Callers
        needing the full body should use ``format='raw'`` (or
        ``read_file`` on the persisted ``output_path``).

    Raises:
        CapabilityDeniedError: When the session does not declare
            ``WebDownload``. The handler enforces default-deny.
        InvalidParamsError: When ``params`` is missing ``url``,
            ``output_path``, or carries an unknown ``format`` value.

    Side effects (network + filesystem contract):
        Performs an HTTP/HTTPS fetch bounded by
        ``WebVisitConfig.timeout_ms`` and ``max_bytes``. Writes the
        response body to ``output_path`` as UTF-8 with ``errors="replace"``.
        A write failure (``OSError``) is captured and returned as a
        non-retryable ``is_error`` result rather than re-raised as a
        -32603 protocol error.
    """
    try:
        require_capability(session, WEB_DOWNLOAD_CAPABILITY, "Download URL")
        url = required_string_param(params, "url")
        output_path = required_string_param(params, "output_path")
    except (CapabilityDeniedError, InvalidParamsError) as exc:
        return ToolResult(content=[ToolContent.text_content(str(exc))], is_error=True)

    format_value = params.get("format", "raw") if isinstance(params, dict) else "raw"
    if format_value not in ("raw", "summary"):
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Invalid download_url format: {format_value!r}; "
                    "expected 'raw' or 'summary'"
                )
            ],
            is_error=True,
        )

    config = web_visit_config or WebVisitConfig()
    outcome = fetch_url(
        url,
        timeout_ms=config.timeout_ms,
        max_bytes=config.max_bytes,
        user_agent=config.user_agent,
        allow_private_networks=config.allow_private_networks,
    )

    if outcome.status != "ok":
        logger.warning("download_url fetch failed: status={s}", s=outcome.status)
        return _error_result(outcome)

    body_bytes = outcome.body or b""
    content_str = body_bytes.decode("utf-8", errors="replace")

    try:
        workspace.write(output_path, content_str)
    except OSError as exc:
        # A write failure (disk full, permission, read-only fs) is an operational
        # error: surface it as a terminal is_error result, not a raw OSError that
        # the bridge would turn into a retryable -32603 protocol error.
        logger.warning("download_url write failed: {e}", e=exc)
        return ToolResult(
            content=[
                ToolContent.text_content(
                    f"Failed to write downloaded content to '{output_path}': {exc}. "
                    "Re-issuing the identical call will fail again — free space, fix "
                    "permissions, or choose a different output_path."
                )
            ],
            is_error=True,
        )

    payload: dict[str, object] = {
        "status": "ok",
        "effective_url": outcome.effective_url,
        "content_type": outcome.content_type,
        "output_path": output_path,
        "bytes_written": len(body_bytes),
    }
    if format_value == "raw":
        return ToolResult(
            content=[ToolContent.text_content(json.dumps(payload))],
            is_error=False,
        )
    # ``format='summary'``: emit metadata + bounded head preview +
    # sha256 fingerprint; the downloaded body itself is dropped from
    # the inline payload. The caller can re-read the body from
    # ``output_path`` or via ``read_file``.
    head_preview_bytes = body_bytes[:_DOWNLOAD_HEAD_PREVIEW_BYTES]
    sha256_full = hashlib.sha256(body_bytes).hexdigest()
    summary_payload: dict[str, object] = {
        **payload,
        "format": "summary",
        "sha256": sha256_full[:16],
        "head_preview": (
            head_preview_bytes.decode("utf-8", errors="replace")
            + (
                f"... [+{len(body_bytes) - len(head_preview_bytes)} bytes]"
                if len(body_bytes) > _DOWNLOAD_HEAD_PREVIEW_BYTES
                else ""
            )
        ),
        "bytes_in": len(body_bytes),
        "truncated": len(body_bytes) > _DOWNLOAD_HEAD_PREVIEW_BYTES,
    }
    summary_payload["bytes_out"] = len(json.dumps(summary_payload).encode("utf-8"))
    return ToolResult(
        content=[ToolContent.text_content(json.dumps(summary_payload))],
        is_error=False,
    )


__all__ = [
    "WEB_DOWNLOAD_CAPABILITY",
    "WEB_VISIT_CAPABILITY",
    "handle_download_url",
    "handle_visit_url",
]
