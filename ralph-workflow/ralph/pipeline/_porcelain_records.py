
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ralph.git.hardening import (
    PorcelainEntry,
    parse_porcelain_z,
    unmerged_paths_z,
)

if TYPE_CHECKING:
    from ralph.pipeline.integration_observation import (
        DirtEvidence,
        DirtShape,
        WorktreeState,
    )


_UNTRACKED_CODE: Final[str] = "??"

_IGNORED_CODE: Final[str] = "!!"

_INDEX_STATUS_CODES: Final[frozenset[str]] = frozenset({"M", "A", "D", "R", "C", "T"})

_WORKTREE_STATUS_CODES: Final[frozenset[str]] = frozenset({"M", "D", "T", "A"})


def _porcelain_to_state(text: str) -> WorktreeState:
    from ralph.pipeline.integration_observation import WorktreeState

    blob = text.replace("\n", "\0")
    v2_entries = _parse_porcelain_v2_z(blob)
    if v2_entries is not None:
        return _porcelain_v2_to_state(v2_entries)
    if unmerged_paths_z(blob):
        return WorktreeState.UNMERGED
    return _state_from_parsed(parse_porcelain_z(blob))


def _state_from_parsed(entries: list[PorcelainEntry]) -> WorktreeState:
    from ralph.pipeline.integration_observation import WorktreeState

    has_staged = False
    has_tracked = False
    has_untracked = False
    for entry in entries:
        code = entry.xy
        if code == _UNTRACKED_CODE:
            has_untracked = True
            continue
        if code == _IGNORED_CODE:
            continue
        if code[0] in _INDEX_STATUS_CODES:
            has_staged = True
        if code[1] in _WORKTREE_STATUS_CODES:
            has_tracked = True
    if has_tracked:
        return WorktreeState.TRACKED_CHANGES
    if has_staged:
        return WorktreeState.STAGED
    if has_untracked:
        return WorktreeState.UNTRACKED
    return WorktreeState.CLEAN


_CQUOTE_ESCAPES: Final[dict[str, str]] = {
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    '"': '"',
}

_CQUOTE_OCTAL_DIGITS: Final[frozenset[str]] = frozenset("01234567")

_CQUOTE_MIN_LENGTH: Final[int] = 2

_CQUOTE_OCTAL_ESCAPE_WIDTH: Final[int] = 4

_CQUOTE_OCTAL_DIGIT_COUNT: Final[int] = 3

_CQUOTE_ASCII_MAX: Final[int] = 0x7F


def _decode_quoted_porcelain_path(path: str) -> str:
    if len(path) < _CQUOTE_MIN_LENGTH or path[0] != '"' or path[-1] != '"':
        return path
    inner = path[1:-1]
    raw = bytearray()
    i = 0
    while i < len(inner):
        char = inner[i]
        if char != "\\":
            if ord(char) > _CQUOTE_ASCII_MAX:
                raw.extend(char.encode("utf-8"))
            else:
                raw.append(ord(char))
            i += 1
            continue
        if i + 1 >= len(inner):
            return path
        nxt = inner[i + 1]
        if nxt in _CQUOTE_ESCAPES:
            raw.extend(_CQUOTE_ESCAPES[nxt].encode("utf-8"))
            i += 2
            continue
        octal = inner[i + 1 : i + _CQUOTE_OCTAL_ESCAPE_WIDTH]
        if len(octal) == _CQUOTE_OCTAL_DIGIT_COUNT and all(
            digit in _CQUOTE_OCTAL_DIGITS for digit in octal
        ):
            raw.append(int(octal, 8))
            i += _CQUOTE_OCTAL_ESCAPE_WIDTH
            continue
        return path
    return bytes(raw).decode("utf-8", errors="surrogateescape")


def _decode_porcelain_entries(entries: list[PorcelainEntry]) -> list[PorcelainEntry]:
    return [
        PorcelainEntry(
            xy=entry.xy,
            path=_decode_quoted_porcelain_path(entry.path),
            rename_source=(
                _decode_quoted_porcelain_path(entry.rename_source)
                if entry.rename_source is not None
                else None
            ),
        )
        for entry in entries
    ]




_PORCELAIN_V2_KINDS: Final[frozenset[str]] = frozenset({"1", "2", "u", "?", "!"})

_PORCELAIN_V2_FIELD_COUNT: Final[dict[str, int]] = {"1": 8, "2": 9, "u": 9}


@dataclass(frozen=True)
class _PorcelainV2Entry:

    xy: str
    path: str
    head_mode: str | None
    index_mode: str | None
    worktree_mode: str | None
    head_hash: str | None
    index_hash: str | None
    rename_source: str | None = None
    unmerged: bool = False


def _parse_porcelain_v2_z(blob: str) -> list[_PorcelainV2Entry] | None:
    if not blob:
        return None
    records = blob.split("\0")
    if records and records[-1] == "":
        records.pop()
    entries: list[_PorcelainV2Entry] = []
    saw_v2 = False
    i = 0
    while i < len(records):
        record = records[i]
        i += 1
        if not record:
            continue
        kind, separator, rest = record.partition(" ")
        if kind not in _PORCELAIN_V2_KINDS or not separator:
            return None
        saw_v2 = True
        if kind == "?":
            entries.append(_v2_scalar_entry(_UNTRACKED_CODE, rest))
            continue
        if kind == "!":
            entries.append(_v2_scalar_entry(_IGNORED_CODE, rest))
            continue
        field_count = _PORCELAIN_V2_FIELD_COUNT[kind]
        fields = rest.split(" ", field_count - 1)
        if len(fields) != field_count:
            continue
        if kind == "1":
            xy, _sub, m_h, m_i, m_w, h_h, h_i, path = fields
            entries.append(
                _PorcelainV2Entry(
                    xy=xy,
                    path=path,
                    head_mode=m_h,
                    index_mode=m_i,
                    worktree_mode=m_w,
                    head_hash=h_h,
                    index_hash=h_i,
                )
            )
        elif kind == "2":
            xy, _sub, m_h, m_i, m_w, h_h, h_i, _score, path = fields
            if i >= len(records):
                continue
            source = records[i]
            i += 1
            entries.append(
                _PorcelainV2Entry(
                    xy=xy,
                    path=path,
                    head_mode=m_h,
                    index_mode=m_i,
                    worktree_mode=m_w,
                    head_hash=h_h,
                    index_hash=h_i,
                    rename_source=source,
                )
            )
        else:
            entries.append(
                _PorcelainV2Entry(
                    xy=fields[0],
                    path=fields[8],
                    head_mode=None,
                    index_mode=None,
                    worktree_mode=None,
                    head_hash=None,
                    index_hash=None,
                    unmerged=True,
                )
            )
    if not saw_v2:
        return None
    return entries


def _v2_scalar_entry(xy: str, path: str) -> _PorcelainV2Entry:
    return _PorcelainV2Entry(
        xy=xy,
        path=path,
        head_mode=None,
        index_mode=None,
        worktree_mode=None,
        head_hash=None,
        index_hash=None,
    )


def _v2_entries_as_v1(entries: list[_PorcelainV2Entry]) -> list[PorcelainEntry]:
    return [
        PorcelainEntry(xy=entry.xy, path=entry.path, rename_source=entry.rename_source)
        for entry in entries
    ]


def _v2_entry_is_mode_only(entry: _PorcelainV2Entry) -> bool:
    return (
        entry.head_hash is not None
        and entry.index_hash is not None
        and entry.head_hash == entry.index_hash
        and entry.head_mode is not None
        and entry.index_mode is not None
        and entry.worktree_mode is not None
        and (entry.head_mode != entry.index_mode or entry.index_mode != entry.worktree_mode)
    )


def _porcelain_v2_to_state(entries: list[_PorcelainV2Entry]) -> WorktreeState:
    from ralph.pipeline.integration_observation import WorktreeState

    if any(entry.unmerged for entry in entries):
        return WorktreeState.UNMERGED
    return _state_from_parsed(_v2_entries_as_v1(entries))


def _shape_for_v2_entries(
    entries: list[_PorcelainV2Entry],
    evidence: DirtEvidence | None,
) -> DirtShape:
    from ralph.pipeline.integration_observation import DirtEvidence, DirtShape

    if any(entry.unmerged for entry in entries):
        return DirtShape.UNMERGED
    derived_mode_only = {entry.path for entry in entries if _v2_entry_is_mode_only(entry)}
    merged = evidence
    if derived_mode_only:
        merged = DirtEvidence(
            mode_only_paths=frozenset(
                derived_mode_only
                | (evidence.mode_only_paths if evidence is not None else frozenset())
            ),
            empty_diff_paths=evidence.empty_diff_paths if evidence is not None else frozenset(),
            incoming_overwrite_paths=(
                evidence.incoming_overwrite_paths if evidence is not None else frozenset()
            ),
        )
    return _shape_for_tracked(_v2_entries_as_v1(entries), merged)


def _porcelain_to_shape(text: str, evidence: DirtEvidence | None) -> DirtShape:
    from ralph.pipeline.integration_observation import DirtShape

    blob = text.replace("\n", "\0")
    v2_entries = _parse_porcelain_v2_z(blob)
    if v2_entries is not None:
        return _shape_for_v2_entries(v2_entries, evidence)
    if unmerged_paths_z(blob):
        return DirtShape.UNMERGED
    return _shape_for_tracked(_decode_porcelain_entries(parse_porcelain_z(blob)), evidence)


def _shape_for_tracked(
    entries: list[PorcelainEntry],
    evidence: DirtEvidence | None,
) -> DirtShape:
    from ralph.pipeline.integration_observation import DirtShape

    tracked = [entry for entry in entries if entry.xy not in (_UNTRACKED_CODE, _IGNORED_CODE)]
    untracked_paths = [entry.path for entry in entries if entry.xy == _UNTRACKED_CODE]
    shape = DirtShape.NONE
    if not tracked:
        blocked = evidence is not None and any(
            path in evidence.incoming_overwrite_paths for path in untracked_paths
        )
        shape = DirtShape.UNTRACKED_BLOCKING if blocked else DirtShape.UNTRACKED_ONLY
        if not untracked_paths:
            shape = DirtShape.NONE
        return shape
    paths = {entry.path for entry in tracked}
    paths |= {entry.rename_source for entry in tracked if entry.rename_source is not None}
    if evidence is not None:
        if paths and paths <= evidence.mode_only_paths:
            return DirtShape.MODE_ONLY
        if paths <= evidence.empty_diff_paths:
            return DirtShape.LINE_ENDING_ONLY
    if any(entry.xy[1] == "A" for entry in tracked):
        shape = DirtShape.INTENT_TO_ADD
    elif any("T" in entry.xy for entry in tracked):
        shape = DirtShape.TYPE_CHANGE
    elif any("D" in entry.xy for entry in tracked):
        shape = DirtShape.DELETED
    elif any(entry.xy[0] in ("R", "C") for entry in tracked):
        shape = DirtShape.RENAMED_OR_COPIED
    else:
        has_staged = any(entry.xy[0] in _INDEX_STATUS_CODES for entry in tracked)
        has_unstaged = any(entry.xy[1] in _WORKTREE_STATUS_CODES for entry in tracked)
        if has_staged and has_unstaged:
            shape = DirtShape.STAGED_AND_UNSTAGED
        elif has_staged:
            shape = DirtShape.STAGED_ONLY
        else:
            shape = DirtShape.UNSTAGED_MODIFICATION
    return shape
