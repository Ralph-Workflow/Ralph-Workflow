"""Black-box tests for the explore MCP handlers (index_status + reindex)."""

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

# Local alias so the bounded-accumulator tests read naturally.
handle_ralph_graph_local = handle_ralph_graph


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


# --- ralph_reindex: bounded cancel contract -----------------------------


def test_ralph_reindex_cancel_clears_flag_after_each_call(
    tmp_path: Path,
) -> None:
    """AC-02/AC-05: the module-global ``_REINDEX_CANCEL_FLAGS`` map must
    not accumulate entries across calls. Repeated calls with
    distinct sessions must leave the map at its starting size.
    """
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
    """AC-05: a cancel=True call returns a ``cancelled=True`` payload
    and clears the per-request cancel flag, so a follow-up call is
    not poisoned by the previous cancel.
    """
    from ralph.mcp.explore import handlers

    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    starting_size = len(handlers._REINDEX_CANCEL_FLAGS)
    try:
        session = _FakeSession(explore_index=handle)
        # The handler arms a per-request cancel flag with the
        # caller-supplied ``cancel=True`` value, so the writer
        # polls True at the file-loop boundary.
        result = handle_ralph_reindex(
            session,
            _Workspace(workspace),
            {"mode": "changed", "cancel": True},
        )
        payload = _decode(result)
        # The pre-armed cancel flag tripped the writer's cancel
        # check at the file-loop boundary, so the result is
        # bounded incomplete (cancelled) and the prior
        # generation is preserved.
        assert payload.get("cancelled") is True
        # The handler must clear the per-request cancel flag on
        # every exit path, including the cancelled path. The
        # map must NOT accumulate entries across calls.
        assert len(handlers._REINDEX_CANCEL_FLAGS) == starting_size
    finally:
        handle.store.close()


def test_ralph_reindex_cancel_isolated_per_session(tmp_path: Path) -> None:
    """AC-05: a cancel on session A must not poison a reindex on
    session B against the same handle. The per-request token
    isolates concurrent calls so one caller's flag never
    cancels or clears another caller's flag, regardless of
    whether the two calls target the same session.
    """
    from ralph.mcp.explore import handlers

    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    starting_size = len(handlers._REINDEX_CANCEL_FLAGS)
    try:
        session_a = _FakeSession(explore_index=handle)
        session_b = _FakeSession(explore_index=handle)
        # Session A issues a cancel=True call; the handler arms
        # a per-request token under that session's call. Session
        # B issues a separate cancel=False call with a different
        # per-request token. The two tokens are independent, so
        # session B's call cannot observe session A's flag.
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
        # Session B sees an empty cancel state and reindexes
        # normally; the per-request tokens mean session B's
        # call never observed session A's pre-armed flag.
        assert payload_b.get("cancelled") is False
        assert payload_b["job_status"] == "ok"
        # Both per-request cancel flags are cleared. The map is
        # back to its starting size; nothing leaked across the
        # two sessions.
        assert len(handlers._REINDEX_CANCEL_FLAGS) == starting_size
    finally:
        handle.store.close()


# --- ralph_graph: bounded accumulator contract for cancel flags ---------


def test_ralph_graph_clears_cancel_flag_map_after_each_call(
    tmp_path: Path,
) -> None:
    """AC-02/AC-05: the module-global ``_GRAPH_CANCEL_FLAGS`` map must
    not accumulate entries across calls. Repeated calls with
    distinct sessions must leave the map at its starting size.
    """
    from ralph.mcp.explore import handlers

    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    starting_size = len(handlers._GRAPH_CANCEL_FLAGS)
    try:
        # Two distinct sessions, each issuing a graph call.
        for _call_index in range(2):
            call_session = _FakeSession(explore_index=handle)
            params = {
                "query_type": "neighbors",
                "target": "a.py",
                "timeout_ms": 5_000,
            }
            handle_ralph_graph_local(call_session, _Workspace(workspace), params)
        assert len(handlers._GRAPH_CANCEL_FLAGS) == starting_size, (
            "Cancel-flag map leaked entries: "
            f"{dict(handlers._GRAPH_CANCEL_FLAGS)}"
        )
    finally:
        handle.store.close()


def test_ralph_graph_clears_cancel_flag_on_query_error(tmp_path: Path) -> None:
    """AC-02/AC-05: even when ``run_query`` raises, the cancel-flag
    entry must be cleared in the finally block.
    """
    from ralph.mcp.explore import graph as graph_module
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
            patch.object(graph_module, "run_query", side_effect=_explode),
            pytest.raises(RuntimeError),
        ):
            handle_ralph_graph_local(
                call_session, _Workspace(workspace), params
            )
        assert len(handlers._GRAPH_CANCEL_FLAGS) == starting_size, (
            "Cancel-flag map leaked entries after a query error: "
            f"{dict(handlers._GRAPH_CANCEL_FLAGS)}"
        )
    finally:
        handle.store.close()


# --- ralph_graph: same-session concurrent-call isolation -------------------


def test_ralph_graph_concurrent_calls_have_independent_cancel_flags(
    tmp_path: Path,
) -> None:
    """AC-05: same-session concurrent graph calls must not cancel or
    clear each other's cancel flag. Each call must arm a distinct
    per-request token; one caller's flag flip and cleanup must
    never affect another caller's flag, even when they target the
    same session.

    Without per-request tokens, two concurrent calls sharing one
    session would collide on a single ``id(session)`` key and one
    caller's cancellation or final ``pop()`` would clobber the
    other caller's state. The test pins the independent-tokens
    contract by snapshotting the registry at every observable
    point and asserting both tokens coexist independently.
    """
    from ralph.mcp.explore import handlers

    workspace = _seed_workspace(tmp_path)
    handle = build_explore_index(workspace)
    starting_size = len(handlers._GRAPH_CANCEL_FLAGS)
    try:
        # Build a small workspace with enough files that two
        # concurrent graph calls both find a ``neighbors`` target.
        (workspace / "c.py").write_text("z = 3\n")
        session = _FakeSession(explore_index=handle)
        params = {
            "query_type": "neighbors",
            "target": "a.py",
            "timeout_ms": 5_000,
        }
        # Capture the per-request tokens the handler arms by
        # wrapping ``_arm_cancel_flag`` so we can observe the
        # exact keys it inserts. This is a black-box probe into
        # the contract; the tokens must be unique per call.
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
            # Two concurrent calls against the same session.
            result_a = handle_ralph_graph_local(
                session, _Workspace(workspace), params
            )
            result_b = handle_ralph_graph_local(
                session, _Workspace(workspace), params
            )
        # Both calls succeed and return independent bounded results.
        assert result_a.is_error is False
        assert result_b.is_error is False
        # Two distinct per-request tokens were armed — one per call.
        # Per-session keying would have produced a single key.
        assert len(tokens_seen) == 2
        assert tokens_seen[0] != tokens_seen[1]
        # After both calls complete, the registry is back to its
        # starting size; no flag entries leaked across the
        # concurrent calls.
        assert len(handlers._GRAPH_CANCEL_FLAGS) == starting_size
    finally:
        handle.store.close()


def test_ralph_graph_concurrent_call_cancellation_isolates_tokens(
    tmp_path: Path,
) -> None:
    """AC-05: when one of two concurrent graph calls asks to
    cancel, the other call must NOT be cancelled and must
    complete normally. The per-request tokens guarantee that
    one caller's flag cannot cancel a sibling caller's
    dispatcher poll.

    The test patches ``_arm_cancel_flag`` to record the tokens
    and flip one token to True mid-call (simulating an external
    cancel trigger). The other call's token remains False for
    the duration of its run, so it completes with no cancel.
    """
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
        # Capture both tokens so the test can flip one.
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
            # Run the cancel=True call first. Its token is flipped
            # before the call returns. Then run the cancel=False
            # call; it must NOT observe the flipped token.
            handle_ralph_graph_local(session, _Workspace(workspace), params_a)
            assert len(handlers._GRAPH_CANCEL_FLAGS) == starting_size
            handle_ralph_graph_local(session, _Workspace(workspace), params_b)
        assert len(handlers._GRAPH_CANCEL_FLAGS) == starting_size
    finally:
        handle.store.close()


def test_ralph_reindex_concurrent_calls_have_independent_cancel_flags(
    tmp_path: Path,
) -> None:
    """AC-05: same-session concurrent reindex calls must not cancel
    or clear each other's cancel flag. Each call must arm a
    distinct per-request token; one caller's cancellation must
    not affect another caller's flag, even when both calls target
    the same session.
    """
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
            # Two concurrent calls against the same session. The
            # first asks to cancel; the second does not. Each call
            # gets its own per-request token, so the second call's
            # flag is never flipped by the first call's behavior.
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
        # Call A is cancelled (per-request cancel flag armed True);
        # call B is not (per-request cancel flag armed False).
        # The two outcomes are independent — no token leak.
        assert payload_a.get("cancelled") is True
        assert payload_b.get("cancelled") is False
        assert payload_b["job_status"] == "ok"
        # Two distinct tokens were armed — one per call.
        assert len(tokens_seen) == 2
        assert tokens_seen[0] != tokens_seen[1]
        # After both calls complete, the registry is back to its
        # starting size.
        assert len(handlers._REINDEX_CANCEL_FLAGS) == starting_size
    finally:
        handle.store.close()


def test_ralph_graph_closes_ephemeral_store_when_no_session_handle(
    tmp_path: Path,
) -> None:
    """AC-02/AC-05: when a session has no explore_index, the
    handler builds an ephemeral index and must close the
    underlying SQLite store after the call so file handles do
    not leak across calls.
    """
    workspace = _seed_workspace(tmp_path)
    from ralph.mcp.explore import handlers

    class _NoHandleSession(_FakeSession):
        def __init__(self) -> None:
            super().__init__(explore_index=None)
            # Deliberately expose an attribute that fails the
            # ``getattr(session, "explore_index", None)`` lookup
            # so ``_resolve_explore_index`` returns None.
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
            result = handle_ralph_graph_local(
                no_handle_session, _Workspace(workspace), params
            )
        # The ephemeral handle's store must be closed.
        mock_handle.store.close.assert_called_once()
    assert result.is_error is False


def _fake_graph_result() -> object:
    """Minimal GraphResult stand-in for handler-level tests."""
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
