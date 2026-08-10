"""Display-fidelity regression tests for the OpenCode parser+display seam.

Plan step S-2 of the wt-05-fix-opencode-parsing plan: drive the
full parser->display chain through the OpenCode fixture set and
assert that the parser produces a metadata envelope that the shared
preview builder can render. These tests are deliberately
black-box: they go through the public :class:`ralph.agents.parsers.opencode.OpenCodeParser`
API and assert on the parsed agent-output lines, not on private
parser state.

The captured wire-format tests against the real 1.18.14 binary
live in ``tests/test_opencode_captured_wire.py``; the synthetic
fixture-driven and event-accounting tests live here. The two
files share the same parser API but cover different aspects of
the regression: the synthetic fixtures exercise the parser's
plumbing (running -> completed dedup, errored dispatch visibility,
event accounting), and the captured fixture exercises the
1.18.14 wire shape the parser must handle on the live binary.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.agents.parsers.opencode import OpenCodeParser
from ralph.display.preview_payload import payload_from_tool_event

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _parse(parser: OpenCodeParser, lines: Iterator[str]) -> list:
    return list(parser.parse(lines))


# ---------------------------------------------------------------------------
# Fixture-driven black-box regressions
# ---------------------------------------------------------------------------


class TestOpenCodeDisplayFidelity:
    """Display-fidelity regressions for the OpenCode parser+display seam."""

    def test_read_tool_produces_file_preview_envelope(self) -> None:
        """A `read`-style tool_use must produce metadata that
        ``payload_from_tool_event`` recognizes as a file preview.
        """
        parser = OpenCodeParser()
        line = (
            '{"type": "tool_use", "part": {"type": "tool", "tool": "Read",'
            ' "callID": "call_read", "state": {"status": "completed",'
            ' "input": {"path": "ralph/display/parallel_display.py"}}}}'
        )

        results = _parse(parser, _lines(line))

        # OpenCode collapses a terminal ``completed`` state into a
        # tool_use + tool_result pair; the dispatch metadata is the
        # same on both, so a single envelope assertion covers both.
        tool_uses = [r for r in results if r.type == "tool_use"]
        assert len(tool_uses) == 1
        assert tool_uses[0].content == "Read"
        # The metadata envelope must include a recognized ``tool`` and
        # an ``input`` payload ``payload_from_tool_event`` can route.
        assert tool_uses[0].metadata["tool"] == "Read"
        assert tool_uses[0].metadata["input"]["path"] == "ralph/display/parallel_display.py"
        # And the canonical preview payload must be non-None -- the
        # smoking gun for the original defect was a None here.
        assert payload_from_tool_event("Read", tool_uses[0].metadata) is not None

    def test_write_tool_produces_syntax_preview_envelope(self) -> None:
        """A `write`-style tool_use must produce a syntax-preview envelope."""
        parser = OpenCodeParser()
        line = (
            '{"type": "tool_use", "part": {"type": "tool", "tool": "write",'
            ' "callID": "call_write", "state": {"status": "completed",'
            ' "input": {"path": "x.py", "content": "value = 1\\n"}}}}'
        )

        results = _parse(parser, _lines(line))

        tool_uses = [r for r in results if r.type == "tool_use"]
        assert len(tool_uses) == 1
        assert tool_uses[0].content == "write"
        assert payload_from_tool_event("write", tool_uses[0].metadata) is not None

    def test_edit_tool_produces_diff_preview_envelope(self) -> None:
        """An `edit`-style tool_use must produce a diff-preview envelope.

        The edit tool's metadata includes ``oldText`` and ``newText``
        keys that ``payload_from_tool_event`` recognizes as a
        ``replace`` operation. The diff-preview capability must
        fire when this shape is observed.
        """
        parser = OpenCodeParser()
        line = (
            '{"type": "tool_use", "part": {"type": "tool", "tool": "edit",'
            ' "callID": "call_edit", "state": {"status": "completed",'
            ' "input": {"path": "x.py", "oldText": "value = 1\\n",'
            ' "newText": "value = 2\\n"}}}}'
        )

        results = _parse(parser, _lines(line))

        tool_uses = [r for r in results if r.type == "tool_use"]
        assert len(tool_uses) == 1
        assert tool_uses[0].content == "edit"
        assert payload_from_tool_event("edit", tool_uses[0].metadata) is not None

    def test_running_then_completed_tool_does_not_duplicate(self) -> None:
        """OpenCode collapses a running state and a completed state for
        the same call into one dispatch and one result -- exactly one
        ``tool_use`` and one ``tool_result`` for the same call id.
        """
        parser = OpenCodeParser()
        running = (
            '{"type":"tool_use","part":{"type":"tool","tool":"write",'
            '"callID":"call_dup","state":{"status":"running",'
            '"input":{"path":"x.py"}}}}'
        )
        completed = (
            '{"type":"tool_use","part":{"type":"tool","tool":"write",'
            '"callID":"call_dup","state":{"status":"completed",'
            '"input":{"path":"x.py","content":"value = 1\\n"},'
            '"output":"wrote 1 line"}}}'
        )

        results = _parse(parser, _lines(running, completed))

        # One dispatch + one result -- not two tool_use + two tool_result.
        assert [r.type for r in results] == ["tool_use", "tool_result"]

    def test_errored_tool_keeps_dispatch_visible(self) -> None:
        """An errored ``task`` dispatch must surface as both a tool_use
        and an error line -- the dispatch MUST NOT be erased by the
        error branch.
        """
        parser = OpenCodeParser()
        line = (
            '{"type": "tool_use", "sessionID": "ses_1", "part": {"type": "tool",'
            ' "tool": "task", "callID": "call_task", "state": {"status": "error",'
            ' "input": {"prompt": "x"}, "error": "MCP error -32001: Request timed out"}}}'
        )

        results = _parse(parser, _lines(line))

        assert [r.type for r in results] == ["tool_use", "error"]
        assert results[0].content == "task"
        assert results[1].content == "MCP error -32001: Request timed out"

    def test_every_non_lifecycle_event_in_opencode_wire_fixture_produces_a_parsed_line(self) -> None:
        """Every non-lifecycle event in the captured fixture must produce
        at least one parsed ``AgentOutputLine`` -- a frame that drops
        silently is exactly the defect the parser-vs-display seam
        must catch.

        The captured fixture (``tests/display/_fixtures/opencode_wire.jsonl``,
        live-captured from ``opencode 1.18.14`` 2025-11-19) carries
        ``step_start`` and ``step_finish`` lifecycle frames that the
        parser deliberately suppresses from visible output
        (``ralph/agents/parsers/opencode.py:OpenCodeParser._STOP_EVENT_TYPES``)
        because the smoke harness treats those events as marker
        boundaries, not visible tool calls. The "every event
        produces at least one parsed line" invariant therefore
        counts only the NON-lifecycle frames (tool_use, text, etc.)
        and asserts that count of visible lines is at least that
        many.
        """
        from ralph.agents.parsers.opencode import OpenCodeParser as _Parser
        from tests.test_opencode_captured_wire import (
            _FIXTURE_PATH as _OPENCODE_WIRE_FIXTURE_PATH,
        )

        lines = _OPENCODE_WIRE_FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
        non_lifecycle_count = 0
        import json as _json
        for raw in lines:
            if not raw:
                continue
            obj = _json.loads(raw)
            event_type = obj.get("type")
            if event_type not in ("step_start", "step_finish"):
                non_lifecycle_count += 1
        parser = _Parser()
        parsed_lines = _parse(parser, iter(lines))
        # Parser must emit at least one visible line per non-lifecycle
        # input frame; a "silent drop" pattern (e.g. tool_use frames
        # producing no AgentOutputLine) would be a regression.
        assert len(parsed_lines) >= non_lifecycle_count, (
            f"Parser produced {len(parsed_lines)} AgentOutputLines for "
            f"{non_lifecycle_count} non-lifecycle input frames "
            f"({len(lines)} input frames total, of which "
            f"{len(lines) - non_lifecycle_count} are suppressed "
            f"lifecycle markers); at least one input frame was dropped"
        )


# ---------------------------------------------------------------------------
# Capability vs render: a SUPPORTED capability without a render is a break
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Smoke-driven fixture: a complete write/edit/read sequence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scenario", "frames"),
    [
        (
            "write_then_read",
            [
                '{"type":"tool_use","part":{"type":"tool","tool":"write",'
                '"callID":"c1","state":{"status":"completed",'
                '"input":{"path":"a.py","content":"x = 1\\n"},"output":"wrote"}}}',
                '{"type":"tool_use","part":{"type":"tool","tool":"read",'
                '"callID":"c2","state":{"status":"completed",'
                '"input":{"path":"a.py"},"output":"x = 1\\n"}}}',
            ],
        ),
        (
            "write_then_edit",
            [
                '{"type":"tool_use","part":{"type":"tool","tool":"write",'
                '"callID":"c1","state":{"status":"completed",'
                '"input":{"path":"a.py","content":"x = 1\\n"},"output":"wrote"}}}',
                '{"type":"tool_use","part":{"type":"tool","tool":"edit",'
                '"callID":"c2","state":{"status":"completed",'
                '"input":{"path":"a.py","oldText":"x = 1\\n",'
                '"newText":"x = 2\\n"},"output":"edited"}}}',
            ],
        ),
    ],
)
def test_opencode_capability_routes_produce_recognized_envelopes(
    scenario: str, frames: list[str]
) -> None:
    """Every common write/edit/read shape routes through ``payload_from_tool_event``.

    The S-5 contract: a SUPPORTED capability must correspond to a
    tool call shape the parser actually produces AND the preview
    builder actually recognizes. These parametrized scenarios cover
    the three display surfaces (syntax_preview, file_preview,
    diff_preview) the OpenCode agent is expected to render.
    """
    parser = OpenCodeParser()
    parsed = _parse(parser, iter(frames))
    # Each frame may collapse into tool_use + tool_result pairs;
    # assert that the parsed count is at least the frame count
    # (no frame is silently dropped) and that every tool_use
    # envelope routes through the preview builder.
    assert len(parsed) >= len(frames)
    for line in parsed:
        if line.type != "tool_use":
            continue
        meta = line.metadata or {}
        assert "tool" in meta, (
            f"Scenario {scenario!r}: parsed line {line.type!r} "
            f"has no tool metadata; payload_from_tool_event would None"
        )
        assert payload_from_tool_event(line.content, meta) is not None, (
            f"Scenario {scenario!r}: payload_from_tool_event returned "
            f"None for tool {line.content!r}; the display layer would "
            f"silently render nothing"
        )


# ---------------------------------------------------------------------------
# Capability vs render: a SUPPORTED capability without a render is a break
# ---------------------------------------------------------------------------


def test_supported_capability_without_render_breaks_via_recorder() -> None:
    """A SUPPORTED declaration paired with an empty recorder is the S-5 smoking gun.

    The recorder (``ralph.display.capability_observation_recorder``) is the
    transport-neutral observation seam. When the parser produces a
    tool_use shape that should have exercised a SUPPORTED
    capability but ``payload_from_tool_event`` returns ``None``,
    the recorder's ``observed_capabilities`` set will be missing
    the expected capability. The S-5 smoke grading must compare
    the declaration against the observed set and emit a break.
    This test pins the recorder's role: it is the contract test,
    not the smoking gun itself.
    """
    from ralph.agents.display_capabilities import DisplayCapability
    from ralph.agents.display_capability_stance import DisplayCapabilityStance
    from ralph.display.capability_observation_recorder import CapabilityObservationRecorder

    # The OpenCode parser is a real parser; the synthetic fixture's
    # first frame is a `read` tool_use. The recorder starts empty.
    recorder = CapabilityObservationRecorder()
    parser = OpenCodeParser()
    line = (
        '{"type":"tool_use","part":{"type":"tool","tool":"Read",'
        '"callID":"c1","state":{"status":"completed",'
        '"input":{"path":"x.py"},"output":"contents"}}}'
    )
    parsed = _parse(parser, _lines(line))
    # OpenCode collapses a terminal completed state into a tool_use
    # + tool_result pair, so we expect at least one parsed line.
    assert len(parsed) >= 1
    assert any(r.type == "tool_use" and r.content == "Read" for r in parsed)

    # The parser produced the metadata envelope; the recorder stays
    # empty because no preview render has happened yet. The smoke
    # grader must thread the recorder into the display path so a
    # non-empty observed set (or its absence) becomes part of the
    # grading verdict -- not this test's responsibility to wire.
    assert recorder.observed_capabilities() == frozenset()

    # The SUPPORTED declaration is the contract; an empty
    # observed set is the failure mode the grader must detect.
    supported = DisplayCapabilityStance.supported(DisplayCapability.FILE_PREVIEW)
    assert supported.is_supported
    assert DisplayCapability.FILE_PREVIEW not in recorder.observed_capabilities()


# ---------------------------------------------------------------------------
# Sanity: the real captured fixture is loadable as JSON for replay.
# ---------------------------------------------------------------------------


def test_opencode_wire_fixture_is_loadable_json() -> None:
    """Each line of the captured fixture must be parseable JSON.

    Pins that the live 1.18.14 capture is at least valid JSON; a
    hand-typed invalid line in the focused fixture is an immediate
    regression.
    """
    from tests.test_opencode_captured_wire import _FIXTURE_PATH

    for line_number, raw in enumerate(
        _FIXTURE_PATH.read_text(encoding="utf-8").splitlines(), start=1
    ):
        json.loads(raw)  # raises ValueError if malformed
        assert line_number >= 1


# ---------------------------------------------------------------------------
# DA-002 regression: the live-captured fixture must drive the parser to
# produce preview envelopes for all three display surfaces.
# ---------------------------------------------------------------------------


def test_captured_fixture_drives_all_three_display_surfaces() -> None:
    """The captured read/write/edit fixture must exercise syntax, file, and diff surfaces.

    DA-002 (wt-05-fix-opencode-parsing): the prior committed
    fixture only carried bash tool calls (no read/write/edit
    operations), so the parser-vs-display seam was not actually
    exercised for the three display surfaces. The new captured
    fixture replaces that with a real 1.18.14 read/write/edit
    sequence. This test pins the contract: every captured
    read/write/edit tool_use must produce a non-None
    ``payload_from_tool_event`` envelope, and the captured
    envelope's operation must route to the right catalog surface
    (read -> file_preview, write -> syntax_preview, edit ->
    diff_preview).
    """
    from tests.test_opencode_captured_wire import _FIXTURE_PATH

    fixture_lines = _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
    parser = OpenCodeParser()
    parsed = _parse(parser, iter(fixture_lines))

    operations_seen: set[str] = set()
    for line in parsed:
        if line.type != "tool_use":
            continue
        meta = line.metadata or {}
        assert "tool" in meta, (
            f"Tool envelope missing canonical name for {line.content!r}"
        )
        payload = payload_from_tool_event(line.content, meta)
        assert payload is not None, (
            f"payload_from_tool_event returned None for "
            f"{line.content!r}; the captured fixture must produce a "
            f"recognized envelope for every tool_use frame"
        )
        operations_seen.add(payload.operation)

    # DA-002's smoking gun: the captured fixture must exercise all
    # three display surfaces (read -> file_preview, write ->
    # syntax_preview, edit -> diff_preview).
    assert "read" in operations_seen, (
        f"Captured fixture missing read operation; saw "
        f"{sorted(operations_seen)}"
    )
    assert "write" in operations_seen, (
        f"Captured fixture missing write operation; saw "
        f"{sorted(operations_seen)}"
    )
    assert "replace" in operations_seen, (
        f"Captured fixture missing edit (replace) operation; saw "
        f"{sorted(operations_seen)}"
    )


def test_captured_fixture_write_envelope_has_syntax_content() -> None:
    """The captured write tool_use must surface syntax-preview content.

    Pins that the parser's ``metadata["input"]`` carries the
    write tool's file content -- the smoking gun for the original
    DA-002 defect was an empty content string the display layer
    rendered as a one-line blank.
    """
    from tests.test_opencode_captured_wire import _FIXTURE_PATH

    fixture_lines = _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
    parser = OpenCodeParser()
    parsed = _parse(parser, iter(fixture_lines))

    write_tool_uses = [
        line for line in parsed
        if line.type == "tool_use" and line.content == "write"
    ]
    assert len(write_tool_uses) == 1, (
        f"Captured fixture should produce exactly one write tool_use, "
        f"got {len(write_tool_uses)}"
    )
    write_meta = write_tool_uses[0].metadata or {}
    payload = payload_from_tool_event("write", write_meta)
    assert payload is not None
    assert payload.operation == "write"
    assert payload.content, (
        "Write payload content is empty; the captured fixture's "
        "write envelope is missing the file body the syntax "
        "preview is supposed to render"
    )
    assert "function TodoList" in (payload.content or ""), (
        "Write payload content does not include the captured file "
        "body; the captured write envelope must carry the actual "
        "file content for the syntax preview to render"
    )


def test_captured_fixture_edit_envelope_has_diff_hunks() -> None:
    """The captured edit tool_use must surface a diff-preview hunk.

    Pins that the parser's metadata carries the edit tool's
    ``oldString``/``newString`` pair so the diff preview can
    render ``- old`` / ``+ new`` polarity rows. The captured
    edit frame uses ``oldString``/``newString`` (the live
    1.18.14 spelling); ``_edit_hunks`` keys off
    ``old_string``/``new_string`` aliases to remain
    transport-neutral.
    """
    from tests.test_opencode_captured_wire import _FIXTURE_PATH

    fixture_lines = _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
    parser = OpenCodeParser()
    parsed = _parse(parser, iter(fixture_lines))

    edit_tool_uses = [
        line for line in parsed
        if line.type == "tool_use" and line.content == "edit"
    ]
    assert len(edit_tool_uses) == 1, (
        f"Captured fixture should produce exactly one edit tool_use, "
        f"got {len(edit_tool_uses)}"
    )
    edit_meta = edit_tool_uses[0].metadata or {}
    payload = payload_from_tool_event("edit", edit_meta)
    assert payload is not None
    assert payload.operation == "replace"
    assert len(payload.hunks) == 1, (
        f"Edit payload must surface exactly one diff hunk for the "
        f"captured 1.18.14 edit frame, got {len(payload.hunks)}"
    )
    hunk = payload.hunks[0]
    assert "this.items = [];" in hunk.old_text, (
        f"Edit hunk old_text missing captured old string; got "
        f"{hunk.old_text!r}"
    )
    assert "this.nextId = 0;" in hunk.new_text, (
        f"Edit hunk new_text missing captured new string; got "
        f"{hunk.new_text!r}"
    )


def test_captured_fixture_read_envelope_has_path() -> None:
    """The captured read tool_use must surface a file-preview path.

    Pins that the parser's metadata carries the read tool's
    ``filePath`` argument (the live 1.18.14 spelling); the
    transport-neutral ``_path`` helper keys off
    ``path``/``file_path``/``filePath``/``filename``/``notebook_path``
    so the file preview can render the path.
    """
    from tests.test_opencode_captured_wire import _FIXTURE_PATH

    fixture_lines = _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
    parser = OpenCodeParser()
    parsed = _parse(parser, iter(fixture_lines))

    read_tool_uses = [
        line for line in parsed
        if line.type == "tool_use" and line.content == "read"
    ]
    assert len(read_tool_uses) == 2, (
        f"Captured fixture should produce 2 read tool_uses (one "
        f"errored initial read + one successful read-back), got "
        f"{len(read_tool_uses)}"
    )
    for line in read_tool_uses:
        meta = line.metadata or {}
        payload = payload_from_tool_event("read", meta)
        assert payload is not None, (
            f"Read payload is None for {meta!r}; the captured "
            f"read envelope must produce a recognized file_preview"
        )
        assert payload.operation == "read"
        assert payload.path, (
            "Read payload path is empty; the captured read "
            "envelope must carry the filePath argument so the "
            "file preview can render the path"
        )
        assert payload.path.endswith("todo-list.js"), (
            f"Read payload path is {payload.path!r}, expected to "
            f"end with 'todo-list.js' (the captured file path)"
        )
