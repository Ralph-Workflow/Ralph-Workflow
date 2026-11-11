from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

CANONICAL_MODELS: frozenset[str] = frozenset(
    {
        "gemini-3.8-flash-high",
        "gemini-3.8-flash-medium",
        "gemini-3.8-flash-low",
        "gemini-3.7-flash-high",
        "gemini-3.7-flash-medium",
        "gemini-3.7-flash-low",
        "gemini-3.6-flash-high",
        "gemini-3.6-flash-medium",
        "gemini-3.6-flash-low",
        "gemini-3.1-pro-high",
        "gemini-3.1-pro-low",
        "claude-opus-5-5-low",
        "claude-opus-5-5-medium",
        "claude-opus-5-5-high",
        "claude-sonnet-5-5-low",
        "claude-sonnet-5-5-medium",
        "claude-sonnet-5-5-high",
        "gpt-oss-120b-medium",
    }
)

OUTPUT_FILE_RELPATH = "tmp/interactive-agy-smoke/todo-list.js"
ARTIFACT_RELPATH = ".agent/tmp/smoke_test_result.md"
PROMPT_RECEIVED_RELPATH = ".agent/artifacts/.mock_agy_prompt.txt"
RUN_ID_ENV = "RALPH_MCP_RUN_ID"


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agy")
    parser.add_argument("--print", "-p", action="store_true", dest="print_mode")
    parser.add_argument("--output-format", choices=("text", "json", "stream-json"), default="text")
    parser.add_argument("--dangerously-skip-permissions", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument("--add-dir", action="append", default=[])
    parser.add_argument("--effort", choices=("low", "medium", "high"), default=None)
    parser.add_argument("--print-timeout", default=None)
    parser.add_argument("--conversation", default=None)
    parser.add_argument("--sandbox", action="store_true")
    parser.add_argument("--agent", default=None)
    parser.add_argument("--mode", default=None)
    parser.add_argument("--json-schema", default=None)
    parser.add_argument("--disable-slash-commands", action="store_true")
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--continue", "-c", action="store_true", dest="continue_session")
    parser.add_argument("--project", default=None)
    parser.add_argument("--new-project", action="store_true")
    parser.add_argument("--log-file", default=None)
    parser.add_argument("--prompt-interactive", "-i", default=None)
    parser.add_argument("prompt", nargs="?", default=None)
    return parser


def _write_todo_list(artifact_dir: Path) -> None:
    todo_path = artifact_dir / OUTPUT_FILE_RELPATH
    todo_path.parent.mkdir(parents=True, exist_ok=True)
    todo_path.write_text(
        "// AGY smoke test todo list implementation\n"
        "function createTodoList() {\n"
        "  const todos = [];\n"
        "  return {\n"
        "    add: (text) => { todos.push({ text, done: false }); },\n"
        "    list: () => todos,\n"
        "    complete: (index) => { if (todos[index]) todos[index].done = true; },\n"
        "    remove: (index) => todos.splice(index, 1),\n"
        "  };\n"
        "}\n"
        "module.exports = { createTodoList };\n",
        encoding="utf-8",
    )


def _write_prompt_received(artifact_dir: Path, prompt: str | None) -> None:
    prompt_path = artifact_dir / PROMPT_RECEIVED_RELPATH
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt or "", encoding="utf-8")


def _smoke_test_result_markdown() -> str:
    return (
        "---\n"
        "type: smoke_test_result\n"
        "status: passed\n"
        f"output_file: {OUTPUT_FILE_RELPATH}\n"
        "---\n"
        "\n"
        "## Summary\n"
        "\n"
        "- [SUM-1] AGY smoke test completed successfully\n"
        "\n"
        "## Observed Working\n"
        "\n"
        "- [OK-1] created todo-list.js\n"
        "- [OK-2] wrote smoke_test_result artifact\n"
        "\n"
        "## Headless Guide Checks\n"
        "\n"
        "- [HG-1] tool activity\n"
        "- [HG-2] parser events\n"
        "- [HG-3] tmp artifact creation\n"
    )


def _write_smoke_test_result_artifact(artifact_dir: Path) -> Path:
    artifact_path = artifact_dir / ARTIFACT_RELPATH
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(_smoke_test_result_markdown(), encoding="utf-8")
    return artifact_path


def _write_completion_sentinel(artifact_dir: Path) -> None:
    run_id = os.environ.get(RUN_ID_ENV)
    if not run_id:
        return
    sentinel = artifact_dir / ".agent" / f"completion_seen_{run_id}.json"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(f'{{"run_id": "{run_id}"}}', encoding="utf-8")


_MOCK_INIT_TOOL_NAMES: tuple[str, ...] = (
    "ask_permission",
    "read_file",
    "write_to_file",
    "view_file",
    "define_subagent",
    "invoke_subagent",
    "manage_subagents",
)


def _emit_normal_stdout(model: str | None, output_format: str = "text") -> None:
    if output_format == "text":
        print("I will create the todo list implementation.")
        if os.environ.get("MOCK_AGY_SUBAGENT") == "1":
            print("[subagent] Inspect two edge cases.")
        print("Writing smoke_test_result artifact.")
        return

    sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", model or "default").strip("-")
    session_id = f"interactive-agy-smoke-{sanitized}"
    events: list[dict[str, object]] = [
        {
            "event": "init",
            "conversation_id": session_id,
            "init": {
                "model": model or "default",
                "cwd": ".",
                "tools": list(_MOCK_INIT_TOOL_NAMES),
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
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 1,
                "state": "DONE",
                "step_type": "unknown",
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 2,
                "state": "ACTIVE",
                "step_type": "agent_response",
                "text_delta": "I will create the todo list ",
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 2,
                "state": "DONE",
                "step_type": "agent_response",
                "text_delta": "implementation.\n",
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 3,
                "state": "ACTIVE",
                "step_type": "tool",
                "tool_name": "readSpec",
                "tool_info": {"name": "readSpec", "parameters": {"path": "SPEC.md"}},
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 3,
                "state": "DONE",
                "step_type": "tool",
                "tool_name": "readSpec",
                "duration_seconds": 0.01,
                "tool_info": {"name": "readSpec", "parameters": {"path": "SPEC.md"}},
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 4,
                "state": "DONE",
                "step_type": "checkpoint",
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 5,
                "state": "ACTIVE",
                "step_type": "tool",
                "tool_name": "createTodoList",
                "tool_info": {"name": "createTodoList"},
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 5,
                "state": "DONE",
                "step_type": "tool",
                "tool_name": "createTodoList",
                "duration_seconds": 0.05,
                "tool_info": {
                    "name": "createTodoList",
                    "output": "File created at tmp/interactive-agy-smoke/todo-list.js.",
                },
            },
        },
    ]
    if os.environ.get("MOCK_AGY_SUBAGENT") == "1":
        subagent_a = {
            "type_name": "file_writer",
            "role": "Write File A",
            "initial_prompt": "Inspect edge case A.",
        }
        subagent_b = {
            "type_name": "file_writer",
            "role": "Write File B",
            "initial_prompt": "Inspect edge case B.",
        }
        events.extend(
            [
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": session_id,
                        "step_index": 6,
                        "state": "ACTIVE",
                        "step_type": "subagent",
                        "subagent_info": {"subagents": [subagent_a, subagent_b]},
                    },
                },
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": session_id,
                        "step_index": 6,
                        "state": "DONE",
                        "step_type": "subagent",
                        "duration_seconds": 0.06,
                        "subagent_info": {
                            "subagents": [
                                {
                                    **subagent_a,
                                    "conversation_id": "mock-subagent-a",
                                    "log_uri": "file:///mock/logs/a.log",
                                },
                                {
                                    **subagent_b,
                                    "conversation_id": "mock-subagent-b",
                                    "log_uri": "file:///mock/logs/b.log",
                                },
                            ]
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
                    "step_index": 7,
                    "state": "ACTIVE",
                    "step_type": "agent_response",
                    "text_delta": "Writing smoke_test_",
                },
            },
            {
                "event": "step_update",
                "step_update": {
                    "conversation_id": session_id,
                    "step_index": 7,
                    "state": "DONE",
                    "step_type": "agent_response",
                    "text_delta": "result artifact.\n",
                    "usage": {"input_tokens": 512, "output_tokens": 32, "total_tokens": 544},
                },
            },
            {
                "event": "result",
                "result": {
                    "conversation_id": session_id,
                    "status": "SUCCESS",
                    "response": "Writing smoke_test_result artifact.\n",
                    "duration_seconds": 0.5,
                    "num_turns": 1,
                    "usage": {"input_tokens": 512, "output_tokens": 32, "total_tokens": 544},
                },
            },
        ]
    )
    for event in events:
        print(json.dumps(event, separators=(",", ":")))


DEGRADED_BASELINE_INIT_TOOL_NAMES: tuple[str, ...] = (
    "ask_permission",
    "ask_question",
    "define_subagent",
    "invoke_subagent",
    "manage_subagents",
    "view_file",
    "write_to_file",
    "grep_search",
    "run_command",
    "read_file",
    "list_directory",
    "edit_file",
    "search_files",
    "todo_write",
)

DEGRADED_BASELINE_RUN_ID = "interactive-agy-smoke-degraded-baseline"


def degraded_baseline_stream_json_lines(model: str = "gemini-3.6-flash-low") -> list[str]:
    session_id = "00000000-0000-0000-0000-0000000000ba"
    events: list[dict[str, object]] = [
        {
            "event": "init",
            "conversation_id": session_id,
            "init": {
                "model": model,
                "cwd": ".",
                "tools": list(DEGRADED_BASELINE_INIT_TOOL_NAMES),
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
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 1,
                "state": "DONE",
                "step_type": "unknown",
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 2,
                "state": "ACTIVE",
                "step_type": "agent_response",
                "text_delta": "I will create the todo list ",
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 2,
                "state": "DONE",
                "step_type": "agent_response",
                "text_delta": "implementation.\n",
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 3,
                "state": "ACTIVE",
                "step_type": "tool",
                "tool_name": "write_to_file",
                "tool_info": {
                    "name": "write_to_file",
                    "parameters": {"TargetFile": "todo-list.js"},
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
                "tool_name": "write_to_file",
                "duration_seconds": 0.076075017,
                "tool_info": {
                    "name": "write_to_file",
                    "parameters": {"TargetFile": "todo-list.js"},
                },
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 4,
                "state": "DONE",
                "step_type": "checkpoint",
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 5,
                "state": "DONE",
                "step_type": "agent_response",
                "text_delta": "",
                "usage": {"input_tokens": 9906, "output_tokens": 0, "total_tokens": 9906},
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 6,
                "state": "DONE",
                "step_type": "user_input",
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 7,
                "state": "ACTIVE",
                "step_type": "agent_response",
                "text_delta": "Since ralph_submit_md_artifact is unavailable in the current ",
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 7,
                "state": "DONE",
                "step_type": "agent_response",
                "text_delta": (
                    "toolset, saved the smoke test result artifact to "
                    ".agent/tmp/smoke_test_result.md as instructed for fallback.\n"
                ),
                "usage": {"input_tokens": 3395, "output_tokens": 441, "total_tokens": 3836},
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 8,
                "state": "DONE",
                "step_type": "unknown",
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 9,
                "state": "DONE",
                "step_type": "checkpoint",
            },
        },
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": session_id,
                "step_index": 10,
                "state": "DONE",
                "step_type": "agent_response",
                "text_delta": "",
                "usage": {"input_tokens": 2905, "output_tokens": 0, "total_tokens": 2905},
            },
        },
        {
            "event": "result",
            "result": {
                "conversation_id": session_id,
                "status": "SUCCESS",
                "response": (
                    "Since ralph_submit_md_artifact is unavailable in the current "
                    "toolset, saved the smoke test result artifact to "
                    ".agent/tmp/smoke_test_result.md as instructed for fallback.\n"
                ),
                "duration_seconds": 3.662374617,
                "num_turns": 1,
                "usage": {"input_tokens": 17140, "output_tokens": 3835, "total_tokens": 20975},
            },
        },
    ]
    assert sum(1 for e in events if e["event"] == "step_update") == 14, (
        "reconstructed scenario must match the brief's measured 14 step_update frames"
    )
    return [json.dumps(event, separators=(",", ":")) for event in events]


def degraded_baseline_artifact_markdown() -> str:
    return (
        "---\n"
        "type: smoke_test_result\n"
        "status: passed\n"
        "output_file: tmp/interactive-agy-smoke/todo-list.js\n"
        "---\n"
        "\n"
        "## Summary\n"
        "\n"
        "- [SUM-1] AGY smoke test completed successfully\n"
        "\n"
        "## Observed Working\n"
        "\n"
        "- [OK-1] created todo-list.js\n"
        "- [OK-2] wrote smoke_test_result artifact\n"
        "\n"
        "## Headless Guide Checks\n"
        "\n"
        "- [HG-1] tool activity\n"
        "- [HG-2] parser events\n"
        "- [HG-3] tmp artifact creation\n"
    )


def _validate_mock_agy_args(args: argparse.Namespace, behavior: str) -> int | None:
    if not args.print_mode:
        print("mock AGY: --print is required", file=sys.stderr)
        return 2

    terminal_failures = {
        "quota_exhausted": ("RESOURCE_EXHAUSTED (code 429)", 1),
        "auth_failure": ("authentication failed", 1),
        "unknown_failure": ("mock AGY unknown failure", 1),
    }
    if failure := terminal_failures.get(behavior):
        message, returncode = failure
        print(message, file=sys.stderr)
        return returncode
    if behavior in {"invalid_model", "no_output"}:
        return 0

    if args.model is not None and args.model not in CANONICAL_MODELS:
        return 0

    v1_1_13_selectors = {
        "failed_result",
        "missing_dispatch",
        "missing_result",
        "missing_artifact",
        "missing_completion",
    }
    is_v1_1_13_selector = behavior in v1_1_13_selectors
    is_v1_1_13_env = os.environ.get("MOCK_AGY_V1_1_13") == "1"

    rc: int | None = None
    if behavior == "malformed_stream":
        if is_v1_1_13_env:
            from tests._support.mock_agy_v1_1_13 import _MOCK_MALFORMED_LINES

            for line in _MOCK_MALFORMED_LINES:
                print(line)
            rc = 0
        else:
            print(f"mock AGY: unknown MOCK_AGY_BEHAVIOR={behavior}", file=sys.stderr)
            rc = 2
    elif is_v1_1_13_selector and not is_v1_1_13_env:
        print(
            f"mock AGY: MOCK_AGY_BEHAVIOR={behavior} requires MOCK_AGY_V1_1_13=1",
            file=sys.stderr,
        )
        rc = 2
    elif behavior != "normal" and not is_v1_1_13_selector:
        print(f"mock AGY: unknown MOCK_AGY_BEHAVIOR={behavior}", file=sys.stderr)
        rc = 2

    return rc


def main(argv: list[str] | None = None) -> int:
    parser = _build_argument_parser()
    args = parser.parse_args(argv)

    behavior = os.environ.get("MOCK_AGY_BEHAVIOR", "normal")
    validation_rc = _validate_mock_agy_args(args, behavior)
    if validation_rc is not None:
        return validation_rc

    artifact_dir = Path(os.environ.get("MOCK_AGY_ARTIFACT_DIR", Path.cwd()))
    if os.environ.get("MOCK_AGY_V1_1_13") == "1":
        from tests._support.mock_agy_v1_1_13 import (
            _emit_v1_1_13_stdout,
            drive_real_mcp_round_trips,
        )

        _write_todo_list(artifact_dir)
        _write_prompt_received(artifact_dir, args.prompt)
        submit_output, complete_output = drive_real_mcp_round_trips()
        _emit_v1_1_13_stdout(
            args.model,
            args.output_format,
            artifact_dir,
            submit_output,
            complete_output,
        )
    else:
        _write_todo_list(artifact_dir)
        _write_prompt_received(artifact_dir, args.prompt)
        _write_smoke_test_result_artifact(artifact_dir)
        _write_completion_sentinel(artifact_dir)
        _emit_normal_stdout(args.model, args.output_format)
    return 0


if __name__ == "__main__":
    sys.exit(main())
