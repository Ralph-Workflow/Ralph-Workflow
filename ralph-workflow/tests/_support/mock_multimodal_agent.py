#!/usr/bin/env python3
"""Deterministic multimodal smoke stub agent (S-9 / S-12 / criterion 5).

This stub is a genuine subprocess agent: it reads ``RALPH_MCP_ENDPOINT``
from its environment, POSTs real ``tools/call`` requests against Ralph's
MCP server for the multimodal smoke scenario, and emits the per-transport
frame vocabulary the production harness's parser expects.

Per-transport frame vocabulary (S-13):

- ``claude`` / ``claude-headless`` -- Claude ``assistant`` /
  ``content_block_start`` / ``tool_use`` / ``tool_result`` / ``result``
  frames. The Claude parser's ``_parse_role_message`` /
  ``_dispatch_json_object`` paths accept these directly.
- ``agy`` -- AGY ``init`` / ``step_update`` / ``result`` stream-json
  frames (measured against AGY v1.1.10; see
  ``tests/display/_fixtures/agy_wire_provenance.md``). The AgyParser's
  ``_dispatch_init_event`` / ``_dispatch_step_update`` /
  ``_dispatch_result_event`` paths accept these.
- ``cursor`` -- Cursor ``system`` / ``assistant`` / ``tool_call`` /
  ``tool_result`` / ``result`` stream-json frames. The CursorParser's
  ``_CursorDispatch.dispatch`` handler map accepts these.
- ``opencode`` -- OpenCode ``step_start`` / ``text`` / ``tool_use`` /
  ``tool_result`` / ``done`` NDJSON frames. The OpenCodeParser's
  ``_OpenCodeDispatch.dispatch`` handler map accepts these.
- ``nanocoder`` -- plain-text frames with the ``[plain] tool: NAME``
  convention the GenericParser / NanocoderParser share, plus a
  ``⚒ Executed <tool>`` line per dispatched tool call so NanocoderParser
  classifies it as ``tool_use`` regardless of which fallback the
  harness reaches.

The transport selection is controlled by the ``MOCK_MULTIMODAL_TRANSPORT``
env var; default ``claude`` (the simplest vocabulary, accepted by the
ClaudeParser without per-event routing).

Three modes, selected by env vars:

- ``MOCK_MULTIMODAL_BEHAVIOR=ok`` (default) -- issues the FULL
  positive-contract call sequence (read_media fixture path,
  re-read the server-minted handle, read_image metadata, write
  the receipts into the smoke output file, submit the
  ``smoke_test_result`` artifact, call ``declare_complete``).
  Used by the positive per-harness case.

- ``MOCK_MULTIMODAL_SKIP_MEDIA=1`` -- does everything except the
  media-tool calls. Used by the negative "no call" case to prove the
  break fires on a missing media call.

- ``MOCK_MULTIMODAL_IGNORE_RESPONSE=1`` -- issues the first
  ``read_media`` call (so a genuine ``read_media`` wire-ledger record
  exists for the run), then DISCARDS the response, fabricates a
  UUID-based receipt, and writes a guessed geometry / sha256 into
  the output file. Used by the negative "ignored response" case to
  prove the assertion fails when the agent dials the endpoint but
  discards the answer -- the graded fact must read the receipt from
  the server registry, not trust the model-authored report.

In every mode the stub emits enough transport frames for the parser
to see a normal tool-use sequence, and is graded against the SAME
harnessing path the production ``--multimodal`` smoke runs use.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

ENDPOINT_ENV = "RALPH_MCP_ENDPOINT"
SECRET_ENV = "RALPH_BROKER_SECRET"
RUN_ID_ENV = "RALPH_RUN_ID"
OUTPUT_FILE_ENV = "MOCK_MULTIMODAL_OUTPUT_FILE"
SKIP_MEDIA_ENV = "MOCK_MULTIMODAL_SKIP_MEDIA"
IGNORE_RESPONSE_ENV = "MOCK_MULTIMODAL_IGNORE_RESPONSE"
TRANSPORT_ENV = "MOCK_MULTIMODAL_TRANSPORT"


def _canonical_run_id_for_transport(transport: str, run_id: str) -> str:
    """Return a deterministic per-transport session-id-style token.

    The harness's session-id extractor (see
    :mod:`ralph.agents.invoke._session`) recognises
    ``conversation_id`` on AGY ``init`` frames, ``session_id`` on Cursor
    ``system: init`` frames, ``sessionID`` (capital) on every OpenCode
    event, and ``session_id`` / ``sessionId`` on JSON ``session`` /
    ``session_ready`` / ``session_start`` / ``session_resume`` events
    (Claude). Plain-text Claude sessions emit ``Claude session ready.
    Session ID: <id>``.

    For each transport we emit the canonical shape so the harness's
    :func:`extract_transport_session_id` returns the same value the
    harness's run_id was bound to (``<transport-specific-prefix>``),
    keeping the wire-ledger ``run_id`` and the extracted session id in
    lockstep so the smoke report's ``session_id`` row is non-empty.
    """
    return run_id


def _emit_json(payload: dict[str, Any]) -> None:
    """Emit one NDJSON frame on stdout and flush."""
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _emit_text_line(text: str) -> None:
    """Emit a plain text line on stdout and flush."""
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Per-transport frame emitters.
# ---------------------------------------------------------------------------


def _emit_claude_init(session_id: str, run_id: str) -> None:
    """Emit a Claude ``session_start`` + ``message_start`` to seed the parser."""
    _emit_json({"type": "system", "subtype": "session_start", "session_id": session_id})
    _emit_json(
        {
            "type": "message_start",
            "message": {"id": f"msg_{run_id}", "role": "assistant", "content": []},
        }
    )


def _emit_claude_assistant_text(text: str) -> None:
    _emit_json(
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        }
    )
    _emit_json(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": text},
        }
    )
    _emit_json({"type": "content_block_stop", "index": 0})


def _emit_claude_tool_use(name: str, arguments: dict[str, Any], call_id: str) -> None:
    _emit_json(
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": call_id, "name": name, "input": arguments}
                ],
            },
        }
    )


def _emit_claude_tool_result(call_id: str, content: str, *, is_error: bool = False) -> None:
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": call_id}
    if is_error:
        block["is_error"] = True
    block["content"] = content
    _emit_json({"type": "user", "message": {"role": "user", "content": [block]}})


def _emit_claude_stop() -> None:
    _emit_json({"type": "message_stop"})


def _emit_claude_session_id_line(session_id: str) -> None:
    """Plain-text session-id line the harness's text extractor recognises."""
    _emit_text_line(f"Claude session ready. Session ID: {session_id}")


def _emit_agy_init(model: str, session_id: str) -> None:
    _emit_json(
        {
            "event": "init",
            "conversation_id": session_id,
            "init": {
                "model": model,
                "cwd": ".",
                "tools": [
                    "read_media",
                    "read_image",
                    "write_file",
                    "ralph_submit_md_artifact",
                    "declare_complete",
                ],
                "permission_mode": "always-proceed",
            },
        }
    )


def _emit_agy_text_delta(text: str, session_id: str, step_index: int) -> None:
    _emit_json(
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": step_index,
                "state": "DONE",
                "step_type": "agent_response",
                "text_delta": text,
            },
        }
    )


def _emit_agy_tool_call(name: str, parameters: dict[str, Any], session_id: str, step_index: int) -> None:
    info: dict[str, Any] = {"name": name, "parameters": parameters}
    _emit_json(
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": step_index,
                "state": "ACTIVE",
                "step_type": "tool",
                "tool_name": name,
                "tool_info": info,
            },
        }
    )
    _emit_json(
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": step_index,
                "state": "DONE",
                "step_type": "tool",
                "tool_name": name,
                "duration_seconds": 0.01,
                "tool_info": info,
            },
        }
    )


def _emit_agy_result(session_id: str, status: str = "SUCCESS") -> None:
    _emit_json(
        {
            "event": "result",
            "result": {
                "conversation_id": session_id,
                "status": status,
                "duration_seconds": 0.5,
                "num_turns": 1,
                "usage": {"input_tokens": 64, "output_tokens": 32, "total_tokens": 96},
            },
        }
    )


def _emit_cursor_init(session_id: str, model: str = "auto") -> None:
    _emit_json(
        {
            "type": "system",
            "subtype": "init",
            "session_id": session_id,
            "model": model,
            "message": f"cursor session {session_id} initialized with model {model}",
        }
    )


def _emit_cursor_assistant_text(text: str) -> None:
    _emit_json(
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        }
    )


def _emit_cursor_tool_call(name: str, args: dict[str, Any]) -> None:
    _emit_json(
        {
            "type": "tool_call",
            "subtype": "started",
            "toolName": name,
            "args": args,
        }
    )


def _emit_cursor_tool_result(name: str, output: str) -> None:
    _emit_json(
        {
            "type": "tool_result",
            "toolName": name,
            "result": output,
        }
    )


def _emit_cursor_stop() -> None:
    _emit_json({"type": "result"})


def _emit_opencode_init(session_id: str) -> None:
    _emit_json(
        {
            "type": "step_start",
            "sessionID": session_id,
            "id": "step-init",
            "part": {"id": "part-init"},
        }
    )


def _emit_opencode_text(text: str) -> None:
    _emit_json({"type": "text", "part": {"text": text}})


def _emit_opencode_tool_call(name: str, args: dict[str, Any], call_id: str) -> None:
    _emit_json(
        {
            "type": "tool_use",
            "sessionID": "oc-session",
            "part": {
                "id": call_id,
                "tool": f"ralph_{name}" if not name.startswith("ralph_") else name,
                "state": {"status": "running", "input": args},
                "callID": call_id,
            },
        }
    )


def _emit_opencode_tool_result(name: str, output: str, call_id: str) -> None:
    _emit_json(
        {
            "type": "tool_result",
            "sessionID": "oc-session",
            "result": output,
            "part": {
                "id": call_id,
                "tool": f"ralph_{name}" if not name.startswith("ralph_") else name,
                "callID": call_id,
            },
        }
    )


def _emit_opencode_done() -> None:
    _emit_json({"type": "done"})


def _emit_nanocoder_text(text: str) -> None:
    _emit_text_line(text)


def _emit_nanocoder_tool(name: str) -> None:
    """Emit a nanocoder-parser-recognised tool line (``⚒ Executed <name>``)."""
    _emit_text_line(f"⚒ Executed {name}")


def _emit_nanocoder_tool_call(name: str) -> None:
    _emit_text_line(f"[plain] tool: {name}")


def _emit_nanocoder_session_id_line(session_id: str) -> None:
    _emit_text_line(f"Claude session ready. Session ID: {session_id}")


# ---------------------------------------------------------------------------
# Transport dispatch tables.
# ---------------------------------------------------------------------------


def _make_emit_functions(transport: str) -> dict[str, Any]:
    """Return a mapping of action names to per-transport emitters."""
    if transport in ("claude", "claude-headless"):
        return {
            "session_id_line": _emit_claude_session_id_line,
            "init": _emit_claude_init,
            "text": _emit_claude_assistant_text,
            "tool_call": _emit_claude_tool_use,
            "tool_result": _emit_claude_tool_result,
            "stop": _emit_claude_stop,
        }
    if transport == "agy":
        return {
            "session_id_line": None,
            "init": _emit_agy_init,
            "text": _emit_agy_text_delta,
            "tool_call": _emit_agy_tool_call,
            "tool_result": None,
            "stop": _emit_agy_result,
        }
    if transport == "cursor":
        return {
            "session_id_line": None,
            "init": _emit_cursor_init,
            "text": _emit_cursor_assistant_text,
            "tool_call": _emit_cursor_tool_call,
            "tool_result": _emit_cursor_tool_result,
            "stop": _emit_cursor_stop,
        }
    if transport == "opencode":
        return {
            "session_id_line": None,
            "init": _emit_opencode_init,
            "text": _emit_opencode_text,
            "tool_call": _emit_opencode_tool_call,
            "tool_result": _emit_opencode_tool_result,
            "stop": _emit_opencode_done,
        }
    if transport == "nanocoder":
        return {
            "session_id_line": _emit_nanocoder_session_id_line,
            "init": None,
            "text": _emit_nanocoder_text,
            "tool_call": _emit_nanocoder_tool_call,
            "tool_result": _emit_nanocoder_tool,
            "stop": _emit_nanocoder_text,
        }
    raise ValueError(f"unknown transport {transport!r}")


# ---------------------------------------------------------------------------
# Wire-call helpers.
# ---------------------------------------------------------------------------


def _dispatch(endpoint: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """POST a real ``tools/call`` JSON-RPC frame to ``endpoint``."""
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read()
    body_text = raw.decode("utf-8", errors="replace") if raw else ""
    if not body_text.strip():
        return {"result": None, "_empty_body": True}
    # Handle SSE-style responses: lines starting with ``data:`` are JSON.
    sse_lines = [
        line[len("data:") :].strip()
        for line in body_text.splitlines()
        if line.startswith("data:")
    ]
    if sse_lines:
        last_data = sse_lines[-1]
        if last_data == "[DONE]":
            return {"result": None, "_sse_done": True}
        try:
            return json.loads(last_data)
        except json.JSONDecodeError as exc:
            sys.stderr.write(
                f"mock_multimodal_agent: {name} SSE response not JSON: {exc!r} body={last_data[:200]!r}\n"
            )
            return {"result": None, "_sse_parse_error": str(exc)}
    try:
        return json.loads(body_text)
    except json.JSONDecodeError as exc:
        sys.stderr.write(
            f"mock_multimodal_agent: {name} response not JSON: {exc!r} body={body_text[:200]!r}\n"
        )
        return {"result": None, "_parse_error": str(exc)}


def _read_env_or_fail() -> tuple[str, str, str]:
    """Read the harness-exported env vars; return ``(endpoint, output_file, run_id)``.

    Looks for the endpoint in this priority order:

    1. ``RALPH_MCP_ENDPOINT`` (the canonical MCP endpoint env var).
    2. ``OPENCODE_CONFIG_CONTENT`` (the JSON config the opencode resolver
       writes into the agent env; the URL is parsed out of the
       ``mcp`` key when the direct env var is absent).
    3. ``MCP_URL`` (a fallback for transports that do not export either
       of the above).
    """
    endpoint = os.environ.get(ENDPOINT_ENV, "")
    if not endpoint:
        opencode_cfg = os.environ.get("OPENCODE_CONFIG_CONTENT")
        if opencode_cfg:
            try:
                cfg = json.loads(opencode_cfg)
            except json.JSONDecodeError:
                cfg = None
            if isinstance(cfg, dict):
                mcp = cfg.get("mcp")
                if isinstance(mcp, dict):
                    for defn in mcp.values():
                        if isinstance(defn, dict):
                            url = defn.get("url") or defn.get("endpoint")
                            if isinstance(url, str) and url:
                                endpoint = url
                                break
    if not endpoint:
        endpoint = os.environ.get("MCP_URL", "")
    output_file = os.environ.get(OUTPUT_FILE_ENV, "")
    run_id = os.environ.get(RUN_ID_ENV, "multimodal-smoke")
    return endpoint, output_file, run_id


def _read_behavior_flags() -> tuple[bool, bool]:
    skip_media = os.environ.get(SKIP_MEDIA_ENV) == "1"
    ignore_response = os.environ.get(IGNORE_RESPONSE_ENV) == "1"
    return skip_media, ignore_response


def _resolve_transport() -> str:
    transport = os.environ.get(TRANSPORT_ENV, "claude")
    if transport not in ("claude", "claude-headless", "agy", "cursor", "opencode", "nanocoder"):
        transport = "claude"
    return transport


def _extract_server_uri(first_response: dict[str, Any]) -> str | None:
    """Return the first ``ralph://media/{artifact_id}`` URI the server minted."""
    result = first_response.get("result") if isinstance(first_response, dict) else None
    if not isinstance(result, dict):
        return None
    content = result.get("content", [])
    if not isinstance(content, list):
        return None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "resource_reference":
            uri = block.get("uri")
            if isinstance(uri, str):
                return uri
    return None


def _extract_metadata_envelope(metadata_response: dict[str, Any]) -> tuple[int | None, str | None]:
    """Return ``(width, height)`` from a ``read_image(format=metadata)`` response."""
    result = metadata_response.get("result") if isinstance(metadata_response, dict) else None
    if not isinstance(result, dict):
        return None, None
    content = result.get("content", [])
    if not isinstance(content, list):
        return None, None
    for block in content:
        if isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str) and text:
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    return None, None
                if isinstance(payload, dict):
                    width = payload.get("width")
                    height = payload.get("height")
                    sha = payload.get("sha256")
                    if isinstance(width, int) and isinstance(height, int):
                        return width, sha if isinstance(sha, str) else None
                break
    return None, None


def _run_dispatch(
    endpoint: str,
    *,
    skip_media: bool,
    ignore_response: bool,
) -> tuple[bool, str | None, int, int]:
    """Run the stub's media-call sequence.

    Returns ``(success, mint_handle, width, height)``. The positive
    contract issues a full sequence of media-tool calls and extracts
    the geometry / sha256 from the ``read_image`` metadata envelope;
    the ignore-response contract issues one ``read_media`` call (so a
    verified wire-ledger record exists) and then forges a UUID-based
    receipt; the skip-media contract skips the dispatch entirely.
    """
    width = 40
    height = 24
    mint_handle: str | None = None
    if skip_media:
        return True, mint_handle, width, height
    try:
        first = _dispatch(
            endpoint,
            "read_media",
            {"path": "smoke-fixture.png", "format": "inline"},
        )
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"mock_multimodal_agent: read_media failed: {exc}\n")
        return False, mint_handle, width, height
    server_uri = _extract_server_uri(first)
    if not ignore_response and server_uri is not None:
        try:
            _dispatch(
                endpoint,
                "read_media",
                {"path": server_uri, "format": "inline"},
            )
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            sys.stderr.write(
                f"mock_multimodal_agent: read_media(replay) failed: {exc}\n"
            )
        try:
            metadata = _dispatch(
                endpoint,
                "read_image",
                {"path": "smoke-fixture.png", "format": "metadata"},
            )
            meta_width, _meta_sha = _extract_metadata_envelope(metadata)
            if isinstance(meta_width, int):
                width = meta_width
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            sys.stderr.write(f"mock_multimodal_agent: read_image failed: {exc}\n")
        mint_handle = server_uri
    elif ignore_response:
        mint_handle = f"ralph://media/{uuid.uuid4()}"
    return True, mint_handle, width, height


# ---------------------------------------------------------------------------
# Smoke-test orchestration.
# ---------------------------------------------------------------------------


def _fixture_geometry_from_disk(workspace_root: Path) -> tuple[int, int, str] | None:
    """Return ``(width, height, sha256)`` of the on-disk fixture, or ``None``.

    The smoke harness materializes ``smoke-fixture.png`` at the workspace
    root before the turns start; reading the IHDR chunk + computing the
    digest gives the stub the authoritative geometry and sha256 to
    mirror into ``DIMENSIONS`` and ``MEDIA_SHA256`` tokens. Failing to
    read the fixture is non-fatal: the stub falls back to the dimensions
    the metadata envelope returned (or to ``None``, which lets the
    harness grader report the missing token as the documented break).
    """
    import hashlib
    import struct

    fixture_path = workspace_root / "smoke-fixture.png"
    if not fixture_path.is_file():
        return None
    try:
        body = fixture_path.read_bytes()
    except OSError:
        return None
    if len(body) < 24:
        return None
    if body[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width, height = struct.unpack(">II", body[16:24])
    sha = hashlib.sha256(body).hexdigest()
    return width, height, sha


def _write_output(
    output_file: Path,
    workspace_root: Path,
    *,
    mint_handle: str | None,
    width: int,
    height: int,
    skip_media: bool,
) -> None:
    """Append the multimodal token lines to ``output_file``."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    if output_file.exists():
        body = output_file.read_text(encoding="utf-8")
    else:
        body = "// multimodal smoke stub output\n"
    if skip_media:
        if "// multimodal smoke stub output\n" not in body:
            body = "// multimodal smoke stub output\n" + body
        body = body.split("\nMEDIA_RECEIPT=")[0]
    else:
        on_disk = _fixture_geometry_from_disk(workspace_root)
        final_width = on_disk[0] if on_disk else width
        final_height = on_disk[1] if on_disk else height
        final_sha = on_disk[2] if on_disk else ""
        body += f"MEDIA_RECEIPT={mint_handle or ''}\n"
        body += f"DIMENSIONS={final_width}x{final_height}\n"
        body += f"MEDIA_SHA256={final_sha}\n"
    output_file.write_text(body, encoding="utf-8")


def _post_artifact_and_complete(
    endpoint: str,
    workspace_root: Path,
    output_file: Path,
    run_id: str,
) -> None:
    """Write the artifact, completion sentinel, and POST ``declare_complete``.

    The smoke harness grades the artifact and completion signals from
    on-disk artifacts (the canonical ``.agent/artifacts/<type>.md`` plus
    the run-scoped completion sentinel) and from the wire ledger
    (which holds the verified ``declare_complete`` record). Posting the
    artifact / sentinel write directly mirrors the deterministic
    ``tests/_support/mock_agy.py`` proof path the AGY harness already
    exercises; the stub still POSTs ``declare_complete`` to the wire so
    a verified wire-ledger record exists for the run.
    """
    artifact_md = (
        "---\n"
        "type: smoke_test_result\n"
        "status: passed\n"
        f"output_file: {output_file}\n"
        "---\n\n"
        "## Summary\n\n- [SUM-1] multimodal smoke stub complete\n"
    )
    artifact_path = workspace_root / ".agent" / "artifacts" / "smoke_test_result.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(artifact_md, encoding="utf-8")

    sentinel_path = workspace_root / ".agent" / f"completion_seen_{run_id}.json"
    sentinel_path.parent.mkdir(parents=True, exist_ok=True)
    sentinel_path.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")

    try:
        _dispatch(
            endpoint,
            "mcp__ralph__declare_complete",
            {"summary": "multimodal smoke stub complete"},
        )
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"mock_multimodal_agent: declare_complete failed: {exc}\n")


def _emit_post_dispatch_frames(
    transport: str,
    emitters: dict[str, Any],
    *,
    session_id: str,
    skip_media: bool,
) -> None:
    """Emit frames that mirror the POSTed tool calls so the parser sees activity."""
    text_emitter = emitters.get("text")
    tool_emitter = emitters.get("tool_call")
    if text_emitter is not None:
        if transport in ("claude", "claude-headless"):
            text_emitter("I will read the multimodal fixture, write receipts, and complete.")
        elif transport == "agy":
            text_emitter(
                "I will read the multimodal fixture, write receipts, and complete.",
                session_id,
                0,
            )
        elif transport in {"cursor", "opencode", "nanocoder"}:
            text_emitter("Reading multimodal fixture and writing receipts.")
    if tool_emitter is not None and not skip_media:
        if transport in ("claude", "claude-headless"):
            tool_emitter(
                "mcp__ralph__read_media",
                {"path": "smoke-fixture.png", "format": "inline"},
                "toolu_media_1",
            )
            tool_emitter(
                "mcp__ralph__declare_complete",
                {"summary": "multimodal smoke stub complete"},
                "toolu_done",
            )
        elif transport == "agy":
            tool_emitter(
                "read_media",
                {"path": "smoke-fixture.png"},
                session_id,
                1,
            )
            tool_emitter(
                "declare_complete",
                {"summary": "multimodal smoke stub complete"},
                session_id,
                2,
            )
        elif transport == "cursor":
            tool_emitter(
                "mcp__ralph__read_media",
                {"path": "smoke-fixture.png", "format": "inline"},
            )
            tool_emitter(
                "mcp__ralph__declare_complete",
                {"summary": "multimodal smoke stub complete"},
            )
        elif transport == "opencode":
            tool_emitter(
                "read_media",
                {"path": "smoke-fixture.png", "format": "inline"},
                "toolu_media_1",
            )
            tool_emitter(
                "declare_complete",
                {"summary": "multimodal smoke stub complete"},
                "toolu_done",
            )
        elif transport == "nanocoder":
            tool_emitter("mcp__ralph__read_media")
            tool_emitter("mcp__ralph__declare_complete")
    stop_emitter = emitters.get("stop")
    if stop_emitter is not None:
        if transport == "agy":
            stop_emitter(session_id, "SUCCESS")
        elif transport in {"opencode", "cursor"} or transport in ("claude", "claude-headless"):
            stop_emitter()
        elif transport == "nanocoder":
            stop_emitter("done")


def main() -> int:
    endpoint, output_file, run_id = _read_env_or_fail()
    if not endpoint:
        sys.stderr.write(
            f"mock_multimodal_agent: {ENDPOINT_ENV} not set; smoke harness "
            "must export the broker endpoint to the stub agent"
        )
        return 2
    if not output_file:
        sys.stderr.write(
            f"mock_multimodal_agent: {OUTPUT_FILE_ENV} not set; smoke harness "
            "must export the output file path"
        )
        return 2
    skip_media, ignore_response = _read_behavior_flags()
    transport = _resolve_transport()
    emitters = _make_emit_functions(transport)
    session_id = _canonical_run_id_for_transport(transport, run_id)

    init_emitter = emitters.get("init")
    if init_emitter is not None:
        if transport in ("claude", "claude-headless"):
            init_emitter(session_id, run_id)
        elif transport == "agy":
            init_emitter("mock-multimodal-stub", session_id)
        elif transport in {"cursor", "opencode"}:
            init_emitter(session_id)
    session_id_line = emitters.get("session_id_line")
    if session_id_line is not None:
        session_id_line(session_id)

    output_path = Path(output_file)
    workspace_root_env = os.environ.get("MOCK_MULTIMODAL_WORKSPACE_ROOT")
    if workspace_root_env:
        workspace_root = Path(workspace_root_env)
    else:
        workspace_root = (
            output_path.parents[2] if len(output_path.parents) >= 3 else output_path.parent
        )

    dispatch_ok, mint_handle, width, height = _run_dispatch(
        endpoint,
        skip_media=skip_media,
        ignore_response=ignore_response,
    )
    if not dispatch_ok:
        return 3

    _write_output(
        output_path,
        workspace_root=workspace_root,
        mint_handle=mint_handle,
        width=width,
        height=height,
        skip_media=skip_media,
    )

    if not skip_media:
        _post_artifact_and_complete(endpoint, workspace_root, output_path, run_id)

    _emit_post_dispatch_frames(
        transport,
        emitters,
        session_id=session_id,
        skip_media=skip_media,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
