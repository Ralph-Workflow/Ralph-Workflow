from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ralph.testing._banned_constructs_config import _audit_config
from ralph.testing._banned_constructs_model import (
    CONFIG_ENV_MODULE,
    FIX_HINT,
    Finding,
    _AuditInputError,
)
from ralph.testing._banned_constructs_scope import (
    _CONFIG_NAMES,
    _git_root,
    _resolve_explicit_paths,
    _scan_root,
    _tracked_python_paths,
)
from ralph.testing._banned_constructs_source import (
    _class_index,
    _comment_findings,
    _source_units,
    _SourceAuditor,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_GOVERNED_PATHS: Final[tuple[str, ...]] = (
    "pyproject.toml",
    "ralph/checkpoint",
    "ralph/files",
    "ralph/pipeline/conflict_resolution/_driver_rounds.py",
    "ralph/pydantic_validation_errors.py",
    "ralph/testing/audit_banned_constructs.py",
    "ralph/testing/_banned_constructs_config.py",
    "ralph/testing/_banned_constructs_model.py",
    "ralph/testing/_banned_constructs_scope.py",
    "ralph/testing/_banned_constructs_source.py",
)

__all__ = [
    "CONFIG_ENV_MODULE",
    "DEFAULT_GOVERNED_PATHS",
    "FIX_HINT",
    "Finding",
    "audit_paths",
    "audit_repository",
    "main",
]


@dataclass(frozen=True, slots=True)
class _CliOptions:
    paths: tuple[str, ...]
    json_output: bool
    enforce: bool = False


def _finding_key(finding: Finding) -> tuple[str, int, str, str]:
    return (finding.path, finding.line, finding.code, finding.construct)


def _config_paths(paths: Sequence[Path], root: Path) -> list[Path]:
    found = {path for path in paths if path.name in _CONFIG_NAMES}
    if not paths:
        for name in _CONFIG_NAMES:
            candidate = root / name
            if candidate.is_file():
                found.add(candidate.resolve())
    return sorted(found)


def audit_paths(paths: Sequence[str], base: Path | None = None) -> list[Finding]:
    current = (base if base is not None else Path.cwd()).resolve()
    resolved = _resolve_explicit_paths(paths, current)
    package_root = current
    external = any(package_root not in path.parents and path != package_root for path in resolved)
    scope_root = _scan_root(resolved) if external else package_root
    python_paths = [path for path in resolved if path.suffix == ".py"]
    units = _source_units(python_paths, scope_root)
    classes = _class_index(units)
    findings: list[Finding] = []
    for unit in units:
        findings.extend(_comment_findings(unit.source, unit.label))
        findings.extend(_SourceAuditor(unit, classes).run())
    findings.extend(_audit_config(_config_paths(resolved, scope_root), scope_root))
    return sorted(findings, key=_finding_key)


def audit_repository(start: Path | None = None) -> list[Finding]:
    current = (start if start is not None else Path.cwd()).resolve()
    repository = _git_root(current)
    paths = _tracked_python_paths(repository)
    units = _source_units(paths, repository)
    classes = _class_index(units)
    findings: list[Finding] = []
    for unit in units:
        findings.extend(_comment_findings(unit.source, unit.label))
        findings.extend(_SourceAuditor(unit, classes).run())
    package_root = (
        current if (current / "pyproject.toml").is_file() else repository / "ralph-workflow"
    )
    findings.extend(_audit_config(_config_paths((), package_root), package_root))
    return sorted(findings, key=_finding_key)


def _parse_cli(argv: Sequence[str]) -> _CliOptions:
    paths: list[str] = []
    json_output = False
    enforce = False
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--json":
            json_output = True
            index += 1
            continue
        if argument in {"--enforce", "--strict"}:
            enforce = True
            index += 1
            continue
        if argument == "--paths":
            index += 1
            start = index
            while index < len(argv) and not argv[index].startswith("-"):
                paths.append(argv[index])
                index += 1
            if index == start:
                raise _AuditInputError("--paths requires at least one path")
            continue
        raise _AuditInputError(f"unrecognized argument: {argument}")
    return _CliOptions(tuple(paths), json_output, enforce)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        options = _parse_cli(tuple(sys.argv[1:] if argv is None else argv))
        if options.paths:
            findings = audit_paths(options.paths)
        elif options.enforce:
            findings = audit_repository()
        else:
            findings = audit_paths(DEFAULT_GOVERNED_PATHS)
    except _AuditInputError as error:
        sys.stderr.write(f"BANNED-CONSTRUCT AUDIT INPUT ERROR: {error}\n")
        return 2
    if options.json_output:
        payload = [finding.as_json() for finding in findings]
        sys.stdout.write(json.JSONEncoder(sort_keys=True).encode(payload))
        sys.stdout.write("\n")
    else:
        for finding in findings:
            sys.stdout.write(f"{finding.render()}\n")
        if not findings:
            sys.stdout.write("No banned constructs found.\n")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
