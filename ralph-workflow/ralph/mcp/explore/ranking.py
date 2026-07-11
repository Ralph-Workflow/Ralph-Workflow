"""Deterministic ranking for indexed ``search_files`` and ``grep_files``.

The ranking pipeline combines path/FTS/role/git-changed signals with
the indexed symbol and graph evidence. Score components are computed
from live indexed rows; when the index lacks the relevant data the
scorer surfaces explicit zero components with a precise
``component:no_indexed_data`` reason so callers can audit the
missing context.

Every ranked response returns a ``score_reasons`` list so tests can
assert WHY a path outranks another. Ties sort by
``(path, line, evidence_id)`` for stable output.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from ralph.mcp.explore.store import ExploreStore

# --- Component constants --------------------------------------------------

# Phase 1 search_files components (architecture finding contract).
SEARCH_EXACT_BASENAME: Final[int] = 100
SEARCH_SYMBOL_DEFINITION: Final[int] = 80
SEARCH_SYMBOL_MENTION: Final[int] = 60
SEARCH_GIT_CHANGED: Final[int] = 40
SEARCH_ROLE_REQUESTED: Final[int] = 30
SEARCH_GRAPH_NEIGHBOR: Final[int] = 20
SEARCH_GENERATED_PENALTY: Final[int] = -50

# Phase 1 grep_files rank_by components.
GREP_DEFINITION_BONUS: Final[int] = 100
GREP_SAME_SYMBOL_BODY: Final[int] = 60
GREP_COMMENT_DOC: Final[int] = 30
GREP_GRAPH_NEIGHBOR: Final[int] = 50
GREP_GRAPH_COMPONENT: Final[int] = 20
GREP_GIT_CHANGED: Final[int] = 40

# Reason emitted when an indexed-evidence component has zero
# contribution because the explore index does not contain the
# relevant rows for the candidate path. The constant is named
# with ``SEARCH`` / ``GREP`` prefixes only where the wording is
# not reusable across components; the auditor surfaces
# ``component:no_indexed_data`` (or ``+0 component_name``) so
# tests can distinguish a missing index from a real scoring
# value.
INDEXED_COMPONENT_NOT_AVAILABLE: Final[str] = "no_indexed_data"


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


def is_docs_role(path: str) -> bool:
    """True for documentation-only files (heuristic).

    Matches Markdown / RST / text files that live under the
    canonical docs tree or carry an obvious documentation name.
    """
    lowered = path.lower()
    if (
        "/docs/" in lowered
        or lowered.startswith("docs/")
        or "/doc/" in lowered
        or lowered.startswith("doc/")
        or "/documentation/" in lowered
    ):
        return True
    name = lowered.rsplit("/", maxsplit=1)[-1]
    return name in {
        "readme.md",
        "readme.rst",
        "readme.txt",
        "license.md",
        "license",
        "license.txt",
        "license.rst",
        "contributing.md",
        "code_of_conduct.md",
        "changelog.md",
        "changes.md",
    }


def is_config_role(path: str) -> bool:
    """True for build/config/toolchain files (heuristic).

    Matches canonical configuration extensions and a small set of
    well-known build / CI filenames. List-extension files
    (``requirements.txt``, ``pyproject.toml`` ...) and dotfile
    configurations (``.gitignore``, ``.ruff.toml`` ...) both count
    so a caller asking for ``role=config`` actually narrows the
    result instead of returning the full glob set.
    """
    lowered = path.lower()
    if lowered.endswith(
        (
            ".toml",
            ".yaml",
            ".yml",
            ".cfg",
            ".ini",
            ".json",
        )
    ):
        return True
    name = lowered.rsplit("/", maxsplit=1)[-1]
    if not name:
        return False
    if name.startswith("."):
        # Dotfile configs (``pyproject.toml`` is a normal ``.toml``
        # match; dotfiles that are NOT configs are usually
        # editor / VCS metadata and belong to ``generated``).
        config_dotfiles = {
            ".gitignore",
            ".gitattributes",
            ".gitkeep",
            ".editorconfig",
            ".ruff.toml",
            ".ruff_cache",
            ".mypy.ini",
            ".flake8",
            ".pylintrc",
            ".env",
            ".env.example",
            ".envrc",
        }
        return name in config_dotfiles
    config_names = {
        "makefile",
        "dockerfile",
        "vagrantfile",
        "rakefile",
        "tox.ini",
        "noxfile.py",
        "pyproject.toml",
        "setup.cfg",
        "setup.py",
        "requirements.txt",
        "poetry.lock",
        "uv.lock",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "tsconfig.json",
        "package.json",
        "gemfile",
        ".gitlab-ci.yml",
        ".github",
    }
    return name in config_names or name.endswith(
        ("-config.json", "-config.yaml", "-config.yml", "-config.toml")
    )


def is_generated_role(path: str) -> bool:
    """True for generated / vendor / build-output files (heuristic).

    Ponytail: kept narrow on purpose so a ``role=generated`` filter
    does not silently nuke results an agent expected to be
    editable source. Vendor trees (``vendor/``, ``node_modules/``)
    and obvious build outputs (``dist/``, ``build/`` ...) count,
    but a regular test fixture is NOT generated just because the
    word "generated" appears in a comment.
    """
    lowered = path.lower()
    if (
        lowered.startswith("vendor/")
        or "/vendor/" in lowered
        or lowered.startswith("node_modules/")
        or "/node_modules/" in lowered
        or lowered.startswith(".venv/")
        or "/.venv/" in lowered
        or lowered.startswith("__pycache__/")
        or "/__pycache__/" in lowered
        or lowered.startswith("dist/")
        or "/dist/" in lowered
        or lowered.startswith("build/")
        or "/build/" in lowered
        or lowered.startswith(".ruff_cache/")
        or "/.ruff_cache/" in lowered
        or lowered.startswith(".mypy_cache/")
        or "/.mypy_cache/" in lowered
        or lowered.startswith(".pytest_cache/")
        or "/.pytest_cache/" in lowered
        or lowered.startswith("target/")
        or "/target/" in lowered
    ):
        return True
    name = lowered.rsplit("/", maxsplit=1)[-1]
    return (
        name in {"package-lock.json", "yarn.lock", "pnpm-lock.yaml"}
        or name.endswith(".min.js")
        or name.endswith(".min.css")
        or name.endswith(".generated.py")
        or name.endswith("_pb2.py")
        or name.endswith("_pb2.pyi")
        or name.endswith(".pb.go")
    )


_ROLE_PREDICATES = {
    "source": is_source_role,
    "test": is_test_role,
    "docs": is_docs_role,
    "config": is_config_role,
    "generated": is_generated_role,
}


def matches_role(path: str, role: str) -> bool:
    """Return True when ``path`` matches the requested ``role`` predicate.

    The set of roles is the canonical ``source`` / ``test`` /
    ``docs`` / ``config`` / ``generated`` / ``any`` taxonomy that
    ``search_files`` advertises. Unrecognized role names return
    False instead of falling back to a free glob, so the handler
    can surface the typo to the caller rather than silently
    returning the full glob set.
    """
    predicate = _ROLE_PREDICATES.get(role)
    if predicate is None:
        return False
    return predicate(path)


def sort_ranked(items: list[RankedItem]) -> list[RankedItem]:
    """Stable sort: score DESC, then path ASC, then line ASC, then evidence_id ASC."""
    def _sort_key(item: RankedItem) -> tuple[int, str, int, str]:
        line_value: int = item.line if item.line is not None else -1
        return (-item.score, item.path, line_value, item.evidence_id or "")

    return sorted(items, key=_sort_key)


# --- search_files scoring -------------------------------------------------


def score_search_file(
    *,
    candidate_path: str,
    basename: str,
    role_requested: str | None,
    is_git_changed: bool,
    contains_symbol: str | None = None,
) -> RankedItem:
    """Compute a search_files score for one candidate path.

    The scoring contract:

    * ``SEARCH_EXACT_BASENAME`` (+100) for an exact basename match.
    * ``SEARCH_GIT_CHANGED`` (+40) for paths the lifecycle hook
      marked dirty or the live ``git status`` reported.
    * ``SEARCH_ROLE_REQUESTED`` (+30) for role-matched paths when
      the caller asked for ``source`` / ``test``.
    * ``SEARCH_GENERATED_PENALTY`` (-50) for vendor/generated paths.
    * ``SEARCH_SYMBOL_DEFINITION`` (+80) and
      ``SEARCH_SYMBOL_MENTION`` (+60) are only emitted when the
      caller passed ``contains_symbol`` AND the candidate already
      survived the symbol filter (i.e. the index recognized the
      symbol as defined in or referenced from the path). Otherwise
      the components stay at 0 with a precise
      ``component:no_indexed_data`` note so callers can audit
      missing index context.
    """
    score = 0
    reasons: list[str] = []

    basename_match = candidate_path.rsplit("/", maxsplit=1)[-1] == basename
    if basename_match:
        score += SEARCH_EXACT_BASENAME
        reasons.append(f"+{SEARCH_EXACT_BASENAME} exact_path_basename")
    else:
        # Symbol and graph components only contribute when the
        # caller passed ``contains_symbol``; otherwise the
        # missing-data note makes the absent context auditable.
        reasons.append(
            f"+0 symbol_definition:{INDEXED_COMPONENT_NOT_AVAILABLE}"
        )
        reasons.append(
            f"+0 symbol_mention:{INDEXED_COMPONENT_NOT_AVAILABLE}"
        )
        reasons.append(
            f"+0 graph_neighbor:{INDEXED_COMPONENT_NOT_AVAILABLE}"
        )

    if is_git_changed:
        score += SEARCH_GIT_CHANGED
        reasons.append(f"+{SEARCH_GIT_CHANGED} git_changed_path")

    if role_requested in {"source", "test"} and (
        (role_requested == "source" and is_source_role(candidate_path))
        or (role_requested == "test" and is_test_role(candidate_path))
    ):
        score += SEARCH_ROLE_REQUESTED
        reasons.append(f"+{SEARCH_ROLE_REQUESTED} role_requested={role_requested}")

    # When ``contains_symbol`` is set, the candidate has already
    # passed the symbol filter (in ``handle_search_files``), so it
    # earns the symbol-mention bonus to make ranking auditable.
    if contains_symbol is not None:
        score += SEARCH_SYMBOL_MENTION
        reasons.append(
            f"+{SEARCH_SYMBOL_MENTION} symbol_mention (contains_symbol={contains_symbol})"
        )

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
    store: ExploreStore | None = None,
    chunk_id: str | None = None,
    graph_target: str | None = None,
) -> RankedItem:
    """Compute a grep_files rank_by score for one match.

    The scoring contract:

    * Baseline: every match carries +1.
    * ``is_git_changed`` adds ``GREP_GIT_CHANGED`` when the file is
      marked dirty or the live ``git status`` reports it.
    * Symbol components: when ``store`` and ``chunk_id`` are
      provided and the chunk is associated with a definition or
      symbol body, the appropriate score bonus is added. Absent
      index context surfaces a ``+0 component:no_indexed_data``
      reason.
    * Graph components: when ``graph_target`` is provided and the
      match is in a caller/importer/test neighbor, the
      ``GREP_GRAPH_NEIGHBOR`` bonus is added. ``GREP_GRAPH_COMPONENT``
      is added when the match file is in the same graph component as
      the target.
    """
    score = 0
    reasons: list[str] = []

    # Baseline: every match carries +1.
    score += 1
    reasons.append("+1 bare_match")

    # Look up symbol/graph context from the index.
    definition_bonus = 0
    same_symbol_bonus = 0
    comment_doc_bonus = 0
    graph_neighbor_bonus = 0
    graph_component_bonus = 0
    if store is not None and chunk_id:
        try:
            ctx = _compute_grep_context(
                store,
                chunk_id=chunk_id,
                path=path,
                graph_target=graph_target,
            )
            definition_bonus = ctx[0]
            same_symbol_bonus = ctx[1]
            comment_doc_bonus = ctx[2]
            graph_neighbor_bonus = ctx[3]
            graph_component_bonus = ctx[4]
        except Exception:
            # Ponytail: ranking must never crash on a malformed
            # index row. Leave the bonuses at zero with a precise
            # missing-data note so the response remains complete
            # and auditable.
            pass

    if definition_bonus:
        score += GREP_DEFINITION_BONUS
        reasons.append(f"+{GREP_DEFINITION_BONUS} definition_name_or_signature")
    else:
        reasons.append(
            f"+0 definition_bonus:{INDEXED_COMPONENT_NOT_AVAILABLE}"
        )
    if same_symbol_bonus:
        score += GREP_SAME_SYMBOL_BODY
        reasons.append(f"+{GREP_SAME_SYMBOL_BODY} same_symbol_body")
    else:
        reasons.append(
            f"+0 same_symbol_body:{INDEXED_COMPONENT_NOT_AVAILABLE}"
        )
    if comment_doc_bonus:
        score += GREP_COMMENT_DOC
        reasons.append(f"+{GREP_COMMENT_DOC} comment_or_doc_tied_to_symbol")
    else:
        reasons.append(
            f"+0 comment_doc:{INDEXED_COMPONENT_NOT_AVAILABLE}"
        )
    if graph_neighbor_bonus:
        score += GREP_GRAPH_NEIGHBOR
        reasons.append(
            f"+{GREP_GRAPH_NEIGHBOR} graph_neighbor_of_target"
        )
    else:
        reasons.append(
            f"+0 graph_neighbor:{INDEXED_COMPONENT_NOT_AVAILABLE}"
        )
    if graph_component_bonus:
        score += GREP_GRAPH_COMPONENT
        reasons.append(
            f"+{GREP_GRAPH_COMPONENT} same_graph_component_as_target"
        )
    else:
        reasons.append(
            f"+0 graph_component:{INDEXED_COMPONENT_NOT_AVAILABLE}"
        )

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


def _compute_grep_context(
    store: ExploreStore | None,
    *,
    chunk_id: str,
    path: str,
    graph_target: str | None,
) -> tuple[int, int, int, int, int]:
    """Compute the five Phase 2 grep-context bonuses.

    Ponytail: inlined as a private helper so callers do not have
    to import a separate module. Returns a 5-tuple of
    (definition, same_symbol, comment_doc, graph_neighbor,
    graph_component) integer bonuses.
    """
    definition_bonus = 0
    same_symbol_bonus = 0
    comment_doc_bonus = 0
    graph_neighbor_bonus = 0
    graph_component_bonus = 0
    if store is None or not chunk_id:
        return (
            definition_bonus,
            same_symbol_bonus,
            comment_doc_bonus,
            graph_neighbor_bonus,
            graph_component_bonus,
        )
    # Definition / same_symbol / comment_doc from chunks + symbols + spans.
    chunk_row: sqlite3.Row | None = None
    try:
        chunk_row = store._conn.execute(
            "SELECT path, start_line, end_line FROM chunks WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
    except Exception:
        chunk_row = None
    if chunk_row is not None:
        chunk_path = _row_str(chunk_row, 0)
        start_line = _row_int(chunk_row, 1)
        end_line = _row_int(chunk_row, 2)
        if chunk_path == path:
            rows: list[sqlite3.Row] = []
            try:
                rows = store._conn.execute(
                    "SELECT s.name, s.qualified_name, s.kind "
                    "FROM symbols s "
                    "JOIN spans sp ON sp.span_id = s.span_id "
                    "WHERE s.path = ? "
                    "AND sp.start_line <= ? AND sp.end_line >= ?",
                    (path, end_line, start_line),
                ).fetchall()
            except Exception:
                rows = []
            for row in rows:
                kind = _row_str(row, 2)
                qname = _row_str(row, 1)
                name = _row_str(row, 0)
                if kind in ("function", "class", "method", "module"):
                    definition_bonus = 1
                if qname and name:
                    same_symbol_bonus = 1
                if kind == "doc":
                    comment_doc_bonus = 1
    if graph_target:
        neighbor_row: sqlite3.Row | None = None
        try:
            neighbor_row = store._conn.execute(
                "SELECT 1 FROM edges "
                "WHERE path = ? AND (source_id = ? OR target_id = ?) "
                "AND relation IN ('calls_syntax','imports','tests') "
                "LIMIT 1",
                (path, graph_target, graph_target),
            ).fetchone()
        except Exception:
            neighbor_row = None
        if neighbor_row is not None:
            graph_neighbor_bonus = 1
        component_row: sqlite3.Row | None = None
        try:
            component_row = store._conn.execute(
                "SELECT 1 FROM edges "
                "WHERE (path = ? AND relation IN "
                "('calls_syntax','imports','tests','contains')) "
                "LIMIT 1",
                (path,),
            ).fetchone()
        except Exception:
            component_row = None
        if component_row is not None:
            graph_component_bonus = 1
    return (
        definition_bonus,
        same_symbol_bonus,
        comment_doc_bonus,
        graph_neighbor_bonus,
        graph_component_bonus,
    )


def _row_str(row: sqlite3.Row, index: int) -> str:
    """Best-effort string extraction for sqlite3.Row positional access."""
    try:
        v: object = row[index]
    except Exception:
        return ""
    if v is None:
        return ""
    return str(v)


def _row_int(row: sqlite3.Row, index: int) -> int:
    """Best-effort integer extraction for sqlite3.Row positional access."""
    try:
        v: object = row[index]
    except Exception:
        return 0
    if v is None:
        return 0
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, (str, bytes, bytearray)):
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0
    return 0


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


def is_fts_eligible(
    pattern: str,
    *,
    is_regex: bool,
    whole_word: bool,
    case_sensitive: bool = False,
) -> bool:
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

    Case-sensitive search: the FTS5 ``unicode61`` tokenizer is
    case-INsensitive by default, so requesting a ``case_sensitive``
    literal would change match semantics compared to the live grep
    path. ``is_fts_eligible`` returns False when ``case_sensitive``
    is True so the handler falls back to live grep in
    ``use_index='auto'`` and fails closed in ``use_index='always'``
    instead of silently returning a case-INsensitive FTS match.

    ``case_sensitive`` defaults to ``False`` because the live grep
    default is case-sensitive and the prior call site omitted the
    argument entirely; ``is_fts_eligible`` keeps accepting its
    legacy ``case_sensitive`` semantics by treating omitted ==
    case-INsensitive == same as the FTS5 tokenizer.
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
    return not case_sensitive


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
    "INDEXED_COMPONENT_NOT_AVAILABLE",
    "SEARCH_EXACT_BASENAME",
    "SEARCH_GENERATED_PENALTY",
    "SEARCH_GIT_CHANGED",
    "SEARCH_GRAPH_NEIGHBOR",
    "SEARCH_ROLE_REQUESTED",
    "SEARCH_SYMBOL_DEFINITION",
    "SEARCH_SYMBOL_MENTION",
    "RankedItem",
    "fts_query_for",
    "is_config_role",
    "is_docs_role",
    "is_fts_eligible",
    "is_generated_path",
    "is_generated_role",
    "is_source_role",
    "is_test_role",
    "matches_role",
    "score_grep_match",
    "score_search_file",
    "sort_ranked",
]  # grouped for readability
