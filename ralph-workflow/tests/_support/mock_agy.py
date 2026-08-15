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
* ``MOCK_AGY_V1_1_13`` - when ``1``, emit the measured AGY v1.1.13 wire
  vocabulary (``system_message`` bodiless steps, ``invoke_subagent``
  step-level ``tool_name``, ``call_mcp_tool`` frames) and drive the real
  Ralph MCP endpoint named by ``RALPH_MCP_ENDPOINT`` with stdlib-only
  JSON-RPC round trips (``initialize`` + ``tools/call``
  ``ralph_submit_md_artifact`` + ``tools/call`` ``declare_complete``) so
  the smoke harness grades artifact submission, the completion sentinel,
  and the wire ledger through the same server path the live binary uses.
  The mock never imports Ralph helpers: the receipt, sentinel, and wire
  records are produced solely by the server's handlers. Unset (or ``0``)
  keeps the default v1.1.10-style output byte-compatible.

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


def _smoke_test_result_markdown() -> str:
    """Return the fallback smoke_test_result markdown the mock authors."""
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
    """Simulate the mock agent's successful ``declare_complete`` MCP call."""
    run_id = os.environ.get(RUN_ID_ENV)
    if not run_id:
        return
    sentinel = artifact_dir / ".agent" / f"completion_seen_{run_id}.json"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(f'{{"run_id": "{run_id}"}}', encoding="utf-8")


# Deliberately Ralph-free (S-4, Evidence Provenance plan): no ``ralph_*`` /
# ``mcp__ralph__*`` / ``call_mcp_tool`` name in this list, so this mock's
# evidence ceiling always grades below WIRE (see
# ``test_mock_agy_evidence_ceiling_grades_below_wire`` in
# tests/test_smoke_agy_end_to_end.py, which imports this constant directly
# rather than duplicating it, so the two cannot drift apart). "The mock
# cannot prove any of this" (product brief) is therefore an enforced
# invariant, not just documentation.
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


# --- 2026-08-05 measured-baseline scenario (Evidence Provenance plan, S-1) ---
#
# Reconstructed from the product brief's own documented measurements of the
# 2026-08-05 baseline run (see ".agent/PRODUCT_CRITERIA.md"'s "Measured
# baseline" section and Workstream A), NOT a new live capture and NOT a
# byte-for-byte replay of the original
# ".agent/raw/agy_gemini-3.6-flash-low.log" (16 JSON frames: 1 init, 14
# step_update, 1 result -- that log was never committed to this repo; see
# tests/display/_fixtures/agy_wire_provenance.md for the provenance note
# on this reconstruction). Reproduces the measured *shape*: an ``init``
# frame advertising tools with zero ``ralph_*`` / ``call_mcp_tool`` route
# (so ``transport_evidence_ceiling`` grades TRANSCRIPT, not WIRE), exactly
# 14 ``step_update`` frames (matching the brief's own frame count), and a
# single closing ``result`` frame -- no ``tools/call`` wire-ledger activity
# and no ``declare_complete`` call anywhere in the stream, since the agent
# never reached Ralph's MCP server on that run.
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
    """Return the 16 stream-json lines for the reconstructed 2026-08-05 shape.

    1 ``init`` frame (no ``ralph_*`` / ``call_mcp_tool`` route advertised) +
    14 ``step_update`` frames + 1 closing ``result`` frame. The
    ``step_update`` sequence carries the measured bodiless-step vocabulary
    (``user_input`` / ``unknown`` / ``checkpoint``), a ``write_to_file``
    tool ACTIVE/DONE pair (so the parser classifies a real ``tool_use``
    event -- the transcript's only authoritative tool-activity signal,
    since no ``tools/call`` ever reached Ralph's MCP server), and two
    ``agent_response`` text_delta pairs whose DONE frames carry ``usage``,
    matching the brief's own description of the model's transcript text
    ("saved the smoke test result artifact to
    ``.agent/tmp/smoke_test_result.md`` as instructed for fallback").
    """
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
                # No `output` key: the measured live capture showed
                # write_to_file produces no output on DONE.
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
    """Return the fallback smoke_test_result markdown the 2026-08-05 run wrote directly.

    The agent never called ``ralph_submit_md_artifact`` (no route to it
    existed under AGY's dispatcher-free tool list), so per the smoke
    prompt's documented fallback instruction it wrote this document
    straight to ``.agent/tmp/smoke_test_result.md`` for the harness to
    promote -- a real, on-disk artifact, but not one attributable to a
    witnessed ``tools/call``.
    """
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

    if os.environ.get("MOCK_AGY_V1_1_13") == "1":
        # Measured v1.1.13 vocabulary path: the mock still writes the todo
        # list it is asked to create, but the smoke_test_result artifact and
        # the completion sentinel are produced ONLY by the real MCP server
        # handlers via ``drive_real_mcp_round_trips`` -- no direct fallback
        # file or sentinel write happens in this mode, so the harness's
        # WORKSPACE_EFFECT fallback cannot mask a failed round trip.
        # Lazy import keeps the default (flag-unset) subprocess path free
        # of the v1.1.13 module and its urllib transport; see
        # tests/_support/mock_agy_v1_1_13.py.
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
        return 0

    _write_todo_list(artifact_dir)
    _write_prompt_received(artifact_dir, args.prompt)
    _write_smoke_test_result_artifact(artifact_dir)
    _write_completion_sentinel(artifact_dir)
    _emit_normal_stdout(args.model, args.output_format)
    return 0


if __name__ == "__main__":
    sys.exit(main())
