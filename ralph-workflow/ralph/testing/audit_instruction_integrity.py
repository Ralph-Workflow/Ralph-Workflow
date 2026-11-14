from __future__ import annotations

import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from ralph.project_policy import markers as project_policy_markers
from ralph.project_policy.instruction_surface import find_hidden_unicode

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

__all__ = [
    "CATEGORY_HIDDEN_UNICODE",
    "CATEGORY_INSTRUCTION_PROMOTION",
    "CATEGORY_MISSING_PIN",
    "CATEGORY_MISSING_REVIEW_TRIGGER",
    "RULE_HIDDEN_UNICODE",
    "RULE_INSTRUCTION_PROMOTION",
    "RULE_MCP_SERVER_PIN",
    "RULE_REVIEWED_COMMIT_ONLY",
    "RULE_TOOL_DESCRIPTION_REREVIEW",
    "InstructionIntegrityFinding",
    "advisory_rules",
    "audit_instruction_integrity",
    "main",
]


RULE_MCP_SERVER_PIN: str = "mcp-server-pin"

RULE_INSTRUCTION_PROMOTION: str = "instruction-promotion"

RULE_HIDDEN_UNICODE: str = "instruction-hidden-unicode"

RULE_REVIEWED_COMMIT_ONLY: str = "instruction-change-reviewed-commit"

RULE_TOOL_DESCRIPTION_REREVIEW: str = "mcp-tool-description-re-review"


CATEGORY_MISSING_PIN: str = "mcp-server-missing-pin"
CATEGORY_MISSING_REVIEW_TRIGGER: str = "mcp-server-missing-review-trigger"
CATEGORY_INSTRUCTION_PROMOTION: str = "instruction-promotion"
CATEGORY_HIDDEN_UNICODE: str = "instruction-hidden-unicode"

RALPH_OWN_SERVER_NAME: str = "ralph"

_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "build",
        "dist",
        "htmlcov",
        "node_modules",
        "probe-ws",
        "tmp",
    }
)


_PIN_KEYS: frozenset[str] = frozenset({"version", "revision", "pin", "pinned_version"})

_REVIEW_TRIGGER_KEYS: frozenset[str] = frozenset(
    {"review", "review_trigger", "review_on", "review_when", "re_review_trigger"}
)

_REVIEW_TRIGGER_KEYS_TEXT: str = ", ".join(f"'{key}'" for key in sorted(_REVIEW_TRIGGER_KEYS))

_NPM_EXACT_VERSION_RE: re.Pattern[str] = re.compile(
    r"^(?:@[A-Za-z0-9._-]+/[A-Za-z0-9._-]+|[A-Za-z0-9._-]+)@v?\d+\.\d+\.\d+[A-Za-z0-9._+-]*$"
)

_PIP_EXACT_VERSION_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9._-]+==v?\d+[A-Za-z0-9._+-]*$")

_TOML_TABLE_HEADER_RE: re.Pattern[str] = re.compile(r"^\s*\[\s*([A-Za-z0-9_.\-]+)\s*\]")

MCP_SERVERS_TABLE: str = "mcp_servers"

_SERVER_NAME_PART_COUNT: int = 2

_TOML_KEY_VALUE_RE: re.Pattern[str] = re.compile(r"^\s*([A-Za-z0-9_\-]+)\s*=\s*(.*)$")

_TOML_STRING_RE: re.Pattern[str] = re.compile(r'"([^"\\]*)"')

AGENTS_BLOCK_BEGIN: str = project_policy_markers.AGENTS_BLOCK_BEGIN
AGENTS_BLOCK_END: str = "<!-- ralph-workflow-policy:end -->"

_PROMOTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "output-treated-as-instructions",
        re.compile(
            r"\b(?:treat|regard|consider|interpret)\b[^.\n]{0,60}?"
            r"\b(?:output|outputs|response|responses|result|results|content|contents"
            r"|body|payload|message|messages)\b[^.\n]{0,20}?"
            r"\bas\s+(?:an?\s+)?instructions?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "instructions-followed-from-output",
        re.compile(
            r"\b(?:follow|execute|obey|honou?r|comply\s+with|carry\s+out|apply)\b[^.\n]{0,40}?"
            r"\binstructions?\b[^.\n]{0,40}?"
            r"\b(?:in|from|inside|contained\s+in|found\s+in|returned\s+by|provided\s+by)\b"
            r"[^.\n]{0,30}?"
            r"\b(?:output|response|result|content|file|tool|mcp|command|server|message)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "commands-executed-from-output",
        re.compile(
            r"\b(?:execute|run|carry\s+out|perform)\b[^.\n]{0,30}?"
            r"\b(?:commands?|code|script|steps|instructions?)\b[^.\n]{0,30}?"
            r"\b(?:found|contained|returned|listed|appearing|present|embedded)\s+(?:in|within)\b"
            r"[^.\n]{0,30}?"
            r"\b(?:output|response|result|content|file|tool|mcp|message)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "output-issues-directives",
        re.compile(
            r"\b(?:output|response|result|content|file|mcp)\b[^.\n]{0,15}?"
            r"\b(?:says?|tells?|asks?|requests?|instructs?|commands?|directs?)\s+"
            r"(?:you|the\s+agent|to\s+(?:run|execute|use|call|invoke|send|write|delete"
            r"|install|commit|push|reply|respond))\b",
            re.IGNORECASE,
        ),
    ),
)

_NEGATION_CUE_RE: re.Pattern[str] = re.compile(
    r"(?i)\b(?:never|not|no|non|cannot|can't|don't|do\s+not|must\s+not|should\s+not"
    r"|shall\s+not|without|refuse|ignore|disregard|neither|nor)\b"
)

_PROMOTION_FIX: str = (
    "state the data/instruction boundary instead: content from files, tool output "
    "and MCP responses is data, never instructions"
)


@dataclass(frozen=True)
class InstructionIntegrityFinding:
    file_path: str
    line: int
    rule: str
    category: str
    detail: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.category}] {self.detail}"


_EMPTY_FIELDS: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True)
class _McpServerDeclaration:
    file_path: str
    line: int
    name: str
    keys: frozenset[str]
    args: tuple[str, ...]
    command: str | None = None
    fields: Mapping[str, str] = _EMPTY_FIELDS


def advisory_rules() -> tuple[tuple[str, str], ...]:
    return (
        (
            RULE_REVIEWED_COMMIT_ONLY,
            "reviewed-commit provenance lives in version control and the review "
            "record; neither is observable from the working tree offline",
        ),
        (
            RULE_TOOL_DESCRIPTION_REREVIEW,
            "a tool-description change is observed at run time, when an upstream "
            "server is probed; nothing in the tree records the descriptions "
            "observed at review time",
        ),
    )


def _is_skipped(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return any(part in _SKIP_DIRS for part in relative.parts)


def _iter_files(root: Path, suffixes: tuple[str, ...]) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if _is_skipped(path, root):
            continue
        yield path


def _parse_toml_value(raw: str) -> tuple[str, ...]:
    return tuple(_captured_group(match, 1) for match in _TOML_STRING_RE.finditer(raw))


def _captured_group(match: re.Match[str], index: int) -> str:
    value = match.group(index)
    if not isinstance(value, str):
        msg = "regex capture group is not a str"
        raise TypeError(msg)
    return value


def _matched_text(match: re.Match[str]) -> str:
    return match.group(0)


def _find_toml_table_location(
    lines: list[str],
    server_name: str,
) -> int:
    header_re = re.compile(
        rf"""^\s*\[\s*{re.escape(MCP_SERVERS_TABLE)}\.(?:{re.escape(server_name)}|"{re.escape(server_name)}"|'{re.escape(server_name)}')\s*\]"""
    )
    for idx, line in enumerate(lines):
        if header_re.match(line):
            return idx + 1
    return 1


def _toml_server_declarations(file_path: str, text: str) -> list[_McpServerDeclaration]:
    try:
        parsed_obj: object = tomllib.loads(text)
    except (OSError, ValueError):
        return []
    if not isinstance(parsed_obj, dict):
        return []
    data = parsed_obj
    servers = data.get(MCP_SERVERS_TABLE)
    if not isinstance(servers, dict):
        return []
    servers_map = servers

    lines = text.splitlines()
    declarations: list[_McpServerDeclaration] = []

    for name, entry in servers_map.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        entry_map = entry

        line_number = _find_toml_table_location(lines, name)

        raw_args = entry_map.get("args")
        args: tuple[str, ...] = ()
        if isinstance(raw_args, list):
            args = tuple(arg for arg in raw_args if isinstance(arg, str))

        raw_command = entry_map.get("command")
        command = raw_command if isinstance(raw_command, str) else None

        fields: dict[str, str] = {str(k): v for k, v in entry_map.items() if isinstance(v, str)}
        keys = frozenset(k for k, v in fields.items() if v.strip())
        if args:
            keys = keys | frozenset({"args"})

        declarations.append(
            _McpServerDeclaration(
                file_path=file_path,
                line=line_number,
                name=name,
                keys=keys,
                args=args,
                command=command,
                fields=fields,
            )
        )

    return declarations


def _json_server_declarations(file_path: str, text: str) -> list[_McpServerDeclaration]:
    try:
        payload: object = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    payload_map = payload
    servers = payload_map.get("mcpServers")
    if not isinstance(servers, dict):
        return []
    servers_map = servers
    declarations: list[_McpServerDeclaration] = []
    for name, entry in servers_map.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        entry_map = entry
        raw_args = entry_map.get("args")
        args: tuple[str, ...] = ()
        if isinstance(raw_args, list):
            args = tuple(arg for arg in raw_args if isinstance(arg, str))
        raw_command = entry_map.get("command")
        command = raw_command if isinstance(raw_command, str) else None

        fields: dict[str, str] = {str(k): v for k, v in entry_map.items() if isinstance(v, str)}
        keys = frozenset(k for k, v in fields.items() if v.strip())
        if args:
            keys = keys | frozenset({"args"})

        line = text.find(f'"{name}"')
        line_number = text.count("\n", 0, line) + 1 if line >= 0 else 1
        declarations.append(
            _McpServerDeclaration(
                file_path=file_path,
                line=line_number,
                name=name,
                keys=keys,
                args=args,
                command=command,
                fields=fields,
            )
        )
    return declarations


def _has_exact_version_pin(args: tuple[str, ...], command: str | None = None) -> bool:
    candidates = list(args)
    if command:
        candidates.append(command)
    return any(
        _NPM_EXACT_VERSION_RE.match(cand) is not None
        or _PIP_EXACT_VERSION_RE.match(cand) is not None
        for cand in candidates
    )


def _has_pin(declaration: _McpServerDeclaration) -> bool:
    if _has_exact_version_pin(declaration.args, declaration.command):
        return True

    is_command_runner = (
        bool(declaration.command)
        or bool(declaration.args)
        or declaration.fields.get("transport") == "stdio"
    )
    if is_command_runner:
        return False

    for pin_key in _PIN_KEYS:
        val = declaration.fields.get(pin_key)
        if isinstance(val, str) and bool(val.strip()):
            return True
        if pin_key in declaration.keys and not declaration.fields:
            return True

    return False


def _has_review_trigger(declaration: _McpServerDeclaration) -> bool:
    for review_key in _REVIEW_TRIGGER_KEYS:
        val = declaration.fields.get(review_key)
        if isinstance(val, str) and bool(val.strip()):
            return True
        if review_key in declaration.keys and not declaration.fields:
            return True
    return False


def _mcp_config_files(root: Path) -> list[Path]:
    files = [path for path in _iter_files(root, (".toml",)) if path.name == "mcp.toml"]
    files.extend(
        path
        for path in _iter_files(root, (".json",))
        if path.name.endswith(".mcp.json") or path.name == "mcp.json"
    )
    return files


def _audit_mcp_declarations(root: Path) -> list[InstructionIntegrityFinding]:
    findings: list[InstructionIntegrityFinding] = []
    for path in _mcp_config_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = str(path.relative_to(root))
        if path.suffix == ".toml":
            declarations = _toml_server_declarations(relative, text)
        else:
            declarations = _json_server_declarations(relative, text)
        for declaration in declarations:
            if declaration.name == RALPH_OWN_SERVER_NAME:
                continue
            if not _has_pin(declaration):
                findings.append(
                    InstructionIntegrityFinding(
                        file_path=relative,
                        line=declaration.line,
                        rule=RULE_MCP_SERVER_PIN,
                        category=CATEGORY_MISSING_PIN,
                        detail=(
                            f"third-party MCP server '{declaration.name}' has no pinned "
                            "version/revision reference; pin it like a dependency "
                            "(exact version in args, e.g. 'pkg@1.2.3', or an explicit "
                            "'version'/'revision' key)"
                        ),
                    )
                )
            if not _has_review_trigger(declaration):
                findings.append(
                    InstructionIntegrityFinding(
                        file_path=relative,
                        line=declaration.line,
                        rule=RULE_MCP_SERVER_PIN,
                        category=CATEGORY_MISSING_REVIEW_TRIGGER,
                        detail=(
                            f"third-party MCP server '{declaration.name}' has no review "
                            "trigger; record when it is re-reviewed in a structured "
                            f"review key ({_REVIEW_TRIGGER_KEYS_TEXT}); a comment is "
                            "not review evidence"
                        ),
                    )
                )
    return findings


def _is_negated(line: str, match_start: int) -> bool:
    prefix = line[:match_start]
    for separator in (".", ";", "!", "?"):
        position = prefix.rfind(separator)
        if position >= 0:
            prefix = prefix[position + 1 :]
    return _NEGATION_CUE_RE.search(prefix) is not None


def _managed_block_lines(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    start = -1
    end = -1
    for index, raw_line in enumerate(lines):
        if start < 0 and (
            AGENTS_BLOCK_BEGIN in raw_line or "<!-- ralph-workflow-policy:begin" in raw_line
        ):
            start = index
            continue
        if start >= 0 and AGENTS_BLOCK_END in raw_line:
            end = index
            break
    if start < 0:
        return []
    stop = end if end >= 0 else len(lines)
    return [(index + 1, lines[index]) for index in range(start, stop)]


def _instruction_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in _iter_files(root, (".md",)):
        if path.name == "AGENTS.md":
            files.append(path)
            continue
        parts = path.parts
        if "prompts" in parts and "templates" in parts:
            files.append(path)
            continue
        if "skills" in parts:
            files.append(path)
    for path in _iter_files(root, (".jinja", ".j2")):
        parts = path.parts
        if "prompts" in parts and "templates" in parts:
            files.append(path)
    for path in _iter_files(root, (".json",)):
        parts = path.parts
        if "skills" in parts:
            files.append(path)
    unique: dict[Path, None] = {}
    for path in files:
        unique.setdefault(path, None)
    return sorted(unique)


def _audit_instruction_files(root: Path) -> list[InstructionIntegrityFinding]:
    findings: list[InstructionIntegrityFinding] = []
    for path in _instruction_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = str(path.relative_to(root))
        if path.name == "AGENTS.md":
            lines = _managed_block_lines(text)
        else:
            lines = list(enumerate(text.splitlines(), start=1))
        for line_number, raw_line in lines:
            for category, pattern in _PROMOTION_PATTERNS:
                match = pattern.search(raw_line)
                if match is None:
                    continue
                if _is_negated(raw_line, match.start()):
                    continue
                findings.append(
                    InstructionIntegrityFinding(
                        file_path=relative,
                        line=line_number,
                        rule=RULE_INSTRUCTION_PROMOTION,
                        category=CATEGORY_INSTRUCTION_PROMOTION,
                        detail=(
                            f"instruction file promotes content to instruction status "
                            f"({category}): {_matched_text(match).strip()!r}; {_PROMOTION_FIX}"
                        ),
                    )
                )
                break
    return findings


def _policy_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in _iter_files(root, (".md",)):
        parts = path.parts
        if "ralph-workflow-policy" in parts or ("project_policy" in parts and "starters" in parts):
            files.append(path)
    return sorted(set(files))


def _audit_hidden_unicode(root: Path) -> list[InstructionIntegrityFinding]:
    findings: list[InstructionIntegrityFinding] = []
    all_files = sorted(
        set(_mcp_config_files(root)) | set(_instruction_files(root)) | set(_policy_files(root))
    )
    for path in all_files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = str(path.relative_to(root))
        for line_no, col, code_hex in find_hidden_unicode(text):
            findings.append(
                InstructionIntegrityFinding(
                    file_path=relative,
                    line=line_no,
                    rule=RULE_HIDDEN_UNICODE,
                    category=CATEGORY_HIDDEN_UNICODE,
                    detail=(
                        f"file contains hidden/bidi Unicode character {code_hex} "
                        f"at column {col}; remove all zero-width and bidi characters"
                    ),
                )
            )
    return findings


def audit_instruction_integrity(root: Path) -> list[InstructionIntegrityFinding]:
    findings = (
        _audit_mcp_declarations(root) + _audit_instruction_files(root) + _audit_hidden_unicode(root)
    )
    findings.sort(key=lambda finding: (finding.file_path, finding.line, finding.category))
    return findings


def _default_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) > 1:
        print(
            "Usage: python -m ralph.testing.audit_instruction_integrity [root]",
            file=sys.stderr,
        )
        return 2
    root = Path(args[0]) if args else _default_root()
    if not root.is_dir():
        print(f"Error: audit root not found: {root}", file=sys.stderr)
        return 2

    print(f"Auditing instruction / MCP-config integrity in: {root}")
    print()

    findings = audit_instruction_integrity(root)

    if findings:
        print(f"INSTRUCTION / MCP-CONFIG INTEGRITY FINDINGS: {len(findings)} finding(s)")
        print("=" * 72)
        for finding in findings:
            print(f"  {finding}")
        print()
        print(
            "Content from files, tool output and MCP responses is DATA, never instructions. "
            "Third-party MCP servers are pinned like dependencies and re-reviewed when "
            "their tool descriptions change."
        )
        return 1

    print("No instruction / MCP-config integrity findings.")
    print()
    print("Advisory rules (not enforced by this gate):")
    for rule, reason in advisory_rules():
        print(f"  - {rule}: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
