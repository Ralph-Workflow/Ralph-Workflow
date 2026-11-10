"""Verification of rejection citations against sealed proposal diffs.

Provides functions to verify that rejection reason citations in a commit_review
artifact legitimately reference actual modified paths or findings from the
proposal diff, preventing unjustified rejections or hallucinated citations.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

from ralph.phases.integrity import DEFAULT_PROMPT_PATH

# Punctuation to strip from token boundaries when extracting path candidates.
_STRIP_PUNCT: str = ".,;:()[]{}'\"`<>!?"

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

# Reference documents commonly cited in policy/reasons that should not be treated
# as unverified diff citations when they are not part of the diff.
_REFERENCE_DOCS: frozenset[str] = frozenset(
    {
        "AGENTS.md",
        DEFAULT_PROMPT_PATH,
        "README.md",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        "LICENSE",
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

_MARKDOWN_LINK_RE: re.Pattern[str] = re.compile(r"\[([^]]*)\]\(([^)]+)\)")
_INLINE_CODE_RE: re.Pattern[str] = re.compile(r"`([^`]+)`")
_QUOTED_STR_RE: re.Pattern[str] = re.compile(r"""(?:'([^']+)'|"([^"]+)")""")
_FILE_COUNT_RE: re.Pattern[str] = re.compile(
    r"\b(\d+)\s+(?:deleted\s+|modified\s+|added\s+|changed\s+|untracked\s+)?files?\b",
    re.IGNORECASE,
)
_EXPLICIT_COUNT_RE: re.Pattern[str] = re.compile(r"\bcount:\s*(\d+)\b", re.IGNORECASE)
_DIGITS_AND_DOTS_RE: re.Pattern[str] = re.compile(r"^\d+(\.\d+)+$")
_ITEM_ID_PREFIX_RE: re.Pattern[str] = re.compile(r"^\[[A-Za-z0-9_-]+\]\s*")


@runtime_checkable
class _DiffEntryLike(Protocol):
    """Protocol for objects representing a file change in a diff."""

    path: str


def _extract_diff_info(
    diff_entries: Sequence[object],
) -> tuple[set[str], set[str], set[str], set[str]]:
    """Extract sets of paths, deleted paths, binary paths, and basenames from diff entries."""
    diff_paths: set[str] = set()
    deleted_paths: set[str] = set()
    binary_paths: set[str] = set()
    basenames: set[str] = set()

    for entry in diff_entries:
        raw_path: str | None = None
        status: str = ""
        is_binary: bool = False

        if isinstance(entry, str):
            raw_path = entry
        elif isinstance(entry, dict):
            entry_dict: dict[object, object] = entry
            path_val: object = entry_dict.get("path")
            if isinstance(path_val, str):
                raw_path = path_val
            status_val: object = entry_dict.get("status", "")
            if isinstance(status_val, str):
                status = status_val
            bin_val: object = entry_dict.get("is_binary", False)
            if isinstance(bin_val, bool):
                is_binary = bin_val
        elif isinstance(entry, _DiffEntryLike):
            raw_path = entry.path
            status_obj: object = getattr(entry, "status", "")
            if isinstance(status_obj, str):
                status = status_obj
            bin_obj: object = getattr(entry, "is_binary", False)
            if isinstance(bin_obj, bool):
                is_binary = bin_obj

        if not raw_path:
            continue

        normalized = PurePosixPath(raw_path.strip().lstrip("./")).as_posix()
        diff_paths.add(normalized)
        basenames.add(PurePosixPath(normalized).name)

        if status == "D":
            deleted_paths.add(normalized)
        if is_binary:
            binary_paths.add(normalized)

    return diff_paths, deleted_paths, binary_paths, basenames


def _is_path_candidate(token: str, diff_paths: set[str], basenames: set[str]) -> bool:
    """Determine whether a token looks like a file path candidate."""
    if (
        not token
        or token in _EXCLUDED_TOKENS
        or token.startswith(("http://", "https://", "mailto:", "file://", "ftp://"))
        or bool(_DIGITS_AND_DOTS_RE.match(token))
    ):
        return False
    if token in diff_paths or token in basenames:
        return True
    if "/" in token and not any(c in token for c in " \t\r\n"):
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


def _matches_diff_path(
    candidate: str,
    diff_paths: set[str],
    basenames: set[str],
) -> bool:
    """Check if candidate path matches any entry in the diff."""
    norm = PurePosixPath(candidate.lstrip("./")).as_posix()
    if norm in diff_paths or norm in basenames:
        return True
    for dp in diff_paths:
        if dp.endswith(f"/{norm}") or norm.endswith(f"/{dp}"):
            return True
        if norm.endswith("/") and dp.startswith(norm):
            return True
    return False


def _extract_candidates_from_text(
    text: str,
    diff_paths: set[str],
    basenames: set[str],
) -> list[str]:
    """Extract candidate path strings from a reason line."""
    candidates: list[str] = []

    # 1. Markdown links: [text](target)
    for match in _MARKDOWN_LINK_RE.finditer(text):
        target_val: object = match.group(2)
        if isinstance(target_val, str):
            target = target_val.strip()
            if target.startswith("file://"):
                target = target[len("file://") :]
            target = target.split("#")[0].split("?")[0].strip()
            if target and not target.startswith(("http://", "https://", "mailto:")):
                candidates.append(target)

    # 2. Backtick code spans: `code`
    for match in _INLINE_CODE_RE.finditer(text):
        code_val: object = match.group(1)
        if isinstance(code_val, str):
            code_token = code_val.strip()
            if _is_path_candidate(code_token, diff_paths, basenames):
                candidates.append(code_token)

    # 3. Quoted strings: 'path' or "path"
    for match in _QUOTED_STR_RE.finditer(text):
        g1_val: object = match.group(1)
        g2_val: object = match.group(2)
        quoted_str = (
            g1_val if isinstance(g1_val, str) else g2_val if isinstance(g2_val, str) else ""
        )
        quoted = quoted_str.strip()
        if _is_path_candidate(quoted, diff_paths, basenames):
            candidates.append(quoted)

    # 4. Words/tokens in line
    clean_text = _ITEM_ID_PREFIX_RE.sub("", text)
    for word in clean_text.split():
        clean_word = word.strip(_STRIP_PUNCT)
        if clean_word.startswith("file://"):
            clean_word = clean_word[len("file://") :]
        clean_word = clean_word.strip(_STRIP_PUNCT)
        if _is_path_candidate(clean_word, diff_paths, basenames):
            candidates.append(clean_word)

    return candidates


def _verify_path_candidates(
    lines: list[str],
    diff_paths: set[str],
    basenames: set[str],
) -> tuple[set[str], set[str]]:
    """Partition extracted path candidates into matched and unmatched sets."""
    matched: set[str] = set()
    unmatched: set[str] = set()

    for line in lines:
        candidates = _extract_candidates_from_text(line, diff_paths, basenames)
        for candidate in candidates:
            if candidate in _REFERENCE_DOCS and candidate not in diff_paths:
                continue

            if _matches_diff_path(candidate, diff_paths, basenames):
                matched.add(candidate)
            else:
                unmatched.add(candidate)

    return matched, unmatched


def _verify_count_citations(
    lines: list[str],
    diff_paths: set[str],
    deleted_paths: set[str],
    binary_paths: set[str],
    matched_paths: set[str],
) -> tuple[set[int], set[int]]:
    """Partition extracted count citations into matched and unmatched sets."""
    matched: set[int] = set()
    unmatched: set[int] = set()
    general_valid_counts = (len(diff_paths), len(deleted_paths), len(matched_paths))

    for line in lines:
        for match in _FILE_COUNT_RE.finditer(line):
            c1_val: object = match.group(1)
            p0_val: object = match.group(0)
            if not isinstance(c1_val, str) or not isinstance(p0_val, str):
                continue
            count_val = int(c1_val)
            matched_phrase = p0_val.lower()
            if "deleted" in matched_phrase:
                expected = count_val == len(deleted_paths)
            elif "binary" in matched_phrase:
                expected = count_val == len(binary_paths)
            else:
                expected = count_val in general_valid_counts

            if expected:
                matched.add(count_val)
            else:
                unmatched.add(count_val)

        for match in _EXPLICIT_COUNT_RE.finditer(line):
            c1_val_exp: object = match.group(1)
            if not isinstance(c1_val_exp, str):
                continue
            count_val = int(c1_val_exp)
            if count_val in general_valid_counts:
                matched.add(count_val)
            else:
                unmatched.add(count_val)

    return matched, unmatched


def verify_reject_citations(
    reason_items: list[str] | str | None,
    diff_entries: Sequence[object],
) -> bool:
    """Verify that citations in a reject reason reference actual paths or findings from the diff.

    Checks that all cited paths appear in the proposal diff, that any cited counts
    match the diff statistics, and that at least one concrete citation (path or count)
    is present.

    Args:
        reason_items: List of reason bullet strings, a single reason string, or None.
        diff_entries: Sequence of DiffEntry objects, dicts with 'path', or path strings.

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

    diff_paths, deleted_paths, binary_paths, basenames = _extract_diff_info(diff_entries)
    if not diff_paths:
        return False

    matched_paths, unmatched_paths = _verify_path_candidates(lines, diff_paths, basenames)
    matched_counts, unmatched_counts = _verify_count_citations(
        lines, diff_paths, deleted_paths, binary_paths, matched_paths
    )

    if unmatched_paths or unmatched_counts:
        return False

    return bool(matched_paths or matched_counts)


__all__ = ["verify_reject_citations"]
