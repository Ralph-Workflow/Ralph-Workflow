"""Tests for the prompt-equality (st_size, st_mtime_ns) fast path.

The wt-024 P4 fix introduces a shared helper
``_prompt_files_differ(source, current, *, stat_fn, read_source,
read_current) -> tuple[bool, str | None]`` that both
``system_prompt._sync_current_prompt_file`` and
``pipeline.prompt_prep._prompt_changed_since_last_materialization``
call instead of doing TWO full file reads just to string-compare.

The fast-path rules are:

  - current does not exist -> changed (caller may write source).
  - (st_size, st_mtime_ns) match -> unchanged, NO content reads.
  - sizes differ -> changed (source read only if caller passed read_source).
  - sizes match but mtime differs -> read both, compare content.

Tests cover the four required cases (a)-(d) plus the missing-file
edge cases. Tests inject a fake ``stat_fn`` so no real Path.stat()
runs and inject ``read_source``/``read_current`` to count content
reads.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline.prompt_prep import _prompt_changed_since_last_materialization
from ralph.prompts import system_prompt

if TYPE_CHECKING:
    from collections.abc import Callable


def _make_stat_fn(
    source: Path,
    source_meta: tuple[int, int] | None,
    current: Path,
    current_meta: tuple[int, int] | None,
) -> Callable[[Path], tuple[int, int] | None]:
    """Build a stat_fn that returns a controlled (size, mtime_ns) tuple.

    ``source`` / ``current`` are the actual paths and ``source_meta`` /
    ``current_meta`` are the controlled metadata to return for each.
    """

    def stat_fn(path: Path) -> tuple[int, int] | None:
        if path == source:
            return source_meta
        if path == current:
            return current_meta
        return None

    return stat_fn


def test_equal_size_and_mtime_is_unchanged_with_zero_reads() -> None:
    """Case (a): equal (st_size, st_mtime_ns) -> unchanged with zero reads."""

    source = Path("/fake/source.md")
    current = Path("/fake/current.md")
    source_meta = (100, 1234567890)
    current_meta = (100, 1234567890)

    read_source_calls: list[Path] = []
    read_current_calls: list[Path] = []

    def read_source(p: Path) -> str:
        read_source_calls.append(p)
        return "BODY"

    def read_current(p: Path) -> str:
        read_current_calls.append(p)
        return "BODY"

    stat_fn = _make_stat_fn(source, source_meta, current, current_meta)

    changed, source_text = system_prompt._prompt_files_differ(
        source,
        current,
        stat_fn=stat_fn,
        read_source=read_source,
        read_current=read_current,
    )

    assert changed is False
    assert source_text is None
    assert read_source_calls == []
    assert read_current_calls == []


def test_different_size_is_changed_no_current_read() -> None:
    """Case (b): sizes differ -> changed. CURRENT not read.

    Source IS read (caller passed read_source) so the returned
    source_text is populated. CURRENT is never read.
    """

    source = Path("/fake/source.md")
    current = Path("/fake/current.md")
    source_meta = (200, 1234567890)
    current_meta = (100, 1234567890)

    read_source_calls: list[Path] = []
    read_current_calls: list[Path] = []

    def read_source(p: Path) -> str:
        read_source_calls.append(p)
        return "NEW-BODY"

    def read_current(p: Path) -> str:
        read_current_calls.append(p)
        return "OLD-BODY"

    stat_fn = _make_stat_fn(source, source_meta, current, current_meta)

    changed, source_text = system_prompt._prompt_files_differ(
        source,
        current,
        stat_fn=stat_fn,
        read_source=read_source,
        read_current=read_current,
    )

    assert changed is True
    assert source_text == "NEW-BODY"
    assert read_source_calls == [source]
    assert read_current_calls == []


def test_same_size_different_mtime_same_content_is_unchanged() -> None:
    """Case (c): sizes match, mtime differs, content same -> unchanged."""

    source = Path("/fake/source.md")
    current = Path("/fake/current.md")
    source_meta = (100, 1234567890)
    current_meta = (100, 1234567891)

    read_source_calls: list[Path] = []
    read_current_calls: list[Path] = []

    def read_source(p: Path) -> str:
        read_source_calls.append(p)
        return "SAME-BODY"

    def read_current(p: Path) -> str:
        read_current_calls.append(p)
        return "SAME-BODY"

    stat_fn = _make_stat_fn(source, source_meta, current, current_meta)

    changed, source_text = system_prompt._prompt_files_differ(
        source,
        current,
        stat_fn=stat_fn,
        read_source=read_source,
        read_current=read_current,
    )

    assert changed is False
    assert source_text == "SAME-BODY"
    assert read_source_calls == [source]
    assert read_current_calls == [current]


def test_same_size_different_mtime_different_content_is_changed() -> None:
    """Case (d): sizes match, mtime differs, content differs -> changed."""

    source = Path("/fake/source.md")
    current = Path("/fake/current.md")
    source_meta = (100, 1234567890)
    current_meta = (100, 1234567891)

    read_source_calls: list[Path] = []
    read_current_calls: list[Path] = []

    def read_source(p: Path) -> str:
        read_source_calls.append(p)
        return "NEW-BODY"

    def read_current(p: Path) -> str:
        read_current_calls.append(p)
        return "OLD-BODY"

    stat_fn = _make_stat_fn(source, source_meta, current, current_meta)

    changed, source_text = system_prompt._prompt_files_differ(
        source,
        current,
        stat_fn=stat_fn,
        read_source=read_source,
        read_current=read_current,
    )

    assert changed is True
    assert source_text == "NEW-BODY"
    assert read_source_calls == [source]
    assert read_current_calls == [current]


def test_missing_source_is_not_changed() -> None:
    """Source missing -> not changed (caller has nothing to compare)."""

    source = Path("/fake/source.md")
    current = Path("/fake/current.md")
    source_meta = None
    current_meta = (100, 1234567890)

    read_source_calls: list[Path] = []
    read_current_calls: list[Path] = []

    def read_source(p: Path) -> str:
        read_source_calls.append(p)
        return ""

    def read_current(p: Path) -> str:
        read_current_calls.append(p)
        return ""

    stat_fn = _make_stat_fn(source, source_meta, current, current_meta)

    changed, source_text = system_prompt._prompt_files_differ(
        source,
        current,
        stat_fn=stat_fn,
        read_source=read_source,
        read_current=read_current,
    )

    assert changed is False
    assert source_text is None
    assert read_source_calls == []
    assert read_current_calls == []


def test_missing_current_is_changed() -> None:
    """Current missing -> changed; source IS read."""

    source = Path("/fake/source.md")
    current = Path("/fake/current.md")
    source_meta = (200, 1234567890)
    current_meta = None

    read_source_calls: list[Path] = []

    def read_source(p: Path) -> str:
        read_source_calls.append(p)
        return "NEW-BODY"

    stat_fn = _make_stat_fn(source, source_meta, current, current_meta)

    changed, source_text = system_prompt._prompt_files_differ(
        source,
        current,
        stat_fn=stat_fn,
        read_source=read_source,
        read_current=lambda p: "",
    )

    assert changed is True
    assert source_text == "NEW-BODY"
    assert read_source_calls == [source]


def test_sizes_differ_no_read_source_returns_none_text() -> None:
    """Case (b) with read_source=None: 0 reads, source_text is None.

    The caller (prompt_prep) doesn't need the source text, only the
    boolean. The helper still avoids reading both files for the
    size-mismatch fast path.
    """

    source = Path("/fake/source.md")
    current = Path("/fake/current.md")
    source_meta = (200, 1234567890)
    current_meta = (100, 1234567890)

    read_source_calls: list[Path] = []
    read_current_calls: list[Path] = []

    stat_fn = _make_stat_fn(source, source_meta, current, current_meta)

    changed, source_text = system_prompt._prompt_files_differ(
        source,
        current,
        stat_fn=stat_fn,
        read_source=None,
        read_current=lambda p: read_current_calls.append(p) or "BODY",
    )

    assert changed is True
    assert source_text is None
    assert read_source_calls == []
    assert read_current_calls == []


def test_prompt_prep_uses_helper_when_stats_match(tmp_path: Path) -> None:
    """prompt_prep._prompt_changed_since_last_materialization returns
    False on equal size+mtime WITHOUT reading the content of either
    file (the fast-path perf claim)."""

    prompt_path = tmp_path / "PROMPT.md"
    current_prompt_path = tmp_path / ".agent" / "CURRENT_PROMPT.md"
    current_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("BODY-CONTENT", encoding="utf-8")
    current_prompt_path.write_text("BODY-CONTENT", encoding="utf-8")

    # Force mtime equality by setting both to the same mtime_ns.
    target_mtime_ns = 1_700_000_000_000_000_000
    for path in (prompt_path, current_prompt_path):
        # Use os.utime to set both atime and mtime to identical values.
        os.utime(path, ns=(target_mtime_ns, target_mtime_ns))

    result = _prompt_changed_since_last_materialization(tmp_path)
    assert result is False


def test_prompt_prep_uses_helper_when_size_differs(tmp_path: Path) -> None:
    """prompt_prep returns True on different sizes (no need to read content)."""

    prompt_path = tmp_path / "PROMPT.md"
    current_prompt_path = tmp_path / ".agent" / "CURRENT_PROMPT.md"
    current_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("NEW-CONTENT-X", encoding="utf-8")
    current_prompt_path.write_text("OLD", encoding="utf-8")

    result = _prompt_changed_since_last_materialization(tmp_path)
    assert result is True
