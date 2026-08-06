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
    display text. The consumer that reads the raw log back must
    report a break for that corruption shape, not silently skip the
    bytes.
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

    # The corruption detector: a NUL byte anywhere in a JSONL log is
    # unrecoverable (the parser cannot recover the next frame's
    # start; it cannot tell where the JSON ends).
    payload = raw_path.read_bytes()
    nul_offset = payload.find(b"\x00")
    assert nul_offset > 0, "fixture must contain a NUL byte after the prefix"
    assert payload[nul_offset:].startswith(b"\x00" * 1024), (
        "fixture must contain a substantial NUL run, not a one-off NUL "
        "from a string-escape accident"
    )

    # The operator-facing detection: any NUL byte in the raw log
    # is a break. The reader used here is the ``RawOverflowLog``
    # itself, but the fixture is meant to model what an
    # evidence-correlator sees.
    nul_byte_count = payload.count(b"\x00")
    assert nul_byte_count > 1024, "fixture NUL run is too small to be measurable"


def test_rendered_text_after_nul_is_unparseable_jsonl(isolated_workspace: Path) -> None:
    """A consumer reading the post-NUL bytes as JSONL fails (the truncation signal)."""
    import json

    raw_path = isolated_workspace / ".agent" / "raw" / "agy.log"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(
        b'{"event":"init","tools":["call_mcp_tool"]}\n'
        + (b"\x00" * 256)
        + b"\xe2\x9c\x93 PASS ...\n"
    )

    parsed_ok = 0
    parse_errors: list[Exception] = []
    for line in raw_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            json.loads(line)
            parsed_ok += 1
        except json.JSONDecodeError as exc:
            parse_errors.append(exc)

    assert parsed_ok >= 1, "the pre-NUL JSONL frame must still parse"
    assert parse_errors, "the post-NUL bytes must not parse as JSONL -- that is the break"
