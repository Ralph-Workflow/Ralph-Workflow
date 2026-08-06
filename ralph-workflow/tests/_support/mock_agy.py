"""Deterministic simulator for the measured AGY v1.1.8 CLI surface.

This module is importable and runnable as ``python -m tests._support.mock_agy``.
It is used by the Ralph smoke harness when ``RALPH_AGY_BINARY`` points at it
(typically via the ``mock_agy.sh`` wrapper), and by black-box subprocess tests
that pin the simulated contract.

Controlled by environment variables:

* ``MOCK_AGY_BEHAVIOR`` - ``normal`` (default), ``quota_exhausted``, or
  ``invalid_model``.
* ``MOCK_AGY_ARTIFACT_DIR`` - directory where ``.agent/tmp/``,
  ``.agent/artifacts/``, the completion sentinel, and ``tmp/`` are written.
  Defaults to the current working directory.
* ``MOCK_AGY_SUBAGENT`` - when ``1``, emit one subagent tool dispatch/result.

The simulator honors the flag set measured from the real binary:
``--print``/``-p``, ``--dangerously-skip-permissions``, ``--model``,
``--add-dir``, ``--print-timeout``, ``--conversation``, ``--sandbox``, and a
single positional prompt argument.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

CANONICAL_MODELS: frozenset[str] = frozenset(
    {
        "gemini-3.6-flash-high",
        "gemini-3.6-flash-medium",
        "gemini-3.6-flash-low",
        "gemini-3.5-flash-high",
        "gemini-3.5-flash-medium",
        "gemini-3.5-flash-low",
        "gemini-3.1-pro-high",
        "gemini-3.1-pro-low",
        "claude-sonnet-4-6",
        "claude-opus-4-6-thinking",
        "gpt-oss-120b-medium",
    }
)

OUTPUT_FILE_RELPATH = "tmp/interactive-agy-smoke/todo-list.js"
# The mock authors the fallback Markdown document that
# ``ralph.mcp.artifacts.canonical_submit.promote_fallback_artifact`` consumes
# and promotes to the canonical ``.agent/artifacts/smoke_test_result.md``
# artifact plus a durable submission receipt.
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
    # v1.1.10 flags
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


def _write_smoke_test_result_artifact(artifact_dir: Path) -> Path:
    artifact_path = artifact_dir / ARTIFACT_RELPATH
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
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
        "- [HG-3] tmp artifact creation\n",
        encoding="utf-8",
    )
    return artifact_path


def _write_completion_sentinel(artifact_dir: Path) -> None:
    """Simulate the mock agent's successful ``declare_complete`` MCP call."""
    run_id = os.environ.get(RUN_ID_ENV)
    if not run_id:
        return
    sentinel = artifact_dir / ".agent" / f"completion_seen_{run_id}.json"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(f'{{"run_id": "{run_id}"}}', encoding="utf-8")


def _emit_normal_stdout(model: str | None, output_format: str = "text") -> None:
    """Emit output based on output_format (stream-json, json, or text).

    The stream-json branch mirrors the frame shapes measured against the
    live v1.1.10 binary (see ``tests/display/_fixtures/agy_wire_provenance.md``):
    ``conversation_id`` and ``step_index`` on every ``step_update``;
    ``user_input`` / ``unknown`` / ``checkpoint`` steps; ``tool_name``
    alongside ``tool_info``; ``tool_info.parameters``; a tool (``readSpec``)
    whose DONE frame has no ``output``; a genuinely incremental ACTIVE/DONE
    ``text_delta`` pair; and a ``result`` carrying ``response``,
    ``duration_seconds``, ``num_turns``, and ``usage``.
    """
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
                "tools": [
                    "ask_permission",
                    "read_file",
                    "write_to_file",
                    "view_file",
                    "define_subagent",
                    "invoke_subagent",
                    "manage_subagents",
                ],
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
                # No `output` key: the measured live capture showed many
                # tools (e.g. write_to_file) produce no output on DONE.
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
        # Two subagents sharing one step_index, matching the measured live
        # multi-subagent capture: conversation_id/log_uri are added only on
        # DONE, never on ACTIVE.
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


def main(argv: list[str] | None = None) -> int:
    parser = _build_argument_parser()
    args = parser.parse_args(argv)

    if not args.print_mode:
        print("mock AGY: --print is required", file=sys.stderr)
        return 2

    behavior = os.environ.get("MOCK_AGY_BEHAVIOR", "normal")
    artifact_dir = Path(os.environ.get("MOCK_AGY_ARTIFACT_DIR", Path.cwd()))

    if behavior in {"quota_exhausted", "invalid_model"}:
        return 0

    if behavior != "normal":
        print(f"mock AGY: unknown MOCK_AGY_BEHAVIOR={behavior}", file=sys.stderr)
        return 2

    if args.model is not None and args.model not in CANONICAL_MODELS:
        return 0

    _write_todo_list(artifact_dir)
    _write_prompt_received(artifact_dir, args.prompt)
    _write_smoke_test_result_artifact(artifact_dir)
    _write_completion_sentinel(artifact_dir)
    _emit_normal_stdout(args.model, args.output_format)
    return 0


if __name__ == "__main__":
    sys.exit(main())
