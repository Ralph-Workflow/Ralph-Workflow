from __future__ import annotations

import ast
import inspect
from typing import NamedTuple

import pytest


class EntryPoint(NamedTuple):
    module: str
    name: str
    allow_none_paths: tuple[str, ...]


_PUBLIC_ENTRY_POINTS: tuple[EntryPoint, ...] = (
    EntryPoint(
        "ralph.pipeline.auto_integrate",
        "auto_integrate_after_commit",
        allow_none_paths=("disabled", "exception"),
    ),
    EntryPoint(
        "ralph.pipeline.auto_integrate",
        "auto_integrate_on_phase_transition",
        allow_none_paths=("disabled", "exception"),
    ),
    EntryPoint(
        "ralph.pipeline.auto_integrate_recovery",
        "recover_incomplete_integration",
        allow_none_paths=("disabled", "exception", "no_record"),
    ),
    EntryPoint(
        "ralph.pipeline.auto_integrate_rebase_merge",
        "run_rebase_or_merge",
        allow_none_paths=("exception",),
    ),
    EntryPoint(
        "ralph.pipeline.auto_integrate_resolve",
        "endpoint_merge_with_resolution",
        allow_none_paths=("exception",),
    ),
    EntryPoint(
        "ralph.pipeline.auto_integrate_ff",
        "fast_forward_target",
        allow_none_paths=("exception",),
    ),
)


def _load_entry_point(entry: EntryPoint):
    import importlib

    mod = importlib.import_module(entry.module)
    return getattr(mod, entry.name)


def _is_disabled_guard(test: ast.expr) -> bool:

    def _match_disabled_operand(operand: ast.expr) -> bool:
        target = operand
        if isinstance(target, ast.UnaryOp) and isinstance(target.op, ast.Not):
            target = target.operand
        if isinstance(target, ast.Attribute):
            return target.attr == "auto_integrate_enabled"
        if isinstance(target, ast.Name):
            return target.id in {"enabled", "auto_integrate_enabled"}
        return False

    if isinstance(test, ast.BoolOp):
        return any(_match_disabled_operand(value) for value in test.values)
    return _match_disabled_operand(test)


def _is_no_record_guard(test: ast.expr) -> bool:
    return (
        isinstance(test, ast.Compare)
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Is)
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value is None
        and isinstance(test.left, ast.Name)
        and test.left.id == "record"
    )


def _return_node_violates(
    node: ast.Return,
    allow_none_paths: tuple[str, ...],
    parents: dict[int, ast.AST],
) -> bool:
    if node.value is None:
        return True
    if isinstance(node.value, ast.Constant) and node.value.value is None:
        return _check_guard_for_none(node, allow_none_paths, parents)
    return False


def _check_guard_for_none(
    node: ast.Return,
    allow_none_paths: tuple[str, ...],
    parents: dict[int, ast.AST],
) -> bool:
    parent: ast.AST | None = parents.get(id(node))
    while parent is not None:
        if isinstance(parent, ast.If):
            guard = parent.test
            if "disabled" in allow_none_paths and _is_disabled_guard(guard):
                return False
            return not ("no_record" in allow_none_paths and _is_no_record_guard(guard))
        if isinstance(parent, (ast.Try, ast.ExceptHandler)):
            return "exception" not in allow_none_paths
        parent = parents.get(id(parent))
    return True


def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


@pytest.mark.parametrize(
    "entry",
    list(_PUBLIC_ENTRY_POINTS),
    ids=lambda e: f"{e.module.rsplit('.', 1)[-1]}.{e.name}",
)
def test_public_entry_points_never_return_silently(entry: EntryPoint) -> None:
    func = _load_entry_point(entry)
    try:
        source = inspect.getsource(func)
    except (OSError, TypeError) as exc:
        pytest.fail(f"could not read source for {entry.module}.{entry.name}: {exc}")
    tree = ast.parse(source)
    parents = _build_parent_map(tree)
    forbidden: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return):
            continue
        if not _return_node_violates(node, entry.allow_none_paths, parents):
            continue
        forbidden.append(
            f"line {node.lineno}: return {'None' if node.value is None or (isinstance(node.value, ast.Constant) and node.value.value is None) else node.value!r}"
        )
    assert not forbidden, (
        f"public entry point {entry.module}.{entry.name} has forbidden "
        f"silent-skip return(s): {'; '.join(forbidden)}. "
        "Every return None must be inside the disabled path "
        "(``if not auto_integrate_enabled``), the no-record "
        "preamble, or an exception handler -- anything else is "
        "an AC-08 violation."
    )


def test_disabled_path_byte_identity() -> None:
    from ralph.pipeline import auto_integrate

    src = inspect.getsource(auto_integrate)
    assert "auto_integrate_enabled" in src, (
        "auto_integrate.py lost the auto_integrate_enabled check; "
        "the AC-01 disabled path is the ONE bare return the spec "
        "allows and must stay a byte-identical no-op"
    )


def test_synthetic_silent_skip_is_detected() -> None:
    synthetic = '''
def _auto_integrate_with_a_silent_skip(target: str) -> None:
    """Synthetic forbidden shape."""
    if target == "main":
        return None  # noqa: silent skip while enabled
    return None
'''
    tree = ast.parse(synthetic)
    parents = _build_parent_map(tree)
    fn = tree.body[0]
    violations = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Return):
            continue
        if node.value is None:
            violations.append(f"line {node.lineno}: bare return")
        elif isinstance(node.value, ast.Constant) and node.value.value is None:
            parent: ast.AST | None = parents.get(id(node))
            guarded = False
            while parent is not None:
                if isinstance(parent, ast.If):
                    guarded = True
                    break
                parent = parents.get(id(parent))
            if not guarded:
                violations.append(f"line {node.lineno}: unconditional return None")
            if guarded:
                inner_parent: ast.AST | None = parents.get(id(node))
                if (
                    isinstance(inner_parent, ast.If)
                    and not _is_disabled_guard(inner_parent.test)
                    and not _is_no_record_guard(inner_parent.test)
                ):
                    violations.append(
                        f"line {node.lineno}: forbidden return None under a non-disabled guard"
                    )
    assert violations, (
        "the audit did not flag the canonical forbidden shape "
        "`if target == 'main': return None`; the audit itself "
        "is too permissive"
    )


def test_known_silent_skip_promotion_is_recorded() -> None:
    from ralph.pipeline import auto_integrate

    src = inspect.getsource(auto_integrate)
    assert "phase-transition pre-check failed" in src, (
        "auto_integrate.py lost the 'phase-transition pre-check "
        "failed' log; AC-08 expects this surface to be recorded "
        "loudly with the underlying exception"
    )
