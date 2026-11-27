from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable

_PROSE_SUFFIXES: Final[tuple[str, ...]] = (".md", ".rst", ".txt", ".adoc")

_EXEMPT_SEGMENTS: Final[tuple[str, ...]] = (
    "vendor/",
    "node_modules/",
    "third_party/",
    "generated/",
    "__generated__/",
    "app/assets/builds/",
)

_EXEMPT_SUFFIXES: Final[tuple[str, ...]] = ("_pb2.py", "_pb2.pyi", ".gen.ts", ".gen.go")

_EXEMPT_FILES: Final[tuple[str, ...]] = ("db/schema.rb", "db/structure.sql")

_INLINE_SUPPRESSION_RE: Final[re.Pattern[str]] = re.compile(
    r"eslint-disable|@ts-(?:ignore|expect-error|nocheck)|(?:istanbul|c8)\s+ignore"
    r"|biome-ignore|rubocop:(?:disable|todo)|#\s*typed:\s*(?:false|ignore)"
    r"|#\s*noqa\b|#\s*type:\s*ignore|pyright:\s*ignore|pylint:\s*disable"
    r"|//\s*nolint\b|#\[allow\(|@SuppressWarnings",
    re.IGNORECASE,
)

_ESLINT_OFF_RE: Final[re.Pattern[str]] = re.compile(
    r"""['"]off['"]|:\s*0\s*,?\s*$|:\s*\[\s*(?:['"]off['"]|0)\b"""
)

_QUOTED_RE: Final[re.Pattern[str]] = re.compile(r"""['"]([^'"]+)['"]""")

_TS_STRICT_OFF_RE: Final[re.Pattern[str]] = re.compile(
    r'"(?:strict|noImplicitAny|strictNullChecks|strictFunctionTypes|strictBindCallApply'
    r"|strictPropertyInitialization|noImplicitThis|noImplicitReturns|alwaysStrict"
    r"|useUnknownInCatchVariables|noUncheckedIndexedAccess|exactOptionalPropertyTypes"
    r'|checkJs)"\s*:\s*false'
)

_PY_CONFIG_LOOSENING_RE: Final[re.Pattern[str]] = re.compile(
    r"ignore_missing_imports\s*=\s*true|ignore_errors\s*=\s*true"
    r"|follow_imports\s*=\s*['\"]?(?:silent|skip)|disallow_untyped_defs\s*=\s*false"
    r"|\bstrict\s*=\s*false|^\s*(?:extend-)?ignore\s*=|per-file-ignores"
    r"""|^\s*['"][^'"]+['"]\s*=\s*\[""",
    re.IGNORECASE,
)

_RUBOCOP_DISABLED_RE: Final[re.Pattern[str]] = re.compile(r"^\s*Enabled:\s*false\b")

_RUBOCOP_MAX_RE: Final[re.Pattern[str]] = re.compile(r"^\s*Max:\s*(\d+)\b")

_PY_CONFIG_FILES: Final[tuple[str, ...]] = (
    "pyproject.toml",
    "setup.cfg",
    "mypy.ini",
    ".mypy.ini",
    "ruff.toml",
    ".ruff.toml",
    ".flake8",
    "tox.ini",
    ".pylintrc",
    "pyrightconfig.json",
)


def _exempt(path: str) -> bool:
    return (
        any(segment in f"/{path}" for segment in (f"/{s}" for s in _EXEMPT_SEGMENTS))
        or path.endswith(_EXEMPT_SUFFIXES)
        or path in _EXEMPT_FILES
    )


def _names_only_exempt_paths(line: str) -> bool:
    quoted: list[str] = [str(match.group(1)) for match in _QUOTED_RE.finditer(line)]
    return bool(quoted) and all(_exempt(glob.lstrip("./")) for glob in quoted)


def _eslint_loosening(line: str, _removed: list[str]) -> bool:
    if "ignores" in line:
        return not _names_only_exempt_paths(line)
    return bool(_ESLINT_OFF_RE.search(line))


def _tsconfig_loosening(line: str, _removed: list[str]) -> bool:
    return bool(_TS_STRICT_OFF_RE.search(line))


def _python_config_loosening(line: str, _removed: list[str]) -> bool:
    if not _PY_CONFIG_LOOSENING_RE.search(line):
        return False
    return not _names_only_exempt_paths(line.split("=", 1)[0])


def _rubocop_loosening(line: str, removed: list[str]) -> bool:
    if _RUBOCOP_DISABLED_RE.search(line):
        return True
    raised = _RUBOCOP_MAX_RE.search(line)
    if raised is None:
        return False
    previous = [int(m.group(1)) for old in removed if (m := _RUBOCOP_MAX_RE.search(old))]
    return not previous or int(raised.group(1)) > min(previous)


def _config_kind(name: str) -> Callable[[str, list[str]], bool] | None:
    if name.startswith(("eslint.config", ".eslintrc")):
        return _eslint_loosening
    if name.startswith("tsconfig") and name.endswith(".json"):
        return _tsconfig_loosening
    if name in _PY_CONFIG_FILES:
        return _python_config_loosening
    if name.startswith(".rubocop") and name.endswith((".yml", ".yaml")):
        return _rubocop_loosening
    return None


def _files(diff_text: str) -> list[tuple[str, list[str], list[str]]]:
    files: list[tuple[str, list[str], list[str]]] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            path = target[2:] if target.startswith("b/") else target
            files.append((path, [], []))
        elif not files or line.startswith("--- "):
            continue
        elif line.startswith("+"):
            files[-1][1].append(line[1:])
        elif line.startswith("-"):
            files[-1][2].append(line[1:])
    return files


def added_lint_bypasses(diff_text: str) -> list[str]:
    found: list[str] = []
    for path, added, removed in _files(diff_text):
        if path == "/dev/null" or path.lower().endswith(_PROSE_SUFFIXES) or _exempt(path):
            continue
        loosens = _config_kind(path.rsplit("/", 1)[-1])
        found.extend(
            f"{path}: {line.strip()}"
            for line in added
            if _INLINE_SUPPRESSION_RE.search(line)
            or (loosens is not None and loosens(line, removed))
        )
    return found


__all__ = ["added_lint_bypasses"]
