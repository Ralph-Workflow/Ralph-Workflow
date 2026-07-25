"""P1 (wt-028-display S-13 / AC-11) tests for the rendered record writer.

The rendered record is the text-first file under
``.agent/raw/<safe_id>.rendered.log`` that lives alongside the
verbatim ``.agent/raw/<safe_id>.log`` capture. It must be:

* one entry per event,
* greppable (no color codes, stable field order),
* safe to redirect (single-line per entry, LF newlines),
* silent-disabled on I/O error (a transient disk failure must not
  crash the display path),
* bounded (the in-memory ring buffer must not grow unbounded on
  a chatty stream).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from ralph.display.record_writer import (
    RenderedRecordWriter,
    rendered_record_path,
    safe_id_for,
)


@dataclass(frozen=True)
class _Entry:
    """Minimal stand-in for :class:`PresentedEntry` used by tests."""

    timestamp: str
    phase: str
    cycle: int | None
    iter: str | None
    agent: str
    severity: str
    body: str


def _entry(**overrides: object) -> _Entry:
    base: dict[str, object] = {
        "timestamp": "2026-01-01T12:34:56+00:00",
        "phase": "development",
        "cycle": 3,
        "iter": "2/4",
        "agent": "claude",
        "severity": "info",
        "body": "Reading source files",
    }
    base.update(overrides)
    timestamp = str(base["timestamp"])
    phase = str(base["phase"])
    cycle_raw = base["cycle"]
    cycle: int | None = int(cycle_raw) if isinstance(cycle_raw, int) else None
    iter_raw = base["iter"]
    iter_: str | None = str(iter_raw) if iter_raw is not None else None
    agent = str(base["agent"])
    severity = str(base["severity"])
    body = str(base["body"])
    return _Entry(
        timestamp=timestamp,
        phase=phase,
        cycle=cycle,
        iter=iter_,
        agent=agent,
        severity=severity,
        body=body,
    )


def test_safe_id_for_distinct_agents() -> None:
    """Two agents produce distinct safe ids."""
    a = safe_id_for("claude", "minimax-M3")
    b = safe_id_for("pi", "minimax-M3")
    assert a != b
    assert "claude" in a
    assert "pi" in b


def test_safe_id_for_strips_unsafe_characters() -> None:
    """Unsafe characters collapse to ``_`` so the path is predictable."""
    assert safe_id_for("foo/bar") == "foo_bar"
    assert safe_id_for("foo bar") == "foo_bar"
    assert safe_id_for("foo:bar") == "foo_bar"
    # Multiple unsafe chars collapse to a single underscore.
    assert safe_id_for("foo//bar") == "foo_bar"


def test_safe_id_for_stable_across_calls() -> None:
    """The same ``(agent, model)`` pair returns the same safe id."""
    a1 = safe_id_for("claude", "minimax-M3")
    a2 = safe_id_for("claude", "minimax-M3")
    assert a1 == a2


def test_safe_id_for_handles_missing_model() -> None:
    """``safe_id_for`` works when only ``agent`` is supplied."""
    assert safe_id_for("claude") == "claude"
    assert "_" not in safe_id_for("claude")


def test_rendered_record_path_layout(tmp_path: Path) -> None:
    """The path lives under ``.agent/raw/`` and ends in ``.rendered.log``."""
    p = rendered_record_path(tmp_path, "claude", "minimax-M3")
    assert p.parent == tmp_path / ".agent" / "raw"
    assert p.name.endswith(".rendered.log")
    assert "claude" in p.name


def test_writer_appends_one_line_per_entry(tmp_path: Path) -> None:
    """``append()`` buffers one entry, ``flush()`` writes one line."""
    writer = RenderedRecordWriter(tmp_path, "claude", model="minimax-M3")
    writer.append(_entry(body="Reading source"))
    assert writer.pending_lines == 1
    written = writer.flush()
    assert written == 1
    assert writer.pending_lines == 0


def test_writer_records_have_stable_field_order(tmp_path: Path) -> None:
    """The rendered line starts with ``[hh:mm:ss] phase cycle=...`` etc."""
    writer = RenderedRecordWriter(tmp_path, "claude", model="minimax-M3")
    writer.append(_entry(body="hello"))
    writer.flush()
    text = writer.path.read_text(encoding="utf-8").strip()
    # Stable field order: timestamp -> phase -> cycle -> iter -> agent -> severity -> body
    assert text.startswith("[12:34:56] development cycle=3 iter=2/4")
    assert "agent=claude" in text
    assert "severity=info" in text
    assert text.endswith("hello")


def test_writer_records_have_no_color_codes(tmp_path: Path) -> None:
    """ANSI escape sequences never appear in the rendered record."""
    writer = RenderedRecordWriter(tmp_path, "claude")
    writer.append(_entry(body="\x1b[31mRED\x1b[0m text"))
    writer.flush()
    text = writer.path.read_text(encoding="utf-8")
    assert "\x1b[" not in text
    assert "RED text" in text


def test_writer_records_are_single_line(tmp_path: Path) -> None:
    """Body newlines are flattened so a grep never matches a partial line."""
    writer = RenderedRecordWriter(tmp_path, "claude")
    writer.append(_entry(body="line one\nline two\r\nline three"))
    writer.flush()
    lines = writer.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert "line one line two line three" in lines[0]


def test_writer_handles_missing_optional_fields(tmp_path: Path) -> None:
    """Missing cycle / iter / phase / agent / severity are omitted (no filler)."""
    writer = RenderedRecordWriter(tmp_path, "claude")
    entry = _Entry(
        timestamp="2026-01-01T12:34:56+00:00",
        phase="",
        cycle=None,
        iter=None,
        agent="",
        severity="",
        body="hello",
    )
    writer.append(entry)
    writer.flush()
    text = writer.path.read_text(encoding="utf-8").strip()
    assert text == "[12:34:56] hello"


def test_writer_appends_multiple_entries(tmp_path: Path) -> None:
    """Multiple entries land in order, one line each, on the same file."""
    writer = RenderedRecordWriter(tmp_path, "claude")
    writer.append(_entry(body="first"))
    writer.append(_entry(body="second"))
    writer.append(_entry(body="third"))
    writer.flush()
    lines = writer.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert lines[0].endswith("first")
    assert lines[1].endswith("second")
    assert lines[2].endswith("third")


def test_writer_re_flush_appends_to_existing_file(tmp_path: Path) -> None:
    """A second ``flush()`` after more ``append()`` calls APPENDS, never truncates."""
    writer = RenderedRecordWriter(tmp_path, "claude")
    writer.append(_entry(body="first"))
    writer.flush()
    writer.append(_entry(body="second"))
    writer.flush()
    lines = writer.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("first")
    assert lines[1].endswith("second")


def test_writer_buffer_is_bounded(tmp_path: Path) -> None:
    """The in-memory buffer is a deque(maxlen=...) -- the oldest entry is dropped on overflow."""
    writer = RenderedRecordWriter(tmp_path, "claude")
    from ralph.display.record_writer import _DEFAULT_BUFFER_CAP

    for i in range(_DEFAULT_BUFFER_CAP + 10):
        writer.append(_entry(body=f"entry-{i}"))
    # Buffer cap is honored: at most ``_DEFAULT_BUFFER_CAP`` lines are pending.
    assert writer.pending_lines <= _DEFAULT_BUFFER_CAP
    # The most recent appends survived; the oldest dropped off the buffer.
    flushed = writer.flush()
    assert flushed <= _DEFAULT_BUFFER_CAP


def test_writer_disable_is_terminal(tmp_path: Path) -> None:
    """``disable()`` permanently silences ``append()`` and ``flush()``."""
    writer = RenderedRecordWriter(tmp_path, "claude")
    writer.disable()
    writer.append(_entry(body="never written"))
    assert writer.disabled
    assert writer.flush() == 0
    # No file was created.
    assert not writer.path.exists()


def test_writer_silently_disables_on_io_error(tmp_path: Path) -> None:
    """An ``OSError`` during ``flush()`` records the failure via ``on_error`` and disables the writer."""
    errors: list[BaseException] = []

    def _on_error(exc: BaseException) -> None:
        errors.append(exc)

    writer = RenderedRecordWriter(tmp_path, "claude", on_error=_on_error)
    writer.append(_entry(body="first"))
    # Replace ``_path`` with a path under a non-existent directory whose
    # parent cannot be created (a regular file where a directory is
    # expected). ``mkdir(parents=True)`` raises in that case.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    blocked_path: Path = blocker / ".agent" / "raw" / "claude.rendered.log"
    writer._set_path_for_testing(blocked_path)
    written = writer.flush()
    assert written == 0
    assert writer.disabled
    assert len(errors) == 1
    assert isinstance(errors[0], OSError)


def test_writer_thread_safe_append_and_flush(tmp_path: Path) -> None:
    """Concurrent ``append()`` / ``flush()`` does not lose lines."""
    writer = RenderedRecordWriter(tmp_path, "claude")
    iterations = 200

    def _append() -> None:
        for i in range(iterations):
            writer.append(_entry(body=f"line-{threading.current_thread().name}-{i}"))

    threads = [threading.Thread(target=_append, name=f"t{i}") for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    written = writer.flush()
    assert written > 0
    # File has the expected number of lines (one per successful append).
    lines = writer.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == written


def test_writer_path_property_is_stable(tmp_path: Path) -> None:
    """``writer.path`` returns the same path across multiple accesses."""
    writer = RenderedRecordWriter(tmp_path, "claude", model="minimax-M3")
    assert writer.path == writer.path
    assert writer.path.name == safe_id_for("claude", "minimax-M3") + ".rendered.log"


def test_writer_accepts_dict_entries(tmp_path: Path) -> None:
    """The writer tolerates dict-shaped entries (forward-compat for typed events)."""
    writer = RenderedRecordWriter(tmp_path, "claude")
    writer.append(
        {
            "timestamp": "2026-01-01T08:00:00Z",
            "phase": "planning",
            "cycle": 1,
            "iter": "1/4",
            "agent": "claude",
            "severity": "info",
            "body": "dict entry",
        }
    )
    writer.flush()
    text = writer.path.read_text(encoding="utf-8").strip()
    assert text.startswith("[08:00:00] planning")
    assert text.endswith("dict entry")


def test_writer_handles_missing_timestamp(tmp_path: Path) -> None:
    """An entry with no timestamp renders with the placeholder ``[hh:mm:ss]`` slot."""
    writer = RenderedRecordWriter(tmp_path, "claude")
    writer.append(_entry(timestamp=""))
    writer.flush()
    text = writer.path.read_text(encoding="utf-8").strip()
    assert text.startswith("[??:??:??]")
