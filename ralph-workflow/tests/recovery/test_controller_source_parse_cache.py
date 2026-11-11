from __future__ import annotations

import ast
import inspect
from typing import TYPE_CHECKING

from ralph.recovery import controller

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest


def test_controller_source_tree_caches_parse_result() -> None:
    controller._reset_controller_source_tree_cache()
    real_source = inspect.getsource(controller)
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
    real_source = inspect.getsource(controller)
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
    controller._reset_controller_source_tree_cache()
    real_source = inspect.getsource(controller)

    parse_calls = {"count": 0}

    def counting_parse(source: str) -> ast.Module:
        parse_calls["count"] += 1
        return ast.parse(source)

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
