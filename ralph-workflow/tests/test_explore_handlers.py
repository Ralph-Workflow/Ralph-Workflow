"""Black-box tests for the explore MCP handlers (index_status + reindex)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.mcp.explore.handlers import (
    build_explore_index,
    handle_ralph_index_status,
    handle_ralph_reindex,
)
from ralph.mcp.explore.store import DEFAULT_INDEX_ROOT


class _FakeSession:
    """Minimal session stub."""

    def __init__(self, explore_index=None):
        self.explore_index = explore_index

    def check_capability(self, capability: str):
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str):
        return {"status": "approved", "path": path}


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root


def _seed_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.py").write_text("x = 1\n")
    (workspace / "b.py").write_text("y = 2\n")
    return workspace


def _decode(result) -> dict:
    return json.loads(result.content[0].text)


def test_index_status_disabled_when_no_handle(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession(explore_index=None)
    result = handle_ralph_index_status(session, _Workspace(workspace), {})
    payload = _decode(result)
    assert payload["enabled"] is False
    assert payload["index_exists"] is False
    assert "generation" in payload
    assert "index_storage_bytes" in payload


def test_index_status_is_side_effect_free_when_no_handle(tmp_path: Path) -> None:
    """AC-03 contract: index_status must not create SQLite files
    or ``.agent/ralph-explore/`` when no handle exists.
    """
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession(explore_index=None)
    index_dir = workspace / ".agent" / "ralph-explore"
    assert not index_dir.exists()
    result = handle_ralph_index_status(session, _Workspace(workspace), {})
    payload = _decode(result)
    assert payload["enabled"] is False
    assert payload["index_exists"] is False
    # No directory or file was created on disk.
    assert not index_dir.exists()
    assert not (index_dir / "index.sqlite").exists()


def test_index_status_reports_existing_disk_state_without_handle(
    tmp_path: Path,
) -> None:
    """AC-03: a side-effect-free status check must report an
    existing on-disk persisted index even when no handle is attached.
    """
    workspace = _seed_workspace(tmp_path)
    # First call from a fresh handle builds the index on disk.
    handle = build_explore_index(workspace)
    import contextlib

    with contextlib.suppress(Exception):
        # Drop the handle but keep the persisted index files.
        handle.store.close()
    index_dir = workspace / ".agent" / "ralph-explore"
    assert (index_dir / "index.sqlite").exists()
    session = _FakeSession(explore_index=None)
    result = handle_ralph_index_status(session, _Workspace(workspace), {})
    payload = _decode(result)
    assert payload["enabled"] is False
    assert payload["index_exists"] is True, payload


def test_index_status_returns_expected_fields(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        result = handle_ralph_index_status(session, _Workspace(workspace), {})
        payload = _decode(result)
        for field in (
            "enabled",
            "index_exists",
            "generation",
            "indexed_at",
            "files_indexed",
            "files_stale",
            "last_job",
            "capabilities",
            "graph_backend",
            "dirty_paths_count",
            "cold_index_required",
            "last_refresh_kind",
            "is_stale",
            "stale_paths_count",
            "index_storage_bytes",
            "managed_ignore_rule_present",
            "managed_ignore_rule_repair",
        ):
            assert field in payload, f"missing field: {field}"
        assert payload["enabled"] is True
        assert payload["graph_backend"] == "sqlite"
    finally:
        handle.store.close()


def test_index_status_reports_managed_ignore_rule(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    (workspace / ".gitignore").write_text(".agent/\n")
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        result = handle_ralph_index_status(session, _Workspace(workspace), {})
        payload = _decode(result)
        assert payload["managed_ignore_rule_present"] is True
    finally:
        handle.store.close()


def test_index_status_managed_ignore_rule_absent(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    # No .gitignore at all.
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        result = handle_ralph_index_status(session, _Workspace(workspace), {})
        payload = _decode(result)
        assert payload["managed_ignore_rule_present"] is False
    finally:
        handle.store.close()


def test_index_status_exposes_managed_ignore_repair_when_present(
    tmp_path: Path,
) -> None:
    """AC-04: when the rule is present, the repair field marks
    itself as not required.
    """
    workspace = _seed_workspace(tmp_path)
    (workspace / ".gitignore").write_text(".agent/\n")
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        result = handle_ralph_index_status(session, _Workspace(workspace), {})
        payload = _decode(result)
        assert "managed_ignore_rule_repair" in payload
        repair = payload["managed_ignore_rule_repair"]
        assert repair["required"] is False
        assert repair["action"] == "none"
        assert repair["reason"] == "managed_ignore_rule_present"
    finally:
        handle.store.close()


def test_index_status_exposes_managed_ignore_repair_when_absent(
    tmp_path: Path,
) -> None:
    """AC-04: when the rule is missing, the repair field carries
    the next Ralph seeding instruction so callers can fix the
    coverage without guessing.
    """
    workspace = _seed_workspace(tmp_path)
    # No .gitignore at all.
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        result = handle_ralph_index_status(session, _Workspace(workspace), {})
        payload = _decode(result)
        assert "managed_ignore_rule_repair" in payload
        repair = payload["managed_ignore_rule_repair"]
        assert repair["required"] is True
        assert repair["action"] == "seed_default_gitignore"
        assert repair["reason"] == "managed_ignore_rule_missing"
        assert repair["target_file"].endswith(".gitignore")
        assert ".agent/" in repair["patterns_to_append"]
        assert repair["next_command"] == "ralph"
        assert repair.get("description")
    finally:
        handle.store.close()


def test_index_status_disabled_payload_also_carries_repair_field(
    tmp_path: Path,
) -> None:
    """AC-04: the disabled payload (no handle) reports the same
    repair field so callers do not have to special-case the
    enabled=False path.
    """
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession(explore_index=None)
    result = handle_ralph_index_status(session, _Workspace(workspace), {})
    payload = _decode(result)
    assert payload["enabled"] is False
    assert "managed_ignore_rule_repair" in payload
    repair = payload["managed_ignore_rule_repair"]
    assert repair["required"] is True
    assert repair["action"] == "seed_default_gitignore"


def test_index_status_reports_last_job_after_reindex(tmp_path: Path) -> None:
    """Regression for AC-03 status reporting after a successful reindex.

    Prior to the fix, ``_build_status_payload`` iterated a
    ``sqlite3.Row`` directly which yields values, not column names, so
    the second ``latest_row[key]`` lookup raised ``IndexError`` and
    ``ralph_index_status`` crashed whenever a job row existed.
    """
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        reindex_payload = _decode(
            handle_ralph_reindex(
                session,
                _Workspace(workspace),
                {"mode": "changed", "timeout_ms": 5_000},
            )
        )
        assert reindex_payload["job_status"] == "ok"
        assert reindex_payload["generation"] == 1

        # Status must not raise and must report the last_job dict.
        status_payload = _decode(
            handle_ralph_index_status(session, _Workspace(workspace), {})
        )
        assert status_payload["enabled"] is True
        assert status_payload["generation"] == 1
        assert isinstance(status_payload["last_job"], dict), status_payload
        assert status_payload["last_job"]["status"] == "ok"
        # last_job carries the same job_id the reindex returned.
        assert (
            status_payload["last_job"]["job_id"]
            == reindex_payload["job_id"]
        )
        # generation is recorded as a string in last_job (raw row
        # values) but the integer appears in the typed status fields.
        assert status_payload["last_job"]["generation"] == "1"
    finally:
        handle.store.close()


def test_reindex_changed_runs_and_records(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        result = handle_ralph_reindex(
            session,
            _Workspace(workspace),
            {"mode": "changed", "timeout_ms": 5_000},
        )
        payload = _decode(result)
        assert payload["job_status"] == "ok"
        assert payload["generation"] == 1
        assert payload["parse_count"] >= 2
    finally:
        handle.store.close()


def test_reindex_full_rebuilds(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        # First build.
        handle_ralph_reindex(
            session, _Workspace(workspace), {"mode": "changed"}
        )
        # Full rebuild resets the generation to 1.
        result = handle_ralph_reindex(
            session, _Workspace(workspace), {"mode": "full"}
        )
        payload = _decode(result)
        assert payload["generation"] == 1
    finally:
        handle.store.close()


def test_reindex_rejects_invalid_mode(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        from ralph.mcp.tools.coordination import InvalidParamsError

        with pytest.raises(InvalidParamsError):
            handle_ralph_reindex(
                session,
                _Workspace(workspace),
                {"mode": "bogus", "timeout_ms": 5_000},
            )
    finally:
        handle.store.close()


def test_reindex_rejects_zero_timeout(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        from ralph.mcp.tools.coordination import InvalidParamsError

        with pytest.raises(InvalidParamsError):
            handle_ralph_reindex(
                session,
                _Workspace(workspace),
                {"mode": "changed", "timeout_ms": 0},
            )
    finally:
        handle.store.close()


def test_reindex_rejects_negative_timeout(tmp_path: Path) -> None:
    """AC-05: timeout_ms must be a positive integer."""
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        from ralph.mcp.tools.coordination import InvalidParamsError

        with pytest.raises(InvalidParamsError):
            handle_ralph_reindex(
                session,
                _Workspace(workspace),
                {"mode": "changed", "timeout_ms": -100},
            )
    finally:
        handle.store.close()


def test_reindex_rejects_oversized_timeout(tmp_path: Path) -> None:
    """AC-05: callers cannot extend the budget arbitrarily. The
    handler rejects ``timeout_ms`` above the documented cap.
    """
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        from ralph.mcp.tools.coordination import InvalidParamsError

        with pytest.raises(InvalidParamsError):
            handle_ralph_reindex(
                session,
                _Workspace(workspace),
                {"mode": "changed", "timeout_ms": 9_999_999_999},
            )
    finally:
        handle.store.close()


def test_reindex_rejects_malformed_timeout_string(tmp_path: Path) -> None:
    """AC-05: malformed ``timeout_ms`` fails closed instead of
    silently falling back to the default.
    """
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        from ralph.mcp.tools.coordination import InvalidParamsError

        with pytest.raises(InvalidParamsError):
            handle_ralph_reindex(
                session,
                _Workspace(workspace),
                {"mode": "changed", "timeout_ms": "bogus"},
            )
    finally:
        handle.store.close()


def test_reindex_rejects_bool_timeout(tmp_path: Path) -> None:
    """AC-05: bool is rejected because Python treats True/False
    as int; the handler must not accept the silently-typed value.
    """
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        from ralph.mcp.tools.coordination import InvalidParamsError

        with pytest.raises(InvalidParamsError):
            handle_ralph_reindex(
                session,
                _Workspace(workspace),
                {"mode": "changed", "timeout_ms": True},
            )
    finally:
        handle.store.close()


def test_reindex_rejects_non_integer_float_timeout(tmp_path: Path) -> None:
    """AC-05: non-integer float values fail closed."""
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        from ralph.mcp.tools.coordination import InvalidParamsError

        with pytest.raises(InvalidParamsError):
            handle_ralph_reindex(
                session,
                _Workspace(workspace),
                {"mode": "changed", "timeout_ms": 1.5},
            )
    finally:
        handle.store.close()


def test_reindex_accepts_string_integer_timeout(tmp_path: Path) -> None:
    """AC-05: a string that parses to an integer in range is
    accepted (JSON params may surface ints as strings on some
    transports).
    """
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        result = handle_ralph_reindex(
            session,
            _Workspace(workspace),
            {"mode": "changed", "timeout_ms": "5000"},
        )
        payload = _decode(result)
        assert payload["job_status"] == "ok"
    finally:
        handle.store.close()


def test_reindex_accepts_at_max_timeout(tmp_path: Path) -> None:
    """AC-05: the boundary value ``timeout_ms = max`` is accepted."""
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        result = handle_ralph_reindex(
            session,
            _Workspace(workspace),
            {"mode": "changed", "timeout_ms": 60_000},
        )
        payload = _decode(result)
        assert payload["job_status"] == "ok"
    finally:
        handle.store.close()


def test_reindex_records_path_scope(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        result = handle_ralph_reindex(
            session,
            _Workspace(workspace),
            {
                "mode": "full",
                "timeout_ms": 5_000,
                "path_scope": ["a.py"],
            },
        )
        payload = _decode(result)
        assert payload["job_status"] == "ok"
        assert "a.py" in payload["changed_files"]
    finally:
        handle.store.close()


def test_build_explore_index_creates_index_dir(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        assert handle.index_dir == workspace / DEFAULT_INDEX_ROOT
        assert handle.index_dir.is_dir()
    finally:
        handle.store.close()


def test_reindex_updates_handle_last_refresh_kind(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        handle_ralph_reindex(
            session, _Workspace(workspace), {"mode": "changed"}
        )
        assert handle.last_refresh_kind == "changed"
        handle_ralph_reindex(session, _Workspace(workspace), {"mode": "full"})
        assert handle.last_refresh_kind == "full"
    finally:
        handle.store.close()
