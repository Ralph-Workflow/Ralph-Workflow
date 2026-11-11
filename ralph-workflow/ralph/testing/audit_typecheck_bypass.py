from __future__ import annotations

import sys
import tomllib
from configparser import ConfigParser
from pathlib import Path
from typing import cast

_WEAKENING_MYPY_KEYS: dict[str, str] = {
    "ignore_missing_imports": "ignore_missing_imports = true",
    "follow_imports": "follow_imports = silent",
    "ignore_errors": "ignore_errors = true",
}
_WEAKENING_MYPY_VALUES: dict[str, frozenset[str]] = {
    "ignore_missing_imports": frozenset({"true"}),
    "follow_imports": frozenset({"silent"}),
    "ignore_errors": frozenset({"true"}),
}

_MYPY_CONFIG_CATEGORY = "mypy-config"


def _mypy_value_weakens(key: str, value: str) -> bool:
    allowed = _WEAKENING_MYPY_VALUES.get(key)
    return allowed is not None and value in allowed


class TypecheckBypassViolation:
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
        return f"{self.file_path}:{self.line}: [TYPECHECK-BYPASS] {self.category}: {self.detail}"


def _load_toml_root(path: Path) -> dict[str, object] | None:
    try:
        parsed_obj: object = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError:
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


def _ini_value(config: ConfigParser, section_name: str, key: str) -> str | None:
    if not config.has_option(section_name, key):
        return None
    try:
        return config.get(section_name, key).strip()
    except Exception:
        return None


def _weakening_key_violation(
    rel_path: str,
    section_name: str,
    config: ConfigParser,
    key: str,
    pattern: str,
) -> TypecheckBypassViolation | None:
    raw = _ini_value(config, section_name, key)
    if raw is None:
        return None
    if not _mypy_value_weakens(key, raw.lower()):
        return None
    return TypecheckBypassViolation(
        file_path=rel_path,
        line=0,
        category=_MYPY_CONFIG_CATEGORY,
        detail=f"[{section_name}] {pattern} — weakens type checking",
    )


def _disable_error_code_violation(
    rel_path: str,
    section_name: str,
    config: ConfigParser,
) -> TypecheckBypassViolation | None:
    raw = _ini_value(config, section_name, "disable_error_code")
    if raw is None:
        return None
    disable_value = raw or "<parse error>"
    if not disable_value:
        return None
    return TypecheckBypassViolation(
        file_path=rel_path,
        line=0,
        category=_MYPY_CONFIG_CATEGORY,
        detail=f"[{section_name}] disable_error_code = {disable_value} — "
        f"globally suppresses mypy error codes, weakening enforcement",
    )


def _false_flag_violation(
    rel_path: str,
    section_name: str,
    config: ConfigParser,
    key: str,
    detail: str,
) -> TypecheckBypassViolation | None:
    raw = _ini_value(config, section_name, key)
    if raw is None:
        return None
    if raw.lower() != "false":
        return None
    return TypecheckBypassViolation(
        file_path=rel_path,
        line=0,
        category=_MYPY_CONFIG_CATEGORY,
        detail=f"[{section_name}] {key} = false — {detail}",
    )


def _exclude_violation(
    rel_path: str,
    section_name: str,
    config: ConfigParser,
) -> TypecheckBypassViolation | None:
    raw = _ini_value(config, section_name, "exclude")
    if raw is None:
        return None
    return TypecheckBypassViolation(
        file_path=rel_path,
        line=0,
        category=_MYPY_CONFIG_CATEGORY,
        detail=f"[{section_name}] exclude = {raw} — "
        f"excludes files from type checking, weakening enforcement",
    )


def _mypy_section_violations(
    rel_path: str,
    section_name: str,
    config: ConfigParser,
) -> list[TypecheckBypassViolation]:
    violations: list[TypecheckBypassViolation] = []
    for key, pattern in _WEAKENING_MYPY_KEYS.items():
        violation = _weakening_key_violation(rel_path, section_name, config, key, pattern)
        if violation is not None:
            violations.append(violation)
    disable_error_code = _disable_error_code_violation(rel_path, section_name, config)
    if disable_error_code is not None:
        violations.append(disable_error_code)
    warn_unused_ignores = _false_flag_violation(
        rel_path,
        section_name,
        config,
        "warn_unused_ignores",
        "weakens type checking by silencing unused ignore warnings",
    )
    if warn_unused_ignores is not None:
        violations.append(warn_unused_ignores)
    disallow_untyped_defs = _false_flag_violation(
        rel_path,
        section_name,
        config,
        "disallow_untyped_defs",
        "weakens type checking by allowing untyped function definitions",
    )
    if disallow_untyped_defs is not None:
        violations.append(disallow_untyped_defs)
    exclude = _exclude_violation(rel_path, section_name, config)
    if exclude is not None:
        violations.append(exclude)
    return violations


def _check_mypy_ini(config_path: Path) -> list[TypecheckBypassViolation]:
    violations: list[TypecheckBypassViolation] = []

    if not config_path.is_file():
        return violations

    try:
        content = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations

    config = ConfigParser()
    try:
        config.read_string(content)
    except Exception:
        return violations

    rel_path = str(config_path)

    for section_name in config.sections():
        if not isinstance(section_name, str):
            continue
        if not section_name.startswith("mypy"):
            continue
        violations.extend(_mypy_section_violations(rel_path, section_name, config))

    return violations


def _pyproject_weakening_key_violation(
    rel_path: str,
    tool_mypy: dict[str, object],
    key: str,
    pattern: str,
) -> TypecheckBypassViolation | None:
    if key not in tool_mypy:
        return None
    value = str(tool_mypy[key]).strip().lower()
    if not _mypy_value_weakens(key, value):
        return None
    return TypecheckBypassViolation(
        file_path=rel_path,
        line=0,
        category=_MYPY_CONFIG_CATEGORY,
        detail=f"[tool.mypy] {pattern} — weakens type checking",
    )


def _pyproject_false_flag_violation(
    rel_path: str,
    tool_mypy: dict[str, object],
    key: str,
    detail: str,
) -> TypecheckBypassViolation | None:
    if tool_mypy.get(key) is not False:
        return None
    return TypecheckBypassViolation(
        file_path=rel_path,
        line=0,
        category=_MYPY_CONFIG_CATEGORY,
        detail=f"[tool.mypy] {key} = false - {detail}",
    )


def _check_pyproject_mypy(pyproject_path: Path) -> list[TypecheckBypassViolation]:
    violations: list[TypecheckBypassViolation] = []

    if not pyproject_path.is_file():
        return violations

    data = _load_toml_root(pyproject_path)
    if data is None:
        return violations

    tool_mypy = _nested_mapping(data, "tool", "mypy")
    if not tool_mypy:
        return violations

    rel_path = str(pyproject_path)

    for key, pattern in _WEAKENING_MYPY_KEYS.items():
        violation = _pyproject_weakening_key_violation(rel_path, tool_mypy, key, pattern)
        if violation is not None:
            violations.append(violation)

    if "exclude" in tool_mypy:
        violations.append(
            TypecheckBypassViolation(
                file_path=rel_path,
                line=0,
                category=_MYPY_CONFIG_CATEGORY,
                detail=f"[tool.mypy] exclude = {tool_mypy['exclude']} - "
                f"excludes files from type checking, weakening enforcement",
            )
        )

    disable_error_code = tool_mypy.get("disable_error_code")
    if disable_error_code:
        violations.append(
            TypecheckBypassViolation(
                file_path=rel_path,
                line=0,
                category=_MYPY_CONFIG_CATEGORY,
                detail=f"[tool.mypy] disable_error_code = {disable_error_code} - "
                f"globally suppresses mypy error codes, weakening enforcement",
            )
        )

    warn_unused_ignores = _pyproject_false_flag_violation(
        rel_path,
        tool_mypy,
        "warn_unused_ignores",
        "weakens type checking by silencing unused ignore warnings",
    )
    if warn_unused_ignores is not None:
        violations.append(warn_unused_ignores)

    disallow_untyped_defs = _pyproject_false_flag_violation(
        rel_path,
        tool_mypy,
        "disallow_untyped_defs",
        "weakens type checking by allowing untyped function definitions",
    )
    if disallow_untyped_defs is not None:
        violations.append(disallow_untyped_defs)

    return violations


def _find_mypy_config_files(codebase_root: Path) -> tuple[list[Path], Path | None]:
    ini_configs: list[Path] = []
    for name in ("mypy.ini", ".mypy.ini"):
        p = codebase_root / name
        if p.is_file():
            ini_configs.append(p)
    pyproject = codebase_root / "pyproject.toml"
    return ini_configs, pyproject if pyproject.is_file() else None


def audit_codebase(codebase_root: Path) -> tuple[list[TypecheckBypassViolation], int]:
    all_violations: list[TypecheckBypassViolation] = []

    ini_configs, pyproject_path = _find_mypy_config_files(codebase_root)
    for ini_path in ini_configs:
        config_violations = _check_mypy_ini(ini_path)
        all_violations.extend(config_violations)
    files_checked = len(ini_configs)
    if pyproject_path is not None:
        pyproject_violations = _check_pyproject_mypy(pyproject_path)
        all_violations.extend(pyproject_violations)
        files_checked += 1

    return all_violations, files_checked


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]

    codebase_root = Path(args[0]) if args else Path(__file__).parent.parent.parent

    if not codebase_root.is_dir():
        print(f"Error: directory not found: {codebase_root}", file=sys.stderr)
        return 2

    print(f"Auditing typecheck bypass in: {codebase_root}")
    print()

    violations, files_checked = audit_codebase(codebase_root)

    if violations:
        print(
            f"TYPECHECK BYPASS VIOLATIONS FOUND: {len(violations)} violation(s) "
            f"in {files_checked} configuration file(s)"
        )
        print("=" * 72)
        for v in violations:
            print(f"  {v}")
        print()
        print("These violations weaken typecheck enforcement. Fix the violation, not the audit.")
        print("Guidance: docs/agents/type-ignore-policy.md")
        return 1

    print(f"No typecheck bypass violations found in {files_checked} configuration file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
