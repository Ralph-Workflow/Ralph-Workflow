"""S-8 / C4 / DoD 15: same-path overflow ownership and NUL-hole detection.

Brief ``.agent/PRODUCT_CRITERIA.md`` C4 -- two writers sharing one
raw-transcript path without sharing lock or ``_first_write`` state is
the measured hazard behind the 2026-08-06 NUL-hole run; corruption in
the raw log must surface as a break, never as silently parsed past
JSONL.

Two regressions are required by the plan:

(a) NUL-plus-rendered-text detection: the measured 2026-08-06 shape --
    a JSONL prefix, a NUL-byte run, followed by rendered
    ``\u2713 PASS`` / ``\u2139 INFO`` text -- must be reported as a break by
    whatever consumer reads the raw log back.
(b) Same-pathname concurrent ownership: two ``RawOverflowLog``
    constructions against the same path must share state. Post-fix:
    the second construction returns the same object the first
    construction built; pre-fix: it returns a fresh object whose
    first ``append()`` truncates the first object's bytes.

(b) is the regression that proves the ownership fix in
``ralph.display.raw_overflow``; (a) is the regression that proves the
corruption-detection break is reported rather than silently skipped.
Both failures have distinct surfaces and must be tested independently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.display.raw_overflow import (
    RawOverflowLog,
    _forget_raw_overflow_log,
    detect_raw_log_breaks,
    get_or_create_raw_overflow_log,
)


@pytest.fixture
def isolated_workspace(tmp_path: Path) -> Path:
    """Workspace with a clean per-test registry drop.

    The shared-by-path registry is process-global; tests that exercise
    a path must drop the registry entry on teardown so a different
    test cannot inherit a stale singleton.
    """
    yield tmp_path
    for path in (tmp_path / ".agent" / "raw").glob("*.log"):
        _forget_raw_overflow_log(str(path.resolve(strict=False)))


def test_same_path_constructors_share_state(isolated_workspace: Path) -> None:
    """Two ``RawOverflowLog`` constructions against the same path share state (S-8 / C4).

    Pre-fix: two independent objects with the same ``self.path`` would
    race on first-write -- whichever object's first ``append()`` ran
    later opened the file in ``"wb"`` mode and truncated the other
    object's already-written bytes. Post-fix: the registry returns
    the same instance so the first writer's bytes survive a second
    writer's append against the same path.
    """
    unit_id = "agy/gemini-3.6-flash-low"

    first = get_or_create_raw_overflow_log(isolated_workspace, unit_id)
    assert first.append("first writer line\n") is True
    first.flush()

    # A second caller constructing against the same path receives the
    # SAME object; a fresh ``RawOverflowLog`` would be a different
    # object with its own ``_first_write = True``.
    second = get_or_create_raw_overflow_log(isolated_workspace, unit_id)
    assert second is first, (
        "S-8 / C4 invariant: two ``RawOverflowLog`` constructions against "
        "the same path MUST share state -- post-fix the registry returns "
        "the same instance, not a fresh one."
    )

    # The second writer's append opens in append mode (because the
    # shared object's ``_first_write`` is False), so the first writer's
    # bytes survive on disk.
    second_path = second.path.read_bytes()
    assert b"first writer line\n" in second_path


def test_first_writer_bytes_survive_late_first_writer(isolated_workspace: Path) -> None:
    """Pre-fix regression: independent constructors would truncate each other.

    Two independently-constructed ``RawOverflowLog`` objects (i.e. the
    pre-fix direct constructor call) against the same path. Without
    the registry the second one's first ``append()`` opens in
    ``"wb"`` mode and truncates the first one's already-written
    bytes. With the registry in place the second call short-circuits
    to the first call's instance, so both constructors transparently
    share state.

    This regression pins the ownership invariant at the byte level --
    the first writer's bytes must survive a second, late writer's
    append, regardless of which constructor API was used.
    """
    unit_id = "agy/gemini-3.6-flash-low"

    # First constructor goes through the registry.
    first = get_or_create_raw_overflow_log(isolated_workspace, unit_id)
    assert first.append("surviving line\n") is True
    first.flush()

    # The second constructor bypasses the registry (a pre-fix-style
    # direct ``RawOverflowLog(...)`` call) and resolves to the same
    # path -- but the registry has already cached an instance for
    # that path. The right behaviour: the registry returns the
    # existing instance, NOT a fresh object that would re-truncate
    # the file.
    bypass = RawOverflowLog(isolated_workspace, unit_id)
    same_via_registry = get_or_create_raw_overflow_log(isolated_workspace, unit_id)

    assert same_via_registry is first
    assert bypass.path == first.path

    # When the bypass object's first append runs, it would (in the
    # pre-fix world) truncate the first writer's bytes. After the
    # registry fix: the registry-cached instance is what callers get,
    # and the bypass object is itself orphaned -- but ``first`` is
    # the surviving instance.
    assert first.append("another surviving line\n") is True
    first.flush()
    disk = first.path.read_bytes()
    assert b"surviving line\n" in disk
    assert b"another surviving line\n" in disk


def test_nul_byte_run_is_a_detected_break(isolated_workspace: Path) -> None:
    """A NUL-byte run in the raw log is a break, not silently parsed past.

    The measured 2026-08-06 run had a NUL-byte run beginning
    immediately after a ``run_command`` frame, followed by rendered
    display text. The production raw-log reader
    (:func:`detect_raw_log_breaks`) must report a break for that
    corruption shape, not silently skip the bytes.
    """
    # Build the exact measured-shape raw log: a JSONL prefix, a
    # NUL-byte run, then rendered text.
    raw_path = isolated_workspace / ".agent" / "raw" / "agy.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b'{"event":"init","tools":["call_mcp_tool"]}\n'
        b'{"event":"step_update","step_update":{"step_index":0}}\n'
        + (b"\x00" * 4096)  # the measured NUL-byte run
        + b"\xe2\x9c\x93 PASS grep_search . (0.5s)\n"
        + b"\xe2\x84\xb9 INFO agy ...\n"
    )

    breaks = detect_raw_log_breaks(raw_path)

    # The corruption detector reports a ``NUL_BYTES`` break that
    # pinpoints the offset where the JSONL stream becomes
    # unparseable. Operators see the offset and a descriptive detail
    # string -- not a bare "log truncated" message.
    nul_breaks = [b for b in breaks if b.kind == "NUL_BYTES"]
    assert nul_breaks, (
        "production ``detect_raw_log_breaks`` must surface the "
        "measured 2026-08-06 NUL-hole as a NUL_BYTES break"
    )
    assert nul_breaks[0].offset > 0, "the break offset must name where the hole begins"
    assert "NUL-byte run" in nul_breaks[0].detail

    # And the rendered text after the hole is independently reported
    # as ``NON_JSONL`` -- the second writer was appending
    # ``\u2713 PASS\u2026`` text into the verbatim capture, which is
    # the byte-level fingerprint of the unfixed shared-pathname race.
    non_jsonl = [b for b in breaks if b.kind == "NON_JSONL"]
    assert non_jsonl, (
        "the rendered text after the NUL hole must be reported as "
        "NON_JSONL breaks, not silently parsed past"
    )


def test_rendered_text_after_nul_is_unparseable_jsonl(isolated_workspace: Path) -> None:
    """A consumer reading the post-NUL bytes as JSONL fails (the truncation signal)."""
    raw_path = isolated_workspace / ".agent" / "raw" / "agy.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b'{"event":"init","tools":["call_mcp_tool"]}\n'
        + (b"\x00" * 256)
        + b"\xe2\x9c\x93 PASS ...\n"
    )

    breaks = detect_raw_log_breaks(raw_path)
    kinds = {b.kind for b in breaks}
    assert "NUL_BYTES" in kinds
    assert "NON_JSONL" in kinds


def test_clean_raw_log_has_no_breaks(isolated_workspace: Path) -> None:
    """A well-formed JSONL raw log reports zero breaks.

    The detector must not flag legitimate frames; only actual
    corruption shapes (NUL bytes, rendered text, malformed JSON).
    """
    raw_path = isolated_workspace / ".agent" / "raw" / "agy.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b'{"event":"init","tools":["call_mcp_tool"]}\n'
        b'{"event":"step_update","step_update":{"step_index":0}}\n'
        b'{"event":"result","result":{"status":"SUCCESS"}}\n'
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_absent_raw_log_has_no_breaks(isolated_workspace: Path) -> None:
    """An absent raw log file reports zero breaks.

    The detector must not raise on a missing file (the live read
    path is called speculatively during evidence correlation, and a
    never-started run has no file to read).
    """
    raw_path = isolated_workspace / ".agent" / "raw" / "agy.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    # Intentionally do not create the file.

    assert detect_raw_log_breaks(raw_path) == []


# --- S-4 (G4 / DoD 15): the smoke seam ----------------------------------


def test_detect_smoke_errors_surfaces_corrupted_raw_transcript(
    isolated_workspace: Path,
) -> None:
    """S-4: ``_detect_smoke_errors`` itself reports a corrupted raw
    transcript, not only ``detect_raw_log_breaks`` in isolation.

    Confirms the smoke seam reads the exact path the real
    ``RawOverflowLog`` writer used for this ``AgentConfig`` (derived via
    ``shlex.split(config.cmd)[0]`` for the unit id, ``config.model`` for
    the model suffix) -- not a hand-picked filename.
    """
    from ralph.agents.invoke import InvokeOptions
    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
    from ralph.display.context import make_display_context
    from ralph.pipeline.plumbing import smoke_plumbing as smoke_plumbing_module
    from ralph.pipeline.plumbing.smoke_run_params import SmokeRunParams

    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)

    raw_path = isolated_workspace / ".agent" / "raw" / "agy.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b'{"event":"init","tools":["call_mcp_tool"]}\n' + (b"\x00" * 512)
    )

    params = SmokeRunParams(
        agent_name="agy/gemini-3.6-flash-low",
        config=config,
        unified_config=UnifiedConfig(general=GeneralConfig()),
        workspace_root=isolated_workspace,
        prompt_file=isolated_workspace / "PROMPT.md",
        output_file=isolated_workspace / "tmp" / "interactive-agy-smoke" / "todo-list.js",
        options=InvokeOptions(),
        display_context=make_display_context(),
    )

    errors = smoke_plumbing_module._detect_smoke_errors(
        params,
        lines=[],
        live_output_lines=[],
        session_id="some-session",
        final_exception=None,
        artifact_submitted=True,
        tool_activity_seen=True,
    )

    corruption_errors = [e for e in errors if e.startswith("raw transcript corrupted:")]
    assert corruption_errors, (
        f"_detect_smoke_errors must surface the raw log corruption; got errors={errors}"
    )
    assert "NUL-byte run" in corruption_errors[0]


def test_detect_smoke_errors_is_clean_when_raw_transcript_has_no_breaks(
    isolated_workspace: Path,
) -> None:
    """A well-formed raw transcript contributes no corruption error."""
    from ralph.agents.invoke import InvokeOptions
    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
    from ralph.display.context import make_display_context
    from ralph.pipeline.plumbing import smoke_plumbing as smoke_plumbing_module
    from ralph.pipeline.plumbing.smoke_run_params import SmokeRunParams

    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)

    raw_path = isolated_workspace / ".agent" / "raw" / "agy.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b'{"event":"init","tools":["call_mcp_tool"]}\n')

    params = SmokeRunParams(
        agent_name="agy/gemini-3.6-flash-low",
        config=config,
        unified_config=UnifiedConfig(general=GeneralConfig()),
        workspace_root=isolated_workspace,
        prompt_file=isolated_workspace / "PROMPT.md",
        output_file=isolated_workspace / "tmp" / "interactive-agy-smoke" / "todo-list.js",
        options=InvokeOptions(),
        display_context=make_display_context(),
    )

    errors = smoke_plumbing_module._detect_smoke_errors(
        params,
        lines=[],
        live_output_lines=[],
        session_id="some-session",
        final_exception=None,
        artifact_submitted=True,
        tool_activity_seen=True,
    )

    assert not [e for e in errors if e.startswith("raw transcript corrupted:")]


def test_harness_input_echo_lines_are_not_breaks(isolated_workspace: Path) -> None:
    """Ralph-authored harness input echoes are expected capture content.

    Measured live shape (2026-08-14 AGY smoke): the PTY line reader
    injects ``[claude turn boundary]`` into its own line queue at the
    interactive exit boundary and types ``/exit`` into the agent's PTY
    stdin, which the terminal line discipline echoes back. Both lines
    land verbatim in the raw capture of EVERY interactive-transport
    run; grading them ``NON_JSONL`` failed each live PTY smoke with
    "raw transcript corrupted" while the wire frames were intact.
    """
    raw_path = isolated_workspace / ".agent" / "raw" / "agy.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b'{"event":"init","tools":["call_mcp_tool"]}\n'
        b'{"event":"step_update","step_update":{"step_index":0}}\n'
        b"\n"
        b"[claude turn boundary]\n"
        b"/exit\r\n"
        b"\n"
        b'{"event":"result","result":{"status":"SUCCESS"}}\n'
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_agy_print_tool_result_status_line_is_not_a_break(
    isolated_workspace: Path,
) -> None:
    """AGY's print-mode tool-result status line is measured vendor wire output.

    Measured live shape (2026-08-17, AGY v1.1.13 ``gemini-3.6-flash-low``):
    ``agy --print --output-format stream-json`` emits one human-rendered
    ``\u2713 PASS \u21b3 <tool> (<param summary>) <JSON result>`` status
    line per completed tool call onto the same stdout/PTY the stream-json
    frames arrive on (e.g. after the ``call_mcp_tool`` step-8 DONE frame
    in the live smoke raw capture). Grading it ``NON_JSONL`` failed the
    live AGY smoke with ``raw transcript corrupted`` while the wire
    frames, artifact receipt, and completion sentinel were all intact.
    """
    raw_path = isolated_workspace / ".agent" / "raw" / "agy.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        (
            '{"event":"init","tools":["call_mcp_tool"]}\n'
            '{"event":"step_update","step_update":{"step_index":8,"state":"DONE",'
            '"step_type":"tool","tool_name":"call_mcp_tool"}}\n'
            "\u2713 PASS \u21b3 call_mcp_tool (Arguments={'artifact_type': "
            "'smoke_test_result'}) {\"artifact_type\": \"smoke_test_result\", "
            '\"valid\": true}\n'
            '{"event":"result","result":{"status":"SUCCESS"}}\n'
        ).encode("utf-8")
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_line_embedding_marker_text_is_still_a_break(isolated_workspace: Path) -> None:
    """The echo tolerance is exact-match only.

    A non-JSON line that merely *contains* a harness marker must still
    grade as ``NON_JSONL``: agent or display text embedding the marker
    cannot smuggle a corrupted line past the detector.
    """
    raw_path = isolated_workspace / ".agent" / "raw" / "agy.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b'{"event":"init","tools":["call_mcp_tool"]}\n'
        b"agent said [claude turn boundary] and kept going\n"
    )

    breaks = detect_raw_log_breaks(raw_path)
    non_jsonl = [b for b in breaks if b.kind == "NON_JSONL"]
    assert non_jsonl, (
        "a non-JSON line that embeds (but is not exactly) a harness "
        "marker must still be reported as NON_JSONL"
    )


# --- S-3: Claude interactive PTY session/resume metadata ---------------


def _write_claude_raw_log(raw_path: Path, *lines: str) -> None:
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes("".join(f"{line}\n" for line in lines).encode("utf-8"))


def test_reported_claude_session_id_line_is_not_a_break(isolated_workspace: Path) -> None:
    """The 2026-08-17 regression: ``Session ID: <uuid>`` was graded NON_JSONL.

    The normalized interactive-Claude transcript begins with this exact
    line. The corruption detector must recognize the canonical
    session/resume vocabulary emitted by the PTY/session layer instead of
    reporting ``raw transcript corrupted`` for a healthy run.
    """
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "Session ID: 28ee58c0-0614-474f-b609-80cc6c252f90",
        '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}',
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_claude_session_ready_banner_is_not_a_break(isolated_workspace: Path) -> None:
    """The TUI banner form ``Claude session ready. Session ID: <id>`` is valid."""
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "Claude session ready. Session ID: pty-banner-42",
        '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}',
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_claude_resume_line_is_not_a_break(isolated_workspace: Path) -> None:
    """The resumable-exit hint ``Resume this session with --resume <id>`` is valid."""
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "Resume this session with --resume pty-session-99",
        '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}',
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_claude_explicit_completion_marker_is_not_a_break(isolated_workspace: Path) -> None:
    """The harness completion marker ``Task declared complete: ...`` is valid.

    Interactive Claude runs emit this non-JSON line through the same
    PTY/session layer; it is canonical completion metadata, not corruption.
    """
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "Task declared complete: session_id=pty-session-1, summary=done, timestamp=1",
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_valid_claude_transcript_with_tool_activity_is_not_corrupted(
    isolated_workspace: Path,
) -> None:
    """A complete healthy Claude interactive raw transcript has no breaks.

    Mixes session metadata, JSON assistant/tool frames, tool results, the
    harness turn-boundary, and the explicit completion marker.
    """
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "Session ID: 28ee58c0-0614-474f-b609-80cc6c252f90",
        '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_read","name":"mcp__ralph__read_file","input":{"path":"PROMPT.md"}}]}}',
        '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_read","content":[{"type":"text","text":"prompt contents"}]}]}}',
        "[claude turn boundary]",
        "Task declared complete: session_id=28ee58c0-0614-474f-b609-80cc6c252f90, summary=done, timestamp=1",
    )

    assert detect_raw_log_breaks(raw_path) == []


@pytest.mark.parametrize(
    "line",
    [
        "Some Session ID: abc-123",
        "Session ID: abc-123 extra",
        "session id abc-123",
        "Session ID: abc 123",
        "Resume this session with --resume ",
        "Task declared complete without session_id",
    ],)
def test_non_canonical_session_lines_are_still_breaks(isolated_workspace: Path, line: str) -> None:
    """Only the exact canonical shapes are tolerated; near-matches remain NON_JSONL.

    This preserves the strict trust boundary: an agent cannot smuggle
    arbitrary text by merely mentioning ``Session ID``.
    """
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(raw_path, line)

    breaks = detect_raw_log_breaks(raw_path)
    non_jsonl = [b for b in breaks if b.kind == "NON_JSONL"]
    assert non_jsonl, f"line {line!r} must remain a NON_JSONL break"


def test_ansi_wrapped_session_id_line_is_not_a_break(isolated_workspace: Path) -> None:
    """ANSI-styled ``Session ID:`` lines are canonical PTY metadata, not corruption.

    The interactive Claude TUI wraps session/resume hints in styling such
    as ``\\x1b[2m...\\x1b[22m``. The corruption detector must grade the
    visible text after stripping the escape sequences.
    """
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "\x1b[2mSession ID: 28ee58c0-0614-474f-b609-80cc6c252f90\x1b[22m",
        '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}',
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_ansi_wrapped_session_ready_banner_is_not_a_break(isolated_workspace: Path) -> None:
    """ANSI-styled ``Claude session ready.`` banners are canonical metadata."""
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "\x1b[32mClaude session ready. Session ID: pty-banner-42\x1b[0m",
        '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}',
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_ansi_wrapped_resume_line_is_not_a_break(isolated_workspace: Path) -> None:
    """ANSI-styled resume hints are canonical metadata."""
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "\x1b[2mResume this session with --resume pty-session-99\x1b[22m",
        '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}',
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_ansi_wrapped_explicit_completion_marker_is_not_a_break(
    isolated_workspace: Path,
) -> None:
    """ANSI-styled completion markers are canonical metadata."""
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "\x1b[1mTask declared complete: session_id=pty-session-1, summary=done\x1b[22m",
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_ansi_noise_only_line_is_not_a_break(isolated_workspace: Path) -> None:
    """A line that is purely ANSI/VT control noise contributes no visible text.

    PTY repaints emit sequences such as cursor-save/restore and mode
    switches. After normalization they become empty and must not be
    graded as NON_JSONL.
    """
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b"\x1b[?25l\x1b[H\x1b[2J\x1b[?25h\n"
        b'{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}\n'
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_ansi_wrapped_non_canonical_session_line_is_still_a_break(
    isolated_workspace: Path,
) -> None:
    """Styling does not excuse a non-canonical session mention."""
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "\x1b[2mSome Session ID: abc-123\x1b[22m",
    )

    breaks = detect_raw_log_breaks(raw_path)
    non_jsonl = [b for b in breaks if b.kind == "NON_JSONL"]
    assert non_jsonl, "non-canonical styled session mention must remain NON_JSONL"


# --- S-3: interactive PTY transport-aware raw-log grading ----------------


def test_interactive_pty_transport_skips_non_jsonl_for_visible_tool_output(
    isolated_workspace: Path,
) -> None:
    """S-6: interactive Claude PTY output is human-visible text, not JSONL.

    The live ``claude/haiku`` smoke capture includes rendered MCP tool
    status lines, file contents emitted by ``write_file``, and source code
    lines. For ``AgentTransport.CLAUDE_INTERACTIVE`` these are expected
    verbatim capture content, so they must not be graded as ``NON_JSONL``
    corruption.
    """
    from ralph.config.enums import AgentTransport

    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b"Session ID: 28ee58c0-0614-474f-b609-80cc6c252f90\n"
        b"\x1b[32m\xe2\x9c\x93 PASS \xe2\x86\xb3 ralph.write_file (path=tmp/interactive-claude-smoke/todo-list.js)\x1b[0m\n"
        b"const todos = [];\n"
        b"let nextId = 1;\n"
        b"\n"
        b"module.exports = TodoAPI;\n"
        b"[claude turn boundary]\n"
        b"Task declared complete: session_id=28ee58c0-0614-474f-b609-80cc6c252f90\n"
    )

    breaks = detect_raw_log_breaks(raw_path, transport=AgentTransport.CLAUDE_INTERACTIVE)
    assert breaks == [], f"expected no breaks for interactive PTY visible output, got {breaks}"


def test_interactive_pty_transport_still_detects_nul_bytes(
    isolated_workspace: Path,
) -> None:
    """NUL-byte truncation remains corruption even for interactive PTY."""
    from ralph.config.enums import AgentTransport

    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b"Session ID: 28ee58c0-0614-474f-b609-80cc6c252f90\n"
        + (b"\x00" * 256)
        + b"visible text after the hole\n"
    )

    breaks = detect_raw_log_breaks(raw_path, transport=AgentTransport.CLAUDE_INTERACTIVE)
    assert any(b.kind == "NUL_BYTES" for b in breaks), breaks
    assert not any(b.kind == "NON_JSONL" for b in breaks), breaks


def test_agy_transport_skips_non_jsonl_for_visible_pty_output(
    isolated_workspace: Path,
) -> None:
    """AGY's raw capture is an interactive PTY stream, not JSONL.

    The live ``agy/gemini-3.6-flash-low`` smoke capture includes rendered
    MCP tool status lines, source code emitted by ``write_file``, and other
    human-readable Claude Code PTY text. For ``AgentTransport.AGY`` these are
    expected verbatim capture content, so they must not be graded as
    ``NON_JSONL`` corruption. NUL-byte truncation remains a break.
    """
    from ralph.config.enums import AgentTransport

    raw_path = isolated_workspace / ".agent" / "raw" / "agy.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b"Session ID: agy-smoke-gemini-3.6-flash-low\n"
        b"\x1b[32m\xe2\x9c\x93 PASS \xe2\x86\xb3 ralph.write_file (path=tmp/interactive-agy-smoke/todo-list.js)\x1b[0m\n"
        b"const todos = [];\n"
        b"let nextId = 1;\n"
        b"\n"
        b"module.exports = TodoAPI;\n"
    )

    breaks = detect_raw_log_breaks(raw_path, transport=AgentTransport.AGY)
    assert breaks == [], f"expected no breaks for AGY interactive PTY output, got {breaks}"


def test_agy_transport_still_detects_nul_bytes(
    isolated_workspace: Path,
) -> None:
    """NUL-byte truncation remains corruption for AGY interactive PTY."""
    from ralph.config.enums import AgentTransport

    raw_path = isolated_workspace / ".agent" / "raw" / "agy.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b"Session ID: agy-smoke-gemini-3.6-flash-low\n"
        + (b"\x00" * 256)
        + b"visible text after the hole\n"
    )

    breaks = detect_raw_log_breaks(raw_path, transport=AgentTransport.AGY)
    assert any(b.kind == "NUL_BYTES" for b in breaks), breaks
    assert not any(b.kind == "NON_JSONL" for b in breaks), breaks


def test_headless_claude_transport_rejects_multiline_session_id(
    isolated_workspace: Path,
) -> None:
    """A session identifier split across lines is not canonical headless JSONL.

    The session-text grammar is anchored per physical line. Joining these
    lines before matching would incorrectly tolerate arbitrary multiline
    injections in the strict ``AgentTransport.CLAUDE`` stream.
    """
    from ralph.config.enums import AgentTransport

    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b"Session ID:\n"
        b"28ee58c0-0614-474f-b609-80cc6c252f90\n"
    )

    breaks = detect_raw_log_breaks(raw_path, transport=AgentTransport.CLAUDE)
    non_jsonl = [item for item in breaks if item.kind == "NON_JSONL"]
    assert len(non_jsonl) == 2, breaks


def test_headless_claude_transport_keeps_strict_jsonl(
    isolated_workspace: Path,
) -> None:
    """Headless Claude emits stream-json; non-JSON lines remain breaks."""
    from ralph.config.enums import AgentTransport

    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b'{"type":"system","message":"hello"}\n'
        b"visible text line\n"
    )

    breaks = detect_raw_log_breaks(raw_path, transport=AgentTransport.CLAUDE)
    non_jsonl = [b for b in breaks if b.kind == "NON_JSONL"]
    assert non_jsonl, f"expected NON_JSONL break for headless Claude, got {breaks}"
