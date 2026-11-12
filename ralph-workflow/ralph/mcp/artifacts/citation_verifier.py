from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_STRIP_PUNCT: str = ".,;:()[]{}'\"`<>!?"

_WRAPPER_CHARS: str = "\"'`*~"

_MIN_WRAPPED_TEXT_LENGTH: int = 2

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

_TOTAL_COUNT_KIND: str = "total"

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

_COUNT_NOUN_WINDOW: int = 3

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

_SPELLED_NUMBERS: dict[str, int] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
    "thousand": 1000,
}

_VAGUE_QUANTIFIERS: frozenset[str] = frozenset(
    {
        "countless",
        "few",
        "many",
        "multiple",
        "numerous",
        "several",
        "various",
    }
)

_COUNT_BACKWARD_FILLER_WORDS: frozenset[str] = frozenset(
    {
        "are",
        "be",
        "been",
        "by",
        "count",
        "got",
        "had",
        "has",
        "have",
        "in",
        "is",
        "number",
        "of",
        "total",
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

_TOKEN_EDGE_RE: re.Pattern[str] = re.compile(r"^[^0-9A-Za-z_-]+|[^0-9A-Za-z_-]+$")

_PLAIN_WORD_RE: re.Pattern[str] = re.compile(r"^[A-Za-z_-]+$")

_NUMBER_TOKEN_RE: re.Pattern[str] = re.compile(r"^(?:\d+|\d{1,3}(?:,\d{3})+)$")


@dataclass(frozen=True)
class _DiffSummary:
    paths: frozenset[str]
    basenames: frozenset[str]
    deleted: frozenset[str]
    added: frozenset[str]
    modified: frozenset[str]
    binary: frozenset[str]
    tracked: frozenset[str] = frozenset()

    def count_for(self, kind: str) -> int:
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
    if isinstance(entry, dict):
        raw: object = entry.get("base_tree_paths")
    else:
        raw = getattr(entry, "base_tree_paths", None)
    if not isinstance(raw, (frozenset, set, tuple, list)):
        return frozenset()
    return frozenset(str(item) for item in raw)


def _status_kind(status: str) -> str:
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
    if not token:
        return False
    if token in summary.paths or token in summary.basenames:
        return True
    if token in summary.tracked:
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
    return candidate in summary.paths


def _extract_candidates_from_text(text: str, summary: _DiffSummary) -> list[str]:
    candidates: list[str] = []

    for match in _MARKDOWN_LINK_RE.finditer(text):
        target = str(match.group(2)).strip()
        if target.startswith("file://"):
            target = target[len("file://") :]
        target = target.split("#")[0].split("?")[0].strip()
        candidate = _candidate_from_token(target, summary)
        if candidate is not None:
            candidates.append(candidate)

    for match in _INLINE_CODE_RE.finditer(text):
        candidate = _candidate_from_token(match.group(1), summary)
        if candidate is not None:
            candidates.append(candidate)

    for match in _QUOTED_STR_RE.finditer(text):
        g1 = match.group(1)
        g2 = match.group(2)
        quoted = g1 if isinstance(g1, str) else g2 if isinstance(g2, str) else ""
        candidate = _candidate_from_token(quoted, summary)
        if candidate is not None:
            candidates.append(candidate)

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
    return [_TOKEN_EDGE_RE.sub("", token).lower() for token in text.split()]


def _count_noun_index(index: int, tokens: list[str]) -> int | None:
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
    position = noun_index + 1
    while position < len(tokens) and tokens[position] in _COUNT_FILLER_WORDS:
        position += 1
    if position < len(tokens):
        word = tokens[position]
        if word in _COUNT_KIND_BY_WORD:
            return word
    return None


def _count_category(index: int, tokens: list[str]) -> tuple[bool, str | None]:
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


def _parse_number_or_quantifier(token: str) -> tuple[int | None, bool]:
    if _NUMBER_TOKEN_RE.match(token):
        return (int(token.replace(",", "")), True)
    if token in _SPELLED_NUMBERS:
        return (_SPELLED_NUMBERS[token], True)
    if "-" in token:
        parts = token.split("-")
        if all(part in _SPELLED_NUMBERS for part in parts):
            return (sum(_SPELLED_NUMBERS[part] for part in parts), True)
    if token in _VAGUE_QUANTIFIERS:
        return (-1, False)
    return (None, False)


_MAX_QUALIFIER_STEPS: int = 2


def _reversed_noun_qualifiers(pos: int, tokens: list[str]) -> list[str] | None:
    q_pos = pos - 1
    while q_pos >= 0 and tokens[q_pos] in _COUNT_BACKWARD_FILLER_WORDS:
        q_pos -= 1

    typed: list[str] = []
    steps = 0
    while q_pos >= 0 and steps < _MAX_QUALIFIER_STEPS:
        q_word = tokens[q_pos]
        if not _PLAIN_WORD_RE.match(q_word) or q_word in _COUNT_BACKWARD_FILLER_WORDS:
            break
        category = _COUNT_KIND_BY_WORD.get(q_word)
        if category is None:
            return None
        typed.append(category)
        q_pos -= 1
        steps += 1
    return typed


def _reversed_category_word(pos: int, tokens: list[str]) -> tuple[bool, str | None]:
    category = _COUNT_KIND_BY_WORD[tokens[pos]]
    q_pos = pos - 1
    while q_pos >= 0 and tokens[q_pos] in _COUNT_BACKWARD_FILLER_WORDS:
        q_pos -= 1
    if (
        q_pos >= 0
        and tokens[q_pos] in _COUNT_NOUN_CATEGORY
        and _COUNT_NOUN_CATEGORY[tokens[q_pos]] is None
    ):
        return (True, None)
    return (True, category)


def _reversed_count_category(index: int, tokens: list[str]) -> tuple[bool, str | None]:
    pos = index - 1
    while pos >= 0 and tokens[pos] in _COUNT_BACKWARD_FILLER_WORDS:
        pos -= 1

    if pos < 0 or not _PLAIN_WORD_RE.match(tokens[pos]):
        return (False, None)

    word = tokens[pos]
    if word in _COUNT_NOUN_CATEGORY:
        quantity = _COUNT_NOUN_CATEGORY[word]
        if quantity is None:
            return (True, None)

        typed = _reversed_noun_qualifiers(pos, tokens)
        if typed is None or len(set(typed)) > 1:
            return (True, None)
        return (True, typed[0] if typed else quantity)

    if word in _COUNT_KIND_BY_WORD:
        return _reversed_category_word(pos, tokens)

    return (False, None)


def _count_claims(tokens: list[str]) -> list[tuple[int, str | None]]:
    claims: list[tuple[int, str | None]] = []
    for index, token in enumerate(tokens):
        num_val, is_concrete = _parse_number_or_quantifier(token)
        if num_val is None:
            continue
        if (
            index > 0
            and tokens[index - 1] in _COUNT_REFERENCE_WORDS
            and not (index > 1 and tokens[index - 2] in _COUNT_KIND_BY_WORD)
        ):
            continue
        claimed, category = _count_category(index, tokens)
        if not claimed:
            claimed, category = _reversed_count_category(index, tokens)
        if not claimed:
            continue
        if not is_concrete:
            claims.append((num_val, None))
        else:
            claims.append((num_val, category))
    return claims


def _verify_count_citations(
    lines: list[str],
    summary: _DiffSummary,
) -> tuple[set[int], set[int]]:
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
