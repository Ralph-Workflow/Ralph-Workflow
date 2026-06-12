"""Lint bypass audit — detects forbidden noqa and per-file-ignores.

Scans the codebase for:
- Bare ``# noqa`` comments without a specific error code
- ``# noqa: CODE`` where CODE is not in the allowlist
- ``[tool.ruff.lint.per-file-ignores]``, ``extend-per-file-ignores``, ``ignore``,
  or ``extend-ignore`` in pyproject.toml

Usage:
    python -m ralph.testing.audit_lint_bypass [codebase_root]

Returns exit code 0 if no lint bypass violations found, 1 otherwise.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterable


def _load_toml_root(path: Path) -> dict[str, object] | None:
    try:
        parsed_obj: object = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(parsed_obj, dict):
        return None
    return cast("dict[str, object]", parsed_obj)


def _nested_mapping(root: dict[str, object], *keys: str) -> dict[str, object]:
    current: object = root
    for key in keys:
        if not isinstance(current, dict):
            return {}
        mapping = cast("dict[str, object]", current)
        next_value = mapping.get(key)
        if next_value is None:
            return {}
        current = next_value
    if not isinstance(current, dict):
        return {}
    return cast("dict[str, object]", current)


def _string_key_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    mapping = cast("dict[object, object]", value)
    return {
        raw_key: raw_value
        for raw_key, raw_value in mapping.items()
        if isinstance(raw_key, str)
    }


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
    ("audit_test_policy", "PLR0911"),
    ("audit_test_policy", "PLW0603"),
    ("audit_typecheck_bypass", "PLR0912"),
    ("audit_lint_bypass", "PLR0912"),
    ("commit_executor", "PLC0415"),
    ("worker_runtime", "PLC0415"),
    ("commit_cleanup", "PLC0415"),
    ("materialize", "PLC0415"),
    ("supervising", "PLC0415"),
    ("pytest_timeout_plugin", "PLC0415"),
    ("_event_classification", "PLC0415"),
    ("commit_plumbing", "UP047"),
    ("claude_interactive_transcript_parser", "PLR0911"),
    ("claude_interactive_transcript_parser", "PLR0912"),
    ("_metrics", "PLW0603"),
    ("_renderers", "PLR0912"),
    ("parallel_display", "PLR0912"),
    ("pydantic_validation_errors", "PLR0911"),
}

# Files to skip entirely (test fixtures, generated code, etc.).
_SKIP_DIRS: frozenset[str] = frozenset({
    "__pycache__",
    ".venv",
    ".mypy_cache",
    "tmp",
    ".ruff_cache",
    ".pytest_cache",
    "htmlcov",
    "build",
    "dist",
})

# ---------------------------------------------------------------------------
# Allowlist: legitimate per-file-ignores entries
#
# Format: dict[str, dict[str, set[str]]] where:
#   - outer key: error code (e.g., "PLR2004", "PLC0415")
#   - value: dict with keys "pattern" (file glob) and "reason" (justification)
#
# Any [tool.ruff.lint.per-file-ignores] entry whose codes match an allowlist
# code AND file pattern matches the allowlist pattern is permitted.
# Any code NOT in this allowlist or applied to a non-matching file pattern
# still triggers a violation.
# ---------------------------------------------------------------------------
_PYPROJECT_IGNORE_ALLOWLIST: dict[str, dict[str, object]] = {
    "PLR2004": {
        "pattern": "tests/**/*.py",
        "reason": "Magic values in tests are acceptable",
    },
    "PLC0415": {
        "pattern": [
            "ralph/cli/**/*.py",
            "ralph/config/**/*.py",
            "ralph/display/**/*.py",
        ],
        "reason": "Lazy imports avoid circular dependencies in CLI/config/display",
    },
}

# Regex for matching ``noqa`` directives on a line.
# Matches both code-specific (colon format) and blanket (no colon) forms,
# including bare noqa with trailing non-colon text.
_NOQA_RE = re.compile(r"#\s*noqa(?:\s*:\s*(.*?))?(?:\s*$|\s+\S)")

# Files that are explicitly testing or documenting lint-bypass behavior and must
# contain simulated directives as fixtures. These are exempt from the noqa check.
_TEST_NOQA_EXEMPT_STEMS: frozenset[str] = frozenset({
    "test_audit_lint_bypass",
    "audit_lint_bypass",
})

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

        if file_stem in _TEST_NOQA_EXEMPT_STEMS:
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
            # Bare ``noqa`` without specific codes.
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
        codes = [c.strip() for c in str(codes_str).split(",") if c.strip()]
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


def _check_pyproject_config(pyproject_path: Path) -> list[LintBypassViolation]:  # noqa: PLR0912
    """Check pyproject.toml for per-file-ignores violations."""
    violations: list[LintBypassViolation] = []

    if not pyproject_path.is_file():
        return violations

    data = _load_toml_root(pyproject_path)
    if data is None:
        return violations

    ruff_lint = _nested_mapping(data, "tool", "ruff", "lint")

    per_file_ignores = _string_key_mapping(ruff_lint.get("per-file-ignores", {}))
    if per_file_ignores:
        for file_pattern, codes in per_file_ignores.items():
            # Normalize codes value: if it's a list, iterate; else treat as single.
            code_list: list[object] = list(codes) if isinstance(codes, list) else [codes]
            for code_raw in code_list:
                code = str(code_raw)
                # Check allowlist: if code is allowlisted AND file_pattern matches
                # the allowlist pattern, skip. Otherwise flag as violation.
                if code in _PYPROJECT_IGNORE_ALLOWLIST:
                    allowlist_entry = _PYPROJECT_IGNORE_ALLOWLIST[code]
                    allowed_patterns = allowlist_entry["pattern"]
                    if isinstance(allowed_patterns, list):
                        if file_pattern in allowed_patterns:
                            continue
                    elif file_pattern == allowed_patterns:
                        continue  # Allowlisted code + matching pattern — permitted
                    # Allowlisted code but wrong file pattern — flag.
                    violations.append(
                        LintBypassViolation(
                            file_path=str(pyproject_path),
                            line=0,
                            category="per-file-ignores",
                            detail=f"[tool.ruff.lint.per-file-ignores] '{file_pattern}': {code} — "
                            f"allowlisted code {code} applied to non-matching file pattern "
                            f"(expected '{allowlist_entry['pattern']}')",
                        )
                    )
                else:
                    # Code not in allowlist — flag.
                    violations.append(
                        LintBypassViolation(
                            file_path=str(pyproject_path),
                            line=0,
                            category="per-file-ignores",
                            detail=f"[tool.ruff.lint.per-file-ignores] '{file_pattern}': {code} — "
                            f"code {code} is not in the per-file-ignores allowlist",
                        )
                    )

    extend_per_file_ignores = _string_key_mapping(
        ruff_lint.get("extend-per-file-ignores", {}),
    )
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
    ruff_tool = _nested_mapping(data, "tool", "ruff")

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
    lint_ignore = ruff_lint.get("ignore")
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

    # ruff.lint extend-ignore (e.g., [tool.ruff.lint] extend-ignore = [...])
    extend_ignore = ruff_lint.get("extend-ignore")
    if extend_ignore:
        violations.append(
            LintBypassViolation(
                file_path=str(pyproject_path),
                line=0,
                category="global-ignore",
                detail=f"[tool.ruff.lint] extend-ignore = {extend_ignore} - "
                f"extend-ignore weakens lint enforcement",
            )
        )

    return violations


def _collect_py_files(root: Path) -> Iterable[Path]:
    """Yield all Python files under *root*, skipping excluded directories."""
    for path in root.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
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

    codebase_root = (
        Path(args[0])
        if args
        else Path(__file__).parent.parent.parent  # default: ralph-workflow root
    )

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
