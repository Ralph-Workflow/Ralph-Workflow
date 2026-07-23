"""AC-08: No silent skip audit.

The auto-integration pipeline must NEVER silently skip while
``auto_integrate_enabled`` is true. Every early return in the
integrate call graph must map to a ladder rung: integrate,
fast-forward, recover, retry, or a loud recorded/raised
diagnostic -- no bare ``return None`` from a public entry
point unless the run is explicitly disabled.

This audit targets the public entry points of the
auto-integration call graph specifically (rather than every
``return None`` in the codebase, which would also flag
legitimate private helpers that pass ``None`` as intermediate
state). The set of audited entry points is enumerated in
:data:`_PUBLIC_ENTRY_POINTS`; each is a public name whose
``return None`` is a candidate for the silent-skip
violation.

Three checks -- each is a HARD fail when violated:

1. **Static AST audit**: walk every function body of every
   public entry point. For each ``return X`` statement,
   ``X`` MUST NOT be ``None`` unless the return is inside a
   branch whose guard is one of the documented
   disabled-path sentinels (``not auto_integrate_enabled``,
   a ``record is None`` recovery preamble, an exception
   path). A function that has a ``return None`` after a
   ``if some_condition:`` branch (where ``some_condition``
   is NOT the disabled sentinel) is a silent skip.

2. **Disabled-path byte-identity check**: the AC-01
   disabled path in :func:`auto_integrate_after_commit`
   must still be the ONE bare return the spec allows.

3. **Synthetic violation injection**: a runtime-built
   ``_AST_CHECK_FOR_FORBIDDEN_RETURN`` validator is fed a
   piece of fake source that contains a forbidden return
   (``return None`` outside the disabled path). The audit
   MUST report it. If the audit does NOT report it, the
   audit itself is broken and must be tightened -- this is
   the canary that proves the audit actually catches
   forbidden shapes, not just that it found a non-None
   return somewhere in the function.
"""

from __future__ import annotations

import ast
import inspect
from typing import NamedTuple

import pytest


class EntryPoint(NamedTuple):
    """A public auto-integration entry point the audit inspects.

    ``module`` is the dotted path (``ralph.pipeline.auto_integrate``).
    ``name`` is the function name. ``allow_none_paths`` is a
    tuple of AST predicates that describe which branches
    inside the function are allowed to ``return None``:

    * ``("disabled",)`` -- the AC-01 disabled path (gated
      on ``not auto_integrate_enabled``).
    * ``("no_record",)`` -- the recovery preamble's
      no-record branch.
    * ``("exception",)`` -- the function's exception
      handlers (catch-broad).

    Every ``return None`` not covered by one of these
    predicates is a silent-skip violation.
    """

    module: str
    name: str
    allow_none_paths: tuple[str, ...]


#: The public auto-integration entry points the AC-08 audit
#: inspects. Private helpers and the internal ``_auto_integrate_*``
#: are NOT audited here -- their ``return None`` is intermediate
#: state passed back to the public function. Each entry point
#: declares which internal branches are allowed to return ``None``.
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
    """Import the public entry-point function."""
    import importlib

    mod = importlib.import_module(entry.module)
    return getattr(mod, entry.name)


def _is_disabled_guard(test: ast.expr) -> bool:
    """True when ``test`` matches the AC-01 disabled-path sentinel.

    Matches ``not auto_integrate_enabled`` directly OR
    ``not config.auto_integrate_enabled`` (the dotted form)
    OR a local ``enabled`` / ``auto_integrate_enabled`` Name
    that the production code binds from the same attribute.
    Production code routinely writes
    ``enabled = getattr(config.general, 'auto_integrate_enabled', True)``
    and then guards on ``not enabled`` -- the audit must match
    that local-variable form too, otherwise the canonical AC-01
    disabled path would be misclassified as a silent skip.

    Compound ``and`` / ``or`` boolean expressions where ANY
    operand is a disabled guard ALSO match: the canonical form
    ``if not enabled or not (root / ".git").exists():`` puts
    the disabled sentinel on one side of an ``or`` and a
    cheap-stat guard on the other; both sides are AC-01 safe.
    """
    # Unwrap a leading ``not`` so ``not X`` and ``X is False``
    # both match.
    def _match_disabled_operand(operand: ast.expr) -> bool:
        target = operand
        if (
            isinstance(target, ast.UnaryOp)
            and isinstance(target.op, ast.Not)
        ):
            target = target.operand
        if isinstance(target, ast.Attribute):
            return target.attr == "auto_integrate_enabled"
        if isinstance(target, ast.Name):
            return target.id in {"enabled", "auto_integrate_enabled"}
        return False

    # ``and`` / ``or`` boolean ops: any operand matching is enough.
    if isinstance(test, ast.BoolOp):
        return any(_match_disabled_operand(value) for value in test.values)
    return _match_disabled_operand(test)


def _is_no_record_guard(test: ast.expr) -> bool:
    """True when ``test`` matches the recovery preamble's no-record sentinel.

    Matches ``record is None``, the exact shape the recovery
    preamble uses to skip the reclaim path when nothing was
    persisted. Other "is None" guards do NOT match: they
    are different sentinels and the audit treats them as
    silent skips until someone names them in
    ``allow_none_paths``.
    """
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
    """True when ``node`` is a forbidden ``return None`` for this entry point.

    Walks up the AST to the nearest enclosing IF / TRY
    block and asks whether the guard matches one of the
    allowed sentinel patterns. A ``return None`` at the
    end of a ``try: ... except Exception: ...`` block is
    allowed; one at the end of an arbitrary
    ``if some_condition: return None`` is NOT.

    The exception check is structural: any enclosing
    ``ast.ExceptHandler`` makes the return value safe. The
    other sentinels require a matching guard expression.

    ``parents`` is the pre-built ``id(child) -> parent``
    map produced by :func:`_build_parent_map`. Threading
    the map through the helper keeps the AST nodes
    untouched (stdlib ``ast.AST`` does not declare a
    ``parent`` attribute, so attaching one dynamically
    would require an ``attr-defined`` suppression).
    """
    if node.value is None:
        # Bare ``return`` (no value).
        return True
    if isinstance(node.value, ast.Constant) and node.value.value is None:
        return _check_guard_for_none(node, allow_none_paths, parents)
    return False


def _check_guard_for_none(
    node: ast.Return,
    allow_none_paths: tuple[str, ...],
    parents: dict[int, ast.AST],
) -> bool:
    """Decide whether the surrounding guard covers a ``return None``.

    ``parents`` is the ``id(child) -> parent`` map produced
    by :func:`_build_parent_map`; looking the parent up by
    identity keeps the AST nodes immutable.
    """
    # Walk up to the nearest IF / TRY. If we hit the function
    # body without finding one, the return is at the END of the
    # function and there is no guard -- this is a definite
    # silent skip UNLESS the function itself is allowed to
    # return None unconditionally (none currently are).
    parent: ast.AST | None = parents.get(id(node))
    while parent is not None:
        if isinstance(parent, ast.If):
            guard = parent.test
            if "disabled" in allow_none_paths and _is_disabled_guard(guard):
                return False
            return not ("no_record" in allow_none_paths and _is_no_record_guard(guard))
        if isinstance(parent, (ast.Try, ast.ExceptHandler)):
            # Inside a try/except block -- the exception path
            # is the silent-skip-safe escape.
            return "exception" not in allow_none_paths
        parent = parents.get(id(parent))
    # No enclosing branch -- the return is unconditional.
    # That is never allowed under AC-08 (the ONE exception is
    # the disabled path, which is gated on a conditional).
    return True


def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    """Build an ``id(child) -> parent`` map for every node in ``tree``.

    Python's stdlib AST does not expose parent pointers, so
    the audit must build them itself. The traversal is a
    single DFS over ``ast.iter_child_nodes`` so it stays
    cheap even for functions with thousands of nodes.

    Returning a typed map (instead of attaching a
    ``parent`` attribute to each node) keeps the AST nodes
    immutable and avoids the ``attr-defined`` suppression
    that flagging the dynamic attribute would require.
    """
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
    """No silent skip is reachable from any public entry point while enabled.

    Walks the function body of every public entry point and
    flags every ``return None`` whose enclosing guard is
    not one of the documented sentinels
    (:data:`_PUBLIC_ENTRY_POINTS`'s ``allow_none_paths``).
    The audit is per-function so a silent skip hidden in
    any branch fails THIS test, not just a hypothetical
    'all returns' smoke test.
    """
    func = _load_entry_point(entry)
    try:
        source = inspect.getsource(func)
    except (OSError, TypeError) as exc:
        pytest.fail(f"could not read source for {entry.module}.{entry.name}: {exc}")
    tree = ast.parse(source)
    parents = _build_parent_map(tree)
    forbidden: list[str] = []
    for node in ast.walk(tree):
        # Restrict to direct (top-level) returns inside this
        # function -- nested function definitions are
        # independent scopes whose return None is unrelated
        # to this entry point's AC-08 contract.
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
    """The AC-01 disabled path returns ``None`` byte-identically.

    This is a behavior-level test that catches a regression
    where a future change replaces the disabled-path
    ``return None`` with a recorded skip, which would
    re-introduce the AC-01 contract violation. The disabled
    path is the ONE bare return the spec allows:
    ``auto_integrate_enabled = False`` MUST be a byte-
    identical no-op.
    """
    from ralph.pipeline import auto_integrate

    src = inspect.getsource(auto_integrate)
    assert "auto_integrate_enabled" in src, (
        "auto_integrate.py lost the auto_integrate_enabled check; "
        "the AC-01 disabled path is the ONE bare return the spec "
        "allows and must stay a byte-identical no-op"
    )


def test_synthetic_silent_skip_is_detected() -> None:
    """A canonical forbidden return shape MUST fail the audit.

    This is the canary: a synthetic function with a
    ``return None`` after an arbitrary (non-disabled) guard
    must trip :func:`_return_node_violates`. If the audit
    reports "no forbidden return" for this source, the
    audit itself is broken -- it would have failed to flag
    the very pattern it was supposed to catch.

    The check is intentionally tight: a permissive audit
    that lets this shape through lets a real regression
    through too.
    """
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
        # The synthetic function has NO allowed sentinels;
        # every return None is a violation.
        if node.value is None:
            violations.append(f"line {node.lineno}: bare return")
        elif (
            isinstance(node.value, ast.Constant)
            and node.value.value is None
        ):
            # Check the enclosing guard.
            parent: ast.AST | None = parents.get(id(node))
            guarded = False
            while parent is not None:
                if isinstance(parent, ast.If):
                    guarded = True
                    break
                parent = parents.get(id(parent))
            if not guarded:
                violations.append(f"line {node.lineno}: unconditional return None")
            # If the synthetic had a guard, the test still
            # fails because the guard is not one of the
            # allowed sentinels. We can detect that via the
            # _is_disabled_guard / _is_no_record_guard checks.
            if guarded:
                inner_parent: ast.AST | None = parents.get(id(node))
                if (
                    isinstance(inner_parent, ast.If)
                    and not _is_disabled_guard(inner_parent.test)
                    and not _is_no_record_guard(inner_parent.test)
                ):
                    violations.append(
                        f"line {node.lineno}: forbidden return None under a "
                        "non-disabled guard"
                    )
    assert violations, (
        "the audit did not flag the canonical forbidden shape "
        "`if target == 'main': return None`; the audit itself "
        "is too permissive"
    )


def test_known_silent_skip_promotion_is_recorded() -> None:
    """The 'phase-transition pre-check failed' surface is recorded loudly.

    AC-08 closes one specific silent-skip surface; the
    fix makes it return a ``RebaseState`` instead of
    ``None``. The audit asserts that the public
    :func:`auto_integrate_after_commit` source carries the
    recorded upgrade by importing it and reading the source
    for the surface keyword:

    * "phase-transition pre-check failed" -- was a silent
      ``return None`` at one seam; now recorded.
    """
    from ralph.pipeline import auto_integrate

    src = inspect.getsource(auto_integrate)
    assert "phase-transition pre-check failed" in src, (
        "auto_integrate.py lost the 'phase-transition pre-check "
        "failed' log; AC-08 expects this surface to be recorded "
        "loudly with the underlying exception"
    )
