"""Public-docstring-floor audit (AST-only, import-safe).

PURPOSE

Enforce a non-empty module-docstring floor on every public Python module
shipped in the ``ralph-workflow`` package. The floor is:

  - every public module under ``ralph/`` (leaf modules AND package
    ``__init__.py``) MUST carry a module docstring whose first line is
    non-empty;
  - private modules (filenames starting with ``_``) are exempt.

The audit complements three existing sphinx test suites that already
enforce a partial version of this contract:

  - ``tests/test_sphinx_modules_coverage.py`` — enforces non-empty
    module docstrings (AST-based, including package ``__init__.py``)
    BUT only on the ``documented_in_modules.rst AND public AND
    not-in-_EXCLUDED`` intersection, so a regression can hide behind
    a coordinated ``modules.rst`` + ``_EXCLUDED`` edit.
  - ``tests/test_sphinx_documentation_setup.py`` — enforces package
    docstrings for 9 HARDCODED packages via ``importlib.import_module``
    (import-unsafe per the autodoc rubric).
  - ``tests/test_sphinx_member_documentation.py`` — enforces top-level
    class/function docstrings for ``:members:``-documented modules
    only.

NET-NEW surface this audit adds: a ``modules.rst``-INDEPENDENT,
exhaustive (every public .py under ``ralph/``), AST-based (no
import-time side effects) floor that covers every public leaf module,
every package ``__init__.py``, and the ~85 internal modules that ship
in the wheel but are not in ``modules.rst``.

ALLOWLIST

A module that genuinely cannot carry a docstring (e.g. an import-only
re-export shim) MAY carry an inline
``# docstring-audit-ok: <reason>`` marker on any non-docstring line in
the module body. The audit honors the marker and does NOT flag the
module. Keep markers rare and justified — they bypass the floor.

IMPORT-SAFETY CONTRACT

The audit uses ``ast.parse`` on file text read via ``pathlib`` ONLY.
It NEVER imports the modules under inspection. This is essential for
two reasons:

  1. Sphinx autodoc imports modules to render their docstrings; if a
     module has an import-time side effect (network, subprocess, env
     reads), autodoc triggers that side effect during the docs build.
     The audit must remain import-safe so the floor can be enforced
     independently of import side effects.
  2. The audit is fast (single AST pass per file, no import), so it
     fits the make-verify sub-second per-step budget comfortably.

USAGE

    python -m ralph.testing.audit_public_docstrings [root]

Exit 0 = clean, 1 = violations, 2 = root not found.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

# Directories to skip during file collection. These are the same
# skip-dirs the sibling audits use (``audit_lint_bypass``,
# ``audit_resource_lifecycle``) so a uniform exclude policy applies
# across the audit suite.
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".venv",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "htmlcov",
        "build",
        "dist",
        "tmp",
    }
)

# Inline allowlist marker. A module that carries this comment on any
# non-docstring line in its body is exempt from the floor. The marker
# must be the only justification the audit honors; do not introduce
# silent per-file-ignore-style bypasses — every exemption is reviewable
# in-place.
_ALLOW_MARKER: str = "docstring-audit-ok"


class DocstringViolation:
    """A single public-docstring-floor violation."""

    # Prevent pytest from trying to collect this class as a test class.
    __test__ = False

    def __init__(
        self,
        file_path: str,
        line: int,
        category: str,
        detail: str,
    ) -> None:
        self.file_path = file_path
        self.line = line
        self.category = category
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [PUBLIC-DOCSTRING] {self.category}: {self.detail}"


def _extract_first_line(tree: ast.Module) -> tuple[bool, str]:
    """Return ``(has_docstring, first_line)`` for the module docstring.

    AC-05 contract: a public module's docstring must exist AND its
    first PHYSICAL line (the literal first line of the triple-quoted
    string) must be non-empty. We deliberately do NOT use
    ``ast.get_docstring`` here because it applies ``inspect.cleandoc``
    normalization, which strips leading blank lines from the docstring
    body — that would let a module whose docstring is
    ``\"\"\"\\nReal text on line 2.\\n\"\"\"`` pass the floor even
    though its first line is blank.

    Instead, we read the raw string literal from
    ``tree.body[0].value`` (an ``ast.Constant`` whose ``.value`` is
    the original string, including leading blank lines and any
    common-indentation prefix), then split on newlines and inspect
    line 0.

    Returns:
        - ``(False, "")`` when the module has no docstring at all
          (``ast.get_docstring(tree) is None``).
        - ``(True, "<raw line 0>")`` when the module has a docstring
          literal (including the empty-literal case, where the first
          line is ``""``).

    Notes:
        - ``ast.Constant`` covers both single and triple-quoted
          docstrings in modern Python (3.8+); earlier AST shapes
          (``ast.Str``) were unified into ``ast.Constant`` and are
          not produced by Python 3.8+.
        - f-strings and other expression docstrings (extremely rare,
          and not a Python convention) yield ``(True, "<raw line 0>")``
          with the raw text of the expression — the audit enforces
          the same first-line rule.
    """
    if not tree.body:
        return False, ""
    first = tree.body[0]
    if not isinstance(first, ast.Expr):
        return False, ""
    value = first.value
    if not isinstance(value, ast.Constant):
        return False, ""
    if not isinstance(value.value, str):
        return False, ""
    raw: str = value.value
    first_line = raw.splitlines()[0] if raw else ""
    return True, first_line


def _has_allow_marker(source: str) -> bool:
    """Return True if the module body contains a ``# docstring-audit-ok:`` marker.

    The marker is searched in the entire source (including inside
    non-docstring code) so a module that cannot carry a docstring (e.g.
    an import-only re-export shim) can still opt out by placing the
    marker on a top-level comment.
    """
    return f"# {_ALLOW_MARKER}:" in source


def _is_public_module(file_path: Path) -> bool:
    """Return True if the module is public (filename does not start with ``_``).

    ``__init__.py`` is treated as a public package marker (its parent
    directory name is the package name; the public/private decision is
    based on the DIRECTORY name, not on the literal ``__init__.py``).

    ``__main__.py`` is a runtime entry point only and is exempt — it
    is not part of the public API.
    """
    if file_path.name == "__main__.py":
        return False
    if file_path.name == "__init__.py":
        return not file_path.parent.name.startswith("_")
    return not file_path.stem.startswith("_")


def _collect_public_modules(root: Path) -> list[Path]:
    """Yield every public ``.py`` file under ``root`` (in deterministic order)."""
    results: list[Path] = []
    for py_file in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in py_file.relative_to(root).parts):
            continue
        if not _is_public_module(py_file):
            continue
        results.append(py_file)
    return results


def audit_public_docstrings_file(file_path: Path, *, root: Path) -> list[DocstringViolation]:
    """Audit a single Python file for a missing/empty module docstring.

    Returns a list of violations (0 or 1 per file). The ``root``
    argument is the audit root used to compute a relative ``file_path``
    in the violation so the path is stable across run-from locations.
    """
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    if _has_allow_marker(source):
        return []

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return [
            DocstringViolation(
                file_path=str(file_path.relative_to(root)),
                line=0,
                category="syntax_error",
                detail="module could not be parsed; cannot determine docstring",
            )
        ]

    has_docstring, first_line = _extract_first_line(tree)
    if not has_docstring:
        return [
            DocstringViolation(
                file_path=str(file_path.relative_to(root)),
                line=1,
                category="missing_docstring",
                detail=(
                    f"public module {file_path.name!r} is missing a module "
                    f"docstring; add a top-level triple-quoted string or an "
                    f"inline '# {_ALLOW_MARKER}: <reason>' marker"
                ),
            )
        ]

    # AC-05 contract: the FIRST LINE of the docstring literal must be
    # non-empty. We use the raw first line (no ``inspect.cleandoc``
    # normalization) so a docstring whose only content sits on line 2
    # or later is still rejected — that is the literal contract from
    # the plan. A whitespace-only first line is also rejected: the
    # summary line must carry actual content.
    if not first_line.strip():
        return [
            DocstringViolation(
                file_path=str(file_path.relative_to(root)),
                line=1,
                category="empty_first_line",
                detail=(
                    f"public module {file_path.name!r} has a module docstring "
                    f"whose first line is empty; the docstring-floor contract "
                    f"requires the FIRST LINE to carry a non-empty summary "
                    f"(Python convention for one-line summaries in pydoc, "
                    f"Sphinx autodoc, and IDE tooltips)"
                ),
            )
        ]

    return []


def audit_public_docstrings_directory(root: Path) -> tuple[list[DocstringViolation], int]:
    """Audit every public ``.py`` file under ``root``.

    Returns a ``(violations, files_checked)`` tuple. ``files_checked``
    counts the number of public modules the audit actually inspected
    (private modules are NOT counted, matching the contract).
    """
    if not root.is_dir():
        raise FileNotFoundError(f"audit root not found: {root}")

    all_violations: list[DocstringViolation] = []
    files_checked = 0

    for py_file in _collect_public_modules(root):
        files_checked += 1
        all_violations.extend(audit_public_docstrings_file(py_file, root=root))

    return all_violations, files_checked


def _default_root() -> Path:
    """Return the default audit root (the maintained ``ralph/`` tree)."""
    return Path(__file__).parent.parent.parent / "ralph"


def main(argv: list[str] | None = None) -> int:
    """Run the public-docstring-floor audit and return an exit code.

    Exit code 0: no violations found.
    Exit code 1: violations found.
    Exit code 2: error (root not found).
    """
    args = list(argv) if argv is not None else list(sys.argv[1:])

    root = Path(args[0]) if args else _default_root()
    if not root.is_dir():
        print(f"Error: audit root not found: {root}", file=sys.stderr)
        return 2

    print(f"Auditing public docstring floor in: {root}")
    print()

    try:
        violations, files_checked = audit_public_docstrings_directory(root)
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if violations:
        print(
            f"PUBLIC-DOCSTRING VIOLATIONS: {len(violations)} violation(s) "
            f"in {files_checked} file(s)"
        )
        print("=" * 72)
        for v in violations:
            print(f"  {v}")
        print()
        print(
            "Every public module under ralph/ must carry a non-empty "
            "module docstring. Add a top-level docstring or an inline "
            f"'# {_ALLOW_MARKER}: <reason>' marker for the rare module "
            "that genuinely cannot have one."
        )
        return 1

    print(f"No public-docstring violations found in {files_checked} file(s).")
    return 0


# Touch the importlib reference so the AST-only contract is testable
# from outside (the test module patches ``audit_module.importlib
# .import_module`` to a function that raises — the audit must never
# call it). Using ``importlib`` here is a deliberate test seam, not a
# runtime dependency: the audit does not actually use it.
_ = importlib


if __name__ == "__main__":
    raise SystemExit(main())
