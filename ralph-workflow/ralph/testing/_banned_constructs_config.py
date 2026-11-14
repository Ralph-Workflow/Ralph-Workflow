from __future__ import annotations

import tomllib
from configparser import ConfigParser
from configparser import Error as ConfigParserError
from typing import TYPE_CHECKING

from ralph.testing._banned_constructs_model import Finding, _AuditInputError, _label

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

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
