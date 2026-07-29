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

The simulator honors the flag set measured from the real binary:
``--print``/``-p``, ``--dangerously-skip-permissions``, ``--model``,
``--add-dir``, ``--print-timeout``, ``--conversation``, ``--sandbox``, and a
single positional prompt argument.
"""

from __future__ import annotations

import argparse
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
        # Legacy fixtures remain accepted by the deterministic simulator only.
        "Gemini 3.5 Flash (Medium)",
        "Gemini 3.5 Flash (High)",
        "Gemini 3.5 Flash (Low)",
        "Gemini 3.1 Pro (Low)",
        "Gemini 3.1 Pro (High)",
        "Claude Sonnet 4.6 (Thinking)",
        "Claude Opus 4.6 (Thinking)",
        "GPT-OSS 120B (Medium)",
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
    parser.add_argument("--dangerously-skip-permissions", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument("--add-dir", action="append", default=[])
    parser.add_argument("--effort", choices=("low", "medium", "high"), default=None)
    parser.add_argument("--print-timeout", default=None)
    parser.add_argument("--conversation", default=None)
    parser.add_argument("--sandbox", action="store_true")
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


def _emit_normal_stdout(model: str | None, prompt: str | None) -> None:
    print("I will create the todo list implementation.")
    print("Using module.exports for CommonJS compatibility.")
    print("Adding add, list, complete, remove methods.")
    # The ``[plain] tool: NAME`` convention is the GenericParser contract for
    # tool-use events. The AGY smoke harness requires authoritative parser
    # / transport evidence for tool activity — not the
    # ``headless_guide_checks`` field in the model-authored artifact — so the
    # mock emits a real tool-use line that the parser classifies as
    # ``type='tool_use'`` (see
    # ``ralph-workflow/ralph/agents/parsers/generic.py::_classify_plaintext_tool_line``).
    print("[plain] tool: createTodoList")
    print("File created at tmp/interactive-agy-smoke/todo-list.js.")
    print("Writing smoke_test_result artifact ...")
    sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", model or "default").strip("-")
    session_id = f"interactive-agy-smoke-{sanitized}"
    print(f"Session ID: {session_id}")
    # The mock omits a spoofable transcript marker. ``main`` writes the same
    # durable sentinel that the real ``declare_complete`` tool would produce
    # in this no-HMAC test harness.


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
    _emit_normal_stdout(args.model, args.prompt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
