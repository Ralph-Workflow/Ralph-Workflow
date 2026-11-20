from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ralph.testing._audit_integration_no_legacy_sentinels import (
    _RETIRED_REVIEW_DETAIL,
    _RETIRED_REVIEW_PATTERN,
    _RETIRED_REVIEW_RULE,
    _RETIRED_STEM,
    _is_denial_only_pre_rename_sentinel,
    _iter_folded_string_literals,
    _package_key,
    _walked_nodes,
)

_STRING_SEGMENT_PATTERN: Final = re.compile(
    r"\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'"
)

_ESCAPED_CODEPOINT_PATTERN: Final = re.compile(
    r"\\(?:x[0-9A-Fa-f]{2}|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}|N\{[^}]*\}|[0-7]{1,3})"
)

_SELF_AUDIT_MODULES: frozenset[str] = frozenset(
    {
        "ralph/testing/audit_integration_invariants.py",
        "ralph/testing/audit_integration_no_legacy.py",
        "ralph/testing/_audit_integration_no_legacy_sentinels.py",
        "ralph/testing/_integration_no_legacy_selftest.py",
        "tests/audit/test_audit_integration_no_legacy.py",
    }
)

_EXEMPT_HISTORICAL_PATH_FRAGMENTS: tuple[str, ...] = (
    "tests/agents/invoke/fixtures/claude_interactive_real_capture",
)

_RETIRED_THREE_SHA_MESSAGE: Final = (
    "integrated_feature_sha, " + "pre_target_sha, " + "or pre_feature_sha"
)
_RETIRED_THREE_SHA_RULE: Final = "retired three-SHA landed-proof message reappeared"
_RETIRED_THREE_SHA_DETAIL: Final = (
    "the retired landed-proof message text "
    f"'{_RETIRED_THREE_SHA_MESSAGE}' must not reappear in production modules"
)
_OPERATOR_PARKING_LITERALS: Final[tuple[str, ...]] = (
    "NeedsOperator",
    "operator action required",
    "LandedProofFailed",
)
_OPERATOR_PARKING_LITERAL_RULE: Final = "operator-parking terminal literal reappeared"
_OPERATOR_PARKING_LITERAL_DETAIL: Final = (
    "pipeline modules must not contain NeedsOperator, operator action required, "
    "or LandedProofFailed"
)
_OPERATOR_PARKING_STATE_RULE: Final = "operator-parking LadderState member reappeared"
_OPERATOR_PARKING_STATE_DETAIL: Final = (
    "LadderState must not define NeedsOperator or any operator-parking member"
)


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str
    detail: str = ""

    def __str__(self) -> str:
        if self.detail:
            return f"{self.path}:{self.line}: {self.rule}: {self.detail}"
        return f"{self.path}:{self.line}: {self.rule}"


def _is_exempt_path(rel: str) -> bool:
    normalized = rel.replace("\\", "/")
    return bool(
        any(fragment in normalized for fragment in _EXEMPT_HISTORICAL_PATH_FRAGMENTS)
        or any(normalized.endswith(mod) for mod in _SELF_AUDIT_MODULES)
        or normalized == ("PROMPT" + ".md")
        or "/.agent/" in normalized
        or normalized.startswith(".agent/")
        or "/.pi-subagents/" in normalized
        or normalized.startswith(".pi-subagents/")
    )


def _folded_retired_review_violations(rel: str, tree: ast.AST) -> list[Violation]:
    violations: list[Violation] = []
    reported: set[tuple[int, str]] = set()
    for folded, lineno in _iter_folded_string_literals(tree):
        match = _RETIRED_REVIEW_PATTERN.search(folded)
        if match is None:
            continue
        token = match.group(0)
        if _is_denial_only_pre_rename_sentinel(rel, token):
            continue
        key = (lineno, token)
        if key in reported:
            continue
        reported.add(key)
        violations.append(
            Violation(
                path=rel,
                line=lineno,
                rule=_RETIRED_REVIEW_RULE,
                detail=_RETIRED_REVIEW_DETAIL.format(token=token),
            )
        )
    return violations


def _string_segment_contents(source: str) -> list[str]:
    contents: list[str] = []
    segments: list[str] = _STRING_SEGMENT_PATTERN.findall(source)
    for segment in segments:
        if (segment.startswith('"""') and segment.endswith('"""')) or (
            segment.startswith("'''") and segment.endswith("'''")
        ):
            contents.append(segment[3:-3])
        else:
            contents.append(segment[1:-1])
    return contents


def _folded_literal_candidate(source: str) -> bool:
    segments = _string_segment_contents(source)
    if not segments:
        return False
    if any(_ESCAPED_CODEPOINT_PATTERN.search(segment) for segment in segments):
        return True
    return _RETIRED_REVIEW_PATTERN.search("".join(segments)) is not None


def _retired_review_candidate(source: str) -> bool:
    lowered = source.lower()
    if "commit" in lowered and "clean" in lowered:
        return True
    return _folded_literal_candidate(source)


def _retired_review_violations(
    rel: str,
    source: str,
    tree: ast.AST | None = None,
) -> list[Violation]:
    if _is_exempt_path(rel):
        return []
    violations: list[Violation] = []
    filename = Path(rel).name
    if _RETIRED_REVIEW_PATTERN.search(filename):
        violations.append(
            Violation(
                path=rel,
                line=1,
                rule=_RETIRED_REVIEW_RULE,
                detail=f"filename '{filename}' contains legacy {_RETIRED_STEM} spelling",
            )
        )
    lowered = source.lower()
    literal_candidate = "commit" in lowered and "clean" in lowered
    if not literal_candidate and not _folded_literal_candidate(source):
        return violations

    if tree is not None:
        violations.extend(_folded_retired_review_violations(rel, tree))

    if not literal_candidate:
        return violations

    reported_lines = {violation.line for violation in violations}
    for lineno, line in enumerate(source.splitlines(), start=1):
        if lineno in reported_lines:
            continue
        match = _RETIRED_REVIEW_PATTERN.search(line)
        if match:
            violations.append(
                Violation(
                    path=rel,
                    line=lineno,
                    rule=_RETIRED_REVIEW_RULE,
                    detail=_RETIRED_REVIEW_DETAIL.format(token=match.group(0)),
                )
            )
    return violations


def _retired_three_sha_message_violations(rel: str, source: str) -> list[Violation]:
    if _is_exempt_path(rel):
        return []
    if not _package_key(rel).startswith("ralph/"):
        return []
    if _RETIRED_THREE_SHA_MESSAGE not in source:
        return []
    return [
        Violation(
            path=rel,
            line=lineno,
            rule=_RETIRED_THREE_SHA_RULE,
            detail=_RETIRED_THREE_SHA_DETAIL,
        )
        for lineno, line in enumerate(source.splitlines(), start=1)
        if _RETIRED_THREE_SHA_MESSAGE in line
    ]


def _operator_parking_literal_violations(rel: str, source: str) -> list[Violation]:
    if _is_exempt_path(rel):
        return []
    if not _package_key(rel).startswith("ralph/pipeline/"):
        return []
    violations: list[Violation] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        for literal in _OPERATOR_PARKING_LITERALS:
            if literal in line:
                violations.append(
                    Violation(
                        path=rel,
                        line=lineno,
                        rule=_OPERATOR_PARKING_LITERAL_RULE,
                        detail=f"{_OPERATOR_PARKING_LITERAL_DETAIL}: found {literal!r}",
                    )
                )
                break
    return violations


def _operator_parking_state_violations(rel: str, tree: ast.AST) -> list[Violation]:
    if not _package_key(rel).startswith("ralph/pipeline/"):
        return []
    violations: list[Violation] = []
    for node in _walked_nodes(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "LadderState":
            continue
        for stmt in node.body:
            target_name = ""
            value_node: ast.AST | None = None
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                target = stmt.targets[0]
                if isinstance(target, ast.Name):
                    target_name = target.id
                    value_node = stmt.value
            elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                target_name = stmt.target.id
                value_node = stmt.value
            lineno = stmt.lineno if isinstance(stmt.lineno, int) else 1
            if target_name in {"NEEDS_OPERATOR", "NeedsOperator"}:
                violations.append(
                    Violation(
                        path=rel,
                        line=lineno,
                        rule=_OPERATOR_PARKING_STATE_RULE,
                        detail=_OPERATOR_PARKING_STATE_DETAIL,
                    )
                )
                continue
            if isinstance(value_node, ast.Constant) and value_node.value == "NeedsOperator":
                violations.append(
                    Violation(
                        path=rel,
                        line=lineno,
                        rule=_OPERATOR_PARKING_STATE_RULE,
                        detail=_OPERATOR_PARKING_STATE_DETAIL,
                    )
                )
    return violations
