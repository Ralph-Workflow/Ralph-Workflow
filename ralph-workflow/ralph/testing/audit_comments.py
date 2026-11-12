from __future__ import annotations

import argparse
import ast
import contextlib
import io
import re
import subprocess
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

__all__ = [
    "COMMENT_SUFFIXES",
    "DOCS_MARKERS",
    "DOCS_SUFFIXES",
    "FIX_HINT",
    "MODE_CSS",
    "MODE_DOCS",
    "MODE_HASH",
    "MODE_JINJA",
    "MODE_JS",
    "MODE_MAKE",
    "MODE_PYTHON",
    "MODE_SHELL",
    "MODE_TOML",
    "MODE_YAML",
    "Finding",
    "audit_paths",
    "audit_repository",
    "main",
    "scan_python_source",
    "scan_source",
]

FIX_HINT = "delete the comment or encode it in code"

MODE_PYTHON = "python"
MODE_SHELL = "shell"
MODE_MAKE = "make"
MODE_TOML = "toml"
MODE_YAML = "yaml"
MODE_JINJA = "jinja"
MODE_DOCS = "docs"
MODE_HASH = "hash"
MODE_JS = "js"
MODE_CSS = "css"

_COMMENT_MODES: dict[str, str] = {
    ".bash": MODE_SHELL,
    ".conf": MODE_HASH,
    ".css": MODE_CSS,
    ".ini": MODE_HASH,
    ".j2": MODE_JINJA,
    ".jinja": MODE_JINJA,
    ".jinja2": MODE_JINJA,
    ".js": MODE_JS,
    ".mk": MODE_MAKE,
    ".py": MODE_PYTHON,
    ".pyi": MODE_PYTHON,
    ".rb": MODE_HASH,
    ".sh": MODE_SHELL,
    ".toml": MODE_TOML,
    ".txt": MODE_JINJA,
    ".yaml": MODE_YAML,
    ".yml": MODE_YAML,
}

COMMENT_SUFFIXES: frozenset[str] = frozenset(_COMMENT_MODES)

MAKEFILE_NAMES: frozenset[str] = frozenset({"Makefile"})

NAME_COMMENT_MODES: dict[str, str] = {
    ".dockerignore": MODE_HASH,
    "Dockerfile": MODE_HASH,
    "py.typed": MODE_HASH,
}

DOCS_SUFFIXES: frozenset[str] = frozenset({".md"})

DOCS_TEXT_SUFFIXES: frozenset[str] = frozenset({".md", ".toml"})

DOCS_MARKERS: tuple[str, ...] = (
    "decision",
    "decided",
    "do not reopen",
    "rationale",
    "non-adoption",
    "we chose",
)

DOCS_MARKER_ROOTS: tuple[tuple[str, ...], ...] = (
    ("docs",),
    ("docs", "ralph-workflow-policy"),
    ("ralph-workflow", "docs"),
    ("ralph-workflow", "ralph", "project_policy", "starters"),
)

DOCS_MARKER_FILES: frozenset[str] = frozenset(
    {
        "AGENTS.md",
        "ralph-workflow/AGENTS.md",
        "ralph-workflow/CHANGELOG.md",
        "ralph-workflow/CONTRIBUTING.md",
        "ralph-workflow/README.md",
    }
)

SCRIPT_DIRS: tuple[str, ...] = (".githooks", "scripts", "ralph-workflow/scripts")

SKIP_DIRS: frozenset[str] = frozenset({"__pycache__", ".git", ".mypy_cache", ".ruff_cache"})

MAX_TEXT_LENGTH = 160

_DOCS_MARKER_RES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (marker, re.compile(r"(?<!\w)" + re.escape(marker) + r"(?!\w)", re.IGNORECASE))
    for marker in DOCS_MARKERS
)

_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")

_INLINE_CODE_RE = re.compile(r"(`+)[^`]*?\1")

_SHELL_WORD_SEPARATORS = ";&|("

_YAML_QUOTED_SEGMENT_RE = re.compile(r"\"[^\"]*\"|'[^']*'")

_YAML_BLOCK_INDICATOR_RE = re.compile(r"(?:^|[\s:\-])[|>][+-]?\d*(?:\s+#.*)?$")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.text} — {FIX_HINT}"


@dataclass(frozen=True)
class _Target:
    path: Path
    rel: str
    mode: str


def _truncate(text: str) -> str:
    if len(text) <= MAX_TEXT_LENGTH:
        return text
    return text[: MAX_TEXT_LENGTH - 3] + "..."


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _group_str(match: re.Match[str], group: int) -> str:
    value = match.group(group)
    if not isinstance(value, str):
        msg = "expected regex capture group to be str"
        raise TypeError(msg)
    return value


def _target_rel(target: _Target) -> str:
    return target.rel


def _finding_order(finding: Finding) -> tuple[str, int, str]:
    return (finding.path, finding.line, finding.text)


def _finding_line_order(finding: Finding) -> tuple[int, str]:
    return (finding.line, finding.text)


def _python_tokens(text: str) -> Iterator[tokenize.TokenInfo]:
    reader = io.StringIO(text).readline
    with contextlib.suppress(tokenize.TokenError, SyntaxError):
        yield from tokenize.generate_tokens(reader)


def _python_docstrings(text: str, path: str) -> list[Finding]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr):
            continue
        value = node.value
        if not isinstance(value, ast.Constant):
            continue
        if not isinstance(value.value, str):
            continue
        findings.append(Finding(path, node.lineno, _truncate(_collapse(value.value))))
    return findings


def _python_comments(text: str, path: str) -> list[Finding]:
    findings: list[Finding] = []
    for token in _python_tokens(text):
        if token.type != tokenize.COMMENT:
            continue
        if token.start[0] == 1 and token.string.startswith("#!"):
            continue
        findings.append(Finding(path, token.start[0], _truncate(token.string.strip())))
    return findings


def scan_python_source(text: str, path: str) -> list[Finding]:
    findings: list[Finding] = []
    if "#" in text:
        findings.extend(_python_comments(text, path))
    findings.extend(_python_docstrings(text, path))
    findings.sort(key=_finding_line_order)
    return findings


def _starts_comment(text: str, index: int, yaml_style: bool, inline_hash: bool) -> bool:
    if index == 0:
        return True
    previous = text[index - 1]
    if previous.isspace():
        return True
    if yaml_style:
        return False
    if inline_hash:
        return True
    return previous in _SHELL_WORD_SEPARATORS


def _make_recipe_line(text: str, index: int) -> bool:
    line_start = text.rfind("\n", 0, index) + 1
    return text[line_start:index].lstrip(" ").startswith("\t")


def _is_make_recipe_literal(text: str, index: int) -> bool:
    if not _make_recipe_line(text, index):
        return False
    return index == 0 or not text[index - 1].isspace()


@dataclass
class _ScanState:
    line: int = 1
    quote: str = ""
    triple: bool = False

    def skip_string(self, text: str, index: int) -> int:
        if self.triple:
            if text.startswith(self.quote * 3, index):
                self.quote = ""
                self.triple = False
                return index + 3
            if text[index] == "\n":
                self.line += 1
            return index + 1
        char = text[index]
        if char == "\\" and self.quote == '"':
            if index + 1 < len(text) and text[index + 1] == "\n":
                self.line += 1
            return index + 2
        if char == self.quote:
            self.quote = ""
            return index + 1
        if char == "\n":
            self.line += 1
        return index + 1


def _yaml_literal_lines(text: str) -> frozenset[int]:
    literal: set[int] = set()
    block_indent: int | None = None
    for line_number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if block_indent is not None:
            if not stripped:
                literal.add(line_number)
                continue
            if len(raw) - len(raw.lstrip(" ")) > block_indent:
                literal.add(line_number)
                continue
            block_indent = None
        content = _YAML_QUOTED_SEGMENT_RE.sub(" ", raw)
        if _YAML_BLOCK_INDICATOR_RE.search(content) is None:
            continue
        block_indent = len(raw) - len(raw.lstrip(" "))
    return frozenset(literal)


def _scan_yaml_comments(text: str, path: str) -> list[Finding]:
    findings: list[Finding] = []
    literal_lines = _yaml_literal_lines(text)
    for line_number, raw in enumerate(text.splitlines(), start=1):
        if line_number in literal_lines:
            continue
        quote = ""
        index = 0
        length = len(raw)
        while index < length:
            char = raw[index]
            if quote:
                if char == "\\" and quote == '"':
                    index += 2
                    continue
                if char == quote:
                    quote = ""
                index += 1
                continue
            if char in {'"', "'"}:
                quote = char
                index += 1
                continue
            if char == "#" and (index == 0 or raw[index - 1].isspace()):
                if not (line_number == 1 and raw.startswith("#!")):
                    findings.append(Finding(path, line_number, _truncate(raw[index:].strip())))
                break
            index += 1
    return findings


def _scan_hash_comments(text: str, path: str, mode: str) -> list[Finding]:
    yaml_style = mode == MODE_YAML
    shell_style = mode in (MODE_SHELL, MODE_MAKE)
    toml_style = mode == MODE_TOML
    inline_hash = mode in (MODE_TOML, MODE_MAKE, MODE_HASH)
    findings: list[Finding] = []
    state = _ScanState()
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if state.quote:
            index = state.skip_string(text, index)
            continue
        if char in {'"', "'"}:
            if toml_style and text.startswith(char * 3, index):
                state.quote = char
                state.triple = True
                index += 3
                continue
            state.quote = char
            index += 1
            continue
        if shell_style and char == "$" and text.startswith("${", index):
            end = text.find("}", index)
            if end < 0:
                break
            state.line += text.count("\n", index, end)
            index = end + 1
            continue
        if char == "#":
            if not _starts_comment(text, index, yaml_style, inline_hash):
                index += 1
                continue
            if inline_hash and mode == MODE_MAKE and _is_make_recipe_literal(text, index):
                index += 1
                continue
            end = text.find("\n", index)
            if end < 0:
                end = length
            if not (state.line == 1 and text.startswith("#!", index)):
                findings.append(Finding(path, state.line, _truncate(text[index:end].strip())))
            index = end
            continue
        if char == "\n":
            state.line += 1
        index += 1
    return findings


def _scan_jinja_comments(text: str, path: str) -> list[Finding]:
    findings: list[Finding] = []
    index = 0
    line = 1
    while True:
        start = text.find("{#", index)
        if start < 0:
            break
        end = text.find("#}", start + 2)
        if end < 0:
            break
        line += text.count("\n", index, start)
        findings.append(Finding(path, line, _truncate(_collapse(text[start + 2 : end]))))
        index = end + 2
    return findings


def _skip_quoted(text: str, index: int, quote: str) -> int:
    length = len(text)
    position = index + 1
    while position < length:
        char = text[position]
        if char == "\\":
            position += 2
            continue
        if char == quote:
            return position + 1
        position += 1
    return length


def _scan_c_style_comments(text: str, path: str, line_comments: bool) -> list[Finding]:
    findings: list[Finding] = []
    line = 1
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in {'"', "'", "`"}:
            end = _skip_quoted(text, index, char)
            line += text.count("\n", index, end)
            index = end
            continue
        if char == "/" and text.startswith("/*", index):
            end = text.find("*/", index + 2)
            body_end = length if end < 0 else end
            findings.append(Finding(path, line, _truncate(_collapse(text[index + 2 : body_end]))))
            line += text.count("\n", index, body_end)
            index = body_end + 2 if end >= 0 else length
            continue
        if line_comments and char == "/" and text.startswith("//", index):
            end = text.find("\n", index)
            if end < 0:
                end = length
            findings.append(Finding(path, line, _truncate(text[index:end].strip())))
            index = end
            continue
        if char == "\n":
            line += 1
        index += 1
    return findings


def _scan_docs_markers(text: str, path: str) -> list[Finding]:
    findings: list[Finding] = []
    fence = ""
    line_number = 0
    for raw in text.splitlines():
        line_number += 1
        fence_match = _FENCE_RE.match(raw)
        if fence_match is not None:
            marker = _group_str(fence_match, 1)
            if not fence:
                fence = marker
                continue
            if marker[0] == fence[0] and len(marker) >= len(fence):
                fence = ""
            continue
        if fence:
            continue
        stripped = _INLINE_CODE_RE.sub(" ", raw)
        for _marker, pattern in _DOCS_MARKER_RES:
            found = pattern.search(stripped)
            if found is not None:
                findings.append(Finding(path, line_number, found.group(0)))
                break
    return findings


def scan_source(text: str, path: str, mode: str) -> list[Finding]:
    if mode == MODE_PYTHON:
        findings = scan_python_source(text, path)
    elif mode == MODE_JINJA:
        findings = _scan_jinja_comments(text, path)
    elif mode == MODE_DOCS:
        findings = _scan_docs_markers(text, path)
    elif mode == MODE_YAML:
        findings = _scan_yaml_comments(text, path)
    elif mode == MODE_CSS:
        findings = _scan_c_style_comments(text, path, False)
    elif mode == MODE_JS:
        findings = _scan_c_style_comments(text, path, True)
    else:
        findings = _scan_hash_comments(text, path, mode)
    return findings


def _comment_mode(path: Path) -> str | None:
    if path.name in MAKEFILE_NAMES:
        return MODE_MAKE
    named_mode = NAME_COMMENT_MODES.get(path.name)
    if named_mode is not None:
        return named_mode
    return _COMMENT_MODES.get(path.suffix)


def _is_docs_target(rel: Path) -> bool:
    if rel.as_posix() in DOCS_MARKER_FILES:
        return True
    if len(rel.parts) == 1:
        return rel.suffix in DOCS_SUFFIXES
    parts = rel.parts
    for root in DOCS_MARKER_ROOTS:
        if parts[: len(root)] != root:
            continue
        allowed = DOCS_TEXT_SUFFIXES if len(root) > 1 else DOCS_SUFFIXES
        if rel.suffix in allowed:
            return True
    return False


def _git_output(args: list[str], cwd: Path) -> str | None:
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            cwd=str(cwd),
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _repo_root() -> Path:
    module_path = Path(__file__).resolve()
    output = _git_output(["git", "rev-parse", "--show-toplevel"], module_path.parent)
    if output is not None:
        candidate = output.strip()
        if candidate:
            return Path(candidate)
    return module_path.parents[3]


def _tracked_files(repo_root: Path) -> list[Path]:
    output = _git_output(["git", "-C", str(repo_root), "ls-files", "-z"], repo_root)
    if output is None:
        return []
    return [repo_root / name for name in output.split("\0") if name]


def _script_files(repo_root: Path) -> list[Path]:
    found: list[Path] = []
    for relative in SCRIPT_DIRS:
        base = repo_root / relative
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            found.append(path)
    return found


def _comment_targets(repo_root: Path, tracked: list[Path]) -> list[_Target]:
    targets: dict[Path, _Target] = {}
    for path in tracked:
        try:
            rel = path.relative_to(repo_root)
        except ValueError:
            continue
        mode = _comment_mode(path)
        if mode is None:
            continue
        targets[path] = _Target(path, rel.as_posix(), mode)
    for path in _script_files(repo_root):
        if path in targets:
            continue
        mode = _comment_mode(path)
        if mode is None and path.suffix == "":
            mode = MODE_SHELL
        if mode is None:
            continue
        try:
            rel = path.relative_to(repo_root)
        except ValueError:
            continue
        targets[path] = _Target(path, rel.as_posix(), mode)
    return sorted(targets.values(), key=_target_rel)


def _docs_targets(repo_root: Path, tracked: list[Path]) -> list[_Target]:
    targets: list[_Target] = []
    for path in tracked:
        try:
            rel = path.relative_to(repo_root)
        except ValueError:
            continue
        if not _is_docs_target(rel):
            continue
        targets.append(_Target(path, rel.as_posix(), MODE_DOCS))
    return sorted(targets, key=_target_rel)


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _scan_target(path: Path, display: str, mode: str) -> list[Finding]:
    text = _read_text(path)
    if text is None:
        return []
    return scan_source(text, display, mode)


def _display_path(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()


def _resolve_excludes(excludes: Sequence[str], base: Path) -> list[tuple[str, Path]]:
    resolved: list[tuple[str, Path]] = []
    for raw in excludes:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = base / candidate
        resolved.append((raw.rstrip("/"), candidate))
    return resolved


def _is_excluded(path: Path, base: Path, excludes: list[tuple[str, Path]]) -> bool:
    for raw, candidate in excludes:
        if path == candidate or candidate in path.parents:
            return True
        relative = _display_path(path, base)
        if relative == raw or relative.startswith(raw + "/"):
            return True
    return False


def _slice_files(paths: Sequence[str], base: Path) -> list[Path]:
    found: list[Path] = []
    for raw in paths:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = base / candidate
        if candidate.is_dir():
            found.extend(sorted(path for path in candidate.rglob("*") if path.is_file()))
        elif candidate.is_file():
            found.append(candidate)
    unique: dict[Path, Path] = {}
    for path in found:
        unique.setdefault(path, path)
    return sorted(unique)


def audit_paths(
    paths: Sequence[str],
    excludes: Sequence[str] = (),
    base: Path | None = None,
) -> list[Finding]:
    root = base if base is not None else Path.cwd()
    resolved_excludes = _resolve_excludes(excludes, root)
    findings: list[Finding] = []
    for path in _slice_files(paths, root):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if _is_excluded(path, root, resolved_excludes):
            continue
        mode = _comment_mode(path)
        if mode is None:
            if path.suffix not in DOCS_SUFFIXES:
                continue
            mode = MODE_DOCS
        findings.extend(_scan_target(path, _display_path(path, root), mode))
    findings.sort(key=_finding_order)
    return findings


def audit_repository(repo_root: Path | None = None) -> list[Finding]:
    root = repo_root if repo_root is not None else _repo_root()
    tracked = _tracked_files(root)
    findings: list[Finding] = []
    for target in _comment_targets(root, tracked):
        findings.extend(_scan_target(target.path, target.rel, target.mode))
    for target in _docs_targets(root, tracked):
        findings.extend(_scan_target(target.path, target.rel, target.mode))
    findings.sort(key=_finding_order)
    return findings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ralph.testing.audit_comments",
        description=(
            "Fail on every source comment, every docstring and every "
            "documentation decision marker. No baseline, no bypass marker, "
            "no allowlist file."
        ),
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        default=None,
        help="Directories or files to scan instead of the pinned tree.",
    )
    parser.add_argument(
        "--exclude",
        nargs="+",
        default=None,
        help="Paths or path prefixes to leave out of the scan.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    parsed = parser.parse_args(list(argv) if argv is not None else None)
    paths: list[str] | None = getattr(parsed, "paths", None)
    excludes: list[str] | None = getattr(parsed, "exclude", None)
    path_list = list(paths) if paths is not None else []
    exclude_list = list(excludes) if excludes is not None else []
    findings = audit_paths(path_list, exclude_list) if path_list else audit_repository()
    if findings:
        print(f"COMMENT-POLICY FINDINGS: {len(findings)}")
        print("=" * 72)
        for finding in findings:
            print(f"  {finding}")
        print()
        print(f"Fix every finding: {FIX_HINT}.")
        return 1
    print("No comment, docstring or documentation-decision findings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
