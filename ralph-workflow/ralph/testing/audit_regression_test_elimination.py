"""Fail-closed audit for regression-test-elimination source markers.

A ``# regression-test-elimination: tests/path.py::test_id`` comment on a
production boundary names the test that must fail if the eliminated behavior is
reintroduced. The audit scans every Python file under ``ralph/`` and requires
that each marker name an existing Python test module under ``tests/``.
"""

from __future__ import annotations

import sys
import tokenize
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_MARKER = "regression-test-elimination:"


@dataclass(frozen=True)
class RegressionTestEliminationViolation:
    """One malformed or unresolved regression-test-elimination marker."""

    kind: str
    file_path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.kind}] {self.message}"


def _marker_tokens(source: str) -> list[tuple[int, str]]:
    """Return line-numbered marker payloads from real Python comments only."""
    return [
        (token.start[0], token.string.split(_MARKER, 1)[1].strip())
        for token in tokenize.generate_tokens(StringIO(source).readline)
        if token.type == tokenize.COMMENT and _MARKER in token.string
    ]


def _violation(
    kind: str,
    file_path: str,
    line: int,
    token: str,
    message: str,
) -> RegressionTestEliminationViolation:
    return RegressionTestEliminationViolation(
        kind=kind,
        file_path=file_path,
        line=line,
        message=f"{message}: {token!r}",
    )


def audit_regression_test_elimination(repo_root: Path) -> list[RegressionTestEliminationViolation]:
    """Validate every regression-test-elimination marker beneath ``ralph/``."""
    source_root = repo_root / "ralph"
    tests_root = repo_root / "tests"
    if not source_root.is_dir():
        return [
            _violation(
                "missing_production_root",
                "ralph",
                0,
                "ralph",
                "production root could not be walked",
            )
        ]
    violations: list[RegressionTestEliminationViolation] = []
    resolved_tests_root = tests_root.resolve()
    for source_path in sorted(source_root.rglob("*.py")):
        if "__pycache__" in source_path.parts:
            continue
        rel_path = source_path.relative_to(repo_root).as_posix()
        try:
            source = source_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            violations.append(
                _violation("unreadable_module", rel_path, 0, rel_path, f"module could not be read ({exc})")
            )
            continue
        for line, token in _marker_tokens(source):
            test_path, separator, _test_id = token.partition("::")
            if not separator or not test_path.startswith("tests/") or not test_path.endswith(".py"):
                violations.append(
                    _violation(
                        "malformed_marker",
                        rel_path,
                        line,
                        token,
                        "marker must be tests/path.py::test-id",
                    )
                )
                continue
            candidate = repo_root / test_path
            try:
                candidate.resolve().relative_to(resolved_tests_root)
            except (OSError, ValueError):
                violations.append(
                    _violation(
                        "test_path_outside_tests",
                        rel_path,
                        line,
                        token,
                        "test path escapes tests/",
                    )
                )
                continue
            if not candidate.is_file():
                violations.append(
                    _violation(
                        "missing_test_file",
                        rel_path,
                        line,
                        token,
                        "referenced test file is missing",
                    )
                )
    return violations


def main(argv: Sequence[str] | None = None) -> int:
    """Run the marker audit and return 0 when every declared proof exists."""
    if argv is None:
        argv = sys.argv[1:]
    repo_root = Path(argv[0]) if argv else Path(__file__).parent.parent.parent
    if not repo_root.is_dir():
        print(f"Repository root not found: {repo_root}", file=sys.stderr)
        return 2
    violations = audit_regression_test_elimination(repo_root)
    if violations:
        for violation in violations:
            print(violation)
        return 1
    print("regression-test elimination audit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
