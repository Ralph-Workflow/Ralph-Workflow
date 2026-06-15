"""Deterministic simulator for the AGY v1.0.8 --print wire format.

This module is importable and runnable as ``python -m tests._support.mock_agy``.
It is used by the Ralph smoke harness when ``RALPH_AGY_BINARY`` points at it
(typically via the ``mock_agy.sh`` wrapper), and by black-box subprocess tests
that pin the simulated contract.

Controlled by environment variables:

* ``MOCK_AGY_BEHAVIOR`` - ``normal`` (default), ``quota_exhausted``, or
  ``invalid_model``.
* ``MOCK_AGY_ARTIFACT_DIR`` - directory where ``.agent/artifacts/`` and
  ``tmp/`` are written. Defaults to the current working directory.

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
ARTIFACT_RELPATH = ".agent/artifacts/smoke_test_result.json"
PROMPT_RECEIVED_RELPATH = ".agent/artifacts/.mock_agy_prompt.txt"


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agy")
    parser.add_argument("--print", "-p", action="store_true", dest="print_mode")
    parser.add_argument("--dangerously-skip-permissions", action="store_true")
    parser.add_argument("--model", default=None)
    parser.add_argument("--add-dir", action="append", default=[])
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
    artifact = {
        "name": "smoke_test_result",
        "type": "smoke_test_result",
        "content": {
            "status": "passed",
            "output_file": OUTPUT_FILE_RELPATH,
            "observed_working": [
                "created todo-list.js",
                "wrote smoke_test_result artifact",
            ],
            "observed_breaks": [],
            "headless_guide_checks": [
                "tool activity",
                "parser events",
                "tmp artifact creation",
            ],
            "summary": "AGY smoke test completed successfully",
        },
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "metadata": {},
    }
    artifact_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return artifact_path


def _emit_normal_stdout(model: str | None, prompt: str | None) -> None:
    print("AGY mock: starting print mode")
    print(f"Model: {model or '(default)'}")
    prompt_len = len(prompt) if prompt else 0
    print(f"Prompt received ({prompt_len} chars)")
    print("Workspace add-dir count: 1")
    print("Creating todo-list.js ...")
    print("Writing smoke_test_result artifact ...")
    sanitized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", model or "default").strip("-")
    session_id = f"interactive-agy-smoke-{sanitized}"
    print(f"Session ID: {session_id}")
    print("Task declared complete:")


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
    _emit_normal_stdout(args.model, args.prompt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
