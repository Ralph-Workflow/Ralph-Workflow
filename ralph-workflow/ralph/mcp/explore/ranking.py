"""Deterministic ranking for indexed ``search_files`` and ``grep_files``.

Phase 1 ranking uses path/FTS/role/git-changed signals only. Symbol
and graph components are stubbed to ``+0`` with a score-reason note
``"disabled:phase2"``; Phase 2 (deferred) will replace the stubs with
real components once the AST/edge extraction ships.

Every ranked response returns a ``score_reasons`` list so tests can
assert WHY a path outranks another. Ties sort by
``(path, line, evidence_id)`` for stable output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

# --- Component constants --------------------------------------------------

# Phase 1 search_files components (CURRENT_PROMPT.md contract).
SEARCH_EXACT_BASENAME: Final[int] = 100
SEARCH_SYMBOL_DEFINITION: Final[int] = 80  # disabled in Phase 1
SEARCH_SYMBOL_MENTION: Final[int] = 60  # disabled in Phase 1
SEARCH_GIT_CHANGED: Final[int] = 40
SEARCH_ROLE_REQUESTED: Final[int] = 30
SEARCH_GRAPH_NEIGHBOR: Final[int] = 20  # disabled in Phase 1
SEARCH_GENERATED_PENALTY: Final[int] = -50

# Phase 1 grep_files rank_by components.
GREP_DEFINITION_BONUS: Final[int] = 100  # disabled in Phase 1
GREP_SAME_SYMBOL_BODY: Final[int] = 60  # disabled in Phase 1
GREP_COMMENT_DOC: Final[int] = 30  # disabled in Phase 1
GREP_GRAPH_NEIGHBOR: Final[int] = 50  # disabled in Phase 1
GREP_GRAPH_COMPONENT: Final[int] = 20  # disabled in Phase 1
GREP_GIT_CHANGED: Final[int] = 40

PHASE2_DISABLED_NOTE: Final[str] = "disabled:phase2"


# --- Public dataclass -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class RankedItem:
    """A single ranked item with explicit score reasons."""

    key: str  # path or (path, line, evidence_id)
    score: int = 0
    reasons: tuple[str, ...] = field(default_factory=tuple)
    line: int | None = None
    evidence_id: str | None = None
    path: str = ""

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "path": self.path,
            "score": self.score,
            "score_reasons": list(self.reasons),
        }
        if self.line is not None:
            out["line"] = self.line
        if self.evidence_id is not None:
            out["evidence_id"] = self.evidence_id
        return out


# --- Helpers --------------------------------------------------------------


def is_generated_path(path: str) -> bool:
    """Heuristic for the -50 generated/vendor penalty."""
    lowered = path.lower()
    return (
        lowered.startswith("vendor/")
        or "/vendor/" in lowered
        or lowered.startswith("build/")
        or lowered.startswith("dist/")
        or "/generated/" in lowered
        or lowered.endswith(".min.js")
    )


def is_source_role(path: str) -> bool:
    """True for source files (heuristic)."""
    lowered = path.lower()
    return lowered.endswith(
        (".py", ".md", ".markdown", ".json", ".yaml", ".yml", ".toml")
    )


def is_test_role(path: str) -> bool:
    """True for test files (heuristic)."""
    lowered = path.lower()
    return (
        "/tests/" in lowered
        or lowered.startswith("tests/")
        or "/test/" in lowered
        or lowered.startswith("test_")
        or "_test.py" in lowered
        or ".test." in lowered
    )


def sort_ranked(items: list[RankedItem]) -> list[RankedItem]:
    """Stable sort: score DESC, then path ASC, then line ASC, then evidence_id ASC."""
    return sorted(
        items,
        key=lambda item: (
            -item.score,
            item.path,
            item.line if item.line is not None else -1,
            item.evidence_id or "",
        ),
    )


# --- search_files scoring -------------------------------------------------


def score_search_file(
    *,
    candidate_path: str,
    basename: str,
    role_requested: str | None,
    is_git_changed: bool,
) -> RankedItem:
    """Compute a search_files score for one candidate path.

    Phase 1 scoring: exact-basename, git-changed, role-requested, and
    generated-penalty components. Symbol and graph components are
    stubbed to ``+0`` with a disabled reason until Phase 2 ships.
    """
    score = 0
    reasons: list[str] = []

    if candidate_path.split("/")[-1] == basename:
        score += SEARCH_EXACT_BASENAME
        reasons.append(f"+{SEARCH_EXACT_BASENAME} exact_path_basename")
    else:
        # Symbol definition / mention are stubbed in Phase 1.
        score += 0
        reasons.append(f"+0 symbol_definition:{PHASE2_DISABLED_NOTE}")
        score += 0
        reasons.append(f"+0 symbol_mention:{PHASE2_DISABLED_NOTE}")
        score += 0
        reasons.append(f"+0 graph_neighbor:{PHASE2_DISABLED_NOTE}")

    if is_git_changed:
        score += SEARCH_GIT_CHANGED
        reasons.append(f"+{SEARCH_GIT_CHANGED} git_changed_path")

    if role_requested in {"source", "test"} and (
        (role_requested == "source" and is_source_role(candidate_path))
        or (role_requested == "test" and is_test_role(candidate_path))
    ):
        score += SEARCH_ROLE_REQUESTED
        reasons.append(f"+{SEARCH_ROLE_REQUESTED} role_requested={role_requested}")

    if is_generated_path(candidate_path):
        score += SEARCH_GENERATED_PENALTY
        reasons.append(f"{SEARCH_GENERATED_PENALTY} generated_or_vendor_path")

    return RankedItem(
        key=candidate_path,
        score=score,
        reasons=tuple(reasons),
        path=candidate_path,
    )


# --- grep_files rank_by scoring ------------------------------------------


def score_grep_match(
    *,
    path: str,
    line: int,
    evidence_id: str,
    is_git_changed: bool = False,
) -> RankedItem:
    """Compute a grep_files rank_by score for one match.

    Phase 1 components: match (baseline), git-changed. Symbol, graph,
    and comment/doc components are stubbed to ``+0`` with a disabled
    reason until Phase 2 ships.
    """
    score = 0
    reasons: list[str] = []

    # Phase 1 baseline: every match carries +1 (the bare match
    # existence). Symbol bonuses arrive with Phase 2.
    score += 1
    reasons.append("+1 bare_match")
    score += 0
    reasons.append(f"+0 definition_bonus:{PHASE2_DISABLED_NOTE}")
    score += 0
    reasons.append(f"+0 same_symbol_body:{PHASE2_DISABLED_NOTE}")
    score += 0
    reasons.append(f"+0 comment_doc:{PHASE2_DISABLED_NOTE}")
    score += 0
    reasons.append(f"+0 graph_neighbor:{PHASE2_DISABLED_NOTE}")
    score += 0
    reasons.append(f"+0 graph_component:{PHASE2_DISABLED_NOTE}")

    if is_git_changed:
        score += GREP_GIT_CHANGED
        reasons.append(f"+{GREP_GIT_CHANGED} git_changed_file")

    return RankedItem(
        key=f"{path}:{line}:{evidence_id}",
        score=score,
        reasons=tuple(reasons),
        line=line,
        evidence_id=evidence_id,
        path=path,
    )


# --- Eligibility contract -------------------------------------------------


# Regex metacharacters that disqualify a pattern from FTS5 indexing
# without changing match semantics. Phase 1 keeps this list narrow;
# Phase 2 will broaden it alongside the FTS5 query plan.
_FTS_DISQUALIFYING_METACHARS: Final[frozenset[str]] = frozenset(
    {
        ".",
        "*",
        "+",
        "?",
        "^",
        "$",
        "|",
        "(",
        ")",
        "[",
        "]",
        "{",
        "}",
        "\\",
    }
)


def is_fts_eligible(pattern: str, *, is_regex: bool, whole_word: bool) -> bool:
    """Return True when the pattern is safe to run through FTS5.

    Index-eligible queries are:

    * literal search,
    * whole-word literal search,
    * simple case-sensitive/case-insensitive token search,
    * phrase search expressible in FTS5 without changing match semantics.

    Non-eligible queries include arbitrary regex constructs,
    multiline patterns, lookaround, capture-group semantics,
    backreferences, byte-oriented searches, and substring patterns
    that FTS tokenization cannot represent exactly.
    """
    if is_regex:
        return False
    if not pattern or pattern.isspace():
        return False
    if any(ch in _FTS_DISQUALIFYING_METACHARS for ch in pattern):
        return False
    # whole_word with literal is supported; reject when combined
    # with multi-word phrase syntax that FTS cannot represent exactly.
    if whole_word and " " in pattern:
        return False
    return True


def fts_query_for(pattern: str, *, whole_word: bool) -> str:
    """Convert a literal pattern into an FTS5 MATCH query.

    Ponytail: minimal FTS5 escape; only the basic characters that
    matter for whole-word vs substring matching. The FTS5
    ``unicode61`` tokenizer used here splits on whitespace and
    punctuation; escaping with ``"..."`` produces a phrase query.
    """
    # Escape internal quotes by doubling them.
    escaped = pattern.replace('"', '""')
    if whole_word:
        return f'"{escaped}"'
    return f'"{escaped}"'


__all__ = [
    "GREP_COMMENT_DOC",
    "GREP_DEFINITION_BONUS",
    "GREP_GIT_CHANGED",
    "GREP_GRAPH_COMPONENT",
    "GREP_GRAPH_NEIGHBOR",
    "GREP_SAME_SYMBOL_BODY",
    "PHASE2_DISABLED_NOTE",
    "RankedItem",
    "SEARCH_EXACT_BASENAME",
    "SEARCH_GENERATED_PENALTY",
    "SEARCH_GIT_CHANGED",
    "SEARCH_GRAPH_NEIGHBOR",
    "SEARCH_ROLE_REQUESTED",
    "SEARCH_SYMBOL_DEFINITION",
    "SEARCH_SYMBOL_MENTION",
    "fts_query_for",
    "is_fts_eligible",
    "is_generated_path",
    "is_source_role",
    "is_test_role",
    "score_grep_match",
    "score_search_file",
    "sort_ranked",
]