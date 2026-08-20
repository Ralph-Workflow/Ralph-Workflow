"""Regression: display-authored overflow must not corrupt the JSONL capture.

The verbatim raw capture (``.agent/raw/<id>.log``) is supposed to hold
exactly what the agent process wrote. It used to hold two writers' output:
the reader appended the agent's wire JSONL, and the display appended the
condensed bodies of tool results and previews that were too long to render
inline.

Those bodies are arbitrary text. When one carried a newline -- markdown
front matter (``---``), a rendered ``PASS`` row, a diff -- it landed as
multiple physical lines, none of which parse as JSON.
``detect_raw_log_breaks`` then read the file back and reported
``raw transcript corrupted``, so Ralph graded the run against damage it
had inflicted on its own transcript.

Measured 2026-08-20 in ``wt-067-claude-headless/.agent/raw/codex_*.log``:
the condensed body of a ``ralph_submit_md_artifact`` call put a bare
``---`` front-matter line into the capture, and the phase handler reported
``raw transcript corrupted: line at byte 157612 is not parseable JSON
(first 60 chars: '---')``.

The fix splits the files: the verbatim capture keeps only agent bytes, and
the display's condensed bodies go to a ``.overflow.log`` sibling as plain
readable text.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from ralph.display.activity_model import ActivityEventKind
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.raw_overflow import (
    CONDENSED_LOG_SUFFIX,
    RawOverflowLog,
    _forget_raw_overflow_log,
    detect_raw_log_breaks,
    raw_log_path_for,
)

pytestmark = pytest.mark.timeout_seconds(5)

#: The exact shape observed in the field: a markdown artifact body whose
#: YAML front matter opens with a bare ``---`` line. Padded past the
#: condenser's limit so the display actually spills it to the log.
_MARKDOWN_ARTIFACT_BODY = (
    "---\ntype: development_result\nstatus: completed\n---\n\n## Summary\n\n"
    + "\n".join(f"- [SUM-{n}] Repaired the headless display path." for n in range(400))
)


def _display(workspace_root: Path) -> ParallelDisplay:
    console = Console(file=io.StringIO(), force_terminal=False, color_system=None, width=200)
    return ParallelDisplay(
        make_display_context(console=console, env={"CI": "1"}),
        workspace_root=workspace_root,
    )


# ---------------------------------------------------------------------------
# The regression, through the real display path
# ---------------------------------------------------------------------------


def test_condensed_tool_result_does_not_corrupt_the_verbatim_capture(
    tmp_path: Path,
) -> None:
    """The measured incident: front matter must not break the JSONL."""
    verbatim = raw_log_path_for(tmp_path, "codex")
    verbatim.parent.mkdir(parents=True, exist_ok=True)
    verbatim.write_text(
        json.dumps({"type": "item.started", "item": {"id": "item_22"}}) + "\n",
        encoding="utf-8",
    )

    display = _display(tmp_path)
    try:
        display.emit_parsed_event(
            "codex",
            ActivityEventKind.TOOL_RESULT,
            _MARKDOWN_ARTIFACT_BODY,
            {},
        )
    finally:
        display.stop()

    assert detect_raw_log_breaks(verbatim) == []


def test_condensed_body_is_written_to_the_overflow_sibling(tmp_path: Path) -> None:
    """The body must still be recoverable -- in the sibling file."""
    display = _display(tmp_path)
    try:
        display.emit_parsed_event(
            "codex",
            ActivityEventKind.TOOL_RESULT,
            _MARKDOWN_ARTIFACT_BODY,
            {},
        )
        display.drop_unit("codex")
    finally:
        display.stop()

    condensed = raw_log_path_for(tmp_path, "codex", condensed=True)

    assert condensed.exists(), "condensed body was not preserved anywhere"
    assert "type: development_result" in condensed.read_text(encoding="utf-8")


def test_condensed_log_is_a_distinct_file_from_the_verbatim_capture(
    tmp_path: Path,
) -> None:
    """The two writers must not share a path -- that was the root cause."""
    verbatim = raw_log_path_for(tmp_path, "codex", model="gpt-5.6-terra")
    condensed = raw_log_path_for(tmp_path, "codex", model="gpt-5.6-terra", condensed=True)

    assert verbatim != condensed
    assert condensed.name.endswith(f"{CONDENSED_LOG_SUFFIX}.log")
    # ``.agent/raw/`` accepts ``.log`` files only (see test_agent_internal_paths).
    assert condensed.suffix == ".log"


# ---------------------------------------------------------------------------
# Readability and detection properties of the two files
# ---------------------------------------------------------------------------


def test_condensed_body_stays_plain_text(tmp_path: Path) -> None:
    """Operators page through this file; it must not be escaped JSON.

    The condensation marker in the live view points an operator at this
    path, so the body has to read as the text it was.
    """
    log = RawOverflowLog(tmp_path, "claude", condensed=True)
    try:
        log.append(_MARKDOWN_ARTIFACT_BODY)
        log.flush()
        body = log.path.read_text(encoding="utf-8")
    finally:
        log.close()
        _forget_raw_overflow_log(str(log.path))

    assert body.startswith("---\ntype: development_result\n")
    assert "\\n" not in body, "body was escaped rather than written verbatim"


def test_condensed_writes_do_not_consume_the_verbatim_byte_budget(
    tmp_path: Path,
) -> None:
    """A large condensed body must not disable the agent's own capture.

    ``append`` permanently disables a log once its byte cap is crossed.
    While both writers shared one file, one oversized preview could stop
    the verbatim capture from recording any further agent output.
    """
    display = _display(tmp_path)
    try:
        display.emit_parsed_event(
            "codex",
            ActivityEventKind.TOOL_RESULT,
            _MARKDOWN_ARTIFACT_BODY,
            {},
        )
        verbatim = raw_log_path_for(tmp_path, "codex")
        assert not verbatim.exists(), "display content reached the verbatim capture"
    finally:
        display.stop()


def test_genuine_wire_corruption_is_still_reported(tmp_path: Path) -> None:
    """The split must not mute real corruption in the verbatim capture."""
    log = RawOverflowLog(tmp_path, "codex")
    try:
        log.append('{"type":"item.completed"}')
        log.append("this is a malformed wire frame")
        log.flush()
        breaks = detect_raw_log_breaks(log.path)
    finally:
        log.close()
        _forget_raw_overflow_log(str(log.path))

    assert [b.kind for b in breaks] == ["NON_JSONL"]


# ---------------------------------------------------------------------------
# Only the readers may write the verbatim capture
# ---------------------------------------------------------------------------


def test_parser_failure_diagnostic_does_not_reach_the_verbatim_capture(
    tmp_path: Path,
) -> None:
    """A display-side parse failure must not inject text into the transcript.

    ``_raw_overflow_write`` exists to preserve a line the parser choked
    on. The line it is handed has already been through the display
    sanitizer (truncated at 200 chars with an ellipsis), so writing it to
    the verbatim capture severs long wire frames into unparseable JSON --
    and the surrounding ``try`` also covers the display's own render
    callback, so a rendering bug is enough to trigger it.
    """
    from ralph.display.activity_model import ActivityProvider

    display = _display(tmp_path)
    long_frame = json.dumps({"type": "item.completed", "item": {"r": "Z" * 600}})
    try:
        display._activity_router._parser_factory = lambda _: _RaisingParser()
        display._activity_router.push_raw_line(
            "codex", long_frame, provider=ActivityProvider.GENERIC
        )
        display.drop_unit("codex")
    finally:
        display.stop()

    verbatim = raw_log_path_for(tmp_path, "codex")
    if verbatim.exists():
        assert detect_raw_log_breaks(verbatim) == []


class _RaisingParser:
    """Parser stub whose failure drives the diagnostic write path."""

    def parse(self, _line: str) -> object:
        raise ValueError("parse failed")


def test_cap_warning_is_not_shared_between_logs(tmp_path: Path) -> None:
    """Each file needs its own one-shot 'log full' warning.

    The slot is keyed by log path rather than by unit, so one log
    reaching the cap cannot silence the warning for another.
    """
    display = _display(tmp_path)
    try:
        first = display._get_condensed_log("unit-a")
        second = display._get_condensed_log("unit-b")
        # Drive both past the byte cap so each guard fires.
        first.disable()
        second.disable()
        display._check_overflow_size("unit-a", first)
        display._check_overflow_size("unit-b", second)

        warned = display._overflow_warned
        assert len(warned) == 2, f"each log must own a warning slot; got {warned}"
    finally:
        display.stop()


def test_stop_flushes_the_condensed_log_to_disk(tmp_path: Path) -> None:
    """A run that ends without drop_unit must not advertise an empty file.

    The live view points the operator at the condensed log by path. If
    ``stop()`` does not flush it, that reference names a zero-byte file.
    """
    display = _display(tmp_path)
    try:
        display.emit_parsed_event(
            "codex",
            ActivityEventKind.TOOL_RESULT,
            _MARKDOWN_ARTIFACT_BODY,
            {},
        )
    finally:
        display.stop()

    condensed = raw_log_path_for(tmp_path, "codex", condensed=True)

    assert condensed.exists()
    assert condensed.stat().st_size > 0, "stop() left the advertised file empty"


def test_a_respawned_unit_does_not_erase_the_earlier_wave(tmp_path: Path) -> None:
    """The condensed log is the only copy; a second wave must not wipe it.

    ``drop_unit`` closes and forgets a unit's log, so a re-spawned worker
    for the same unit id builds a fresh writer. While truncation was
    keyed to the writer instance, that second writer opened the path
    ``wb`` and erased wave one -- including bodies wave one's rendered
    record still points at by path.
    """
    display = _display(tmp_path)
    try:
        display.emit_parsed_event(
            "codex", ActivityEventKind.TOOL_RESULT, _MARKDOWN_ARTIFACT_BODY, {}
        )
        display.drop_unit("codex")
        display.emit_parsed_event(
            "codex",
            ActivityEventKind.TOOL_RESULT,
            "second wave body\n" + "y" * 5000,
            {},
        )
        display.drop_unit("codex")
    finally:
        display.stop()

    body = raw_log_path_for(tmp_path, "codex", condensed=True).read_text(encoding="utf-8")

    assert "type: development_result" in body, "wave one was erased"
    assert "second wave body" in body
