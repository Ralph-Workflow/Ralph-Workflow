from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.testing._audit_baseline import worktree_diff
from ralph.testing._test_integrity_diff import (
    CATEGORY_TEST_WEAKENING,
    PRODUCTION_PACKAGE_DIR,
    TEST_REASON_CONSTANT,
    TEST_TREE_DIR,
    TestIntegrityFinding,
    audit_test_integrity_diff,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "CATEGORY_PYTEST_DETECTION",
    "CATEGORY_TEST_WEAKENING",
    "PRODUCTION_PACKAGE_DIR",
    "TEST_REASON_CONSTANT",
    "TEST_TREE_DIR",
    "TestIntegrityFinding",
    "audit_pytest_detection",
    "audit_test_integrity_diff",
    "main",
]

CATEGORY_PYTEST_DETECTION = "pytest-detection"

_PYTEST_ENV_TOKEN = "PYTEST_CURRENT" + "_TEST"

_PYTEST_MODULE_NAME = "pytest"

_DETECTOR_ENV = "pytest current-test environment variable"
_DETECTOR_MODULE = "pytest module lookup"

_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".venv",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "build",
        "dist",
        "htmlcov",
        "tmp",
    }
)

_USAGE = "Usage: python -m ralph.testing.audit_test_integrity [--diff PATH|-] [--root DIR]"


def _sys_module_names(tree: ast.AST) -> frozenset[str]:
    names = {"sys"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sys":
                    names.add(alias.asname or "sys")
    return frozenset(names)


def _is_sys_modules(node: ast.AST, sys_names: frozenset[str]) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "modules"
        and isinstance(node.value, ast.Name)
        and node.value.id in sys_names
    )


def _is_pytest_constant(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value == _PYTEST_MODULE_NAME
    )


def _is_pytest_env_constant(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value == _PYTEST_ENV_TOKEN
    )


def _is_pytest_env_name(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == _PYTEST_ENV_TOKEN
    if isinstance(node, ast.Attribute):
        return node.attr == _PYTEST_ENV_TOKEN
    return False


def _module_probe_detector(node: ast.AST, sys_names: frozenset[str]) -> str | None:
    if isinstance(node, ast.Subscript):
        probes = _is_sys_modules(node.value, sys_names) and _is_pytest_constant(node.slice)
    elif isinstance(node, ast.Call):
        probes = (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _is_sys_modules(node.func.value, sys_names)
            and bool(node.args)
            and _is_pytest_constant(node.args[0])
        )
    elif isinstance(node, ast.Compare):
        probes = (
            _is_pytest_constant(node.left)
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.In)
            and len(node.comparators) == 1
            and _is_sys_modules(node.comparators[0], sys_names)
        )
    else:
        probes = False
    if probes:
        return _DETECTOR_MODULE
    return None


def _pytest_detector(node: ast.AST, sys_names: frozenset[str]) -> str | None:
    if _is_pytest_env_constant(node) or _is_pytest_env_name(node):
        return _DETECTOR_ENV
    module_detector = _module_probe_detector(node, sys_names)
    if module_detector is not None:
        return module_detector
    return None


def _detection_detail(detector: str) -> str:
    return (
        f"production code reads the test runner through the {detector}: shipped code must "
        "not branch on pytest, or the test stops proving the shipped path. "
        "fix: remove the detection and pass the behaviour in explicitly (a parameter, an "
        "injected flag, or a dependency) instead of sniffing the test environment"
    )


def _detect_pytest_usage(source: str, file_path: str) -> list[TestIntegrityFinding]:
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return []
    sys_names = _sys_module_names(tree)
    findings: list[TestIntegrityFinding] = []
    for node in ast.walk(tree):
        detector = _pytest_detector(node, sys_names)
        if detector is None:
            continue
        lineno: int = getattr(node, "lineno", 0)
        findings.append(
            TestIntegrityFinding(
                file_path=file_path,
                line=lineno,
                category=CATEGORY_PYTEST_DETECTION,
                detail=_detection_detail(detector),
            )
        )
    return findings


def _production_package_root(root: Path) -> Path:
    if root.name == PRODUCTION_PACKAGE_DIR:
        return root
    candidate = root / PRODUCTION_PACKAGE_DIR
    if candidate.is_dir():
        return candidate
    msg = f"production package not found under root: {root}"
    raise FileNotFoundError(msg)


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def audit_pytest_detection(root: Path) -> list[TestIntegrityFinding]:
    package_root = _production_package_root(root)
    findings: list[TestIntegrityFinding] = []
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(package_root)
        if any(part in _SKIP_DIRS for part in relative.parts):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "pytest" not in source.lower():
            continue
        findings.extend(_detect_pytest_usage(source, _relative_path(path, root)))
    findings.sort(key=lambda finding: (finding.file_path, finding.line, finding.category))
    return findings


def _default_root() -> Path:
    return Path(__file__).parent.parent.parent


def _read_diff(diff_path: str) -> str:
    if diff_path == "-":
        text: str = sys.stdin.read()
        return text
    return Path(diff_path).read_text(encoding="utf-8")


def _parse_arguments(args: Sequence[str]) -> tuple[str | None, str | None] | None:
    diff_path: str | None = None
    root_arg: str | None = None
    index = 0
    while index < len(args):
        argument = args[index]
        if argument not in {"--diff", "--root"}:
            print(f"Error: unknown argument: {argument}", file=sys.stderr)
            return None
        index += 1
        if index >= len(args):
            print(f"Error: {argument} requires a value", file=sys.stderr)
            return None
        if argument == "--diff":
            diff_path = args[index]
        else:
            root_arg = args[index]
        index += 1
    return diff_path, root_arg


def _collect_findings(root: Path, diff_text: str | None) -> list[TestIntegrityFinding]:
    findings: list[TestIntegrityFinding] = []
    if diff_text is not None:
        findings.extend(audit_test_integrity_diff(diff_text))
    findings.extend(audit_pytest_detection(root))
    return findings


def _resolve_root(root_arg: str | None) -> Path | None:
    root = Path(root_arg) if root_arg else _default_root()
    if not root.is_dir():
        print(f"Error: audit root not found: {root}", file=sys.stderr)
        return None
    try:
        _production_package_root(root)
    except FileNotFoundError:
        print(f"Error: production package not found under root: {root}", file=sys.stderr)
        return None
    return root


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else list(sys.argv[1:])
    if any(argument in {"-h", "--help"} for argument in args):
        print(_USAGE)
        return 0
    parsed = _parse_arguments(args)
    if parsed is None:
        print(_USAGE, file=sys.stderr)
        return 2
    diff_path, root_arg = parsed

    root = _resolve_root(root_arg)
    if root is None:
        return 2

    diff_text = ""
    if diff_path is not None:
        try:
            diff_text = _read_diff(diff_path)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"Error: could not read diff {diff_path}: {exc}", file=sys.stderr)
            return 2

    else:
        diff_text = worktree_diff(root)

    diff_label = diff_path if diff_path is not None else "(working-tree diff vs HEAD)"
    print(f"Auditing test integrity: root={root} diff={diff_label}")
    print()

    findings = _collect_findings(root, diff_text)

    if findings:
        print(f"TEST-INTEGRITY FINDINGS: {len(findings)} finding(s)")
        print("=" * 72)
        for finding in findings:
            print(f"  {finding}")
        print()
        print(
            "A test may not be weakened to let a change pass, and production code may not "
            "detect the test runner. Restore the check (or record why it changed with a "
            f"'{TEST_REASON_CONSTANT}' entry in the test module) and remove the "
            "pytest detection from production code."
        )
        return 1

    print("No test-integrity findings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
