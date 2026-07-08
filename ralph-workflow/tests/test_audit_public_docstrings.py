"""Tests for ralph.testing.audit_public_docstrings.

The public-docstring-floor contract (enforced by ``make verify``):

Every public Python module under ``ralph/`` MUST carry a non-empty module
docstring. ``public`` means: the module's filename does NOT start with
``_`` (private modules are exempt) BUT the file IS included in the
audit, including package ``__init__.py`` files (this is the package
coverage the audit adds over the existing
``test_sphinx_documentation_setup`` and ``test_sphinx_modules_coverage``
checks). The audit is AST-only — it never imports the modules it
inspects, so it is safe to run against a directory that is not on
``sys.path`` and cannot trigger import-time side effects in the
inspected code.

These tests pin:
  (a) the real ralph/ tree is GREEN (every public module + every
      package __init__.py has a non-empty docstring today);
  (b) a public leaf module with a missing/empty docstring IS flagged
      (red case for the leaf contract);
  (c) a public package __init__.py with a missing/empty docstring IS
      flagged (red case for the package contract — PA-004);
  (d) underscore-prefixed (private) modules are NOT flagged (privacy
      exemption is honoured);
  (e) the ``main`` entry point returns 0 for a clean tree, 1 for a
      tree with a violation, and 2 for a missing root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing import audit_public_docstrings as audit_module
from ralph.testing.audit_public_docstrings import main

# The maintained ralph/ tree (sibling of tests/ in ralph-workflow/).
_RALPH_PKG_ROOT: Path = Path(audit_module.__file__).parent.parent.parent / "ralph"


def _write(tmp_path: Path, rel: str, body: str) -> Path:
    """Write a file at ``tmp_path / rel`` with the given body and return the path."""
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# (a) GREEN — the maintained ralph/ tree is clean today.
# ---------------------------------------------------------------------------


@pytest.mark.timeout_seconds(15)
@pytest.mark.subprocess_e2e
def test_real_ralph_tree_is_green() -> None:
    """Every public .py under ralph/ — leaf modules and package __init__.py —
    must carry a non-empty module docstring. The audit's main() should
    return 0 when run against the real ralph-workflow/ralph/ tree.
    """
    if not _RALPH_PKG_ROOT.is_dir():
        pytest.skip(f"ralph/ package root not found at {_RALPH_PKG_ROOT}")
    assert main([str(_RALPH_PKG_ROOT)]) == 0, (
        "Real ralph/ tree reports a public-docstring-floor violation. "
        "The floor is green today (524/524 public .py files have a "
        "non-empty module docstring); a regression here means a new "
        "module landed without a docstring."
    )


@pytest.mark.timeout_seconds(15)
@pytest.mark.subprocess_e2e
def test_audit_function_returns_no_violations_for_real_tree() -> None:
    """The internal audit function also reports zero violations on the real
    tree, and reports a positive files_checked count.
    """
    if not _RALPH_PKG_ROOT.is_dir():
        pytest.skip(f"ralph/ package root not found at {_RALPH_PKG_ROOT}")
    violations, files_checked = audit_module.audit_public_docstrings_directory(_RALPH_PKG_ROOT)
    assert violations == []
    assert files_checked > 0, "audit did not inspect any public modules"


# ---------------------------------------------------------------------------
# (b) RED-LEAF — a public leaf module with no docstring IS flagged.
# ---------------------------------------------------------------------------


def test_missing_leaf_module_docstring_is_flagged(tmp_path: Path) -> None:
    """A public leaf module (filename not starting with ``_``) with no
    module docstring MUST be reported as a violation. The audit does
    not import the module — it parses it with ``ast`` only — so the
    file can sit in a tmp_path that is not on sys.path.
    """
    _write(
        tmp_path,
        "leaf.py",
        "x = 1\n",
    )
    violations, _ = audit_module.audit_public_docstrings_directory(tmp_path)
    assert len(violations) == 1, f"expected 1 violation, got {len(violations)}"
    violation = violations[0]
    assert "leaf.py" in violation.file_path
    assert "missing" in violation.detail.lower() or "docstring" in violation.detail.lower()


def test_empty_leaf_module_docstring_is_flagged(tmp_path: Path) -> None:
    """A module whose docstring is a present-but-empty string literal is
    still a violation — the floor is presence AND non-empty first line.
    """
    _write(
        tmp_path,
        "empty_doc.py",
        '"""\n\n"""\n',
    )
    violations, _ = audit_module.audit_public_docstrings_directory(tmp_path)
    assert len(violations) == 1, (
        f"empty docstring must be flagged, got {len(violations)} violations"
    )
    assert "empty_doc.py" in violations[0].file_path


def test_blank_first_line_docstring_is_flagged(tmp_path: Path) -> None:
    """AC-05 contract: the FIRST LINE of the module docstring literal
    must be non-empty. A docstring whose only content sits on the
    second line (i.e. the literal starts with ``"\"\"\"\\n`` and the
    first physical line is blank) is a violation, even though
    ``ast.get_docstring(tree)`` would return the non-empty second
    line as the cleaned string.

    This is the regression test for the AC-05 contract gap: a previous
    version of the audit used ``ast.get_docstring(tree).strip()``
    which (via ``inspect.cleandoc`` normalization) accepted this case
    and silently let a non-conforming docstring pass. The current
    audit extracts the raw literal from ``tree.body[0].value`` and
    inspects the literal first line, so this case is flagged.
    """
    _write(
        tmp_path,
        "blank_first_line.py",
        '"""\nReal text on second line.\n"""\nimport os\n',
    )
    violations, _ = audit_module.audit_public_docstrings_directory(tmp_path)
    assert len(violations) == 1, (
        f"docstring with blank first line MUST be flagged under AC-05, "
        f"got {len(violations)} violations"
    )
    violation = violations[0]
    assert "blank_first_line.py" in violation.file_path
    assert violation.category == "empty_first_line", (
        f"expected category=empty_first_line, got {violation.category}"
    )


def test_whitespace_only_first_line_is_flagged(tmp_path: Path) -> None:
    """AC-05 contract: the first line must carry actual content, not just
    whitespace. A docstring whose first line is whitespace-only (e.g.
    ``\"\"\"   \\nReal text on second line.\\n\"\"\"``) is still a
    violation because the summary line is empty after ``strip()``.
    """
    _write(
        tmp_path,
        "whitespace_first_line.py",
        '"""   \nReal text on second line.\n"""\nimport os\n',
    )
    violations, _ = audit_module.audit_public_docstrings_directory(tmp_path)
    assert len(violations) == 1, (
        f"docstring with whitespace-only first line MUST be flagged, "
        f"got {len(violations)} violations"
    )
    assert violations[0].category == "empty_first_line"


def test_package_init_blank_first_line_is_flagged(tmp_path: Path) -> None:
    """The AC-05 first-line rule applies to package __init__.py too —
    PA-004 also requires package coverage. A package whose
    ``__init__.py`` docstring has a blank first line is flagged.
    """
    _write(
        tmp_path,
        "blankpkg/__init__.py",
        '"""\nReal package summary on line 2.\n"""\n',
    )
    violations, _ = audit_module.audit_public_docstrings_directory(tmp_path)
    assert len(violations) == 1
    assert "__init__.py" in violations[0].file_path
    assert violations[0].category == "empty_first_line"


def test_present_leaf_module_docstring_is_not_flagged(tmp_path: Path) -> None:
    """Sanity check: a public leaf module with a real docstring produces
    zero violations.
    """
    _write(
        tmp_path,
        "good.py",
        '"""Module docstring."""\n',
    )
    violations, _ = audit_module.audit_public_docstrings_directory(tmp_path)
    assert violations == []


# ---------------------------------------------------------------------------
# (c) RED-PACKAGE — a public package's __init__.py is a module too.
# ---------------------------------------------------------------------------


def test_missing_package_init_docstring_is_flagged(tmp_path: Path) -> None:
    """A package is a module — its ``__init__.py`` MUST carry a docstring
    even when the package has no other content. The audit MUST flag a
    package whose ``__init__.py`` is empty (no docstring).
    """
    _write(
        tmp_path,
        "pkg/__init__.py",
        "x = 1\n",
    )
    violations, _ = audit_module.audit_public_docstrings_directory(tmp_path)
    assert len(violations) == 1, (
        f"public package __init__.py without a docstring must be flagged, "
        f"got {len(violations)} violations"
    )
    violation = violations[0]
    assert "__init__.py" in violation.file_path


def test_present_package_init_docstring_is_not_flagged(tmp_path: Path) -> None:
    """A package with a real ``__init__.py`` docstring is clean."""
    _write(
        tmp_path,
        "good_pkg/__init__.py",
        '"""Public package."""\n',
    )
    violations, _ = audit_module.audit_public_docstrings_directory(tmp_path)
    assert violations == []


# ---------------------------------------------------------------------------
# (d) PRIVATE — underscore-prefixed modules are NOT audited.
# ---------------------------------------------------------------------------


def test_private_module_with_no_docstring_is_not_flagged(tmp_path: Path) -> None:
    """A module whose filename starts with ``_`` is private and is
    exempt from the docstring-floor contract.
    """
    _write(
        tmp_path,
        "_private.py",
        "x = 1\n",
    )
    violations, files_checked = audit_module.audit_public_docstrings_directory(tmp_path)
    assert violations == []
    assert files_checked == 0, "private modules must not be counted as checked"


def test_private_package_init_with_no_docstring_is_not_flagged(tmp_path: Path) -> None:
    """An underscore-prefixed package (``_pkg/``) is private and is exempt
    even at the ``__init__.py`` level.
    """
    _write(
        tmp_path,
        "_pkg/__init__.py",
        "x = 1\n",
    )
    violations, files_checked = audit_module.audit_public_docstrings_directory(tmp_path)
    assert violations == []
    assert files_checked == 0


# ---------------------------------------------------------------------------
# Skip-dirs — internal cache/build directories are skipped.
# ---------------------------------------------------------------------------


def test_skip_dirs_are_ignored(tmp_path: Path) -> None:
    """Directories like ``__pycache__``, ``.venv``, ``.mypy_cache``,
    ``.ruff_cache``, ``.pytest_cache``, ``htmlcov``, ``build``, ``dist``,
    and ``tmp`` MUST be skipped even if they contain violations.
    """
    for skip in [
        "__pycache__",
        ".venv",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "htmlcov",
        "build",
        "dist",
        "tmp",
    ]:
        _write(
            tmp_path,
            f"{skip}/file.py",
            "x = 1\n",  # no docstring
        )
    violations, files_checked = audit_module.audit_public_docstrings_directory(tmp_path)
    assert violations == []
    assert files_checked == 0


# ---------------------------------------------------------------------------
# main() exit-code contract.
# ---------------------------------------------------------------------------


def test_main_returns_zero_when_clean(tmp_path: Path) -> None:
    _write(tmp_path, "ok.py", '"""Module docstring."""\n')
    assert main([str(tmp_path)]) == 0


def test_main_returns_one_when_violation_present(tmp_path: Path) -> None:
    _write(tmp_path, "broken.py", "x = 1\n")
    assert main([str(tmp_path)]) == 1


def test_main_returns_two_when_root_missing(tmp_path: Path) -> None:
    missing = tmp_path / "this_path_does_not_exist_xyz"
    assert main([str(missing)]) == 2


# ---------------------------------------------------------------------------
# Inline allowlist — a ``# docstring-audit-ok: <reason>`` marker on the
# module body exempts the module. Used for genuinely private-exception
# public modules with a documented reason.
# ---------------------------------------------------------------------------


def test_inline_allowlist_marker_suppresses_violation(tmp_path: Path) -> None:
    """A public module that legitimately cannot carry a docstring
    (e.g. an import-only re-export shim whose first statement must be
    a non-docstring expression) MAY carry an inline
    ``# docstring-audit-ok: <reason>`` marker. The audit MUST honor
    the marker and NOT flag the module.
    """
    _write(
        tmp_path,
        "shim.py",
        "# docstring-audit-ok: import-only re-export shim, no public surface\n"
        "from somewhere import *\n",
    )
    violations, _ = audit_module.audit_public_docstrings_directory(tmp_path)
    assert violations == []


# ---------------------------------------------------------------------------
# Import-safety contract: the audit never imports the modules it inspects.
# A side-effect-laden module under tmp_path that would explode if
# imported is fine because the audit only AST-parses it.
# ---------------------------------------------------------------------------


def test_audit_does_not_import_inspected_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The audit must use ast.parse on file text only and NEVER import
    the modules it inspects. We assert this by:
      1. placing a side-effect module under tmp_path whose top-level
         code would crash at import time;
      2. monkey-patching importlib.import_module so any attempt to
         import the file raises;
      3. confirming the audit still runs and reports the missing-
         docstring violation normally (i.e. the import path was not
         used).
    """
    _write(
        tmp_path,
        "side_effect.py",
        'raise RuntimeError("if you can read this, the audit imported me")\n',
    )

    def _fail_import(name: str, *args: object, **kwargs: object) -> object:
        del name, args, kwargs
        raise AssertionError(
            "audit_public_docstrings imported a module under inspection; the audit must be AST-only"
        )

    monkeypatch.setattr(audit_module.importlib, "import_module", _fail_import)
    violations, _ = audit_module.audit_public_docstrings_directory(tmp_path)
    assert len(violations) == 1
    assert "side_effect.py" in violations[0].file_path


# ---------------------------------------------------------------------------
# Violation class has __test__ = False so pytest does not try to collect it.
# ---------------------------------------------------------------------------


def test_violation_class_is_not_collected_by_pytest() -> None:
    assert getattr(audit_module.DocstringViolation, "__test__", None) is False, (
        "Violation class must set __test__ = False so pytest does not treat it as a test class."
    )


# ---------------------------------------------------------------------------
# Path mapping — relative module path in the Violation's file_path.
# ---------------------------------------------------------------------------


def test_violation_file_path_is_relative_to_root(tmp_path: Path) -> None:
    _write(tmp_path, "mymod.py", "x = 1\n")
    violations, _ = audit_module.audit_public_docstrings_directory(tmp_path)
    assert len(violations) == 1
    # The audit must report the file relative to the audit root so the
    # path is stable across run-from and contains no absolute prefix.
    assert not Path(violations[0].file_path).is_absolute()
    assert violations[0].file_path.endswith("mymod.py")
