"""Tests for the deduped self-source AST parse in ``ralph.recovery.controller``.

The recovery controller module reads+parses its own source at import
time in TWO separate invariant functions:

  - ``_assert_two_state_invariant``
  - ``_assert_never_exit_invariant``

This is wasteful: each call re-reads the same file from disk and
re-parses the same ``ast.Module``. The module now exposes a shared
helper, ``_controller_source_tree(parse_fn, read_fn) -> tuple[str, ast.Module]``,
that memoizes the parsed tree so the two invariant functions only
trigger ONE read+parse per process import.

These tests are black-box on the public module surface:
- ``_controller_source_tree`` returns the cached tuple.
- A second call does not invoke the injected ``parse_fn`` again.
- ``_reset_controller_source_tree_cache`` forces a fresh parse.

All tests are <1s and use injected fakes (no real disk reads of
arbitrary files, no real subprocess).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.recovery import controller

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest


def test_controller_source_tree_caches_parse_result() -> None:
    """Calling ``_controller_source_tree`` twice invokes the parser ONCE.

    This proves the dedup: both ``_assert_two_state_invariant`` and
    ``_assert_never_exit_invariant`` end up triggering only one
    ``parse_fn`` call each invocation.
    """
    controller._reset_controller_source_tree_cache()
    real_source = Path(controller.__file__).read_text(encoding="utf-8")
    parse_calls = {"count": 0}

    def counting_parse(source: str) -> ast.Module:
        parse_calls["count"] += 1
        return ast.parse(source)

    first_source, first_tree = controller._controller_source_tree(
        parse_fn=counting_parse,
        read_fn=lambda: real_source,
    )

    second_source, second_tree = controller._controller_source_tree(
        parse_fn=counting_parse,
        read_fn=lambda: real_source,
    )

    assert parse_calls["count"] == 1, (
        f"parse_fn must be called exactly once across two"
        f" _controller_source_tree invocations, got {parse_calls['count']}"
    )
    assert first_source == real_source
    assert second_source == real_source
    assert isinstance(first_tree, ast.Module)
    assert second_tree is first_tree, (
        "second call must return the cached ast.Module (identity check)"
    )


def test_reset_controller_source_tree_cache_forces_fresh_parse() -> None:
    """``_reset_controller_source_tree_cache`` invalidates the cache.

    After reset, the next ``_controller_source_tree`` call re-parses
    using the supplied ``parse_fn``. This is the seam tests need to
    avoid leaking state across test cases.
    """
    real_source = Path(controller.__file__).read_text(encoding="utf-8")
    parse_calls = {"count": 0}

    def counting_parse(source: str) -> ast.Module:
        parse_calls["count"] += 1
        return ast.parse(source)

    controller._reset_controller_source_tree_cache()
    controller._controller_source_tree(
        parse_fn=counting_parse,
        read_fn=lambda: real_source,
    )
    assert parse_calls["count"] == 1

    controller._reset_controller_source_tree_cache()
    controller._controller_source_tree(
        parse_fn=counting_parse,
        read_fn=lambda: real_source,
    )
    assert parse_calls["count"] == 2, (
        "after _reset_controller_source_tree_cache, parse_fn must run again"
    )


def test_controller_source_tree_uses_injected_reader() -> None:
    """The injected ``read_fn`` is the source of truth.

    The helper does NOT call ``Path(__file__).read_text()`` directly;
    the caller controls the read via the injected ``read_fn``. This
    is the seam that keeps the helper testable and prevents ambient
    I/O inside core logic (AGENTS.md 'Non-negotiables').
    """
    expected_source = "print('hello world')\n"
    read_calls = {"count": 0}

    def counting_read() -> str:
        read_calls["count"] += 1
        return expected_source

    def parse_fn(source: str) -> ast.Module:
        return ast.parse(source)

    controller._reset_controller_source_tree_cache()
    source, tree = controller._controller_source_tree(
        parse_fn=parse_fn,
        read_fn=counting_read,
    )

    assert source == expected_source
    assert read_calls["count"] == 1
    assert isinstance(tree, ast.Module)
    assert ast.unparse(tree).strip() == "print('hello world')"


def test_controller_source_tree_propagates_syntax_error() -> None:
    """A SyntaxError raised by ``parse_fn`` is re-raised with the
    controller's own RuntimeError message so the existing import-time
    invariant contract is preserved verbatim (tests/recovery/test_two_state_invariant.py
    asserts the module-level invocations still raise RuntimeError on
    broken source).
    """
    controller._reset_controller_source_tree_cache()

    def bad_parse(source: str) -> ast.Module:
        raise SyntaxError("test syntax failure")

    try:
        controller._controller_source_tree(
            parse_fn=bad_parse,
            read_fn=lambda: "def broken(:\n",
        )
    except RuntimeError as exc:
        assert "failed to parse" in str(exc).lower(), (
            f"RuntimeError message must mention parse failure, got: {exc}"
        )
    else:
        raise AssertionError("expected RuntimeError when parse_fn raises SyntaxError")


def test_module_level_invariant_calls_still_use_shared_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two module-level invariant invocations share the cached parse.

    After resetting the cache, calling both module-level invariants
    in sequence triggers the parser exactly ONCE (the second call is
    served from the cache). This is the perf claim of the wt-024 P1
    change: import-time work is halved.

    Uses ``monkeypatch.setattr`` to swap in a counting wrapper for
    ``_controller_source_tree`` so no type-ignore comment is needed.
    """
    controller._reset_controller_source_tree_cache()
    real_source = Path(controller.__file__).read_text(encoding="utf-8")

    parse_calls = {"count": 0}

    def counting_parse(source: str) -> ast.Module:
        parse_calls["count"] += 1
        return ast.parse(source)

    # Replace the helper temporarily with a wrapper that uses our
    # counting parse_fn. The two invariant functions share the
    # module-level cache slot, so the second call must be served
    # from cache.
    original_helper = controller._controller_source_tree

    def counting_helper(
        *,
        parse_fn: Callable[[str], ast.Module],
        read_fn: Callable[[], str],
    ) -> tuple[str, ast.Module]:
        return original_helper(parse_fn=counting_parse, read_fn=lambda: real_source)

    monkeypatch.setattr(controller, "_controller_source_tree", counting_helper)
    try:
        controller._assert_two_state_invariant()
        controller._assert_never_exit_invariant()
    finally:
        controller._reset_controller_source_tree_cache()

    assert parse_calls["count"] == 1, (
        f"both module-level invariants must share ONE parse via the cache;"
        f" expected parse_calls['count']==1, got {parse_calls['count']}"
    )
