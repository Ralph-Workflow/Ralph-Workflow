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
    """Ordinary event rows omit header context and retain their role."""
    writer = RenderedRecordWriter(tmp_path, "claude", model="minimax-M3")
    writer.append(_entry(body="hello"))
    writer.flush()
    text = writer.path.read_text(encoding="utf-8").strip()
    assert text == "[12:34:56] hello role=agent_text"


def test_writer_records_have_no_color_codes(tmp_path: Path) -> None:
    """ANSI escape sequences never appear in the rendered record."""
    writer = RenderedRecordWriter(tmp_path, "claude")
    writer.append(_entry(body="\x1b[31mRED\x1b[0m text"))
    writer.flush()
    text = writer.path.read_text(encoding="utf-8")
    assert "\x1b[" not in text
    assert "RED text" in text


def test_writer_record_structure_is_ansi_free_for_every_field(tmp_path: Path) -> None:
    """S-3: record structure sanitizes metadata as well as the body."""
    writer = RenderedRecordWriter(tmp_path, "claude")
    writer.append(
        _entry(
            timestamp="2026-01-01T12:34:56+00:00\x1b[31m",
            phase="dev\x1b[31m",
            iter="1/2\x1b[31m",
            agent="claude\x1b[31m",
            severity="info\x1b[31m",
            body="body\x1b[31m",
        )
    )
    writer.flush()
    text = writer.path.read_text(encoding="utf-8")
    assert "\x1b" not in text
    assert text == "[12:34:56] body role=agent_text\n"


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
    assert text == "[12:34:56] hello role=agent_text"


def test_writer_appends_multiple_entries(tmp_path: Path) -> None:
    """Multiple entries land in order, one line each, on the same file."""
    writer = RenderedRecordWriter(tmp_path, "claude")
    writer.append(_entry(body="first"))
    writer.append(_entry(body="second"))
    writer.append(_entry(body="third"))
    writer.flush()
    lines = writer.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert lines[0].endswith("first role=agent_text")
    assert lines[1].endswith("second role=agent_text")
    assert lines[2].endswith("third role=agent_text")


def test_writer_re_flush_appends_to_existing_file(tmp_path: Path) -> None:
    """A second ``flush()`` after more ``append()`` calls APPENDS, never truncates."""
    writer = RenderedRecordWriter(tmp_path, "claude")
    writer.append(_entry(body="first"))
    writer.flush()
    writer.append(_entry(body="second"))
    writer.flush()
    lines = writer.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert lines[0].endswith("first role=agent_text")
    assert lines[1].endswith("second role=agent_text")


def test_writer_buffer_is_bounded(tmp_path: Path) -> None:
    """The in-memory buffer is bounded; the writer never grows it past the cap.

    DA-003 (wt-028-display): the post-fix writer exposes the
    ``buffer_capacity`` public property so this regression is
    black-box. The pre-fix ``deque(maxlen=...)`` lived behind a
    private constant; the audit refuses private imports in tests,
    so the test now reads the cap through the public property.
    """
    writer = RenderedRecordWriter(tmp_path, "claude")
    cap = writer.buffer_capacity
    for i in range(cap + 10):
        writer.append(_entry(body=f"entry-{i}"))
    # Buffer cap is honored: at most ``cap`` lines are pending.
    assert writer.pending_lines <= cap
    # The most recent appends survived; the oldest dropped off the buffer.
    flushed = writer.flush()
    assert flushed <= cap


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
    """Concurrent ``append()`` / ``flush()`` does not lose lines.

    DA-003 (wt-028-display): ``append()`` flushes eagerly when the
    buffer reaches ``_BUFFER_FLUSH_THRESHOLD`` so the ``deque``
    never silently evicts an accepted entry on a chatty
    long-running run. The thread-safety contract here is still
    "no append is dropped": the file MUST contain one line per
    successful append, even when some appends trigger an eager
    flush while others are still buffered. The assertion compares
    the line count in the file against the expected count (4
    threads * 200 iterations = 800 appends), not against the
    return value of the final ``flush()`` call -- the final
    flush only writes whatever remains in the buffer at that
    point.
    """
    writer = RenderedRecordWriter(tmp_path, "claude")
    iterations = 200
    expected_total = 4 * iterations

    def _append() -> None:
        for i in range(iterations):
            writer.append(_entry(body=f"line-{threading.current_thread().name}-{i}"))

    threads = [threading.Thread(target=_append, name=f"t{i}") for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    final_written = writer.flush()
    assert final_written > 0
    # File has the expected number of lines (one per successful append).
    lines = writer.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == expected_total


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
    assert text == "[08:00:00] dict entry role=agent_text"


def test_writer_handles_missing_timestamp(tmp_path: Path) -> None:
    """An entry with no timestamp renders with the placeholder ``[hh:mm:ss]`` slot."""
    writer = RenderedRecordWriter(tmp_path, "claude")
    writer.append(_entry(timestamp=""))
    writer.flush()
    text = writer.path.read_text(encoding="utf-8").strip()
    assert text.startswith("[??:??:??]")


def test_writer_eager_flush_before_deque_eviction(tmp_path: Path) -> None:
    """DA-003 (wt-028-display): a chatty run producing more than the buffer
    cap never silently evicts an accepted entry.

    The pre-fix contract left ``RenderedRecordWriter._buffer`` as a
    ``deque(maxlen=512)`` that silently dropped the oldest line on
    overflow. A self-run production-path probe emitting 513
    distinct tool events wrote 512 lines and omitted the first
    event (``tool-0``), violating the one-entry-per-event record
    contract for a long-running run. The post-fix ``append()``
    flushes eagerly when the buffer reaches the eager-flush
    threshold so the deque never silently evicts an accepted
    entry. The test reads the cap through the public
    ``buffer_capacity`` property so it is black-box w.r.t. the
    private constants.
    """
    writer = RenderedRecordWriter(tmp_path, "claude")
    # Emit cap + 1 events; the post-fix writer must flush eagerly
    # so the very first entry survives the run.
    total = writer.buffer_capacity + 1
    for index in range(total):
        writer.append(_entry(body=f"tool-{index}"))
    writer.flush()

    text = writer.path.read_text(encoding="utf-8")
    # ``tool-0`` must appear in the file -- it was the first
    # entry the buffer accepted, and the pre-fix contract would
    # have silently evicted it once the cap was hit.
    assert "tool-0" in text, (
        f"first accepted entry lost; eager flush did not protect it:\n"
        f"{text[:200]}\n..."
    )
    # ``tool-{total-1}`` must also appear so the last accepted
    # entry made it through the eager-flush boundary.
    assert f"tool-{total - 1}" in text
