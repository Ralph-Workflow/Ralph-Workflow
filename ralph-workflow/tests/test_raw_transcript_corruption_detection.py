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
