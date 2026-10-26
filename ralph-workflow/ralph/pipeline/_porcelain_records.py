"""Porcelain record parsing for the single worktree classifier.

Every raw ``git status --porcelain`` form the integration boundary
accepts — line-oriented v1, NUL-delimited v1 (``-z``), and porcelain v2
(``--porcelain=v2 -z`` with its mode columns) — is parsed here and folded
onto the shared code tables the one classifier consumes. The C-quote /
``core.quotePath`` decoder lives here too: line-oriented porcelain quotes
paths whose bytes are non-ASCII or hold a quote/backslash, while evidence
paths are raw filesystem spellings, so a quoted record must be decoded
before it can match.

The enum vocabulary (:class:`WorktreeState`, :class:`DirtShape`,
:class:`DirtEvidence`) stays in :mod:`ralph.pipeline.integration_observation`;
the enum imports below are deferred to call time so this private module
and the public observation module never import each other at module load.
"""

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


#: Porcelain v1 status code for an untracked path.
_UNTRACKED_CODE: Final[str] = "??"

#: Porcelain v1 status code for an ignored path. ``git status`` only emits
#: it under ``--ignored``; it is skipped so an ignored build artefact can
#: never be mistaken for work.
_IGNORED_CODE: Final[str] = "!!"

#: First (``X``) status codes that mean "staged into the index".
_INDEX_STATUS_CODES: Final[frozenset[str]] = frozenset({"M", "A", "D", "R", "C", "T"})

#: Second (``Y``) status codes that mean "modified in the worktree". ``A``
#: is the intent-to-add code: ``git add -N`` renders as `` A <path>`` and
#: is real uncommitted work, not scratch.
_WORKTREE_STATUS_CODES: Final[frozenset[str]] = frozenset({"M", "D", "T", "A"})


def _porcelain_to_state(text: str) -> WorktreeState:
    """Map one porcelain blob onto a :class:`WorktreeState`.

    Accepts the line-oriented and both NUL-delimited forms: the canonical
    v1 parser in :mod:`ralph.git.hardening` splits on ``\\0`` and every
    line-oriented record has the same ``XY<space>path`` shape, so
    newline-to-NUL normalisation reuses the one parser that already owns
    the unmerged-code table. Porcelain v2 blobs (detected per record by
    the v2 kind letter) carry their own mode columns and are folded into
    the same state via :func:`_state_from_parsed`.
    """
    from ralph.pipeline.integration_observation import WorktreeState

    blob = text.replace("\n", "\0")
    v2_entries = _parse_porcelain_v2_z(blob)
    if v2_entries is not None:
        return _porcelain_v2_to_state(v2_entries)
    if unmerged_paths_z(blob):
        return WorktreeState.UNMERGED
    return _state_from_parsed(parse_porcelain_z(blob))


def _state_from_parsed(entries: list[PorcelainEntry]) -> WorktreeState:
    """Fold parsed v1-shaped entries into a :class:`WorktreeState`.

    Shared by the v1 parser and the porcelain v2 path (whose records are
    widened to the v1 shape by :func:`_v2_entries_as_v1`), so the two
    porcelain forms can never disagree about staged/tracked/untracked.
    """
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


#: Escape letters git's C-style path quoting (``core.quotePath``) emits
#: inside a quoted porcelain path, mapped to the character they stand for.
#: Mirrors git's ``quote.c`` table; any other character after ``\\`` is
#: either a three-digit octal byte or makes the quoting malformed.
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

#: Octal digit vocabulary of a C-quoted byte escape (``\\ooo``).
_CQUOTE_OCTAL_DIGITS: Final[frozenset[str]] = frozenset("01234567")

#: Smallest well-formed C-quoted path: both delimiters plus one payload char.
_CQUOTE_MIN_LENGTH: Final[int] = 2

#: Width of one C-quoted octal byte escape: backslash plus three digits.
_CQUOTE_OCTAL_ESCAPE_WIDTH: Final[int] = 4

#: Digit count of the octal payload inside one C-quoted byte escape.
_CQUOTE_OCTAL_DIGIT_COUNT: Final[int] = 3

#: Highest byte value git's C-style quoting treats as printable ASCII; at
#: or below it a payload character is copied verbatim, above it git would
#: have octal-escaped it.
_CQUOTE_ASCII_MAX: Final[int] = 0x7F


def _decode_quoted_porcelain_path(path: str) -> str:
    """Decode one ``core.quotePath`` C-quoted porcelain path to its raw path.

    Line-oriented porcelain (a form this classifier accepts and at least
    one production probe feeds) quotes paths whose bytes are non-ASCII or
    hold a quote/backslash: `` M "caf\\303\\251.py"``. ``-z`` porcelain
    never quotes. Evidence paths (``DirtEvidence``) are raw filesystem
    spellings, so a quoted record must be decoded before it can match —
    otherwise a chmod on a non-ASCII path would silently miss its
    mode-only refinement.

    The quoted payload is a byte sequence: octal escapes are single bytes,
    so the decoded bytes are re-interpreted as UTF-8 with
    ``surrogateescape`` — exactly how Python decodes the raw ``-z`` form
    of the same path. Anything that is not a well-formed C-quote (missing
    delimiters, a dangling backslash, an unknown escape) returns the path
    unchanged: a malformed record still classifies by its XY code.
    """
    if len(path) < _CQUOTE_MIN_LENGTH or path[0] != '"' or path[-1] != '"':
        return path
    inner = path[1:-1]
    raw = bytearray()
    i = 0
    while i < len(inner):
        char = inner[i]
        if char != "\\":
            if ord(char) > _CQUOTE_ASCII_MAX:
                # A literal non-ASCII character inside the quotes is not
                # something git emits (it would have octal-escaped it),
                # but encoding it keeps the round-trip total.
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
    """Return copies of ``entries`` with C-quoted paths decoded.

    Only the path spelling changes; the XY code that drives shape
    classification is untouched. Already-raw ``-z`` paths decode to
    themselves, so both porcelain forms share one evidence-matching path.
    """
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


# ---------------------------------------------------------------------------
# Porcelain v2: the raw form that carries mode bits.
#
# Porcelain v1 never quotes under ``-z``, but it also never says WHY a path
# is dirty — a chmod and a content edit both render as `` M <path>``. Only
# porcelain v2 (``git status --porcelain=v2 -z``) adds the mode columns
# (``mH mI mW``) and the HEAD/index content hashes, which is the one raw
# porcelain form that can prove a mode-only change without any
# :class:`DirtEvidence`. The v2 records are parsed here and widened back
# to the v1 entry shape so the shared classification tables stay single.
# ---------------------------------------------------------------------------


#: Record-kind letters of ``git status --porcelain=v2``. Ordinary changed
#: entries are ``1``, renames/copies ``2``, unmerged ``u``; untracked and
#: ignored paths are single-character ``?`` / ``!`` (widened to the v1
#: ``??`` / ``!!`` codes so the shared tables classify them unchanged).
_PORCELAIN_V2_KINDS: Final[frozenset[str]] = frozenset({"1", "2", "u", "?", "!"})

#: Field count (including the trailing path) of each multi-field v2
#: record kind: ``1`` is ordinary (XY sub mH mI mW hH hI path), ``2`` is
#: rename/copy (same plus the similarity score), ``u`` is unmerged
#: (XY sub m1 m2 m3 h1 h2 h3 path).
_PORCELAIN_V2_FIELD_COUNT: Final[dict[str, int]] = {"1": 8, "2": 9, "u": 9}


@dataclass(frozen=True)
class _PorcelainV2Entry:
    """One porcelain v2 record with the mode columns the shape needs.

    ``xy`` keeps the v1-shaped status code so the shared classification
    tables apply unchanged. Modes and hashes are ``None`` on records that
    do not carry them (renames, unmerged, untracked, ignored); a ``None``
    hash never satisfies the mode-only equality check.
    """

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
    """Parse a NUL-delimited porcelain v2 blob, or return ``None`` for v1.

    Detection is per record: any record whose kind letter is a v2 kind
    makes the whole blob v2 (git never mixes the two forms), and anything
    else hands the blob back to the v1 parser untouched. Malformed v2
    records — wrong field count, a rename missing its source record —
    are skipped, mirroring the v1 parser's tolerance: a fixture that is
    not a well-formed record cannot be acted on anyway.

    Under ``-z`` v2 never C-quotes, so paths arrive verbatim (they may
    hold spaces, which the bounded field split preserves) and need no
    decode pass.
    """
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
                continue  # rename source record missing: malformed
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
        else:  # "u": ``u XY sub m1 m2 m3 h1 h2 h3 path``
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
    """Build the mode-less v2 record for an untracked or ignored path."""
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
    """Widen v2 records to the v1 entry shape the shared tables classify on."""
    return [
        PorcelainEntry(xy=entry.xy, path=entry.path, rename_source=entry.rename_source)
        for entry in entries
    ]


def _v2_entry_is_mode_only(entry: _PorcelainV2Entry) -> bool:
    """True when a v2 ``1`` record proves a pure mode change for its path.

    A mode-only change leaves the content blob untouched: the HEAD and
    index content hashes compare equal while at least one mode column
    differs (staged side ``mH`` vs ``mI``, worktree side ``mI`` vs
    ``mW``). Porcelain v2 does not carry a worktree content hash, so an
    UNSTAGED change that edits content AND flips a mode bit is
    indistinguishable from mode-only here; that residual case stays what
    the evidence path (:class:`DirtEvidence`) is for.
    """
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
    """Fold v2 entries into a worktree state via the shared code tables."""
    from ralph.pipeline.integration_observation import WorktreeState

    if any(entry.unmerged for entry in entries):
        return WorktreeState.UNMERGED
    return _state_from_parsed(_v2_entries_as_v1(entries))


def _shape_for_v2_entries(
    entries: list[_PorcelainV2Entry],
    evidence: DirtEvidence | None,
) -> DirtShape:
    """Map porcelain v2 entries onto a shape, deriving mode-only from modes.

    Every tracked v2 record whose mode columns prove a pure mode change
    is folded into the mode-only path set, then the shared
    :func:`_shape_for_tracked` decides — so raw-porcelain and
    :class:`DirtEvidence` refinement go through one subset check and can
    never drift apart.
    """
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
    """Map one porcelain blob plus optional evidence onto a :class:`DirtShape`.

    Accepts the line-oriented form and both NUL-delimited porcelain
    versions (see :func:`_porcelain_to_state`). The precedence is
    deterministic and documented, most specific first:

    1. Any unmerged code is a real conflict: ``UNMERGED`` (for v1 the
       canonical table in :func:`ralph.git.hardening.unmerged_paths_z`
       decides, so the unmerged vocabulary exists exactly once; v2 ``u``
       records are flagged by the v2 parser).
    2. Mode-only dirt: when every dirty tracked path is provably a pure
       mode change — either from raw porcelain v2 mode columns
       (:func:`_v2_entry_is_mode_only`) or from ``mode_only_paths``
       evidence (``git diff --summary``) — the shape is ``MODE_ONLY``.
    3. Empty-diff evidence: when every dirty tracked path has an empty
       content diff (autocrlf renormalisation, a racy index entry), the
       shape is ``LINE_ENDING_ONLY`` — "clean after refresh", not a
       block.
    4. Intent-to-add (`` A``) is the most specific tracked shape: it
       names work git will lose on checkout unless it is staged.
    5. A type change (``T``), a deletion (``D``), a rename/copy (``R`` /
       ``C``), then the staged/unstaged split.
    6. Untracked-only trees are ``UNTRACKED_BLOCKING`` when evidence
       proves an incoming target commit would overwrite one of the paths
       (git would refuse mid-rebase, per-file), else ``UNTRACKED_ONLY``.

    Before any path is compared against evidence, C-quoted path spellings
    (``core.quotePath`` line-oriented records) are decoded to their raw
    form by :func:`_decode_quoted_porcelain_path`, so refinement matching
    never depends on how git chose to spell the path. Porcelain v2 never
    quotes, so its paths need no decode pass.
    """
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
    """Map parsed entries onto a shape once the unmerged check passed."""
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
