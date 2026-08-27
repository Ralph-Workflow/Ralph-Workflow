"""Tests pinning what ``runner.execute_fan_out_sync`` forwards into ``opts``.

The fan-out seam is untyped: ``runner.execute_fan_out_sync`` collects ``**opts``
and ``fan_out.execute_fan_out_sync`` reads the keys back out by string name.
Nothing checks that the two spellings agree, so the seam has two distinct
failure modes and both have bitten this code:

  * a key passed twice -- once explicitly, once through the forward -- raises
    ``TypeError: got multiple values for keyword argument`` at call time; and
  * a key passed under the wrong name is silently dropped, and the feature it
    carries just never happens.

``_run_fan_out_phase`` spelled the monitor-stop callback ``monitor_stop_cb``
while ``fan_out`` reads ``_monitor_stop_cb``, so the fan-out's ``SignalBridge``
never received ``_connectivity_stop`` on the production path. These tests pin
the names at the seam so a rename on either side fails here.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.pipeline import runner as runner_module

if TYPE_CHECKING:
    import pytest


def _captured_opts(monkeypatch: pytest.MonkeyPatch, **call_kwargs: object) -> dict[str, object]:
    """Call the runner seam with a stubbed callee and return the forwarded opts."""
    seen: dict[str, object] = {}

    def _capture(**opts: object) -> object:
        seen.update(opts)
        return MagicMock()

    monkeypatch.setattr(runner_module, "_fan_out_execute_fan_out_sync", _capture)
    call_kwargs.setdefault("effect", MagicMock())
    call_kwargs.setdefault("state", MagicMock())
    call_kwargs.setdefault("display", MagicMock())
    runner_module.execute_fan_out_sync(**call_kwargs)
    return seen


def test_seeds_the_module_globals_as_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no override, the callee sees the runner's current module globals."""
    opts = _captured_opts(monkeypatch)

    assert opts["_install_signal_handlers"] is runner_module.install_signal_handlers
    assert opts["_executor_cls"] is runner_module.SubprocessAgentExecutor
    assert opts["_mcp_factory_cls"] is runner_module.DynamicBindingMcpServerFactory
    assert opts["_run_process_async"] is runner_module.run_process_async
    assert opts["_reducer_reduce"] is runner_module.reducer_reduce


def test_caller_supplied_injectables_win_over_the_module_globals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Naming an injectable must override, not collide with, the default."""
    sentinel = object()

    opts = _captured_opts(
        monkeypatch,
        _install_signal_handlers=sentinel,
        _executor_cls=sentinel,
        _mcp_factory_cls=sentinel,
        _run_process_async=sentinel,
        _reducer_reduce=sentinel,
    )

    assert opts["_install_signal_handlers"] is sentinel
    assert opts["_executor_cls"] is sentinel
    assert opts["_mcp_factory_cls"] is sentinel
    assert opts["_run_process_async"] is sentinel
    assert opts["_reducer_reduce"] is sentinel


def test_reads_the_monitor_stop_callback_under_the_name_fan_out_reads() -> None:
    """``fan_out`` looks up ``_monitor_stop_cb``; the producer must spell it that way.

    Both halves are asserted from source so the test fails on a rename to
    either side, which is the only thing keeping the untyped seam honest.
    """
    consumed = _string_keys_read_from_opts(Path(runner_module.__file__).parent / "fan_out.py")
    produced = _keywords_passed_to_execute_fan_out_sync(Path(runner_module.__file__))

    assert "_monitor_stop_cb" in consumed, "fan_out no longer reads '_monitor_stop_cb'"
    assert "_monitor_stop_cb" in produced, "_run_fan_out_phase no longer passes it"
    assert "monitor_stop_cb" not in produced, (
        "the un-prefixed spelling is silently dropped by fan_out"
    )


def _string_keys_read_from_opts(path: Path) -> set[str]:
    """Return every literal key read via ``opts.get(...)`` / ``opts[...]``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "opts"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            keys.add(node.args[0].value)
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "opts"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            keys.add(node.slice.value)
    return keys


def _keywords_passed_to_execute_fan_out_sync(path: Path) -> set[str]:
    """Return keyword names passed to ``execute_fan_out_sync(...)`` in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if called != "execute_fan_out_sync":
            continue
        names.update(keyword.arg for keyword in node.keywords if keyword.arg is not None)
    return names
