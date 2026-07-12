"""Regression tests proving agent spawn sites detach the controlling TTY.

When an interactive agent child inherits Ralph's controlling-terminal
stdin it can put the shared TTY into raw mode and steal keystrokes.
Ralph feeds agents via a prompt FILE / argv, never via stdin, so the
two agent-spawn sites must request ``stdin=subprocess.DEVNULL`` and
keep ``start_new_session=True``.

The actual ``SpawnOptions(...)`` block at each call site is small
(about 8 kwargs) and deterministic. We pin it with two complementary
tests per site:

  1. **AST inspection** of the spawn block -- confirms the file
     contains a ``SpawnOptions(...)`` call whose keyword arguments
     include ``stdin`` set to a non-None value and ``start_new_session``
     set to ``True``. This is structural and survives whatever happens
     to the rest of the runtime.

  2. **Source-text guard** for the specific kwargs the plan pins:
     ``stdin=subprocess.DEVNULL`` (or the file-level alias
     ``stdin=_DEVNULL``) is present; ``stdin=None`` is absent;
     ``start_new_session=True`` is present.

Both test layers are needed: the AST test proves the SpawnOptions
block shape is correct at parse-time (catches e.g. accidentally
re-introducing ``stdin=None`` because of a refactor that drops the
``stdin=`` kwarg entirely), and the source-text test pins the
exact literal strings the audit will look for.
"""

from __future__ import annotations

import ast
import pathlib

from ralph.agents import subprocess_executor as _executor_module
from ralph.agents.invoke import _process_reader


def _find_spawn_options_calls(tree: ast.AST) -> list[ast.Call]:
    """Return every ``SpawnOptions(...)`` call in ``tree``."""
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _is_spawn_options(n)]


def _is_spawn_options(node: ast.Call) -> bool:
    func = node.func
    # Bare ``SpawnOptions(...)`` (the test targets).
    return isinstance(func, ast.Name) and func.id == "SpawnOptions"


def _kwargs(call: ast.Call) -> dict[str, ast.AST]:
    """Return the keyword arguments of a Call node, keyed by name."""
    return {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}


def test_invoke_reader_spawn_options_block_passes_non_none_stdin() -> None:
    """``SpawnOptions(...)`` at the invoke reader carries a non-None stdin value.

    Without this, the subprocess inherits Ralph's controlling-terminal
    stdin and can put the shared TTY into raw mode.
    """
    src_path = _process_reader.__file__
    assert src_path is not None
    text = pathlib.Path(src_path).read_text(encoding="utf-8")
    tree = ast.parse(text)

    # Find the SpawnOptions block inside ``_run_subprocess_and_read_lines``.
    target = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_run_subprocess_and_read_lines"
        ),
        None,
    )
    assert target is not None, "_run_subprocess_and_read_lines not found"

    spawn_calls = _find_spawn_options_calls(target)
    assert spawn_calls, "no SpawnOptions(...) call found in _run_subprocess_and_read_lines"

    kwargs = _kwargs(spawn_calls[0])
    stdin_value = kwargs.get("stdin")
    assert stdin_value is not None, (
        "SpawnOptions.stdin must be set (not None -- None means INHERIT the "
        "controlling terminal); found no stdin= kwarg"
    )
    assert not (isinstance(stdin_value, ast.Constant) and stdin_value.value is None), (
        "SpawnOptions.stdin must NOT be None (INHERIT would let the agent "
        "steal raw-mode from Ralph's terminal)"
    )
    sns = kwargs.get("start_new_session")
    assert sns is not None, "SpawnOptions.start_new_session must be set"
    assert isinstance(sns, ast.Constant) and sns.value is True, (
        f"SpawnOptions.start_new_session must be True; got {ast.dump(sns)!r}"
    )


def test_invoke_reader_source_carries_devnull_literal() -> None:
    """Source-pin: ``stdin=subprocess.DEVNULL`` appears; ``stdin=None,`` absent."""
    src_path = _process_reader.__file__
    assert src_path is not None
    text = pathlib.Path(src_path).read_text(encoding="utf-8")

    assert "stdin=subprocess.DEVNULL" in text, (
        f"invoke reader source must pin stdin=subprocess.DEVNULL; file={src_path!r}"
    )
    # ``stdin=None,`` with a trailing comma is the leak pattern; the literal
    # is unique enough to grep (no other code in the reader uses it).
    assert "stdin=None," not in text, (
        f"invoke reader source must not carry stdin=None, (INHERIT = leaks TTY); "
        f"file={src_path!r}"
    )


def test_subprocess_executor_spawn_options_block_passes_non_none_stdin() -> None:
    """``SpawnOptions(...)`` at the executor carries a non-None stdin value."""
    src_path = _executor_module.__file__
    assert src_path is not None
    text = pathlib.Path(src_path).read_text(encoding="utf-8")
    tree = ast.parse(text)

    # The executor's SpawnOptions block lives inside ``SubprocessAgentExecutor.run``.
    cls = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "SubprocessAgentExecutor"
    )
    run_method = next(
        n
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "run"
    )
    spawn_calls = _find_spawn_options_calls(run_method)
    assert spawn_calls, (
        "no SpawnOptions(...) call found in SubprocessAgentExecutor.run"
    )

    kwargs = _kwargs(spawn_calls[0])
    stdin_value = kwargs.get("stdin")
    assert stdin_value is not None, (
        "SpawnOptions.stdin must be set (not None -- INHERIT = leaks TTY)"
    )
    assert not (isinstance(stdin_value, ast.Constant) and stdin_value.value is None), (
        "SpawnOptions.stdin must NOT be None"
    )
    sns = kwargs.get("start_new_session")
    assert sns is not None, "SpawnOptions.start_new_session must be set"
    assert isinstance(sns, ast.Constant) and sns.value is True, (
        f"SpawnOptions.start_new_session must be True; got {ast.dump(sns)!r}"
    )


def test_subprocess_executor_source_carries_devnull_literal() -> None:
    """Source-pin: ``stdin=_DEVNULL`` (the file's local alias) appears; ``start_new_session=True`` kept."""
    src_path = _executor_module.__file__
    assert src_path is not None
    text = pathlib.Path(src_path).read_text(encoding="utf-8")

    assert "stdin=_DEVNULL" in text, (
        f"subprocess_executor source must pin stdin=_DEVNULL; file={src_path!r}"
    )
    assert "start_new_session=True" in text, (
        f"subprocess_executor source must keep start_new_session=True; "
        f"file={src_path!r}"
    )
    assert "from subprocess import DEVNULL as _DEVNULL" in text, (
        f"subprocess_executor source must alias DEVNULL as _DEVNULL "
        f"(matching the PIPE / STDOUT aliases); file={src_path!r}"
    )
