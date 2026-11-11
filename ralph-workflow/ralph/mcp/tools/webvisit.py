
from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from loguru import logger

from ralph.config.mcp_models import WebVisitConfig
from ralph.mcp.tools._envelope_bytes import finalize_envelope_bytes_out
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

_VISIT_HEAD_PREVIEW_CHARS = 480

_DOWNLOAD_HEAD_PREVIEW_BYTES = 240


def handle_visit_url(
    session: CoordinationSessionLike,
    _workspace: Workspace,
    params: dict[str, object],
    *,
    web_visit_config: WebVisitConfig | None = None,
) -> ToolResult:
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
                    f"Invalid visit_url format: {format_value!r}; expected 'raw' or 'metadata'"
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
        if with_links:
            metadata_payload["links"] = list(page.links)[:10]
        metadata_payload = finalize_envelope_bytes_out(metadata_payload)
        return ToolResult(
            content=[ToolContent.text_content(json.dumps(metadata_payload, separators=(",", ":")))],
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
                    f"Invalid download_url format: {format_value!r}; expected 'raw' or 'summary'"
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
    head_preview_bytes = body_bytes[:_DOWNLOAD_HEAD_PREVIEW_BYTES]
    sha256_full = hashlib.sha256(body_bytes).hexdigest()
    summary_payload = finalize_envelope_bytes_out(
        {
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
    )
    return ToolResult(
        content=[ToolContent.text_content(json.dumps(summary_payload, separators=(",", ":")))],
        is_error=False,
    )


__all__ = [
    "WEB_DOWNLOAD_CAPABILITY",
    "WEB_VISIT_CAPABILITY",
    "handle_download_url",
    "handle_visit_url",
]
