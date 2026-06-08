"""MCP tool handler for visit_url: fetch one URL and return readable text."""

from __future__ import annotations

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


def handle_visit_url(
    session: CoordinationSessionLike,
    _workspace: Workspace,
    params: dict[str, object],
    *,
    web_visit_config: WebVisitConfig | None = None,
) -> ToolResult:
    """Fetch a URL and return readable extracted text."""
    try:
        require_capability(session, WEB_VISIT_CAPABILITY, "Visit URL")
        url = required_string_param(params, "url")
    except (CapabilityDeniedError, InvalidParamsError) as exc:
        return ToolResult(content=[ToolContent.text_content(str(exc))], is_error=True)

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
    """Download a URL and save its content to a workspace file."""
    try:
        require_capability(session, WEB_DOWNLOAD_CAPABILITY, "Download URL")
        url = required_string_param(params, "url")
        output_path = required_string_param(params, "output_path")
    except (CapabilityDeniedError, InvalidParamsError) as exc:
        return ToolResult(content=[ToolContent.text_content(str(exc))], is_error=True)

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
    return ToolResult(
        content=[ToolContent.text_content(json.dumps(payload))],
        is_error=False,
    )


__all__ = [
    "WEB_DOWNLOAD_CAPABILITY",
    "WEB_VISIT_CAPABILITY",
    "handle_download_url",
    "handle_visit_url",
]
