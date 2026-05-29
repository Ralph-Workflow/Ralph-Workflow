"""Lint bypass audit — detects forbidden noqa and per-file-ignores.

Scans the codebase for:
- Bare ``# noqa`` comments without a specific error code
- ``# noqa: CODE`` where CODE is not in the allowlist
- ``[tool.ruff.lint.per-file-ignores]`` or ``extend-per-file-ignores`` in pyproject.toml

Usage:
    python -m ralph.testing.audit_lint_bypass [codebase_root]

Returns exit code 0 if no lint bypass violations found, 1 otherwise.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

# ---------------------------------------------------------------------------
# Allowlist: known-legitimate noqa uses
#
# Format: {(file_stem, error_code), ...}
# - file_stem matches the filename stem only (not full path), so
#   "audit_test_policy" matches tests/test_audit_xxx.py as well as
#   ralph/testing/audit_test_policy.py — any file with that stem.
# - error_code is the ruff code (e.g. "PLR0911", "PLW0603").
# ---------------------------------------------------------------------------
_NOQA_ALLOWLIST: set[tuple[str, str]] = {
    ("exec_overlay", "PLR0912"),
    ("audit_test_policy", "PLR0911"),
    ("audit_test_policy", "PLW0603"),
}

# Files to skip entirely (test fixtures, generated code, etc.).
_SKIP_DIRS: frozenset[str] = frozenset({"__pycache__", ".venv", ".mypy_cache", "tmp", ".ruff_cache", ".pytest_cache", "htmlcov", "build", "dist"})

# Regex for matching # noqa comments on a line.
_NOQA_RE = re.compile(r"#\s*noqa(?:\s*:\s*(.*?))?(?:\s*$|\s*$)")

# Acceptable noqa codes — any code NOT in this set requires an allowlist entry.
# Currently only complexity and global-state codes are acceptable when used
# with a documented reason in the allowlist.
_ACCEPTABLE_NOQA_CODES: frozenset[str] = frozenset(
    {"PLR0911", "PLR0912", "PLW0603"}
)


class LintBypassViolation:
    """A single lint bypass violation found during scanning."""

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
        return f"{self.file_path}:{self.line}: [LINT-BYPASS] {self.category}: {self.detail}"


def _is_inside_triple_quoted(lines: list[str], line_index: int) -> bool:
    """Return True if *line_index* is inside a triple-quoted string literal.

    Walks from index 0 to line_index - 1 to detect unclosed ''' or \"\"\".
    This is a simple heuristic that works for most real-world Python.
    """
    in_triple: bool = False
    for i in range(line_index):
        stripped = lines[i].strip()
        # Count triple-quote occurrences — odd count means state toggled.
        count = stripped.count('"""') + stripped.count("'''")
        if count % 2 == 1:
            in_triple = not in_triple
    return in_triple


def _find_noqa_violations(lines: list[str], rel_path: str) -> list[LintBypassViolation]:
    """Scan source lines for forbidden noqa annotations."""
    violations: list[LintBypassViolation] = []
    file_stem = Path(rel_path).stem

    for idx, raw_line in enumerate(lines):
        lineno = idx + 1

        # Skip lines inside triple-quoted strings.
        if _is_inside_triple_quoted(lines, idx):
            continue

        match = _NOQA_RE.search(raw_line)
        if not match:
            continue

        if rel_path.startswith("tests/"):
            violations.append(
                LintBypassViolation(
                    file_path=rel_path,
                    line=lineno,
                    category="test-noqa",
                    detail="# noqa in test file — tests must follow all lint rules",
                )
            )
            continue

        codes_str = match.group(1)

        if codes_str is None:
            # Bare # noqa without specific codes.
            violations.append(
                LintBypassViolation(
                    file_path=rel_path,
                    line=lineno,
                    category="bare-noqa",
                    detail="bare '# noqa' without specific error code",
                )
            )
            continue

        # Parse comma-separated codes.
        codes = [c.strip() for c in codes_str.split(",") if c.strip()]
        for code in codes:
            if (file_stem, code) in _NOQA_ALLOWLIST:
                continue
            if code in _ACCEPTABLE_NOQA_CODES:
                # In allowlist but not for this file — flag.
                violations.append(
                    LintBypassViolation(
                        file_path=rel_path,
                        line=lineno,
                        category="unauthorized-noqa",
                        detail=f"'# noqa: {code}' — code {code} is not "
                        f"allowlisted for file '{file_stem}.py'",
                    )
                )
            else:
                violations.append(
                    LintBypassViolation(
                        file_path=rel_path,
                        line=lineno,
                        category="forbidden-noqa",
                        detail=f"'# noqa: {code}' — code {code} is not an "
                        f"acceptable noqa code (acceptable: {sorted(_ACCEPTABLE_NOQA_CODES)})",
                    )
                )

    return violations


def _check_pyproject_config(pyproject_path: Path) -> list[LintBypassViolation]:
    """Check pyproject.toml for per-file-ignores violations."""
    violations: list[LintBypassViolation] = []

    if not pyproject_path.is_file():
        return violations

    try:
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return violations

    ruff_lint = (
        data.get("tool", {})
        .get("ruff", {})
        .get("lint", {})
    )

    per_file_ignores = ruff_lint.get("per-file-ignores", {})
    if per_file_ignores:
        for file_pattern, codes in per_file_ignores.items():
            violations.append(
                LintBypassViolation(
                    file_path=str(pyproject_path),
                    line=0,
                    category="per-file-ignores",
                    detail=f"[tool.ruff.lint.per-file-ignores] '{file_pattern}': {codes} — "
                    f"per-file-ignores weakens lint enforcement",
                )
            )

    extend_per_file_ignores = ruff_lint.get("extend-per-file-ignores", {})
    if extend_per_file_ignores:
        for file_pattern, codes in extend_per_file_ignores.items():
            violations.append(
                LintBypassViolation(
                    file_path=str(pyproject_path),
                    line=0,
                    category="extend-per-file-ignores",
                    detail=f"[tool.ruff.lint.extend-per-file-ignores] '{file_pattern}': {codes} — "
                    f"per-file-ignores weakens lint enforcement",
                )
            )

    # --- check for global lint ignore (whole-project weakening) ---
    ruff_tool = data.get("tool", {}).get("ruff", {})

    # top-level ruff ignore (e.g., [tool.ruff] ignore = [...])
    top_ignore = ruff_tool.get("ignore")
    if top_ignore:
        violations.append(
            LintBypassViolation(
                file_path=str(pyproject_path),
                line=0,
                category="global-ignore",
                detail=f"[tool.ruff] ignore = {top_ignore} - "
                f"global ignore weakens lint enforcement",
            )
        )

    # ruff.lint ignore (e.g., [tool.ruff.lint] ignore = [...])
    lint_ignore = ruff_tool.get("lint", {}).get("ignore")
    if lint_ignore:
        violations.append(
            LintBypassViolation(
                file_path=str(pyproject_path),
                line=0,
                category="global-ignore",
                detail=f"[tool.ruff.lint] ignore = {lint_ignore} - "
                f"global ignore weakens lint enforcement",
            )
        )

    return violations


def _collect_py_files(root: Path) -> Iterable[Path]:
    """Yield all Python files under *root*, skipping excluded directories."""
    for path in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        yield path


def audit_codebase(codebase_root: Path) -> tuple[list[LintBypassViolation], int]:
    """Audit the entire codebase for lint bypass violations.

    Returns (violations, files_checked).
    """
    all_violations: list[LintBypassViolation] = []
    files_checked = 0

    for py_file in sorted(_collect_py_files(codebase_root)):
        files_checked += 1
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        lines = content.splitlines()
        rel_path = str(py_file.relative_to(codebase_root))
        violations = _find_noqa_violations(lines, rel_path)
        all_violations.extend(violations)

    # Also check pyproject.toml config.
    pyproject_path = codebase_root / "pyproject.toml"
    config_violations = _check_pyproject_config(pyproject_path)
    all_violations.extend(config_violations)

    return all_violations, files_checked


def main(argv: list[str] | None = None) -> int:
    """Run the lint bypass audit and return exit code.

    Exit code 0: no violations found.
    Exit code 1: violations found.
    Exit code 2: error.
    """
    args = argv if argv is not None else sys.argv[1:]

    if args:
        codebase_root = Path(args[0])
    else:
        # Default: scan the ralph-workflow package root.
        codebase_root = Path(__file__).parent.parent.parent

    if not codebase_root.is_dir():
        print(f"Error: directory not found: {codebase_root}", file=sys.stderr)
        return 2

    print(f"Auditing lint bypass in: {codebase_root}")
    print()

    violations, files_checked = audit_codebase(codebase_root)

    if violations:
        print(
            f"LINT BYPASS VIOLATIONS FOUND: {len(violations)} violation(s) "
            f"in {files_checked} file(s)"
        )
        print("=" * 72)
        for v in violations:
            print(f"  {v}")
        print()
        print("These violations weaken lint enforcement. Fix the violation, not the audit.")
        print("Guidance: AGENTS.md §'Non-negotiables' — no weakening checks.")
        return 1

    print(f"No lint bypass violations found in {files_checked} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
