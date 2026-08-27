"""Tests pinning the duplicate-keyword forwarding audit.

The audit (``ralph.testing.audit_kwargs_forwarding``) is the gate that keeps
``ralph/pipeline/runner.py::execute_commit_effect``'s failure mode from
recurring anywhere in the package: a wrapper that forwards ``**opts`` while
also passing an explicit keyword its own signature does not bind. Such a call
raises ``TypeError: got multiple values for keyword argument`` only when a
caller happens to name that keyword, so it type-checks, imports cleanly, and
breaks in production.

These tests prove the gate fires on the regression shape, stays quiet on the
two shapes that genuinely cannot collide, and that the live package is clean.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing import audit_kwargs_forwarding


def _violations(source: str) -> list[audit_kwargs_forwarding.Violation]:
    return audit_kwargs_forwarding.audit_source(source, "fake/module.py")


def test_flags_explicit_keyword_absent_from_the_wrapper_signature() -> None:
    """The exact shape that broke every commit the pipeline attempted."""
    source = """
def wrapper(effect, repo_root, **opts):
    return inner(
        effect,
        repo_root,
        has_residual_work_fn=repo_has_commit_work,
        **opts,
    )
"""

    violations = _violations(source)

    assert [item.keyword for item in violations] == ["has_residual_work_fn"]
    assert "setdefault" in str(violations[0])


def test_flags_every_colliding_keyword_in_one_call() -> None:
    """A call forwarding several unbindable keywords reports each of them."""
    source = """
def wrapper(effect, **opts):
    return inner(effect=effect, first=A, second=B, **opts)
"""

    assert sorted(item.keyword for item in _violations(source)) == ["first", "second"]


def test_allows_keywords_bound_by_the_wrapper_signature() -> None:
    """A caller naming a real parameter binds it there, so it never reaches opts."""
    source = """
def wrapper(effect, create_commit_fn, *, display=None, **opts):
    return inner(
        effect,
        create_commit_fn=create_commit_fn,
        display=display,
        **opts,
    )
"""

    assert _violations(source) == []


def test_allows_keywords_the_wrapper_rejects_before_forwarding() -> None:
    """An explicit membership guard turns the collision into a described error."""
    source = """
def wrapper(path, adder, **kwargs):
    if "buffering" in kwargs:
        raise TypeError("callers must NOT pass buffering")
    return adder(str(path), buffering=8192, **kwargs)
"""

    assert _violations(source) == []


def test_ignores_calls_that_do_not_forward_the_catchall() -> None:
    """Explicit keywords are only hazardous alongside a forwarded catch-all."""
    source = """
def wrapper(effect, **opts):
    return inner(effect, has_residual_work_fn=probe)
"""

    assert _violations(source) == []


def test_ignores_functions_without_a_catchall() -> None:
    source = """
def plain(effect, probe):
    return inner(effect, has_residual_work_fn=probe)
"""

    assert _violations(source) == []


def test_flags_async_wrappers_too() -> None:
    source = """
async def wrapper(effect, **opts):
    return await inner(effect, probe=default, **opts)
"""

    assert [item.keyword for item in _violations(source)] == ["probe"]


def test_reports_the_enclosing_function_and_call_line() -> None:
    source = "def wrapper(effect, **opts):\n    return inner(effect, probe=default, **opts)\n"

    violation = _violations(source)[0]

    assert violation.function == "wrapper"
    assert violation.lineno == 2
    assert violation.path == "fake/module.py"


@pytest.mark.timeout_seconds(30)
def test_live_package_has_no_duplicate_keyword_forwarding() -> None:
    """The shipped package must stay clean; this is the standing regression gate."""
    package_root = _package_root()

    violations = audit_kwargs_forwarding.audit_tree(package_root)

    assert violations == [], "\n".join(str(item) for item in violations)


def test_main_reports_missing_root() -> None:
    """An absent root exits 2 rather than reporting a spurious clean run."""
    assert audit_kwargs_forwarding.main(["definitely-not-a-package"]) == 2


def _package_root() -> Path:
    """Return the repo root that contains the shipped ``ralph/`` package."""
    return Path(audit_kwargs_forwarding.__file__).resolve().parents[2]
