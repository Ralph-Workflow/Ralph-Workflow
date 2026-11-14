from __future__ import annotations

import ast
import io
import json
import re
import sys
import tokenize
import tomllib
from configparser import ConfigParser
from configparser import Error as ConfigParserError
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.executor.process import ProcessExecutionError, ProcessRunOptions, run_process
from ralph.testing._banned_constructs_auditor import (
    _class_index,
    _module_from_label,
    _package_from_label,
    _SourceAuditor,
    _SourceUnit,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

CONFIG_ENV_MODULE = "ralph.config._env"
FIX_HINT = "restructure so the type checker can verify it"
_GIT_PROBE_TIMEOUT_SECONDS = 30.0
_SKIP_PARTS = frozenset(
    [
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "site-packages",
        "vendor",
    ]
)
_CONFIG_NAMES = frozenset({"pyproject.toml", "mypy.ini", ".mypy.ini"})
_BANNED_RUFF_RULES = frozenset(
    [
        "ANN002",
        "ANN003",
        "ANN401",
        "ASYNC251",
        "B006",
        "B008",
        "B009",
        "B010",
        "BLE001",
        "E722",
        "E731",
        "F403",
        "F405",
        "PGH003",
        "PGH004",
        "PLC0206",
        "PLR1701",
        "PLW0603",
        "PLW0642",
        "S101",
        "S102",
        "S301",
        "S302",
        "S307",
        "S401",
        "S403",
        "S602",
        "S604",
        "T201",
        "TC006",
    ]
)
_MYPY_FALSE_OPTIONS = frozenset(
    [
        "allow_redefinition",
        "check_untyped_defs",
        "disallow_any_decorated",
        "disallow_any_explicit",
        "disallow_any_expr",
        "disallow_any_unimported",
        "disallow_incomplete_defs",
        "disallow_untyped_defs",
        "no_implicit_optional",
        "strict",
        "strict_equality",
        "strict_optional",
        "warn_return_any",
        "warn_unreachable",
        "warn_unused_configs",
        "warn_unused_ignores",
    ]
)
_MYPY_TRUE_OPTIONS = frozenset(
    [
        "allow_incomplete_defs",
        "allow_untyped_calls",
        "allow_untyped_defs",
        "allow_untyped_globals",
        "ignore_errors",
        "ignore_missing_imports",
        "local_partial_types",
    ]
)
_TYPE_IGNORE_RE = re.compile(r"#\s*type\s*:\s*ignore(?:\s*\[[^]]*\])?", re.IGNORECASE)
_NOQA_RE = re.compile(r"#\s*noqa(?:\s*:|\s|$)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    line: int
    construct: str
    code: str
    rule_id: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.construct} ({self.rule_id}) — {FIX_HINT}"

    def as_json(self) -> dict[str, str | int]:
        return {
            "path": self.path,
            "line": self.line,
            "construct": self.construct,
            "code": self.code,
            "rule_id": self.rule_id,
            "fix_hint": FIX_HINT,
        }


class _AuditInputError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _CliOptions:
    paths: tuple[str, ...]
    json_output: bool
    enforce: bool = False


def _comment_findings(source: str, label: str) -> list[Finding]:
    findings: list[Finding] = []
    reader = io.StringIO(source).readline
    try:
        tokens = tokenize.generate_tokens(reader)
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            if _TYPE_IGNORE_RE.search(token.string) is not None:
                findings.append(
                    Finding(label, token.start[0], "# type: ignore", "TYPE-IGNORE", "PGH003")
                )
            if _NOQA_RE.search(token.string) is not None:
                findings.append(Finding(label, token.start[0], "# noqa", "NOQA", "PGH004"))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return findings
    return findings


def _parse_unit(path: Path, label: str) -> _SourceUnit:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise _AuditInputError(f"cannot read {path}: {error}") from error
    try:
        tree = ast.parse(source, filename=label)
    except SyntaxError as error:
        line = error.lineno if error.lineno is not None else 1
        message = error.msg
        raise _AuditInputError(f"{label}:{line}: cannot parse Python source: {message}") from error
    return _SourceUnit(
        path,
        label,
        _module_from_label(label),
        _package_from_label(label),
        source,
        tree,
        Finding,
    )


def _skip_path(path: Path) -> bool:
    return any(part in _SKIP_PARTS or part.startswith(".venv") for part in path.parts)


def _resolve_explicit_paths(paths: Sequence[str], base: Path) -> list[Path]:
    found: dict[Path, None] = {}
    for raw in paths:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = base / candidate
        candidate = candidate.resolve()
        if candidate.is_file():
            if (candidate.suffix == ".py" or candidate.name in _CONFIG_NAMES) and not _skip_path(
                candidate
            ):
                found[candidate] = None
            continue
        if candidate.is_dir():
            for nested in sorted(candidate.rglob("*.py")):
                if nested.is_file() and not _skip_path(nested):
                    found[nested.resolve()] = None
            for name in _CONFIG_NAMES:
                nested = candidate / name
                if nested.is_file() and not _skip_path(nested):
                    found[nested.resolve()] = None
            continue
        raise _AuditInputError(f"--paths entry is neither a file nor directory: {raw}")
    return sorted(found)


def _git_root(start: Path) -> Path:
    try:
        completed = run_process(
            "git",
            ["rev-parse", "--show-toplevel"],
            options=ProcessRunOptions(
                cwd=start,
                timeout=_GIT_PROBE_TIMEOUT_SECONDS,
                capture_output=True,
                label="testing:audit-banned-constructs",
            ),
        )
    except ProcessExecutionError as error:
        raise _AuditInputError(f"cannot locate git repository: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit status {completed.returncode}"
        raise _AuditInputError(f"cannot locate git repository: {detail}")
    return Path(completed.stdout.strip()).resolve()


def _tracked_python_paths(root: Path) -> list[Path]:
    try:
        completed = run_process(
            "git",
            ["ls-files", "-z", "--", "*.py"],
            options=ProcessRunOptions(
                cwd=root,
                timeout=_GIT_PROBE_TIMEOUT_SECONDS,
                capture_output=True,
                label="testing:audit-banned-constructs",
            ),
        )
    except ProcessExecutionError as error:
        raise _AuditInputError(f"cannot enumerate tracked Python files: {error}") from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit status {completed.returncode}"
        raise _AuditInputError(f"cannot enumerate tracked Python files: {detail}")
    paths: list[Path] = []
    for relative in completed.stdout.split("\0"):
        if not relative:
            continue
        path = root / relative
        if path.is_file() and not _skip_path(Path(relative)):
            paths.append(path.resolve())
    return sorted(paths)


def _common_root(paths: Sequence[Path]) -> Path:
    if not paths:
        return Path.cwd().resolve()
    common = paths[0].parent if paths[0].is_file() else paths[0]
    for path in paths[1:]:
        candidate = path.parent if path.is_file() else path
        while common != common.parent and common not in candidate.parents and common != candidate:
            common = common.parent
    return common


def _scan_root(paths: Sequence[Path]) -> Path:
    for path in paths:
        parts = path.parts
        for root in ("ralph", "tests"):
            if root in parts:
                index = parts.index(root)
                return Path(*parts[:index])
    return _common_root(paths)


def _label(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _source_units(paths: Sequence[Path], label_root: Path) -> list[_SourceUnit]:
    return [_parse_unit(path, _label(path, label_root)) for path in paths]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise _AuditInputError(f"cannot read {path}: {error}") from error


def _load_toml(path: Path) -> dict[str, object]:
    try:
        parsed: object = tomllib.loads(_read_text(path))
    except ValueError as error:
        raise _AuditInputError(f"cannot parse {path}: {error}") from error
    if not isinstance(parsed, dict):
        raise _AuditInputError(f"cannot parse {path}: expected a TOML table")
    return parsed


def _nested_mapping(root: dict[str, object], *keys: str) -> dict[str, object]:
    current: object = root
    for key in keys:
        if not isinstance(current, dict):
            return {}
        nested = current.get(key)
        if not isinstance(nested, dict):
            return {}
        current = nested
    if not isinstance(current, dict):
        return {}
    return {str(key): value for key, value in current.items()}


def _selectors(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list | tuple):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _matching_banned_rules(selectors: Iterable[str]) -> tuple[str, ...]:
    matched: set[str] = set()
    for selector in selectors:
        normalized = selector.strip().upper()
        if not normalized:
            continue
        matched.update(rule for rule in _BANNED_RUFF_RULES if rule.startswith(normalized))
    return tuple(sorted(matched))


def _config_finding(path: str, line: int, construct: str) -> Finding:
    return Finding(path, line, construct, "CONFIG-BYPASS", "CONFIG-BYPASS")


def _audit_pyproject(path: Path, display: str) -> list[Finding]:
    if not path.is_file():
        return []
    data = _load_toml(path)
    findings: list[Finding] = []
    ruff = _nested_mapping(data, "tool", "ruff")
    lint = _nested_mapping(data, "tool", "ruff", "lint")
    for table_name in ("per-file-ignores", "extend-per-file-ignores"):
        for table in (lint.get(table_name), ruff.get(table_name)):
            if not isinstance(table, dict):
                continue
            for pattern, value in table.items():
                findings.extend(
                    _config_finding(
                        display,
                        1,
                        f"ruff {table_name} re-enables {rule} for {pattern}",
                    )
                    for rule in _matching_banned_rules(_selectors(value))
                )
    for setting in ("ignore", "extend-ignore"):
        for table in (lint, ruff):
            rules = _matching_banned_rules(_selectors(table.get(setting)))
            findings.extend(
                _config_finding(display, 1, f"ruff {setting} re-enables {rule}") for rule in rules
            )
    tool = _nested_mapping(data, "tool")
    for table_name, table in tool.items():
        if table_name != "mypy" and not table_name.startswith("mypy-"):
            continue
        if not isinstance(table, dict):
            continue
        for option, value in table.items():
            reason = _mypy_relaxation(option, value)
            if reason is not None:
                findings.append(
                    _config_finding(
                        display,
                        1,
                        f"mypy {table_name} relaxes {reason}",
                    )
                )
    return findings


def _is_false(value: object) -> bool:
    return str(value).strip().strip("\"'").lower() in {"0", "false", "no", "off"}


def _is_true(value: object) -> bool:
    return str(value).strip().strip("\"'").lower() in {"1", "true", "yes", "on"}


def _mypy_relaxation(option: str, value: object) -> str | None:
    key = option.lower()
    if (key in _MYPY_FALSE_OPTIONS or key.startswith("disallow_any_")) and _is_false(value):
        return key
    if (key in _MYPY_TRUE_OPTIONS or key.startswith("allow_untyped_")) and _is_true(value):
        return key
    if key == "follow_imports" and str(value).strip().lower() == "silent":
        return key
    if key in {"disable_error_code", "exclude"} and str(value).strip():
        return key
    return None


def _audit_mypy_ini(path: Path, display: str) -> list[Finding]:
    if not path.is_file():
        return []
    parser = ConfigParser(interpolation=None)
    try:
        parser.read_string(_read_text(path))
    except ConfigParserError as error:
        raise _AuditInputError(f"cannot parse {path}: {error}") from error
    findings: list[Finding] = []
    raw_sections: list[str] = list(parser.sections())
    for raw_section in raw_sections:
        section = str(raw_section)
        if section != "mypy" and not section.startswith("mypy-"):
            continue
        for option, value in parser.items(section):
            reason = _mypy_relaxation(option, value)
            if reason is not None:
                findings.append(_config_finding(display, 1, f"mypy {section} relaxes {reason}"))
    return findings


def _audit_config(paths: Iterable[Path], label_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(set(paths)):
        display = _label(path, label_root)
        if path.name == "pyproject.toml":
            findings.extend(_audit_pyproject(path, display))
        elif path.name in {"mypy.ini", ".mypy.ini"}:
            findings.extend(_audit_mypy_ini(path, display))
    return findings


def _finding_key(finding: Finding) -> tuple[str, int, str, str]:
    return (finding.path, finding.line, finding.code, finding.construct)


def _config_paths(paths: Sequence[Path], root: Path) -> list[Path]:
    found = {path for path in paths if path.name in _CONFIG_NAMES}
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
        if not options.paths and not options.enforce:
            sys.stdout.write(
                "Banned constructs audit (advisory): repository-wide enforcement pending full sweep.\n"
            )
            return 0
        findings = audit_paths(options.paths) if options.paths else audit_repository()
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
