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


def test_condensed_tool_result_does_not_corrupt_the_verbatim_capture(
    tmp_path: Path,
) -> None:
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
    verbatim = raw_log_path_for(tmp_path, "codex", model="gpt-5.6-terra")
    condensed = raw_log_path_for(tmp_path, "codex", model="gpt-5.6-terra", condensed=True)

    assert verbatim != condensed
    assert condensed.name.endswith(f"{CONDENSED_LOG_SUFFIX}.log")
    assert condensed.suffix == ".log"


def test_condensed_body_stays_plain_text(tmp_path: Path) -> None:
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


def test_parser_failure_diagnostic_does_not_reach_the_verbatim_capture(
    tmp_path: Path,
) -> None:
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
    def parse(self, _line: str) -> object:
        raise ValueError("parse failed")


def test_cap_warning_is_not_shared_between_logs(tmp_path: Path) -> None:
    display = _display(tmp_path)
    try:
        first = display._get_condensed_log("unit-a")
        second = display._get_condensed_log("unit-b")
        first.disable()
        second.disable()
        display._check_overflow_size("unit-a", first)
        display._check_overflow_size("unit-b", second)

        warned = display._overflow_warned
        assert len(warned) == 2, f"each log must own a warning slot; got {warned}"
    finally:
        display.stop()


def test_stop_flushes_the_condensed_log_to_disk(tmp_path: Path) -> None:
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


def test_a_dropped_units_writer_cannot_reopen_its_path(tmp_path: Path) -> None:
    display = _display(tmp_path)
    try:
        first = display._get_condensed_log("codex")
        first.append("wave one")
        display.drop_unit("codex")

        assert first.is_disabled, "the dropped writer can still reopen the file"
        assert first.append("must not land") is False
    finally:
        display.stop()


def test_watchdog_drained_lines_reach_the_capture(tmp_path: Path) -> None:
    from ralph.display.raw_overflow import RawOverflowLog

    log = RawOverflowLog(tmp_path, "codex")
    captured: list[str] = []

    class _Reader:
        _raw_overflow = log

        def _capture_pending(self, pending_lines: list[str]) -> list[str]:
            from ralph.agents.invoke._process_reader import ProcessLineReader

            return ProcessLineReader._capture_pending(self, pending_lines)

    try:
        result = _Reader()._capture_pending(['{"a":1}', '{"b":2}'])
        log.flush()
        captured = log.path.read_text(encoding="utf-8").splitlines()
    finally:
        log.close()

    assert result == ['{"a":1}', '{"b":2}'], "the lines must still reach the parser"
    assert captured == ['{"a":1}', '{"b":2}'], "and must also reach the capture"
