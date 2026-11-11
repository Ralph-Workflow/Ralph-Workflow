from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pipeline.prompt_prep import _prompt_changed_since_last_materialization
from ralph.prompts import master_prompt

if TYPE_CHECKING:
    from collections.abc import Callable


def _make_stat_fn(
    source: Path,
    source_meta: tuple[int, int] | None,
    current: Path,
    current_meta: tuple[int, int] | None,
) -> Callable[[Path], tuple[int, int] | None]:

    def stat_fn(path: Path) -> tuple[int, int] | None:
        if path == source:
            return source_meta
        if path == current:
            return current_meta
        return None

    return stat_fn


def test_equal_size_and_mtime_is_unchanged_with_zero_reads() -> None:

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

    changed, source_text = master_prompt._prompt_files_differ(
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

    changed, source_text = master_prompt._prompt_files_differ(
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

    changed, source_text = master_prompt._prompt_files_differ(
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

    changed, source_text = master_prompt._prompt_files_differ(
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

    changed, source_text = master_prompt._prompt_files_differ(
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

    source = Path("/fake/source.md")
    current = Path("/fake/current.md")
    source_meta = (200, 1234567890)
    current_meta = None

    read_source_calls: list[Path] = []

    def read_source(p: Path) -> str:
        read_source_calls.append(p)
        return "NEW-BODY"

    stat_fn = _make_stat_fn(source, source_meta, current, current_meta)

    changed, source_text = master_prompt._prompt_files_differ(
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

    source = Path("/fake/source.md")
    current = Path("/fake/current.md")
    source_meta = (200, 1234567890)
    current_meta = (100, 1234567890)

    read_source_calls: list[Path] = []
    read_current_calls: list[Path] = []

    stat_fn = _make_stat_fn(source, source_meta, current, current_meta)

    changed, source_text = master_prompt._prompt_files_differ(
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

    prompt_path = tmp_path / "PROMPT.md"
    product_criteria_path = tmp_path / ".agent" / "PRODUCT_CRITERIA.md"
    product_criteria_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("BODY-CONTENT", encoding="utf-8")
    product_criteria_path.write_text("BODY-CONTENT", encoding="utf-8")

    target_mtime_ns = 1_700_000_000_000_000_000
    for path in (prompt_path, product_criteria_path):
        os.utime(path, ns=(target_mtime_ns, target_mtime_ns))

    result = _prompt_changed_since_last_materialization(tmp_path)
    assert result is False


def test_prompt_prep_uses_helper_when_size_differs(tmp_path: Path) -> None:

    prompt_path = tmp_path / "PROMPT.md"
    product_criteria_path = tmp_path / ".agent" / "PRODUCT_CRITERIA.md"
    product_criteria_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("NEW-CONTENT-X", encoding="utf-8")
    product_criteria_path.write_text("OLD", encoding="utf-8")

    result = _prompt_changed_since_last_materialization(tmp_path)
    assert result is True
