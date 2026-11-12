from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ralph.mcp.explore.handlers import (
    build_explore_index,
    handle_ralph_graph,
    handle_ralph_index_status,
    handle_ralph_reindex,
)
from ralph.mcp.explore.store import DEFAULT_INDEX_ROOT

RALPH_TEST_CHANGE_REASONS = {
    "test_ralph_graph_clears_cancel_flag_on_query_error": (
        "patch target corrected to _handlers_graph module where run_query is imported"
    ),
}

pytestmark = pytest.mark.timeout_seconds(2.0)

handle_ralph_graph_local = handle_ralph_graph


class _FakeSession:
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
    assert payload["workspace_awareness"] == {
        "workspace": str(workspace.absolute()),
        "mode": "watch",
        "freshness": "current",
        "cause": None,
        "dirty_paths_count": 0,
        "automatic_recovery": False,
        "safe_next_action": "None required.",
    }


def test_index_status_disabled_surfaces_live_fallback_awareness(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    from ralph.workspace.awareness import awareness_for_workspace, release_workspace_awareness

    awareness_for_workspace(workspace).set_live_fallback("watch_capacity")
    try:
        payload = _decode(handle_ralph_index_status(_FakeSession(), _Workspace(workspace), {}))
        awareness = payload["workspace_awareness"]
        assert awareness["mode"] == "live_fallback"
        assert awareness["freshness"] == "live_fallback"
        assert awareness["cause"] == "watch_capacity"
        assert awareness["automatic_recovery"] is True
    finally:
        release_workspace_awareness(workspace)


def test_index_status_is_side_effect_free_when_no_handle(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession(explore_index=None)
    index_dir = workspace / ".agent" / "ralph-explore"
    assert not index_dir.exists()
    result = handle_ralph_index_status(session, _Workspace(workspace), {})
    payload = _decode(result)
    assert payload["enabled"] is False
    assert payload["index_exists"] is False
    assert not index_dir.exists()
    assert not (index_dir / "index.sqlite").exists()


def test_index_status_reports_existing_disk_state_without_handle(
    tmp_path: Path,
) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    import contextlib

    with contextlib.suppress(Exception):
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
    workspace = _seed_workspace(tmp_path)
    (workspace / ".gitignore").write_text(".agent/ralph-explore/\n")
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
        assert repair["coverage"] == "explicit_child_rule"
    finally:
        handle.store.close()


def test_index_status_parent_only_repair_recommends_child_rule(
    tmp_path: Path,
) -> None:
    workspace = _seed_workspace(tmp_path)
    (workspace / ".gitignore").write_text(".agent/\n")
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        result = handle_ralph_index_status(session, _Workspace(workspace), {})
        payload = _decode(result)
        assert payload["managed_ignore_rule_present"] is True
        repair = payload["managed_ignore_rule_repair"]
        assert repair["required"] is True
        assert repair["action"] == "append_explicit_child_rule"
        assert repair["reason"] == "managed_ignore_rule_parent_only"
        assert repair["coverage"] == "parent_only"
        assert repair["missing_rule"] == ".agent/ralph-explore/"
        assert repair["next_command"] == "ralph"
    finally:
        handle.store.close()


def test_index_status_exposes_managed_ignore_repair_when_absent(
    tmp_path: Path,
) -> None:
    workspace = _seed_workspace(tmp_path)
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

        status_payload = _decode(handle_ralph_index_status(session, _Workspace(workspace), {}))
        assert status_payload["enabled"] is True
        assert status_payload["generation"] == 1
        assert isinstance(status_payload["last_job"], dict), status_payload
        assert status_payload["last_job"]["status"] == "ok"
        assert status_payload["last_job"]["job_id"] == reindex_payload["job_id"]
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


@pytest.mark.timeout_seconds(5)
def test_reindex_full_rebuilds(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    try:
        session = _FakeSession(explore_index=handle)
        handle_ralph_reindex(session, _Workspace(workspace), {"mode": "changed"})
        result = handle_ralph_reindex(session, _Workspace(workspace), {"mode": "full"})
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


@pytest.mark.timeout_seconds(5)
def test_reindex_accepts_at_max_timeout(tmp_path: Path) -> None:
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
        handle_ralph_reindex(session, _Workspace(workspace), {"mode": "changed"})
        assert handle.last_refresh_kind == "changed"
        handle_ralph_reindex(session, _Workspace(workspace), {"mode": "full"})
        assert handle.last_refresh_kind == "full"
    finally:
        handle.store.close()


def test_ralph_reindex_cancel_clears_flag_after_each_call(
    tmp_path: Path,
) -> None:
    from ralph.mcp.explore import handlers

    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    starting_size = len(handlers._REINDEX_CANCEL_FLAGS)
    try:
        for _call_index in range(2):
            call_session = _FakeSession(explore_index=handle)
            handle_ralph_reindex(
                call_session,
                _Workspace(workspace),
                {"mode": "changed", "cancel": False},
            )
        assert len(handlers._REINDEX_CANCEL_FLAGS) == starting_size
    finally:
        handle.store.close()


def test_ralph_reindex_cancel_returns_cancelled_status(tmp_path: Path) -> None:
    from ralph.mcp.explore import handlers

    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    starting_size = len(handlers._REINDEX_CANCEL_FLAGS)
    try:
        session = _FakeSession(explore_index=handle)
        result = handle_ralph_reindex(
            session,
            _Workspace(workspace),
            {"mode": "changed", "cancel": True},
        )
        payload = _decode(result)
        assert payload.get("cancelled") is True
        assert len(handlers._REINDEX_CANCEL_FLAGS) == starting_size
    finally:
        handle.store.close()


def test_ralph_reindex_cancel_isolated_per_session(tmp_path: Path) -> None:
    from ralph.mcp.explore import handlers

    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    starting_size = len(handlers._REINDEX_CANCEL_FLAGS)
    try:
        session_a = _FakeSession(explore_index=handle)
        session_b = _FakeSession(explore_index=handle)
        result_a = handle_ralph_reindex(
            session_a,
            _Workspace(workspace),
            {"mode": "changed", "cancel": True},
        )
        payload_a = _decode(result_a)
        assert payload_a.get("cancelled") is True
        result_b = handle_ralph_reindex(
            session_b,
            _Workspace(workspace),
            {"mode": "changed", "cancel": False},
        )
        payload_b = _decode(result_b)
        assert payload_b.get("cancelled") is False
        assert payload_b["job_status"] == "ok"
        assert len(handlers._REINDEX_CANCEL_FLAGS) == starting_size
    finally:
        handle.store.close()


def test_ralph_graph_clears_cancel_flag_map_after_each_call(
    tmp_path: Path,
) -> None:
    from ralph.mcp.explore import handlers

    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    starting_size = len(handlers._GRAPH_CANCEL_FLAGS)
    try:
        for _call_index in range(2):
            call_session = _FakeSession(explore_index=handle)
            params = {
                "query_type": "neighbors",
                "target": "a.py",
                "timeout_ms": 5_000,
            }
            handle_ralph_graph_local(call_session, _Workspace(workspace), params)
        assert len(handlers._GRAPH_CANCEL_FLAGS) == starting_size, (
            f"Cancel-flag map leaked entries: {dict(handlers._GRAPH_CANCEL_FLAGS)}"
        )
    finally:
        handle.store.close()


def test_ralph_graph_clears_cancel_flag_on_query_error(tmp_path: Path) -> None:
    from ralph.mcp.explore import handlers

    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    starting_size = len(handlers._GRAPH_CANCEL_FLAGS)

    def _explode(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("simulated dispatcher failure")

    try:
        call_session = _FakeSession(explore_index=handle)
        params = {
            "query_type": "neighbors",
            "target": "a.py",
            "timeout_ms": 5_000,
        }
        with (
            patch(
                "ralph.mcp.explore._handlers_graph.run_query",
                side_effect=_explode,
            ) as patched_run_query,
            pytest.raises(RuntimeError),
        ):
            handle_ralph_graph_local(call_session, _Workspace(workspace), params)
        assert patched_run_query.called, (
            "the graph handler must resolve run_query through "
            "ralph.mcp.explore._handlers_graph for the query-error path to be exercised"
        )
        assert len(handlers._GRAPH_CANCEL_FLAGS) == starting_size, (
            "Cancel-flag map leaked entries after a query error: "
            f"{dict(handlers._GRAPH_CANCEL_FLAGS)}"
        )
    finally:
        handle.store.close()


def test_ralph_graph_concurrent_calls_have_independent_cancel_flags(
    tmp_path: Path,
) -> None:
    from ralph.mcp.explore import handlers

    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    starting_size = len(handlers._GRAPH_CANCEL_FLAGS)
    try:
        (workspace / "c.py").write_text("z = 3\n")
        session = _FakeSession(explore_index=handle)
        params = {
            "query_type": "neighbors",
            "target": "a.py",
            "timeout_ms": 5_000,
        }
        tokens_seen: list[str] = []

        original_arm = handlers._arm_cancel_flag

        def _spy_arm(
            registry: dict[str, bool],
            lock: object,
            token: str,
            initial: bool,
        ) -> object:
            tokens_seen.append(token)
            return original_arm(registry, lock, token, initial)

        with patch.object(handlers, "_arm_cancel_flag", side_effect=_spy_arm):
            result_a = handle_ralph_graph_local(session, _Workspace(workspace), params)
            result_b = handle_ralph_graph_local(session, _Workspace(workspace), params)
        assert result_a.is_error is False
        assert result_b.is_error is False
        assert len(tokens_seen) == 2
        assert tokens_seen[0] != tokens_seen[1]
        assert len(handlers._GRAPH_CANCEL_FLAGS) == starting_size
    finally:
        handle.store.close()


def test_ralph_graph_concurrent_call_cancellation_isolates_tokens(
    tmp_path: Path,
) -> None:
    from ralph.mcp.explore import handlers

    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    starting_size = len(handlers._GRAPH_CANCEL_FLAGS)
    token_a: list[str] = []
    token_b: list[str] = []

    original_arm = handlers._arm_cancel_flag

    def _spy_arm(
        registry: dict[str, bool],
        lock: object,
        token: str,
        initial: bool,
    ) -> object:
        if not token_a:
            token_a.append(token)
        elif not token_b:
            token_b.append(token)
        return original_arm(registry, lock, token, initial)

    try:
        session = _FakeSession(explore_index=handle)
        params_a = {
            "query_type": "neighbors",
            "target": "a.py",
            "timeout_ms": 5_000,
            "cancel": True,
        }
        params_b = {
            "query_type": "neighbors",
            "target": "b.py",
            "timeout_ms": 5_000,
            "cancel": False,
        }
        with patch.object(handlers, "_arm_cancel_flag", side_effect=_spy_arm):
            handle_ralph_graph_local(session, _Workspace(workspace), params_a)
            assert len(handlers._GRAPH_CANCEL_FLAGS) == starting_size
            handle_ralph_graph_local(session, _Workspace(workspace), params_b)
        assert len(handlers._GRAPH_CANCEL_FLAGS) == starting_size
    finally:
        handle.store.close()


def test_ralph_reindex_concurrent_calls_have_independent_cancel_flags(
    tmp_path: Path,
) -> None:
    from ralph.mcp.explore import handlers

    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    starting_size = len(handlers._REINDEX_CANCEL_FLAGS)
    tokens_seen: list[str] = []

    original_arm = handlers._arm_cancel_flag

    def _spy_arm(
        registry: dict[str, bool],
        lock: object,
        token: str,
        initial: bool,
    ) -> object:
        tokens_seen.append(token)
        return original_arm(registry, lock, token, initial)

    try:
        session = _FakeSession(explore_index=handle)
        with patch.object(handlers, "_arm_cancel_flag", side_effect=_spy_arm):
            result_a = handle_ralph_reindex(
                session,
                _Workspace(workspace),
                {"mode": "changed", "cancel": True},
            )
            result_b = handle_ralph_reindex(
                session,
                _Workspace(workspace),
                {"mode": "changed", "cancel": False},
            )
        payload_a = _decode(result_a)
        payload_b = _decode(result_b)
        assert payload_a.get("cancelled") is True
        assert payload_b.get("cancelled") is False
        assert payload_b["job_status"] == "ok"
        assert len(tokens_seen) == 2
        assert tokens_seen[0] != tokens_seen[1]
        assert len(handlers._REINDEX_CANCEL_FLAGS) == starting_size
    finally:
        handle.store.close()


def test_ralph_graph_closes_ephemeral_store_when_no_session_handle(
    tmp_path: Path,
) -> None:
    workspace = _seed_workspace(tmp_path)
    from ralph.mcp.explore import handlers

    class _NoHandleSession(_FakeSession):
        def __init__(self) -> None:
            super().__init__(explore_index=None)
            delattr(self, "explore_index")

        def check_capability(self, capability: str):
            return {"status": "approved", "capability": capability}

        def check_edit_area(self, path: str):
            return {"status": "approved", "path": path}

    no_handle_session = _NoHandleSession()
    with patch.object(handlers, "build_explore_index") as mock_build:
        mock_handle = MagicMock()
        mock_handle.store.close = MagicMock()
        mock_build.return_value = mock_handle
        with patch(
            "ralph.mcp.explore.handlers.graph_module.run_query",
            return_value=_fake_graph_result(),
        ):
            params = {
                "query_type": "neighbors",
                "target": "a.py",
                "timeout_ms": 5_000,
            }
            result = handle_ralph_graph_local(no_handle_session, _Workspace(workspace), params)
        mock_handle.store.close.assert_called_once()
    assert result.is_error is False


def _fake_graph_result() -> object:
    from ralph.mcp.explore.graph import GraphResult

    return GraphResult(
        query_type="neighbors",
        nodes=(),
        edges=(),
        missing_data=(),
        index_generation=0,
        is_stale=False,
        truncated=False,
        metadata={},
    )
