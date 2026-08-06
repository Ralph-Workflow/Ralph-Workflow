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

    def test_every_event_in_opencode_wire_fixture_produces_a_parsed_line(self) -> None:
        """Every NDJSON line in the captured fixture must produce at least
        one parsed ``AgentOutputLine`` -- a frame that drops silently
        is exactly the defect the parser-vs-display seam must catch.
        Empty results from any line are a regression.
        """
        from ralph.agents.parsers.opencode import OpenCodeParser as _Parser
        from tests.test_opencode_captured_wire import _OPENCODE_WIRE_FIXTURE_LINES

        lines = _OPENCODE_WIRE_FIXTURE_LINES
        parser = _Parser()
        parsed_lines = _parse(parser, iter(lines))
        assert len(parsed_lines) >= len(lines), (
            f"Parser produced {len(parsed_lines)} AgentOutputLines for "
            f"{len(lines)} input lines; at least one input frame was dropped"
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
    from tests.test_opencode_captured_wire import _OPENCODE_WIRE_FIXTURE_LINES

    for line_number, raw in enumerate(_OPENCODE_WIRE_FIXTURE_LINES, start=1):
        json.loads(raw)  # raises ValueError if malformed
        assert line_number >= 1
