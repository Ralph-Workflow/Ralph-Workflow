from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing import audit_kwargs_forwarding
from ralph.testing.audit_kwargs_forwarding import AuditParseError

pytestmark = pytest.mark.subprocess_e2e

_EXPECTED_PACKAGE_ROOTS = ("ralph", "tests")


def _keywords(source: str) -> list[str]:
    return sorted(item.keyword for item in audit_kwargs_forwarding.audit_source(source, "fake.py"))


def test_flags_explicit_keyword_absent_from_the_wrapper_signature() -> None:
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
    source = """
def wrapper(a, /, **opts):
    return inner(a=a, **opts)
"""

    assert _keywords(source) == ["a"]


def test_flags_the_catchall_parameter_name_itself() -> None:
    source = """
def wrapper(**opts):
    return inner(opts=DEFAULT, **opts)
"""

    assert _keywords(source) == ["opts"]


def test_flags_the_vararg_parameter_name() -> None:
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
    source = f"""
def wrapper(**opts):
    return inner(hook=DEFAULT, {forward})
"""

    assert _keywords(source) == ["hook"]


def test_flags_a_guard_that_does_not_raise() -> None:
    source = """
def wrapper(path, adder, **kwargs):
    if "buffering" in kwargs:
        logger.debug("ignoring buffering")
    return adder(path, buffering=8192, **kwargs)
"""

    assert _keywords(source) == ["buffering"]


def test_flags_a_nested_scope_that_forwards_the_outer_catchall() -> None:
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
    source = """
def wrapper(effect, create_commit_fn, *, display=None, **opts):
    return inner(effect, create_commit_fn=create_commit_fn, display=display, **opts)
"""

    assert _keywords(source) == []


def test_allows_a_guard_that_raises_before_forwarding() -> None:
    source = """
def wrapper(path, adder, **kwargs):
    if "buffering" in kwargs:
        raise TypeError("callers must NOT pass buffering")
    return adder(str(path), buffering=8192, **kwargs)
"""

    assert _keywords(source) == []


def test_allows_a_nested_def_that_binds_the_keyword_itself() -> None:
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
    with pytest.raises(AuditParseError):
        audit_kwargs_forwarding.audit_source("def broken(:\n", "fake.py")


def test_missing_root_raises_rather_than_scanning_nothing() -> None:
    with pytest.raises(FileNotFoundError):
        audit_kwargs_forwarding.audit_tree(Path("/nonexistent"), ("ralph",))


def test_audit_tree_counts_all_files_but_parses_only_files_with_unpacking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "ralph").mkdir()
    (tmp_path / "tests").mkdir()
    plain = tmp_path / "ralph" / "plain.py"
    relevant = tmp_path / "tests" / "relevant.py"
    plain.write_text("def broken(:\n", encoding="utf-8")
    relevant.write_text(
        "def wrapper(**opts):\n    return inner(hook=DEFAULT, **opts)\n",
        encoding="utf-8",
    )
    parsed: list[str] = []
    real_audit_source = audit_kwargs_forwarding.audit_source

    def recording_audit_source(source: str, path: str) -> list[object]:
        parsed.append(path)
        return real_audit_source(source, path)

    monkeypatch.setattr(audit_kwargs_forwarding, "audit_source", recording_audit_source)

    violations, scanned = audit_kwargs_forwarding.audit_tree(tmp_path)

    assert scanned == 2
    assert parsed == ["tests/relevant.py"]
    assert [item.keyword for item in violations] == ["hook"]


def test_main_reports_missing_root() -> None:
    assert audit_kwargs_forwarding.main(["definitely-not-a-package"]) == 2


def test_live_tree_has_no_duplicate_keyword_forwarding(tmp_path: Path) -> None:
    ralph_dir = tmp_path / "ralph"
    ralph_dir.mkdir()
    (ralph_dir / "__init__.py").write_text("", encoding="utf-8")
    (ralph_dir / "mod.py").write_text(
        "def inner(**opts):\n    return opts\n\n"
        "def outer(val, **opts):\n    return inner(**opts)\n",
        encoding="utf-8",
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_mod.py").write_text(
        "def test_foo():\n    pass\n",
        encoding="utf-8",
    )

    violations, scanned = audit_kwargs_forwarding.audit_tree(
        tmp_path, audit_kwargs_forwarding.DEFAULT_ROOTS
    )

    assert scanned == 3, f"audit scanned nothing under {tmp_path}; root resolution is wrong"
    assert violations == [], "\n".join(str(item) for item in violations)


def test_default_roots_cover_the_package_and_its_tests() -> None:
    assert audit_kwargs_forwarding.DEFAULT_ROOTS == _EXPECTED_PACKAGE_ROOTS
