from __future__ import annotations

from pathlib import Path

import pytest

from ralph.display.raw_overflow import (
    RawOverflowLog,
    _forget_raw_overflow_log,
    detect_raw_log_breaks,
    get_or_create_raw_overflow_log,
    raw_log_path_for,
    raw_log_unit_id_for,
)


@pytest.fixture
def isolated_workspace(tmp_path: Path) -> Path:
    yield tmp_path
    for path in (tmp_path / ".agent" / "raw").glob("*.log"):
        _forget_raw_overflow_log(str(path.resolve(strict=False)))


def test_same_path_constructors_share_state(isolated_workspace: Path) -> None:
    unit_id = "agy/gemini-3.6-flash-low"

    first = get_or_create_raw_overflow_log(isolated_workspace, unit_id)
    assert first.append("first writer line\n") is True
    first.flush()

    second = get_or_create_raw_overflow_log(isolated_workspace, unit_id)
    assert second is first, (
        "S-8 / C4 invariant: two ``RawOverflowLog`` constructions against "
        "the same path MUST share state -- post-fix the registry returns "
        "the same instance, not a fresh one."
    )

    second_path = second.path.read_bytes()
    assert b"first writer line\n" in second_path


def test_headless_claude_uses_a_distinct_raw_log_identity(
    isolated_workspace: Path,
) -> None:
    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig

    interactive = AgentConfig(
        cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE, model="haiku"
    )
    headless = AgentConfig(cmd="claude -p", transport=AgentTransport.CLAUDE, model="haiku")
    non_claude = AgentConfig(cmd="agy --print", transport=AgentTransport.AGY, model="flash")

    interactive_path = raw_log_path_for(
        isolated_workspace, raw_log_unit_id_for(interactive), model=interactive.model
    )
    headless_path = raw_log_path_for(
        isolated_workspace, raw_log_unit_id_for(headless), model=headless.model
    )
    assert interactive_path != headless_path
    assert headless_path.name.startswith("claude-headless-")
    assert headless_path.name.endswith("_haiku.log")
    assert raw_log_unit_id_for(non_claude).startswith("agy-")


def test_first_writer_bytes_survive_late_first_writer(isolated_workspace: Path) -> None:
    unit_id = "agy/gemini-3.6-flash-low"

    first = get_or_create_raw_overflow_log(isolated_workspace, unit_id)
    assert first.append("surviving line\n") is True
    first.flush()

    bypass = RawOverflowLog(isolated_workspace, unit_id)
    same_via_registry = get_or_create_raw_overflow_log(isolated_workspace, unit_id)

    assert same_via_registry is first
    assert bypass.path == first.path

    assert first.append("another surviving line\n") is True
    first.flush()
    disk = first.path.read_bytes()
    assert b"surviving line\n" in disk
    assert b"another surviving line\n" in disk


def test_nul_byte_run_is_a_detected_break(isolated_workspace: Path) -> None:
    raw_path = isolated_workspace / ".agent" / "raw" / "agy.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b'{"event":"init","tools":["call_mcp_tool"]}\n'
        b'{"event":"step_update","step_update":{"step_index":0}}\n'
        + (b"\x00" * 4096)
        + b"\xe2\x9c\x93 PASS grep_search . (0.5s)\n"
        + b"\xe2\x84\xb9 INFO agy ...\n"
    )

    breaks = detect_raw_log_breaks(raw_path)

    nul_breaks = [b for b in breaks if b.kind == "NUL_BYTES"]
    assert nul_breaks, (
        "production ``detect_raw_log_breaks`` must surface the "
        "measured 2026-08-06 NUL-hole as a NUL_BYTES break"
    )
    assert nul_breaks[0].offset > 0, "the break offset must name where the hole begins"
    assert "NUL-byte run" in nul_breaks[0].detail

    non_jsonl = [b for b in breaks if b.kind == "NON_JSONL"]
    assert non_jsonl, (
        "the rendered text after the NUL hole must be reported as "
        "NON_JSONL breaks, not silently parsed past"
    )


def test_rendered_text_after_nul_is_unparseable_jsonl(isolated_workspace: Path) -> None:
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
    raw_path = isolated_workspace / ".agent" / "raw" / "agy.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b'{"event":"init","tools":["call_mcp_tool"]}\n'
        b'{"event":"step_update","step_update":{"step_index":0}}\n'
        b'{"event":"result","result":{"status":"SUCCESS"}}\n'
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_absent_raw_log_has_no_breaks(isolated_workspace: Path) -> None:
    raw_path = isolated_workspace / ".agent" / "raw" / "agy.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    assert detect_raw_log_breaks(raw_path) == []


def test_detect_smoke_errors_surfaces_corrupted_raw_transcript(
    isolated_workspace: Path,
) -> None:
    from ralph.agents.invoke import InvokeOptions
    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
    from ralph.display.context import make_display_context
    from ralph.pipeline.plumbing import smoke_plumbing as smoke_plumbing_module
    from ralph.pipeline.plumbing.smoke_run_params import SmokeRunParams

    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)

    from ralph.display.raw_overflow import raw_log_path_for, raw_log_unit_id_for

    raw_path = raw_log_path_for(isolated_workspace, raw_log_unit_id_for(config), model=config.model)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b'{"event":"init","tools":["call_mcp_tool"]}\n' + (b"\x00" * 512))

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
    from ralph.config.enums import AgentTransport

    raw_path = isolated_workspace / ".agent" / "raw" / "agy.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        (
            '{"event":"init","tools":["call_mcp_tool"]}\n'
            '{"event":"step_update","step_update":{"step_index":8,"state":"DONE",'
            '"step_type":"tool","tool_name":"call_mcp_tool"}}\n'
            "\u2713 PASS \u21b3 call_mcp_tool (Arguments={'artifact_type': "
            '\'smoke_test_result\'}) {"artifact_type": "smoke_test_result", '
            '"valid": true}\n'
            '{"event":"result","result":{"status":"SUCCESS"}}\n'
        ).encode("utf-8")
    )

    assert detect_raw_log_breaks(raw_path, transport=AgentTransport.AGY) == []


def test_agy_status_prefix_is_not_exempt_on_a_jsonl_transport(
    isolated_workspace: Path,
) -> None:
    from ralph.config.enums import AgentTransport

    raw_path = isolated_workspace / ".agent" / "raw" / "codex.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        (
            '{"type":"item.completed","item":{"id":"item_18"}}\n'
            "\u2713 PASS \u21b3 exec (command=make verify) Exit code: 0\n"
        ).encode("utf-8")
    )

    breaks = detect_raw_log_breaks(raw_path, transport=AgentTransport.CODEX)

    assert [b.kind for b in breaks] == ["NON_JSONL"]


def test_line_embedding_marker_text_is_still_a_break(isolated_workspace: Path) -> None:
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


def _write_claude_raw_log(raw_path: Path, *lines: str) -> None:
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes("".join(f"{line}\n" for line in lines).encode("utf-8"))


def test_reported_claude_session_id_line_is_not_a_break(isolated_workspace: Path) -> None:
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "Session ID: 28ee58c0-0614-474f-b609-80cc6c252f90",
        '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}',
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_claude_session_ready_banner_is_not_a_break(isolated_workspace: Path) -> None:
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "Claude session ready. Session ID: pty-banner-42",
        '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}',
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_claude_resume_line_is_not_a_break(isolated_workspace: Path) -> None:
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "Resume this session with --resume pty-session-99",
        '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}',
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_claude_explicit_completion_marker_is_not_a_break(isolated_workspace: Path) -> None:
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "Task declared complete: session_id=pty-session-1, summary='done', timestamp=1",
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_valid_claude_transcript_with_tool_activity_is_not_corrupted(
    isolated_workspace: Path,
) -> None:
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "Session ID: 28ee58c0-0614-474f-b609-80cc6c252f90",
        '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"toolu_read","name":"mcp__ralph__read_file","input":{"path":"PROMPT.md"}}]}}',
        '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"toolu_read","content":[{"type":"text","text":"prompt contents"}]}]}}',
        "[claude turn boundary]",
        "Task declared complete: session_id=28ee58c0-0614-474f-b609-80cc6c252f90, summary='done', timestamp=1",
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
    ],
)
def test_non_canonical_session_lines_are_still_breaks(isolated_workspace: Path, line: str) -> None:
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(raw_path, line)

    breaks = detect_raw_log_breaks(raw_path)
    non_jsonl = [b for b in breaks if b.kind == "NON_JSONL"]
    assert non_jsonl, f"line {line!r} must remain a NON_JSONL break"


def test_ansi_wrapped_session_id_line_is_not_a_break(isolated_workspace: Path) -> None:
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "\x1b[2mSession ID: 28ee58c0-0614-474f-b609-80cc6c252f90\x1b[22m",
        '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}',
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_ansi_wrapped_session_ready_banner_is_not_a_break(isolated_workspace: Path) -> None:
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "\x1b[32mClaude session ready. Session ID: pty-banner-42\x1b[0m",
        '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}',
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_ansi_wrapped_resume_line_is_not_a_break(isolated_workspace: Path) -> None:
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
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "\x1b[1mTask declared complete: session_id=pty-session-1, "
        "summary='done', timestamp=1699999999\x1b[22m",
    )

    assert detect_raw_log_breaks(raw_path) == []


def test_ansi_noise_only_line_is_not_a_break(isolated_workspace: Path) -> None:
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
    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    _write_claude_raw_log(
        raw_path,
        "\x1b[2mSome Session ID: abc-123\x1b[22m",
    )

    breaks = detect_raw_log_breaks(raw_path)
    non_jsonl = [b for b in breaks if b.kind == "NON_JSONL"]
    assert non_jsonl, "non-canonical styled session mention must remain NON_JSONL"


def test_interactive_pty_transport_skips_non_jsonl_for_visible_tool_output(
    isolated_workspace: Path,
) -> None:
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
    from ralph.config.enums import AgentTransport

    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b"Session ID:\n28ee58c0-0614-474f-b609-80cc6c252f90\n")

    breaks = detect_raw_log_breaks(raw_path, transport=AgentTransport.CLAUDE)
    non_jsonl = [item for item in breaks if item.kind == "NON_JSONL"]
    assert len(non_jsonl) == 2, breaks


def test_headless_claude_transport_keeps_strict_jsonl(
    isolated_workspace: Path,
) -> None:
    from ralph.config.enums import AgentTransport

    raw_path = isolated_workspace / ".agent" / "raw" / "claude.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(b'{"type":"system","message":"hello"}\nvisible text line\n')

    breaks = detect_raw_log_breaks(raw_path, transport=AgentTransport.CLAUDE)
    non_jsonl = [b for b in breaks if b.kind == "NON_JSONL"]
    assert non_jsonl, f"expected NON_JSONL break for headless Claude, got {breaks}"
