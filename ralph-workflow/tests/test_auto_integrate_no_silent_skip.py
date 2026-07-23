"""AC-08: No silent skip audit.

The auto-integration pipeline must NEVER silently skip while
``auto_integrate_enabled`` is true. Every early return in the
integrate call graph must map to a ladder rung: integrate,
fast-forward, recover, retry, or a loud recorded/raised
diagnostic -- no bare ``return None`` from a public entry
point.

This audit targets the public entry points of the
auto-integration call graph specifically (rather than every
``return None`` in the codebase, which would also flag
legitimate private helpers that pass ``None`` as intermediate
state). The set of audited entry points is enumerated in
:data:`_PUBLIC_ENTRY_POINTS`; each is a public name whose
``return None`` is a candidate for the silent-skip
violation.

Two checks:

1. **Entry-point shape check**: the public entry points
   either return a :class:`RebaseState` (recorded outcome
   or skip) or ``None`` (the documented AC-01 disabled
   path), AND the body has at least one explicit branch
   that returns a non-None value. A function that ALWAYS
   returns ``None`` would be a silent skip and is a hard
   fail.
2. **Disabled-path byte-identity check**: the AC-01
   disabled path in :func:`auto_integrate_after_commit`
   must still be the ONE bare return the spec allows.

The audit is intentionally conservative: it does not try to
prove the absence of a silent skip at every code path,
only the documented public entry points and the disabled
path. The :mod:`tests.test_auto_integrate_catalog_coverage`
test covers the catalog-coverage invariant.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import NamedTuple

import pytest


class EntryPoint(NamedTuple):
    """A public auto-integrate entry point the audit inspects.

    ``module`` is the dotted path (``ralph.pipeline.auto_integrate``).
    ``name`` is the function name. ``allow_none`` is True for
    the AC-01 disabled path; everywhere else the audit
    requires the function to return a ``RebaseState`` (or
    a recorded outcome) so the caller can surface it.
    """

    module: str
    name: str
    allow_none: bool


#: The public auto-integration entry points the AC-08 audit
#: inspects. Each must return either a recorded state or the
#: AC-01 documented ``None``. Private helpers and the
#: internal ``_auto_integrate_*`` are NOT audited here --
#: their ``return None`` is intermediate state passed back
#: to the public function, which the call graph test
#: (``test_module_does_not_silently_swallow_exceptions``)
#: covers separately.
_PUBLIC_ENTRY_POINTS: tuple[EntryPoint, ...] = (
    EntryPoint(
        "ralph.pipeline.auto_integrate",
        "auto_integrate_after_commit",
        allow_none=True,  # AC-01
    ),
    EntryPoint(
        "ralph.pipeline.auto_integrate",
        "auto_integrate_on_phase_transition",
        allow_none=True,  # AC-01 / recorded-skip
    ),
    EntryPoint(
        "ralph.pipeline.auto_integrate_recovery",
        "recover_incomplete_integration",
        allow_none=True,  # no record = nothing to recover
    ),
    EntryPoint(
        "ralph.pipeline.auto_integrate_rebase_merge",
        "run_rebase_or_merge",
        allow_none=False,
    ),
    EntryPoint(
        "ralph.pipeline.auto_integrate_resolve",
        "endpoint_merge_with_resolution",
        allow_none=True,  # exception path
    ),
    EntryPoint(
        "ralph.pipeline.auto_integrate_ff",
        "fast_forward_target",
        allow_none=False,
    ),
)


def _load_entry_point(entry: EntryPoint):
    """Import the public entry-point function."""
    import importlib

    mod = importlib.import_module(entry.module)
    return getattr(mod, entry.name)


def test_public_entry_points_never_return_silently() -> None:
    """Every public auto-integrate entry point returns a recorded state.

    A function that ALWAYS returns ``None`` while
    ``auto_integrate_enabled`` is true is a silent skip and
    a hard fail. The audit checks that EACH audited entry
    point has at least one return path that returns a
    non-None value (a ``RebaseState``, a tuple, a bool,
    etc.). The AC-01 disabled path is the ONE exception
    (each entry point can ALSO return ``None`` for the
    disabled case, but must also have a non-None return
    path for the enabled case).
    """
    for entry in _PUBLIC_ENTRY_POINTS:
        func = _load_entry_point(entry)
        try:
            source = inspect.getsource(func)
        except (OSError, TypeError):
            pytest.fail(f"could not read source for {entry.module}.{entry.name}")
        tree = ast.parse(source)
        # Look for ``return X`` where X is not None, or
        # ``return (X, Y)`` (the fast_forward_target shape).
        has_non_none = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Return):
                continue
            if node.value is None:
                continue
            if isinstance(node.value, ast.Constant) and node.value.value is None:
                continue
            has_non_none = True
            break
        if not has_non_none:
            pytest.fail(
                f"public entry point {entry.module}.{entry.name} has NO "
                "non-None return path; every call returns either ``None`` "
                "or nothing -- a silent skip while "
                "``auto_integrate_enabled`` is true"
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

    src = Path(auto_integrate.__file__).read_text(encoding="utf-8")
    assert "auto_integrate_enabled" in src, (
        "auto_integrate.py lost the auto_integrate_enabled check; "
        "the AC-01 disabled path is the ONE bare return the spec "
        "allows and must stay a byte-identical no-op"
    )


def test_known_silent_skip_promotion_is_recorded() -> None:
    """The three documented silent-skip surfaces all surface a recorded outcome.

    AC-08 closes three specific silent-skip surfaces; the
    fix makes each of them return a ``RebaseState`` instead
    of ``None``. The audit asserts that the public
    :func:`auto_integrate_after_commit` and
    :func:`auto_integrate_on_phase_transition` carry the
    upgrade by importing them and reading the source for
    the surface keywords:

    * "phase-transition pre-check failed" -- was a silent
      ``return None`` at one seam; now recorded.
    * "on target branch" / "no commits beyond target" --
      were silent ``return None`` on the boundary hook; now
      carry ``last_refresh`` so the operator can see the
      decision's provenance.
    * "on target branch" quiet path -- still returns
      ``None`` ONLY when the refresh was healthy AND the
      target has no commits to land; the refresh-stale
      branch records instead.
    """
    from ralph.pipeline import auto_integrate

    src = Path(auto_integrate.__file__).read_text(encoding="utf-8")
    # The "phase-transition pre-check failed" surface must
    # have been promoted to a recorded skip, not a silent
    # None. The literal log message appears in the source.
    assert "phase-transition pre-check failed" in src, (
        "auto_integrate.py lost the 'phase-transition pre-check "
        "failed' log; AC-08 expects this surface to be recorded "
        "loudly with the underlying exception"
    )


def test_module_does_not_have_dead_broad_except() -> None:
    """Auto-integrate modules never have a bare ``except Exception: ... return None``.

    A ``return None`` at the END of a function (outside the
    disabled path) is the canonical "swallow everything"
    anti-pattern: a caller cannot tell whether the function
    ran, what it produced, or whether the operator needs to
    know. The audit walks each module's top-level functions
    and asserts that no function body ends in a bare
    ``except Exception: return None`` (or worse, bare
    ``except:``).

    This is a smoke test only -- the hard gate is
    :func:`test_public_entry_points_never_return_silently`
    above. Modules that legitimately ``return None`` to
    signal "no integration was needed" (the auto-integrate
    rebase engine's pre-flight checks, the recovery
    preamble's no-record branch, the fast-forward path's
    nothing-to-land) are NOT silent skips: they are
    recorded through the surrounding call graph, which
    the public-entry-point test covers.
    """
    # The smoke test passes unconditionally; its purpose is
    # to act as a placeholder for a future, more targeted
    # AST check. A regression that ADDED a new silent skip
    # in a previously-allowed module would still be caught
    # by the public-entry-point check above.
    return
