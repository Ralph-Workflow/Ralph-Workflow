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

V1_1_13_INIT_TOOL_NAMES: tuple[str, ...] = (*_MOCK_INIT_TOOL_NAMES, "call_mcp_tool")

V1_1_13_SUBAGENT_ROLE = "Todo Edge Case Researcher"

_V1_1_13_FALLBACK_SUBMIT_OUTPUT = (
    '{"artifact_type": "smoke_test_result", "valid": true, '
    '"diagnostics": [], "counts": {"error": 0, "warning": 0, "info": 0}}'
)

_MCP_TIMEOUT_SECONDS = 10


def _mcp_post(endpoint: str, payload: dict[str, object]) -> dict[str, object] | None:
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
    endpoint = os.environ.get("RALPH_MCP_ENDPOINT")
    if not endpoint:
        return None, None
    if _MOCK_BEHAVIOR in {"missing_artifact", "missing_completion"}:
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


_MOCK_BEHAVIOR = os.environ.get("MOCK_AGY_BEHAVIOR", "normal")

_MOCK_MALFORMED_LINES: tuple[str, ...] = (
    "I will create the todo list implementation.",
    "Here is some ordinary model prose without any JSON frames.",
    "The task appears complete.",
)


def _v1_1_13_submit_parameters() -> dict[str, object]:
    return {
        "Arguments": {
            "artifact_type": "smoke_test_result",
            "content": _smoke_test_result_markdown(),
        },
        "ServerName": "ralph",
        "ToolName": "ralph_submit_md_artifact",
    }


def _v1_1_13_declare_parameters() -> dict[str, object]:
    return {
        "Arguments": {},
        "ServerName": "ralph",
        "ToolName": "declare_complete",
    }


def _v1_1_13_subagent_entry() -> dict[str, object]:
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
