"""Measured AGY v1.1.13 wire vocabulary for the AGY smoke mock.

Split out of ``tests/_support/mock_agy.py`` (wt-015-agy-support S-5) so the
default simulator stays under the repository file-size floor; see that
module's docstring for the ``MOCK_AGY_V1_1_13`` contract. This module is
imported lazily from ``mock_agy.main`` only when the flag is set, so the
default (flag-unset) subprocess path never pays for it and byte
compatibility is preserved.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from tests._support.mock_agy import (
    _MOCK_INIT_TOOL_NAMES,
    OUTPUT_FILE_RELPATH,
    _emit_normal_stdout,
    _smoke_test_result_markdown,
)

# --- v1.1.13 measured vocabulary (wt-015-agy-support S-5) ---
#
# Replays the frame shapes measured from the live v1.1.13 capture
# (.agent/raw/agy_gemini-3.6-flash-low.log, sanitized as
# tests/display/_fixtures/agy_wire_v1_1_13.jsonl): a step-level
# ``tool_name`` of ``invoke_subagent`` on subagent frames, the bodiless
# ``system_message`` step_type, and ``call_mcp_tool`` frames whose
# ``tool_info.parameters`` carry ``ServerName`` / ``ToolName`` /
# ``Arguments``. Unlike the deliberately Ralph-free default tool list,
# this init advertises ``call_mcp_tool`` so the smoke harness's evidence
# ceiling can reach WIRE for a run that actually dialed the server.
V1_1_13_INIT_TOOL_NAMES: tuple[str, ...] = (*_MOCK_INIT_TOOL_NAMES, "call_mcp_tool")

V1_1_13_SUBAGENT_ROLE = "Todo Edge Case Researcher"

#: Fallback receipt shape mirroring the live capture's DONE output when the
#: real MCP round trip could not be made (no endpoint). The harness still
#: grades submission authoritatively from the server-side receipt, so a
#: synthesized transcript output can never pass a failed round trip off as
#: a submission.
_V1_1_13_FALLBACK_SUBMIT_OUTPUT = (
    '{"artifact_type": "smoke_test_result", "valid": true, '
    '"diagnostics": [], "counts": {"error": 0, "warning": 0, "info": 0}}'
)

_MCP_TIMEOUT_SECONDS = 10


def _mcp_post(endpoint: str, payload: dict[str, object]) -> dict[str, object] | None:
    """POST one JSON-RPC frame and return the parsed SSE ``data:`` payload."""
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_MCP_TIMEOUT_SECONDS) as response:
        body = response.read().decode("utf-8")
    for part in body.splitlines():
        if part.startswith("data: "):
            try:
                decoded = json.loads(part[len("data: ") :])
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                return decoded
    return None


def _mcp_result_text(response: dict[str, object] | None) -> str | None:
    """Extract the first text block from a tools/call JSON-RPC response."""
    if not isinstance(response, dict):
        return None
    result = response.get("result")
    if not isinstance(result, dict):
        return None
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                return str(item["text"])
    return None


def drive_real_mcp_round_trips() -> tuple[str | None, str | None]:
    """Submit the smoke artifact and declare completion over real MCP.

    Stdlib-only JSON-RPC client mirroring the calls the live binary makes:
    ``initialize``, ``tools/call ralph_submit_md_artifact`` (the same
    markdown the default fallback writer produces), then ``tools/call
    declare_complete``. Returns the server's text outputs for the two
    tools/call frames (``None`` per call when the round trip could not be
    made). No Ralph helper is imported here: the receipt, completion
    sentinel, and wire-ledger records are produced solely by the server's
    handlers, so a graded WIRE provenance always reflects a real round
    trip (the PA-005 failure mode).
    """
    endpoint = os.environ.get("RALPH_MCP_ENDPOINT")
    if not endpoint:
        return None, None
    if _MOCK_BEHAVIOR in {"missing_artifact", "missing_completion"}:
        # The selector prunes the round trip itself, not only the frame
        # that reports it: a run that STILL submits the artifact (or
        # declares completion) over the real wire leaves a valid
        # HMAC-bound receipt/sentinel behind, so the harness has no
        # observable break to grade and exits 0 -- defeating the
        # negative contract. Skipping the call here makes the wire
        # record genuinely absent.
        return None, None
    try:
        _mcp_post(
            endpoint,
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "mock-agy", "version": "1.1.13"},
                },
            },
        )
        submit_response = _mcp_post(
            endpoint,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "ralph_submit_md_artifact",
                    "arguments": {
                        "artifact_type": "smoke_test_result",
                        "content": _smoke_test_result_markdown(),
                    },
                },
            },
        )
        complete_response = _mcp_post(
            endpoint,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "declare_complete",
                    "arguments": {"summary": "AGY smoke test completed via MCP"},
                },
            },
        )
    except OSError as exc:
        print(f"mock AGY: MCP round trip failed: {exc}", file=sys.stderr)
        return None, None
    return _mcp_result_text(submit_response), _mcp_result_text(complete_response)


# ---------------------------------------------------------------------------
# MOCK_AGY_BEHAVIOR selectors (wt-015-agy-support S-5 negative-contract
# selectors). Each selector alters ONLY its named contract signal so a
# superficial green smoke cannot mask a broken one:
#
#   ``no_output``          -> empty stdout (parser receives nothing)
#   ``malformed_stream``   -> plain-text, non-JSON output (no parser events)
#   ``failed_result``      -> closing ``result`` frame with status FAILED
#   ``missing_dispatch``   -> no subagent dispatch frame
#   ``missing_result``     -> subagent ACTIVE frames but no DONE result
#   ``missing_artifact``   -> no ralph_submit_md_artifact MCP round trip
#   ``missing_completion`` -> no declare_complete MCP round trip
#
# ``drive_real_mcp_round_trips`` consults the same constant and skips the
# real round trip for both selectors; pruning only the FRAMES while still
# performing the call would leave a valid broker receipt/sentinel on disk
# and the harness would PASS the run it was asked to break.
# ---------------------------------------------------------------------------
_MOCK_BEHAVIOR = os.environ.get("MOCK_AGY_BEHAVIOR", "normal")

#: Plain-text bytes for ``malformed_stream``: definitively NOT stream-json.
_MOCK_MALFORMED_LINES: tuple[str, ...] = (
    "I will create the todo list implementation.",
    "Here is some ordinary model prose without any JSON frames.",
    "The task appears complete.",
)


def _v1_1_13_submit_parameters() -> dict[str, object]:
    """Measured ``call_mcp_tool`` parameters for ralph_submit_md_artifact."""
    return {
        "Arguments": {
            "artifact_type": "smoke_test_result",
            "content": _smoke_test_result_markdown(),
        },
        "ServerName": "ralph",
        "ToolName": "ralph_submit_md_artifact",
    }


def _v1_1_13_declare_parameters() -> dict[str, object]:
    """Measured ``call_mcp_tool`` parameters for declare_complete."""
    return {
        "Arguments": {},
        "ServerName": "ralph",
        "ToolName": "declare_complete",
    }


def _v1_1_13_subagent_entry() -> dict[str, object]:
    """Measured subagent entry carrying role/prompt/workspace metadata."""
    return {
        "type_name": "research",
        "role": V1_1_13_SUBAGENT_ROLE,
        "initial_prompt": (
            "Inspect the requested todo-list API and return two concise edge "
            "cases the main agent should account for. Do not modify files."
        ),
        "conversation_id": "00000000-0000-0000-0000-0000000000aa",
        "log_uri": (
            "file:///mock/antigravity-cli/brain/"
            "00000000-0000-0000-0000-0000000000aa/"
            ".system_generated/logs/transcript.jsonl"
        ),
        "workspace_uris": ["file:///workspace"],
    }


def _emit_v1_1_13_stdout(
    model: str | None,
    output_format: str,
    artifact_dir: Path,
    submit_output: str | None,
    complete_output: str | None,
) -> None:
    """Emit the measured v1.1.13 stream-json vocabulary in the plan's order.

    Order: subagent ACTIVE/DONE pair (step-level ``tool_name``
    ``invoke_subagent``), bodiless ``system_message``, the
    ``call_mcp_tool`` -> ``ralph_submit_md_artifact`` ACTIVE/DONE pair whose
    DONE output is the real server receipt, the ``call_mcp_tool`` ->
    ``declare_complete`` pair, a ``read_file`` pair aimed at the todo list,
    a ``view_file`` pair (proving the ``view_file <basename>`` summary
    rule), and a ``write_to_file`` pair (the write-surface event whose
    correlated result renders through the display's syntax preview). Non
    stream-json formats fall back to the default emitter; the v1.1.13
    vocabulary is stream-json only.

    The ``_MOCK_BEHAVIOR`` negative selectors prune exactly one contract
    signal each from the event stream below.
    """
    if output_format != "stream-json":
        _emit_normal_stdout(model, output_format)
        return

    sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", model or "default").strip("-")
    session_id = f"interactive-agy-smoke-{sanitized}"
    submit_parameters = _v1_1_13_submit_parameters()
    declare_parameters = _v1_1_13_declare_parameters()
    subagent_entry = _v1_1_13_subagent_entry()

    events: list[dict[str, object]] = [
        {
            "event": "init",
            "conversation_id": session_id,
            "init": {
                "model": model or "default",
                "cwd": ".",
                "tools": list(V1_1_13_INIT_TOOL_NAMES),
                "permission_mode": "always-proceed",
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 0,
                "state": "DONE",
                "step_type": "user_input",
            },
        },
    ]

    if _MOCK_BEHAVIOR != "missing_dispatch":
        events.append(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": session_id,
                    "step_index": 1,
                    "state": "ACTIVE",
                    "step_type": "subagent",
                    "tool_name": "invoke_subagent",
                    "subagent_info": {"subagents": [dict(subagent_entry)]},
                },
            }
        )
    if _MOCK_BEHAVIOR not in {"missing_dispatch", "missing_result"}:
        events.append(
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": session_id,
                    "step_index": 1,
                    "state": "DONE",
                    "step_type": "subagent",
                    "tool_name": "invoke_subagent",
                    "duration_seconds": 0.4,
                    "subagent_info": {"subagents": [dict(subagent_entry)]},
                },
            }
        )

    events.append(
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 2,
                "state": "DONE",
                "step_type": "system_message",
            },
        }
    )

    if _MOCK_BEHAVIOR != "missing_artifact":
        events.extend(
            [
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": session_id,
                        "step_index": 3,
                        "state": "ACTIVE",
                        "step_type": "tool",
                        "tool_name": "call_mcp_tool",
                        "tool_info": {
                            "name": "call_mcp_tool",
                            "parameters": dict(submit_parameters),
                        },
                    },
                },
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": session_id,
                        "step_index": 3,
                        "state": "DONE",
                        "step_type": "tool",
                        "tool_name": "call_mcp_tool",
                        "duration_seconds": 0.3,
                        "tool_info": {
                            "name": "call_mcp_tool",
                            "parameters": dict(submit_parameters),
                            "output": submit_output or _V1_1_13_FALLBACK_SUBMIT_OUTPUT,
                        },
                    },
                },
            ]
        )

    if _MOCK_BEHAVIOR != "missing_completion":
        events.extend(
            [
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": session_id,
                        "step_index": 4,
                        "state": "ACTIVE",
                        "step_type": "tool",
                        "tool_name": "call_mcp_tool",
                        "tool_info": {
                            "name": "call_mcp_tool",
                            "parameters": dict(declare_parameters),
                        },
                    },
                },
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": session_id,
                        "step_index": 4,
                        "state": "DONE",
                        "step_type": "tool",
                        "tool_name": "call_mcp_tool",
                        "duration_seconds": 0.1,
                        "tool_info": {
                            "name": "call_mcp_tool",
                            "parameters": dict(declare_parameters),
                            "output": complete_output
                            or (
                                "Task declared complete: session_id=smoke-mock, "
                                "summary='No summary provided', timestamp=0"
                            ),
                        },
                    },
                },
            ]
        )

    events.extend(
        [
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": session_id,
                    "step_index": 5,
                    "state": "ACTIVE",
                    "step_type": "tool",
                    "tool_name": "read_file",
                    "tool_info": {
                        "name": "read_file",
                        "parameters": {"path": OUTPUT_FILE_RELPATH},
                    },
                },
            },
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": session_id,
                    "step_index": 5,
                    "state": "DONE",
                    "step_type": "tool",
                    "tool_name": "read_file",
                    "duration_seconds": 0.02,
                    "tool_info": {
                        "name": "read_file",
                        "parameters": {"path": OUTPUT_FILE_RELPATH},
                        "output": "13 lines, 438 bytes",
                    },
                },
            },
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": session_id,
                    "step_index": 6,
                    "state": "ACTIVE",
                    "step_type": "tool",
                    "tool_name": "view_file",
                    "tool_info": {
                        "name": "view_file",
                        "parameters": {"AbsolutePath": str(artifact_dir / OUTPUT_FILE_RELPATH)},
                    },
                },
            },
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": session_id,
                    "step_index": 6,
                    "state": "DONE",
                    "step_type": "tool",
                    "tool_name": "view_file",
                    "duration_seconds": 0.02,
                    "tool_info": {
                        "name": "view_file",
                        "parameters": {"AbsolutePath": str(artifact_dir / OUTPUT_FILE_RELPATH)},
                        "output": "13 lines, 438 bytes",
                    },
                },
            },
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": session_id,
                    "step_index": 7,
                    "state": "ACTIVE",
                    "step_type": "tool",
                    "tool_name": "write_to_file",
                    "tool_info": {
                        "name": "write_to_file",
                        "parameters": {"TargetFile": OUTPUT_FILE_RELPATH},
                    },
                },
            },
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": session_id,
                    "step_index": 7,
                    "state": "DONE",
                    "step_type": "tool",
                    "tool_name": "write_to_file",
                    "duration_seconds": 0.08,
                    # No ``output`` key: the measured capture shows write_to_file
                    # produces no output on DONE.
                    "tool_info": {
                        "name": "write_to_file",
                        "parameters": {"TargetFile": OUTPUT_FILE_RELPATH},
                    },
                },
            },
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": session_id,
                    "step_index": 8,
                    "state": "ACTIVE",
                    "step_type": "agent_response",
                    "text_delta": "Submitted smoke_test_result and declared ",
                },
            },
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": session_id,
                    "step_index": 8,
                    "state": "DONE",
                    "step_type": "agent_response",
                    "text_delta": "completion via ralph MCP.\n",
                    "usage": {"input_tokens": 4096, "output_tokens": 64, "total_tokens": 4160},
                },
            },
            {
                "event": "result",
                "result": {
                    "conversation_id": session_id,
                    "status": "FAILED" if _MOCK_BEHAVIOR == "failed_result" else "SUCCESS",
                    "response": (
                        "Submitted smoke_test_result and declared completion via ralph MCP.\n"
                    ),
                    "duration_seconds": 0.9,
                    "num_turns": 1,
                    "usage": {"input_tokens": 4096, "output_tokens": 64, "total_tokens": 4160},
                },
            },
        ]
    )
    for event in events:
        print(json.dumps(event, separators=(",", ":")))
