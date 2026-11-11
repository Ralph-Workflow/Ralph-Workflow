"""Instruction / MCP-config integrity audit.

The rule this audit enforces is the data/instruction boundary:

* content that comes from FILES, TOOL OUTPUT and MCP RESPONSES is DATA,
  never instructions;
* instruction files and MCP config files change only through reviewed
  commits;
* third-party MCP servers are pinned like dependencies and re-reviewed when
  their tool descriptions change.

Only the part of that rule that is deterministically checkable OFFLINE is
implemented as a gate. The rest is reported as ADVISORY (see
:func:`advisory_rules`), because an unimplementable rule is worse than an
absent one: a fake gate gets trusted and then gets disabled.

DETERMINISTIC RULES

``RULE_MCP_SERVER_PIN`` (``mcp-server-pin``)
    Every THIRD-PARTY MCP server declaration must carry a pinned
    version/revision reference AND a review trigger. The declaration
    surface is the MCP configuration files of the scanned tree:
    ``mcp.toml`` files (the ``[mcp_servers.<name>]`` tables) and
    ``*.mcp.json`` files (the ``mcpServers`` object). A missing pin or a
    missing review trigger is a finding that names the file, the line, and
    the fix.

    A declaration is pinned when:

    * for executable command runners (stdio transport, command, or args),
      an exact-version reference inside ``args`` or ``command`` (npm ``pkg@1.2.3`` /
      ``@scope/pkg@1.2.3``, pip ``pkg==1.2.3``), which is the pin the
      runtime actually honours; declaration metadata without an execution
      pin is metadata-only and rejected;
    * for unsupported runtime forms (e.g. HTTP / URL transports where runtime
      package pinning is not supported), an explicit, non-empty ``version`` /
      ``revision`` / ``pin`` key is accepted as advisory declaration-level
      provenance.

    A declaration carries a review trigger when it carries a
    ``review`` / ``review_trigger`` / ``review_on`` key with a non-empty
    value, or a comment that names a re-review condition.

    Commented-out example blocks are INERT templates, not declarations:
    the bundled default ``mcp.toml`` ships every third-party example
    commented out so that no unpinned server is ever active. The gate
    fires the moment such a block is uncommented into a live declaration.

``RULE_INSTRUCTION_PROMOTION`` (``instruction-promotion``)
    Instruction files that agents read AS instructions -- the managed
    ``AGENTS.md`` block, the packaged prompt templates
    (``ralph/prompts/templates``) and the packaged skills
    (``ralph/skills``) -- must not embed an imperative that promotes tool
    output, file content or an MCP response to instruction status. Only
    unambiguous phrases are detected, and a NEGATED statement ("never
    treat tool output as instructions") is not a finding: the rule
    forbids the promotion, not the warning about it.

ADVISORY RULES (not enforced by this gate)

``RULE_REVIEWED_COMMIT_ONLY``
    "Instruction files and MCP config files change only through reviewed
    commits" needs version-control history and a review record; neither is
    observable from the working tree offline, so this stays advisory.

``RULE_TOOL_DESCRIPTION_REREVIEW``
    "Third-party MCP servers are re-reviewed when their tool descriptions
    change" needs the tool descriptions observed at run time across
    versions; nothing in the tree records them, so this stays advisory.

Usage:
    python -m ralph.testing.audit_instruction_integrity [root]

Exit 0 = clean, 1 = findings, 2 = root not found.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

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


#: Third-party MCP server declarations must carry a pin and a review trigger.
RULE_MCP_SERVER_PIN: str = "mcp-server-pin"

#: Instruction files must not promote tool/file/MCP output to instructions.
RULE_INSTRUCTION_PROMOTION: str = "instruction-promotion"

#: Instruction surface files must not contain hidden or bidirectional Unicode.
RULE_HIDDEN_UNICODE: str = "instruction-hidden-unicode"

#: ADVISORY: instruction and MCP-config files change only through reviewed commits.
RULE_REVIEWED_COMMIT_ONLY: str = "instruction-change-reviewed-commit"

#: ADVISORY: third-party MCP servers are re-reviewed when tool descriptions change.
RULE_TOOL_DESCRIPTION_REREVIEW: str = "mcp-tool-description-re-review"


CATEGORY_MISSING_PIN: str = "mcp-server-missing-pin"
CATEGORY_MISSING_REVIEW_TRIGGER: str = "mcp-server-missing-review-trigger"
CATEGORY_INSTRUCTION_PROMOTION: str = "instruction-promotion"
CATEGORY_HIDDEN_UNICODE: str = "instruction-hidden-unicode"

#: The server name Ralph reserves for its own MCP server. A declaration
#: under this name is FIRST PARTY and is exempt from the pin rule: it is
#: the in-process server, not a third-party dependency.
#: Mirrors ``RALPH_MCP_SERVER_NAME`` in ``ralph/mcp/tools/names.py``.
RALPH_OWN_SERVER_NAME: str = "ralph"

#: Directory names that never contain a shipped declaration or instruction file.
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
        "tmp",
    }
)


#: Keys whose presence (with a non-empty value) records a pinned version.
_PIN_KEYS: frozenset[str] = frozenset({"version", "revision", "pin", "pinned_version"})

#: Keys whose presence (with a non-empty value) records a review trigger.
_REVIEW_TRIGGER_KEYS: frozenset[str] = frozenset(
    {"review", "review_trigger", "review_on", "review_when", "re_review_trigger"}
)

#: npm-style exact version: ``pkg@1.2.3`` or ``@scope/pkg@1.2.3``.
_NPM_EXACT_VERSION_RE: re.Pattern[str] = re.compile(
    r"^(?:@[A-Za-z0-9._-]+/[A-Za-z0-9._-]+|[A-Za-z0-9._-]+)@v?\d+\.\d+\.\d+[A-Za-z0-9._+-]*$"
)

#: pip-style exact version: ``pkg==1.2.3``.
_PIP_EXACT_VERSION_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9._-]+==v?\d+[A-Za-z0-9._+-]*$")

#: A TOML table header: ``[mcp_servers.github]``.
_TOML_TABLE_HEADER_RE: re.Pattern[str] = re.compile(r"^\s*\[\s*([A-Za-z0-9_.\-]+)\s*\]")

#: The TOML table that declares MCP servers.
MCP_SERVERS_TABLE: str = "mcp_servers"

#: Number of dot-separated parts in a server declaration header
#: (``mcp_servers`` + the server name).
_SERVER_NAME_PART_COUNT: int = 2

#: A TOML key/value assignment.
_TOML_KEY_VALUE_RE: re.Pattern[str] = re.compile(r"^\s*([A-Za-z0-9_\-]+)\s*=\s*(.*)$")

#: A double-quoted TOML string literal.
_TOML_STRING_RE: re.Pattern[str] = re.compile(r'"([^"\\]*)"')

#: A comment that names a re-review condition.
_REVIEW_TRIGGER_COMMENT_RE: re.Pattern[str] = re.compile(
    r"(?i)(?:\bre-?review\b|\breview\s+(?:trigger|when|on)\b)"
)

#: How many lines above a table header may carry its review-trigger comment.
_REVIEW_COMMENT_LOOKAHEAD_LINES: int = 3


#: The managed instruction block inside ``AGENTS.md``.
AGENTS_BLOCK_BEGIN: str = "<!-- ralph-workflow-policy:begin v1 -->"
AGENTS_BLOCK_END: str = "<!-- ralph-workflow-policy:end -->"

#: Unambiguous promotion imperatives. Each pattern names a distinct shape
#: of the defect; none of them fires on a NEGATED statement (see
#: :func:`_is_negated`).
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

#: Negation cues. A promotion phrase inside a negated clause states the
#: RULE, not a violation of it, so the detector must not fire on it.
_NEGATION_CUE_RE: re.Pattern[str] = re.compile(
    r"(?i)\b(?:never|not|no|non|cannot|can't|don't|do\s+not|must\s+not|should\s+not"
    r"|shall\s+not|without|refuse|ignore|disregard|neither|nor)\b"
)

#: The fix hint every promotion finding carries.
_PROMOTION_FIX: str = (
    "state the data/instruction boundary instead: content from files, tool output "
    "and MCP responses is data, never instructions"
)


@dataclass(frozen=True)
class InstructionIntegrityFinding:
    """One instruction / MCP-config integrity finding."""

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
    """One third-party MCP server declaration found in a config file."""

    file_path: str
    line: int
    name: str
    keys: frozenset[str]
    args: tuple[str, ...]
    comments: tuple[str, ...]
    command: str | None = None
    fields: Mapping[str, str] = _EMPTY_FIELDS


def advisory_rules() -> tuple[tuple[str, str], ...]:
    """Return the ``(rule, reason)`` pairs this gate does NOT enforce.

    The pairs are part of the gate's contract: a reader must be able to
    tell which half of the data/instruction rule is machine-checked and
    which half is carried by review.
    """
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
    """Return True when *path* lies in an excluded directory."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return any(part in _SKIP_DIRS for part in relative.parts)


def _iter_files(root: Path, suffixes: tuple[str, ...]) -> Iterator[Path]:
    """Yield every file under *root* with one of *suffixes*, in sorted order."""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if _is_skipped(path, root):
            continue
        yield path


def _parse_toml_value(raw: str) -> tuple[str, ...]:
    """Return the string literals of a TOML array value."""
    return tuple(_captured_group(match, 1) for match in _TOML_STRING_RE.finditer(raw))


def _captured_group(match: re.Match[str], index: int) -> str:
    """Return capture group *index* of *match* as a plain string.

    ``re.Match.group`` is typed ``str | Any`` for a non-zero group, and
    this repository forbids ``Any`` in expressions. The narrowing keeps the
    returned value a real ``str``.
    """
    value = match.group(index)
    if not isinstance(value, str):
        msg = "regex capture group is not a str"
        raise TypeError(msg)
    return value


def _matched_text(match: re.Match[str]) -> str:
    """Return the whole matched text of *match* as a plain string."""
    return match.group(0)


def _find_toml_table_location(
    lines: list[str],
    server_name: str,
) -> tuple[int, tuple[str, ...]]:
    """Return the 1-based start line and comments for ``[mcp_servers.<name>]``."""
    header_re = re.compile(
        rf"""^\s*\[\s*{re.escape(MCP_SERVERS_TABLE)}\.(?:{re.escape(server_name)}|"{re.escape(server_name)}"|'{re.escape(server_name)}')\s*\]"""
    )
    line_number = 1
    start_index = -1
    for idx, line in enumerate(lines):
        if header_re.match(line):
            line_number = idx + 1
            start_index = idx
            break

    comments: list[str] = []
    if start_index >= 0:
        for line in lines[start_index + 1 :]:
            if re.match(r"^\s*\[", line):
                break
            stripped = line.strip()
            if stripped.startswith("#"):
                comments.append(stripped)

    return line_number, tuple(comments)


def _toml_server_declarations(file_path: str, text: str) -> list[_McpServerDeclaration]:
    """Return every ``[mcp_servers.<name>]`` declaration found in *text*."""
    try:
        parsed_obj: object = tomllib.loads(text)
    except (OSError, ValueError):
        return []
    if not isinstance(parsed_obj, dict):
        return []
    data = cast("dict[str, object]", parsed_obj)
    servers = data.get(MCP_SERVERS_TABLE)
    if not isinstance(servers, dict):
        return []
    servers_map = cast("dict[str, object]", servers)

    lines = text.splitlines()
    declarations: list[_McpServerDeclaration] = []

    for name, entry in servers_map.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        entry_map = cast("dict[str, object]", entry)

        line_number, comments = _find_toml_table_location(lines, name)

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
                comments=comments,
                command=command,
                fields=fields,
            )
        )

    return declarations


def _json_server_declarations(file_path: str, text: str) -> list[_McpServerDeclaration]:
    """Return every ``mcpServers`` entry found in a ``*.mcp.json`` document."""
    try:
        payload: object = cast("object", json.loads(text))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(payload, dict):
        return []
    payload_map = cast("dict[str, object]", payload)
    servers = payload_map.get("mcpServers")
    if not isinstance(servers, dict):
        return []
    servers_map = cast("dict[str, object]", servers)
    declarations: list[_McpServerDeclaration] = []
    for name, entry in servers_map.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        entry_map = cast("dict[str, object]", entry)
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
                comments=(),
                command=command,
                fields=fields,
            )
        )
    return declarations


def _has_exact_version_pin(args: tuple[str, ...], command: str | None = None) -> bool:
    """Return True when arguments or command pin an exact upstream version."""
    candidates = list(args)
    if command:
        candidates.append(command)
    return any(
        _NPM_EXACT_VERSION_RE.match(cand) is not None
        or _PIP_EXACT_VERSION_RE.match(cand) is not None
        for cand in candidates
    )


def _has_pin(declaration: _McpServerDeclaration) -> bool:
    """Return True when the declaration carries a pinned version reference.

    For executable runtime forms (command runners using stdio transport, command,
    or args), deterministic status requires an execution-honoured exact pin in
    args or command (e.g. npm 'pkg@1.2.3' or pip 'pkg==1.2.3'). Declaration-level
    metadata keys without an execution pin are metadata-only and do not satisfy
    the requirement.

    For unsupported runtime forms (e.g. HTTP / URL transports where runtime package
    pinning is not supported), an explicit, non-empty version/revision key is accepted
    as advisory declaration-level provenance.
    """
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


def _has_review_trigger(declaration: _McpServerDeclaration, text: str) -> bool:
    """Return True when the declaration carries a review trigger."""
    for review_key in _REVIEW_TRIGGER_KEYS:
        val = declaration.fields.get(review_key)
        if isinstance(val, str) and bool(val.strip()):
            return True
        if review_key in declaration.keys and not declaration.fields:
            return True
    if any(_REVIEW_TRIGGER_COMMENT_RE.search(comment) for comment in declaration.comments):
        return True
    lines = text.splitlines()
    start = max(0, declaration.line - 1 - _REVIEW_COMMENT_LOOKAHEAD_LINES)
    for raw_line in lines[start : declaration.line - 1]:
        stripped = raw_line.strip()
        if not stripped.startswith("#"):
            continue
        if _REVIEW_TRIGGER_COMMENT_RE.search(stripped):
            return True
    return False


def _mcp_config_files(root: Path) -> list[Path]:
    """Return the MCP declaration files of the scanned tree."""
    files = [path for path in _iter_files(root, (".toml",)) if path.name == "mcp.toml"]
    files.extend(
        path
        for path in _iter_files(root, (".json",))
        if path.name.endswith(".mcp.json") or path.name == "mcp.json"
    )
    return files


def _audit_mcp_declarations(root: Path) -> list[InstructionIntegrityFinding]:
    """Audit every MCP server declaration of the scanned tree."""
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
            if not _has_review_trigger(declaration, text):
                findings.append(
                    InstructionIntegrityFinding(
                        file_path=relative,
                        line=declaration.line,
                        rule=RULE_MCP_SERVER_PIN,
                        category=CATEGORY_MISSING_REVIEW_TRIGGER,
                        detail=(
                            f"third-party MCP server '{declaration.name}' has no review "
                            "trigger; record when it is re-reviewed (a 'review' key, or "
                            "a comment naming the re-review condition)"
                        ),
                    )
                )
    return findings


def _is_negated(line: str, match_start: int) -> bool:
    """Return True when the clause before *match_start* negates the match."""
    prefix = line[:match_start]
    for separator in (".", ";", "!", "?"):
        position = prefix.rfind(separator)
        if position >= 0:
            prefix = prefix[position + 1 :]
    return _NEGATION_CUE_RE.search(prefix) is not None


def _managed_block_lines(text: str) -> list[tuple[int, str]]:
    """Return the ``(line_number, text)`` pairs of the managed AGENTS.md block."""
    lines = text.splitlines()
    start = -1
    end = -1
    for index, raw_line in enumerate(lines):
        if start < 0 and AGENTS_BLOCK_BEGIN in raw_line:
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
    """Return the instruction files of the scanned tree.

    The surface is: every ``AGENTS.md`` (only its MANAGED block is
    instruction content), the packaged prompt templates, and the packaged
    skills. The packaged surfaces are matched by their directory role
    (``prompts/templates`` and ``skills``) so the same tree layout works
    from the package root and from the repository root.
    """
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
    """Audit every instruction file of the scanned tree."""
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
    """Return the policy documents of the scanned tree."""
    files: list[Path] = []
    for path in _iter_files(root, (".md",)):
        parts = path.parts
        if "ralph-workflow-policy" in parts or ("project_policy" in parts and "starters" in parts):
            files.append(path)
    return sorted(set(files))


def _audit_hidden_unicode(root: Path) -> list[InstructionIntegrityFinding]:
    """Audit instruction surface files for hidden or bidirectional Unicode."""
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
    """Audit the instruction / MCP-config integrity of the tree at *root*.

    Args:
        root: The tree to scan.

    Returns:
        Every finding, ordered by file path then line.
    """
    findings = (
        _audit_mcp_declarations(root) + _audit_instruction_files(root) + _audit_hidden_unicode(root)
    )
    findings.sort(key=lambda finding: (finding.file_path, finding.line, finding.category))
    return findings


def _default_root() -> Path:
    """Return the repository root the gate scans when no root is given."""
    return Path(__file__).resolve().parent.parent.parent


def main(argv: list[str] | None = None) -> int:
    """Run the instruction / MCP-config integrity audit and return an exit code.

    Exit code 0: no findings. Exit code 1: findings. Exit code 2: the root
    was not found.
    """
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
