"""Verification of rejection citations against sealed proposal diffs.

Provides functions to verify that rejection reason citations in a commit_review
artifact legitimately reference actual modified paths or findings from the
proposal diff, preventing unjustified rejections or hallucinated citations.

Citation verification is a safety boundary: an unverifiable citation is a
reviewer malfunction (a gate failure), never a rollback.  The verifier
therefore fails closed.

* A cited path is verified only when its normalized repository-relative form
  equals a diff entry path exactly.  A basename, a path suffix, a substring,
  or a path that is merely absent from the diff never verifies.
* A cited count is verified only when it is typed and backed by the diff:
  ``N deleted files`` needs exactly ``N`` deleted entries, ``N binary files``
  exactly ``N`` binary entries, ``N added files`` exactly ``N`` added
  entries, and ``N modified files`` exactly ``N`` modified entries.  A count
  whose category has no matching entry fails, and so does a count whose
  category cannot be determined.
* Every count-like noun is a count, not only the noun ``file``: ``N paths``,
  ``N deletions``, ``N entries``, ``N changes``, ``N lines``, ``N items`` and
  ``N modifications`` assert quantities of the diff too.  A count the diff
  cannot back, and a count whose noun or qualifier names a category the
  verifier does not know, are unverifiable and fail closed.
* A number that names no count is not a count claim: a path that carries
  digits, a ``path:line`` suffix, and a bare line number are references to
  a place in the tree.
* Every cited path and every cited count of a reason must verify, and a
  reason without a single verified citation fails.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Punctuation to strip from token boundaries when extracting path candidates.
_STRIP_PUNCT: str = ".,;:()[]{}'\"`<>!?"

# Characters that may wrap a cited path (quotes, backticks, markdown emphasis).
_WRAPPER_CHARS: str = "\"'`*~"

#: The shortest text that can carry a matching wrapper pair.
_MIN_WRAPPED_TEXT_LENGTH: int = 2

# Common abbreviations and non-path tokens that should not be mistaken for file paths.
_EXCLUDED_TOKENS: frozenset[str] = frozenset(
    {
        "e.g.",
        "i.e.",
        "etc.",
        "vs.",
        "v.",
        "n/a",
        "tbd",
        "http:",
        "https:",
        "file:",
        "git:",
    }
)

# Known file extensions to recognize when a token represents a path.
_COMMON_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".a",
        ".bash",
        ".bin",
        ".c",
        ".cfg",
        ".cpp",
        ".css",
        ".dll",
        ".dylib",
        ".exe",
        ".gif",
        ".go",
        ".gz",
        ".h",
        ".hpp",
        ".html",
        ".ini",
        ".java",
        ".jpeg",
        ".jpg",
        ".js",
        ".json",
        ".jsx",
        ".lock",
        ".log",
        ".md",
        ".o",
        ".php",
        ".png",
        ".py",
        ".pyc",
        ".rb",
        ".rst",
        ".rs",
        ".scss",
        ".sh",
        ".so",
        ".sql",
        ".svg",
        ".tar",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".wasm",
        ".xml",
        ".yaml",
        ".yml",
        ".zip",
        ".zsh",
    }
)

# Diff categories a count claim may be typed against.  The word is the one a
# reviewer writes; the category is the diff fact the count must be backed by.
_COUNT_KIND_BY_WORD: dict[str, str] = {
    "added": "added",
    "binary": "binary",
    "changed": "modified",
    "deleted": "deleted",
    "modified": "modified",
    "new": "added",
    "removed": "deleted",
    "total": "total",
    "untracked": "added",
}

# The category used when a count names no category at all ("3 files").
_TOTAL_COUNT_KIND: str = "total"

# The nouns a reviewer may write to assert a quantity of the diff, mapped to
# the diff category the noun asserts.  A noun whose value is None names a
# quantity the diff summary cannot back at all: such a count is
# unverifiable, so it fails closed instead of passing unexamined.
_COUNT_NOUN_CATEGORY: dict[str, str | None] = {
    "addition": "added",
    "additions": "added",
    "change": _TOTAL_COUNT_KIND,
    "changes": _TOTAL_COUNT_KIND,
    "deletion": "deleted",
    "deletions": "deleted",
    "diff": _TOTAL_COUNT_KIND,
    "diffs": _TOTAL_COUNT_KIND,
    "edit": "modified",
    "edits": "modified",
    "entry": _TOTAL_COUNT_KIND,
    "entries": _TOTAL_COUNT_KIND,
    "file": _TOTAL_COUNT_KIND,
    "files": _TOTAL_COUNT_KIND,
    "hunk": None,
    "hunks": None,
    "item": _TOTAL_COUNT_KIND,
    "items": _TOTAL_COUNT_KIND,
    "line": None,
    "lines": None,
    "modification": "modified",
    "modifications": "modified",
    "patch": None,
    "patches": None,
    "path": _TOTAL_COUNT_KIND,
    "paths": _TOTAL_COUNT_KIND,
    "removal": "deleted",
    "removals": "deleted",
}

# The words that name a place inside a file rather than a quantity of the
# diff.  A number that follows one of them is a reference, not a count.
_COUNT_REFERENCE_WORDS: frozenset[str] = frozenset(
    {
        "byte",
        "bytes",
        "char",
        "chars",
        "character",
        "characters",
        "column",
        "columns",
        "line",
        "lines",
        "page",
        "pages",
        "row",
        "rows",
        "version",
        "versions",
    }
)

# How many tokens may sit between a number and the noun it counts.
_COUNT_NOUN_WINDOW: int = 3

# The filler words that may sit between a counted noun and a trailing
# category word ("2 files were deleted").
_COUNT_FILLER_WORDS: frozenset[str] = frozenset(
    {
        "are",
        "be",
        "been",
        "got",
        "had",
        "has",
        "have",
        "is",
        "was",
        "were",
    }
)

_MARKDOWN_LINK_RE: re.Pattern[str] = re.compile(r"\[([^]]*)\]\(([^)]+)\)")
_INLINE_CODE_RE: re.Pattern[str] = re.compile(r"`([^`]+)`")
_QUOTED_STR_RE: re.Pattern[str] = re.compile(r"""(?:'([^']+)'|"([^"]+)")""")
_EXPLICIT_COUNT_RE: re.Pattern[str] = re.compile(r"\bcount:\s*(\d+)\b", re.IGNORECASE)
_DIGITS_AND_DOTS_RE: re.Pattern[str] = re.compile(r"^\d+(\.\d+)+$")
_ITEM_ID_PREFIX_RE: re.Pattern[str] = re.compile(r"^\[[A-Za-z0-9_-]+\]\s*")
_MULTI_SLASH_RE: re.Pattern[str] = re.compile(r"/{2,}")
_LINE_SUFFIX_RE: re.Pattern[str] = re.compile(r":\d+(?:-\d+)?(?::\d+)?$")

# The characters that are stripped from the edges of a token before it is
# read as a word.
_TOKEN_EDGE_RE: re.Pattern[str] = re.compile(r"^[^0-9A-Za-z_-]+|[^0-9A-Za-z_-]+$")

# A token made of word characters only: a plain word a count phrase may
# span ("999 secret files").  A token that carries a path separator ends
# the phrase.
_PLAIN_WORD_RE: re.Pattern[str] = re.compile(r"^[A-Za-z_-]+$")

# A token that is a bare number, optionally grouped with thousands separators.
_NUMBER_TOKEN_RE: re.Pattern[str] = re.compile(r"^(?:\d+|\d{1,3}(?:,\d{3})+)$")


@dataclass(frozen=True)
class _DiffSummary:
    """Normalized diff facts a citation is verified against.

    Attributes:
        paths: Every repository-relative path in the diff.
        basenames: The bare file names of those paths.  They are used to
            recognize a path citation at all; they never verify one.
        deleted: Paths whose diff entry has deletion status.
        added: Paths whose diff entry has added status.
        modified: Paths whose diff entry has modified status.
        binary: Paths whose diff entry is marked binary.
        tracked: The repository paths the base tree holds.  A token that
            names one of them is a path citation even when it carries no
            file extension, so an absent tracked file can never be cited
            silently.
    """

    paths: frozenset[str]
    basenames: frozenset[str]
    deleted: frozenset[str]
    added: frozenset[str]
    modified: frozenset[str]
    binary: frozenset[str]
    tracked: frozenset[str] = frozenset()

    def count_for(self, kind: str) -> int:
        """Return how many diff entries back a count of the given category."""
        if kind == "deleted":
            return len(self.deleted)
        if kind == "added":
            return len(self.added)
        if kind == "modified":
            return len(self.modified)
        if kind == "binary":
            return len(self.binary)
        return len(self.paths)


def _strip_wrappers(text: str) -> str:
    """Remove surrounding quotes, backticks, and markdown emphasis markers."""
    previous: str | None = None
    while previous != text:
        previous = text
        text = text.strip()
        if (
            len(text) >= _MIN_WRAPPED_TEXT_LENGTH
            and text[0] == text[-1]
            and text[0] in _WRAPPER_CHARS
        ):
            text = text[1:-1]
    return text.strip()


def _normalize_path(raw: str) -> str:
    """Normalize a cited or diff path to its repository-relative form.

    Surrounding quotes, backticks, and markdown emphasis are stripped, Windows
    separators become POSIX separators, a ``:line`` citation suffix is dropped,
    and a leading ``./`` or ``/`` is removed.  Two paths are the same path
    only when their normalized forms are equal.

    Args:
        raw: The raw path text.

    Returns:
        The normalized repository-relative path, or an empty string when the
        text does not name a path.
    """
    text = _strip_wrappers(raw)
    if not text:
        return ""
    text = text.replace("\\", "/")
    text = _MULTI_SLASH_RE.sub("/", text)
    text = _LINE_SUFFIX_RE.sub("", text)
    while text.startswith("./"):
        text = text[2:]
    text = text.lstrip("/")
    return text.rstrip("/")


def _entry_fields(entry: object) -> tuple[str, str, bool]:
    """Extract ``(path, status, is_binary)`` from a diff entry of any shape.

    Args:
        entry: A diff entry object, a mapping with the same keys, or a path.

    Returns:
        The raw path, the raw status, and the binary flag.  Missing or
        wrongly typed fields fall back to their empty value.
    """
    if isinstance(entry, dict):
        raw_path: object = entry.get("path")
        raw_status: object = entry.get("status")
        raw_binary: object = entry.get("is_binary")
    else:
        raw_path = getattr(entry, "path", None)
        raw_status = getattr(entry, "status", None)
        raw_binary = getattr(entry, "is_binary", None)

    path = raw_path if isinstance(raw_path, str) else ""
    status = raw_status if isinstance(raw_status, str) else ""
    is_binary = raw_binary if isinstance(raw_binary, bool) else False
    return path, status, is_binary


def _entry_tracked_paths(entry: object) -> frozenset[str]:
    """Return the base tree's tracked paths carried by a diff entry.

    Args:
        entry: A diff entry object or a mapping with the same keys.

    Returns:
        The tracked paths, or an empty set when the entry carries none.
    """
    if isinstance(entry, dict):
        raw: object = entry.get("base_tree_paths")
    else:
        raw = getattr(entry, "base_tree_paths", None)
    if not isinstance(raw, (frozenset, set, tuple, list)):
        return frozenset()
    return frozenset(str(item) for item in raw)


def _status_kind(status: str) -> str:
    """Classify a git diff status into the category a count is typed against."""
    code = status.strip().upper()
    if not code:
        return ""
    if code.startswith("D"):
        return "deleted"
    if code.startswith("A"):
        return "added"
    if code.startswith("M"):
        return "modified"
    return ""


def _extract_diff_info(diff_entries: Sequence[object]) -> _DiffSummary:
    """Collect the normalized diff facts citations are verified against.

    Args:
        diff_entries: Sequence of diff entry objects, mappings, or path strings.

    Returns:
        The normalized summary of the diff.
    """
    paths: set[str] = set()
    deleted: set[str] = set()
    added: set[str] = set()
    modified: set[str] = set()
    binary: set[str] = set()
    tracked: set[str] = set()

    for entry in diff_entries:
        tracked.update(_entry_tracked_paths(entry))
        raw_path, status, is_binary = _entry_fields(entry)
        if not raw_path:
            continue

        normalized = _normalize_path(raw_path)
        if not normalized:
            continue

        paths.add(normalized)
        kind = _status_kind(status)
        if kind == "deleted":
            deleted.add(normalized)
        elif kind == "added":
            added.add(normalized)
        elif kind == "modified":
            modified.add(normalized)
        if is_binary:
            binary.add(normalized)

    basenames = {path.rsplit("/", 1)[-1] for path in paths}
    return _DiffSummary(
        paths=frozenset(paths),
        basenames=frozenset(basenames),
        deleted=frozenset(deleted),
        added=frozenset(added),
        modified=frozenset(modified),
        binary=frozenset(binary),
        tracked=frozenset(tracked),
    )


def _is_path_candidate(token: str, summary: _DiffSummary) -> bool:
    """Determine whether a normalized token should be read as a cited path."""
    if not token:
        return False
    if token in summary.paths or token in summary.basenames:
        return True
    if token in summary.tracked:
        # The token names a tracked repository file, so it IS a path
        # citation even without a file extension.  A tracked file that is
        # absent from the diff cannot verify, so it must not pass silently.
        return True
    if "/" in token:
        return True
    lower = token.lower()
    if any(lower.endswith(ext) for ext in _COMMON_EXTENSIONS):
        return True
    return bool(
        token.startswith(".")
        and len(token) > 1
        and "/" not in token
        and not token.startswith("..")
        and any(c.isalnum() for c in token[1:])
    )


def _candidate_from_token(raw: str, summary: _DiffSummary) -> str | None:
    """Return the normalized path a token cites, or None when it cites none."""
    token = raw.strip()
    if not token or token in _EXCLUDED_TOKENS:
        return None
    if token.startswith(("http://", "https://", "mailto:", "file://", "ftp://")):
        return None
    if _DIGITS_AND_DOTS_RE.match(token):
        return None

    normalized = _normalize_path(token)
    if not normalized or normalized in _EXCLUDED_TOKENS:
        return None
    if not _is_path_candidate(normalized, summary):
        return None
    return normalized


def _matches_diff_path(candidate: str, summary: _DiffSummary) -> bool:
    """Check whether a normalized candidate is exactly a diff entry path.

    Only equality verifies a citation: a basename, a path suffix, and a
    substring all fail.
    """
    return candidate in summary.paths


def _extract_candidates_from_text(text: str, summary: _DiffSummary) -> list[str]:
    """Extract cited paths from a reason line.

    Args:
        text: The reason line.
        summary: The normalized diff facts.

    Returns:
        The normalized paths the line cites.
    """
    candidates: list[str] = []

    # 1. Markdown links: [text](target)
    for match in _MARKDOWN_LINK_RE.finditer(text):
        target = str(match.group(2)).strip()
        if target.startswith("file://"):
            target = target[len("file://") :]
        target = target.split("#")[0].split("?")[0].strip()
        candidate = _candidate_from_token(target, summary)
        if candidate is not None:
            candidates.append(candidate)

    # 2. Backtick code spans: `code`
    for match in _INLINE_CODE_RE.finditer(text):
        candidate = _candidate_from_token(match.group(1), summary)
        if candidate is not None:
            candidates.append(candidate)

    # 3. Quoted strings: 'path' or "path"
    for match in _QUOTED_STR_RE.finditer(text):
        g1 = match.group(1)
        g2 = match.group(2)
        quoted = g1 if isinstance(g1, str) else g2 if isinstance(g2, str) else ""
        candidate = _candidate_from_token(quoted, summary)
        if candidate is not None:
            candidates.append(candidate)

    # 4. Words/tokens in line
    clean_text = _ITEM_ID_PREFIX_RE.sub("", text)
    for word in clean_text.split():
        clean_word = word.strip(_STRIP_PUNCT)
        if clean_word.startswith("file://"):
            clean_word = clean_word[len("file://") :]
        clean_word = clean_word.strip(_STRIP_PUNCT)
        candidate = _candidate_from_token(clean_word, summary)
        if candidate is not None:
            candidates.append(candidate)

    return candidates


def _verify_path_candidates(
    lines: list[str],
    summary: _DiffSummary,
) -> tuple[set[str], set[str]]:
    """Partition extracted path citations into matched and unmatched sets.

    A cited path that is absent from the diff is never excused: it lands in
    the unmatched set and fails the whole reason.
    """
    matched: set[str] = set()
    unmatched: set[str] = set()

    for line in lines:
        for candidate in _extract_candidates_from_text(line, summary):
            if _matches_diff_path(candidate, summary):
                matched.add(candidate)
            else:
                unmatched.add(candidate)

    return matched, unmatched


def _word_tokens(text: str) -> list[str]:
    """Split a reason line into the lower-case words it carries.

    Args:
        text: The reason line.

    Returns:
        One token per whitespace-separated word, with the markdown and
        punctuation wrappers removed from its edges and its letters folded
        to lower case, so a count claim is recognised whatever case the
        reviewer wrote it in.
    """
    return [_TOKEN_EDGE_RE.sub("", token).lower() for token in text.split()]


def _count_noun_index(index: int, tokens: list[str]) -> int | None:
    """Return the index of the noun a number counts, if it names one.

    The counted noun is the word that follows the number, optionally behind
    other words ("999 secret files").  The scan stops at a token that is
    not a plain word, because a path or a symbol ends the phrase.

    Args:
        index: The index of the number token.
        tokens: The word tokens of the reason line.

    Returns:
        The index of the counted noun, or None when the number names none.
    """
    for offset in range(1, _COUNT_NOUN_WINDOW + 1):
        position = index + offset
        if position >= len(tokens):
            return None
        word = tokens[position]
        if not _PLAIN_WORD_RE.match(word):
            return None
        if word in _COUNT_NOUN_CATEGORY:
            return position
    return None


def _trailing_category(noun_index: int, tokens: list[str]) -> str | None:
    """Return the category word that follows a counted noun, if any.

    Args:
        noun_index: The index of the counted noun.
        tokens: The word tokens of the reason line.

    Returns:
        The category word, or None when the noun carries no trailing one.
    """
    position = noun_index + 1
    while position < len(tokens) and tokens[position] in _COUNT_FILLER_WORDS:
        position += 1
    if position < len(tokens):
        word = tokens[position]
        if word in _COUNT_KIND_BY_WORD:
            return word
    return None


def _count_category(index: int, tokens: list[str]) -> tuple[bool, str | None]:
    """Return the diff category a number asserts, and whether it asserts one.

    Args:
        index: The index of the number token.
        tokens: The word tokens of the reason line.

    Returns:
        Whether the number asserts a count at all, and the diff category the
        count must be backed by.  The category is None when the claim cannot
        be typed: the noun names a quantity the diff cannot back, an unknown
        word qualifies the noun, or two categories conflict.
    """
    noun_index = _count_noun_index(index, tokens)
    if noun_index is None:
        return (False, None)

    noun = tokens[noun_index]
    quantity = _COUNT_NOUN_CATEGORY[noun]
    if quantity is None:
        return (True, None)

    typed: list[str] = []
    for word in tokens[index + 1 : noun_index]:
        category = _COUNT_KIND_BY_WORD.get(word)
        if category is None:
            # An unknown word qualifies the noun, so the claim names a
            # category the verifier does not know.
            return (True, None)
        typed.append(category)

    trailing = _trailing_category(noun_index, tokens)
    if trailing is not None:
        typed.append(_COUNT_KIND_BY_WORD[trailing])

    if not typed:
        return (True, quantity)
    if len(set(typed)) > 1:
        return (True, None)
    return (True, typed[0])


def _count_claims(tokens: list[str]) -> list[tuple[int, str | None]]:
    """Return the count claims one reason line makes.

    A claim is a number that asserts a quantity of the diff: it names a
    counted noun ("999 paths"), optionally typed by a category word ("999
    deleted files").  A number that names a place in a file instead of a
    quantity is a reference, not a claim.

    Args:
        tokens: The word tokens of the reason line.

    Returns:
        The count of every claim and the diff category it must be backed
        by.  The category is None when the claim cannot be typed, which
        makes the count unverifiable.
    """
    claims: list[tuple[int, str | None]] = []
    for index, token in enumerate(tokens):
        if not _NUMBER_TOKEN_RE.match(token):
            continue
        if index > 0 and tokens[index - 1] in _COUNT_REFERENCE_WORDS:
            continue
        claimed, category = _count_category(index, tokens)
        if not claimed:
            continue
        claims.append((int(token.replace(",", "")), category))
    return claims


def _verify_count_citations(
    lines: list[str],
    summary: _DiffSummary,
) -> tuple[set[int], set[int]]:
    """Partition extracted count citations into matched and unmatched sets.

    A count is backed only when the diff holds exactly that many entries of
    the claimed category.  A category with no entry never backs a count, and
    a count without a determinable category is unverifiable.
    """
    matched: set[int] = set()
    unmatched: set[int] = set()

    for line in lines:
        for count, category in _count_claims(_word_tokens(line)):
            if category is None:
                unmatched.add(count)
                continue
            backing = summary.count_for(category)
            if backing > 0 and count == backing:
                matched.add(count)
            else:
                unmatched.add(count)

        for match in _EXPLICIT_COUNT_RE.finditer(line):
            count_text = match.group(1)
            if not isinstance(count_text, str):
                continue
            count = int(count_text)
            backing = summary.count_for(_TOTAL_COUNT_KIND)
            if backing > 0 and count == backing:
                matched.add(count)
            else:
                unmatched.add(count)

    return matched, unmatched


def verify_reject_citations(
    reason_items: list[str] | str | None,
    diff_entries: Sequence[object],
) -> bool:
    """Verify that citations in a reject reason reference actual diff evidence.

    Every cited path must equal a diff entry path exactly after
    normalization, and every cited count must be typed and backed by exactly
    that many diff entries of the claimed category.  One unverifiable claim
    fails the whole reason, and a reason without a single verified citation
    fails too.

    Args:
        reason_items: List of reason bullet strings, a single reason string, or None.
        diff_entries: Sequence of diff entry objects, mappings with 'path',
            or path strings.

    Returns:
        True if all citations are verified against the diff and at least one
        concrete citation exists; False otherwise.
    """
    if reason_items is None:
        return False
    if isinstance(reason_items, str):
        lines = [line.strip() for line in reason_items.splitlines() if line.strip()]
    else:
        lines = [str(item).strip() for item in reason_items if str(item).strip()]

    if not lines or not diff_entries:
        return False

    summary = _extract_diff_info(diff_entries)
    if not summary.paths:
        return False

    matched_paths, unmatched_paths = _verify_path_candidates(lines, summary)
    matched_counts, unmatched_counts = _verify_count_citations(lines, summary)

    if unmatched_paths or unmatched_counts:
        return False

    return bool(matched_paths or matched_counts)


__all__ = ["verify_reject_citations"]
