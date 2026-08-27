"""Tests pinning the duplicate-keyword forwarding audit.

The audit (``ralph.testing.audit_kwargs_forwarding``) is the gate that keeps
``ralph/pipeline/runner.py::execute_commit_effect``'s failure mode from
recurring: a wrapper that forwards ``**opts`` while also passing an explicit
keyword its own signature does not bind. Such a call raises ``TypeError: got
multiple values for keyword argument`` only when a caller happens to name that
keyword, so it type-checks, imports cleanly, and breaks in production.

Every "flags" case below is a shape verified to raise a real ``TypeError``
when executed, and every "allows" case is verified not to. The gate is only
worth having if it agrees with the interpreter, so the cases are written from
the interpreter's behaviour rather than from the rule's wording.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing import audit_kwargs_forwarding
from ralph.testing.audit_kwargs_forwarding import AuditParseError

_EXPECTED_PACKAGE_ROOTS = ("ralph", "tests")


def _keywords(source: str) -> list[str]:
    return sorted(item.keyword for item in audit_kwargs_forwarding.audit_source(source, "fake.py"))


def test_flags_explicit_keyword_absent_from_the_wrapper_signature() -> None:
    """The exact shape that broke every commit the pipeline attempted."""
    source = """
def wrapper(effect, repo_root, **opts):
    return inner(effect, repo_root, has_residual_work_fn=probe, **opts)
"""

    violations = audit_kwargs_forwarding.audit_source(source, "fake.py")

    assert [item.keyword for item in violations] == ["has_residual_work_fn"]
    assert "setdefault" in str(violations[0])


def test_flags_every_colliding_keyword_in_one_call() -> None:
    source = """
def wrapper(effect, **opts):
    return inner(effect=effect, first=A, second=B, **opts)
"""

    assert _keywords(source) == ["first", "second"]


def test_flags_positional_only_parameter_names() -> None:
    """``w(1, a=2)`` routes 'a' into the catch-all even though 'a' is a parameter."""
    source = """
def wrapper(a, /, **opts):
    return inner(a=a, **opts)
"""

    assert _keywords(source) == ["a"]


def test_flags_the_catchall_parameter_name_itself() -> None:
    """``w(opts=5)`` puts 'opts' INTO the dict; the name binds nothing."""
    source = """
def wrapper(**opts):
    return inner(opts=DEFAULT, **opts)
"""

    assert _keywords(source) == ["opts"]


def test_flags_the_vararg_parameter_name() -> None:
    """``w(args=5)`` routes 'args' into the catch-all, same as any other name."""
    source = """
def wrapper(*args, **opts):
    return inner(args=DEFAULT, **opts)
"""

    assert _keywords(source) == ["args"]


@pytest.mark.parametrize(
    "forward",
    ["**opts", "**{**opts}", "**dict(opts)"],
    ids=["direct", "dict-splat", "dict-call"],
)
def test_flags_derived_unpacking_of_the_catchall(forward: str) -> None:
    """A derived dict delivers the caller's keys and collides identically."""
    source = f"""
def wrapper(**opts):
    return inner(hook=DEFAULT, {forward})
"""

    assert _keywords(source) == ["hook"]


def test_flags_a_guard_that_does_not_raise() -> None:
    """Logging the collision does not prevent it; only raising does."""
    source = """
def wrapper(path, adder, **kwargs):
    if "buffering" in kwargs:
        logger.debug("ignoring buffering")
    return adder(path, buffering=8192, **kwargs)
"""

    assert _keywords(source) == ["buffering"]


def test_flags_a_nested_scope_that_forwards_the_outer_catchall() -> None:
    """A closure over the catch-all collides exactly as the outer call would."""
    source = """
def outer(**opts):
    def run():
        return inner(hook=DEFAULT, **opts)
    return run()
"""

    assert _keywords(source) == ["hook"]


def test_flags_async_wrappers() -> None:
    source = """
async def wrapper(effect, **opts):
    return await inner(effect, probe=DEFAULT, **opts)
"""

    assert _keywords(source) == ["probe"]


def test_allows_keywords_bound_by_the_wrapper_signature() -> None:
    """A caller naming a real parameter binds it there, so it never reaches opts."""
    source = """
def wrapper(effect, create_commit_fn, *, display=None, **opts):
    return inner(effect, create_commit_fn=create_commit_fn, display=display, **opts)
"""

    assert _keywords(source) == []


def test_allows_a_guard_that_raises_before_forwarding() -> None:
    """``ralph/logging.py::_add_buffered_file_sink`` uses this form."""
    source = """
def wrapper(path, adder, **kwargs):
    if "buffering" in kwargs:
        raise TypeError("callers must NOT pass buffering")
    return adder(str(path), buffering=8192, **kwargs)
"""

    assert _keywords(source) == []


def test_allows_a_nested_def_that_binds_the_keyword_itself() -> None:
    """The keyword must be attributed to the scope that passes it, not the parent."""
    source = """
def outer(a, **opts):
    def run(hook, **rest):
        return inner(hook=hook, **rest)
    return run(**opts)
"""

    assert _keywords(source) == []


def test_allows_a_lambda_that_binds_the_keyword_itself() -> None:
    source = """
def outer(**opts):
    run = lambda hook, **rest: inner(hook=hook, **rest)
    return run(**opts)
"""

    assert _keywords(source) == []


def test_ignores_calls_that_do_not_forward_the_catchall() -> None:
    source = """
def wrapper(effect, **opts):
    return inner(effect, has_residual_work_fn=probe)
"""

    assert _keywords(source) == []


def test_ignores_functions_without_a_catchall() -> None:
    source = """
def plain(effect, probe):
    return inner(effect, has_residual_work_fn=probe)
"""

    assert _keywords(source) == []


def test_reports_the_enclosing_function_and_call_line() -> None:
    source = "def wrapper(effect, **opts):\n    return inner(effect, probe=DEFAULT, **opts)\n"

    violation = audit_kwargs_forwarding.audit_source(source, "fake.py")[0]

    assert (violation.function, violation.lineno, violation.path) == ("wrapper", 2, "fake.py")


def test_unparseable_source_raises_rather_than_reporting_clean() -> None:
    """A file the audit cannot read must never be counted as cleared."""
    with pytest.raises(AuditParseError):
        audit_kwargs_forwarding.audit_source("def broken(:\n", "fake.py")


def test_missing_root_raises_rather_than_scanning_nothing() -> None:
    """Silently skipping a root would make the live-tree gate vacuously green."""
    with pytest.raises(FileNotFoundError):
        audit_kwargs_forwarding.audit_tree(Path("/nonexistent"), ("ralph",))


def test_main_reports_missing_root() -> None:
    assert audit_kwargs_forwarding.main(["definitely-not-a-package"]) == 2


@pytest.mark.timeout_seconds(60)
def test_live_tree_has_no_duplicate_keyword_forwarding() -> None:
    """The standing regression gate over both the package and its tests."""
    package_root = _package_root()

    violations, scanned = audit_kwargs_forwarding.audit_tree(package_root)

    assert scanned > 0, f"audit scanned nothing under {package_root}; root resolution is wrong"
    assert violations == [], "\n".join(str(item) for item in violations)


def test_default_roots_cover_the_package_and_its_tests() -> None:
    """Narrowing the roots would silently shrink the gate."""
    assert audit_kwargs_forwarding.DEFAULT_ROOTS == _EXPECTED_PACKAGE_ROOTS


def _package_root() -> Path:
    """Return the repo root that contains the shipped ``ralph/`` package."""
    return Path(audit_kwargs_forwarding.__file__).resolve().parents[2]
