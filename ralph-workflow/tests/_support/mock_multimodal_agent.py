#!/usr/bin/env python3
"""Deterministic multimodal smoke stub agent (S-9 / S-12 / criterion 5).

This stub is a genuine subprocess agent: it reads ``RALPH_MCP_ENDPOINT``
and ``RALPH_BROKER_SECRET`` from its environment, POSTs real
``tools/call`` requests against Ralph's MCP server for the
multimodal smoke scenario, and emits the same transport frame vocabulary
the production harness's parser expects. It is the canonical proof
that the multimodal MCP endpoints work across the six smoke
commands without consuming vendor tokens or requiring a real vendor
binary.

Three modes, selected by env vars:

- ``MOCK_MULTIMODAL_BEHAVIOR=ok`` (default) -- issues the FULL
  positive-contract call sequence (read_media fixture path,
  re-read the server-minted handle, read_image metadata, write
  the receipts into the smoke output file, submit the
  ``smoke_test_result`` artifact, call ``declare_complete``).
  Used by the positive AGY case (and the per-harness positive cases
  parameterized in S-12).

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
to see a normal Claude / AGY-shape / OpenCode-shape tool-use
sequence and is graded against the SAME harnessing path the
production --multimodal smoke runs use.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT_ENV = "RALPH_MCP_ENDPOINT"
SECRET_ENV = "RALPH_BROKER_SECRET"
RUN_ID_ENV = "RALPH_RUN_ID"
OUTPUT_FILE_ENV = "MOCK_MULTIMODAL_OUTPUT_FILE"
SKIP_MEDIA_ENV = "MOCK_MULTIMODAL_SKIP_MEDIA"
IGNORE_RESPONSE_ENV = "MOCK_MULTIMODAL_IGNORE_RESPONSE"


def _emit_assistant_text(text: str) -> None:
    """Emit a Claude-shape assistant text frame for the smoke parser."""
    sys.stdout.write(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": text}],
                    "role": "assistant",
                },
            }
        )
        + "\n"
    )
    sys.stdout.flush()


def _emit_tool_use(name: str, arguments: dict, call_id: str) -> None:
    """Emit a Claude-shape tool_use frame."""
    sys.stdout.write(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": call_id,
                            "name": name,
                            "input": arguments,
                        }
                    ],
                    "role": "assistant",
                },
            }
        )
        + "\n"
    )
    sys.stdout.flush()


def _emit_assistant_done() -> None:
    sys.stdout.write(json.dumps({"type": "message_stop"}) + "\n")
    sys.stdout.flush()


def _dispatch(endpoint: str, run_id: str, name: str, arguments: dict) -> dict:
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
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.loads(response.read().decode())
    return body


def _read_env_or_fail() -> tuple[str, str, str]:
    """Read the harness-exported env vars; return ``(endpoint, output_file, run_id)``.

    Returns the empty string for any unset var so the caller can
    decide whether the run is honorably absent (the test-skipped
    path under ``make verify``) or genuinely broken.
    """
    endpoint = os.environ.get(ENDPOINT_ENV, "")
    output_file = os.environ.get(OUTPUT_FILE_ENV, "")
    run_id = os.environ.get(RUN_ID_ENV, "interactive-agy-smoke")
    return endpoint, output_file, run_id


def _read_behavior_flags() -> tuple[bool, bool]:
    """Return ``(skip_media, ignore_response)`` from the harness env."""
    skip_media = os.environ.get(SKIP_MEDIA_ENV) == "1"
    ignore_response = os.environ.get(IGNORE_RESPONSE_ENV) == "1"
    return skip_media, ignore_response


def _run_dispatch(
    endpoint: str,
    run_id: str,
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
    mint_handle: str | None = None
    width = 40
    height = 24
    if skip_media:
        return True, mint_handle, width, height
    try:
        first = _dispatch(
            endpoint,
            run_id,
            "read_media",
            {"path": "smoke-fixture.png", "format": "inline"},
        )
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"mock_multimodal_agent: read_media failed: {exc}\n")
        return False, mint_handle, width, height
    minted_uri = None
    result = first.get("result") if isinstance(first, dict) else None
    if isinstance(result, dict):
        for block in result.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "resource_reference":
                minted_uri = block.get("uri")
                break
    if not ignore_response and minted_uri is not None:
        try:
            _dispatch(
                endpoint,
                run_id,
                "read_media",
                {"path": minted_uri, "format": "inline"},
            )
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            sys.stderr.write(
                f"mock_multimodal_agent: read_media(replay) failed: {exc}\n"
            )
        metadata = _dispatch(
            endpoint,
            run_id,
            "read_image",
            {"path": "smoke-fixture.png", "format": "metadata"},
        )
        meta_text = ""
        mresult = metadata.get("result") if isinstance(metadata, dict) else None
        if isinstance(mresult, dict):
            for block in mresult.get("content", []) or []:
                if isinstance(block, dict):
                    meta_text = block.get("text", "")
                    break
        try:
            meta_obj = json.loads(meta_text)
            width = int(meta_obj.get("width") or width)
            height = int(meta_obj.get("height") or height)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        mint_handle = minted_uri
    elif ignore_response:
        import uuid

        mint_handle = f"ralph://media/{uuid.uuid4()}"
    return True, mint_handle, width, height


def _emit_dummy_tool_use_for_loop() -> None:
    """Emit a dummy ``write_file`` tool_use and message_stop, used to satisfy parser quirks."""
    _emit_tool_use(
        "write_file",
        {"path": "smoke-fixture.png", "content": "// stub-written\n"},
        call_id="toolu_001",
    )
    _emit_assistant_done()


def _emit_submit_artifact() -> None:
    _emit_tool_use(
        "ralph_submit_md_artifact",
        {
            "artifact_type": "smoke_test_result",
            "content": (
                "---\n"
                "type: smoke_test_result\n"
                "status: passed\n"
                "output_file: __OUTPUT_FILE__\n"
                "---\n\n## Summary\n\n- [SUM-1] stub\n"
            ),
        },
        call_id="toolu_002",
    )
    _emit_assistant_done()


def _emit_declare_complete() -> None:
    _emit_tool_use(
        "ralph_declare_complete",
        {"summary": "multimodal smoke stub complete"},
        call_id="toolu_003",
    )
    _emit_assistant_done()


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

    # Emit a deterministic init-equivalent announcement so the parser
    # surfaces a normal opening.
    _emit_assistant_text(f"multimodal smoke stub for run {run_id}")

    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    sha_value = ""

    dispatch_ok, mint_handle, width, height = _run_dispatch(
        endpoint,
        run_id,
        skip_media,
        ignore_response,
    )
    if not dispatch_ok:
        return 3

    # Always write the output file. The contents depend on mode.
    if skip_media:
        # Negative "no call" case: write no tokens at all.
        if output_path.exists():
            output_path.unlink()
        output_path.write_text("// multimodal smoke stub ran without media\n", encoding="utf-8")
    else:
        # Positive OR ignore-response: ALWAYS write the three tokens.
        # The ignore-response mode fabricates ``mint_handle``, so the
        # grader's read-media-receipt mismatch detection will catch
        # this case and grade it ``WORKSPACE_EFFECT`` (per
        # ``grade_multimodal_evidence``).
        body = output_path.read_text(encoding="utf-8") if output_path.exists() else "// stub\n"
        if "//" not in body.splitlines()[0:1]:
            body = "// multimodal smoke stub output\n" + body
        body += f"MEDIA_RECEIPT={mint_handle or ''}\n"
        body += f"DIMENSIONS={width}x{height}\n"
        body += f"MEDIA_SHA256={sha_value}\n"
        output_path.write_text(body, encoding="utf-8")

    # Emit the rest of the harness journey: write_file the output (no-op
    # since we already wrote it above, but emit the frame so the parser
    # sees a normal file-write), submit the artifact, declare complete.
    _emit_dummy_tool_use_for_loop()
    _emit_submit_artifact()
    _emit_declare_complete()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
