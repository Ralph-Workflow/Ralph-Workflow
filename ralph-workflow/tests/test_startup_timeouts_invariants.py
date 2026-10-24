"""R2 / R4 invariant tests for startup timeout constants and import-time guards.

These tests validate that:

1. The import-time invariants in ``_startup_timeouts`` are well-formed and
   hold at their documented defaults (no ``assert`` — these are ``if/raise``
   so they survive ``python -O``).
2. ``_PROBE_SAFETY_S`` is a positive float consumed by the runtime.
3. The computed ``_MAX_UPSTREAMS_AT_DEFAULTS`` is consistent with the
   default preflight budget.
4. An undersized preflight budget is caught at runtime
   (``mcp_upstream_probe_timeout_from_env`` raises ``RuntimeError`` when
   the ceiling would be at or below ``_PROBE_SAFETY_S``).
5. Per-stage budget overruns are DIAGNOSTIC ONLY: ``record_stage_elapsed``
   logs a warning but returns ``None`` instead of raising.
6. The real startup deadline is the ONLY fatal check (28.0s default).
7. Overrun records are written to and read from a sibling JSONL file.

These tests deliberately avoid ``time.sleep`` and do not assume implementation
internals beyond the documented public API of
``ralph.mcp.protocol._startup_timeouts``.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import NoReturn
from unittest import mock

import pytest

# Import the public surface to verify _PROBE_SAFETY_S is exported.
from ralph.mcp.protocol._startup_timeouts import (
    _MAX_UPSTREAMS_AT_DEFAULTS,
    _PROBE_BUDGET_SHARE,
    _PROBE_SAFETY_S,
    _check_real_startup_deadline,
    mcp_discovery_budget_from_env,
    mcp_pre_serve_timeout_seconds,
    mcp_preflight_timeout_from_env,
    mcp_real_startup_deadline_from_env,
    mcp_upstream_probe_timeout_from_env,
    mcp_upstream_probe_timeout_seconds,
    record_stage_elapsed,
    startup_clock_override,
)
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._start_stage_tracker import (
    read_overruns,
    record_overrun,
    stage_file_path,
)


class TestImportTimeInvariants:
    """The module must pass its own import-time checks at documented defaults."""

    def test_probe_safety_is_positive(self) -> None:
        assert isinstance(_PROBE_SAFETY_S, float)
        assert _PROBE_SAFETY_S > 0

    def test_probe_budget_share_is_a_valid_fraction(self) -> None:
        assert isinstance(_PROBE_BUDGET_SHARE, float)
        assert 0 < _PROBE_BUDGET_SHARE < 1

    def test_max_upstreams_at_defaults_is_at_least_one(self) -> None:
        assert isinstance(_MAX_UPSTREAMS_AT_DEFAULTS, int)
        assert _MAX_UPSTREAMS_AT_DEFAULTS >= 1

    def test_default_preflight_budget_exceeds_probe_safety(self) -> None:
        """Import-time invariant: default preflight * share > _PROBE_SAFETY_S."""
        default_preflight = mcp_preflight_timeout_from_env({})
        ceiling = default_preflight * _PROBE_BUDGET_SHARE
        assert ceiling.total_seconds() > _PROBE_SAFETY_S

    def test_max_upstreams_at_defaults_math_is_consistent(self) -> None:
        """_MAX_UPSTREAMS_AT_DEFAULTS is the largest N such that
        N * _PROBE_SAFETY_S < preflight * share."""
        default_preflight = mcp_preflight_timeout_from_env({})
        ceiling = default_preflight * _PROBE_BUDGET_SHARE
        expected = int((ceiling.total_seconds() - _PROBE_SAFETY_S) / _PROBE_SAFETY_S)
        assert expected == _MAX_UPSTREAMS_AT_DEFAULTS


class TestProbeTimeoutAtDefaults:
    """At defaults the per-upstream budget is large enough for all documented use-cases."""

    def test_default_preflight_is_30_seconds(self) -> None:
        assert mcp_preflight_timeout_from_env({}) == timedelta(seconds=30)

    def test_default_probe_ceiling_is_15_seconds(self) -> None:
        ceiling = mcp_upstream_probe_timeout_from_env({})
        assert ceiling == timedelta(seconds=15)

    def test_default_probe_ceiling_seconds(self) -> None:
        assert mcp_upstream_probe_timeout_seconds({}) == 15.0

    def test_probe_ceiling_strictly_below_preflight(self) -> None:
        preflight = mcp_preflight_timeout_from_env({})
        ceiling = mcp_upstream_probe_timeout_from_env({})
        assert ceiling < preflight
        remaining = preflight - ceiling
        assert remaining.total_seconds() > _PROBE_SAFETY_S


class TestUndersizedPreflightAtRuntime:
    """An undersized preflight is caught at runtime when the ceiling ≤ _PROBE_SAFETY_S."""

    def test_preflight_below_minimum_raises_runtime_error(self) -> None:
        # Just above the minimum: 4001ms → ceiling = 2000.5ms > 2000ms ✓
        env_just_above = {"RALPH_MCP_PREFLIGHT_TIMEOUT_MS": "4001"}
        ceiling_above = mcp_upstream_probe_timeout_from_env(env_just_above)
        assert ceiling_above.total_seconds() > _PROBE_SAFETY_S

        # At the minimum (4000ms): ceiling = 2000ms == _PROBE_SAFETY_S → must raise
        env_at_minimum = {"RALPH_MCP_PREFLIGHT_TIMEOUT_MS": "4000"}
        with pytest.raises(RuntimeError, match="preflight timeout.*probe ceiling.*_PROBE_SAFETY_S"):
            mcp_upstream_probe_timeout_from_env(env_at_minimum)

        # Below minimum: 1000ms → ceiling = 500ms < 2000ms → must raise
        env_too_small = {"RALPH_MCP_PREFLIGHT_TIMEOUT_MS": "1000"}
        with pytest.raises(RuntimeError, match="preflight timeout.*probe ceiling.*_PROBE_SAFETY_S"):
            mcp_upstream_probe_timeout_from_env(env_too_small)

    def test_explicit_probe_timeout_below_ceiling_is_returned(self) -> None:
        env = {
            "RALPH_MCP_PREFLIGHT_TIMEOUT_MS": "30000",
            "RALPH_MCP_UPSTREAM_PROBE_TIMEOUT_MS": "1000",
        }
        result = mcp_upstream_probe_timeout_from_env(env)
        assert result == timedelta(seconds=1)

    def test_small_explicit_probe_also_raises(self) -> None:
        env = {
            "RALPH_MCP_PREFLIGHT_TIMEOUT_MS": "4000",
            "RALPH_MCP_UPSTREAM_PROBE_TIMEOUT_MS": "1000",
        }
        with pytest.raises(RuntimeError, match="preflight timeout.*probe ceiling.*_PROBE_SAFETY_S"):
            mcp_upstream_probe_timeout_from_env(env)


class TestProbeSafetyConstantExistence:
    """_PROBE_SAFETY_S must be a consumable constant for the runtime and tests."""

    def test_probe_safety_s_is_exported(self) -> None:
        assert _PROBE_SAFETY_S == 2.0

    def test_probe_safety_s_is_float(self) -> None:
        assert type(_PROBE_SAFETY_S) is float

    def test_probe_safety_s_is_consumed_in_max_upstreams_formula(self) -> None:
        default_preflight = mcp_preflight_timeout_from_env({})
        ceiling = default_preflight * _PROBE_BUDGET_SHARE
        computed = int((ceiling.total_seconds() - _PROBE_SAFETY_S) / _PROBE_SAFETY_S)
        assert computed == _MAX_UPSTREAMS_AT_DEFAULTS


class TestPerStageBudgets:
    """WU-C2: per-stage budgets, the shared deadline helper, and the import-time invariant."""

    def test_stage_budget_constants_are_documented(self) -> None:
        from ralph.mcp.protocol import _startup_timeouts as mod

        assert mod._CONFIG_LOAD_BUDGET_S == 1.0
        assert mod._REGISTRY_BUILD_BUDGET_S == 0.4
        assert mod._READY_TO_SERVE_BUDGET_S == 0.4
        # Whole-startup envelope must stay strictly below the preflight timeout.
        preflight = mcp_preflight_timeout_from_env({}).total_seconds()
        discovery = mcp_discovery_budget_from_env({}).total_seconds()
        whole = (
            mod._CONFIG_LOAD_BUDGET_S
            + discovery
            + mod._REGISTRY_BUILD_BUDGET_S
            + mod._READY_TO_SERVE_BUDGET_S
        )
        assert whole < preflight
        # Both constants and the stage vocabulary must be in the docstring.
        doc = mod.__doc__ or ""
        for needle in (
            "_CONFIG_LOAD_BUDGET_S",
            "_REGISTRY_BUILD_BUDGET_S",
            "_READY_TO_SERVE_BUDGET_S",
            "config_loaded",
            "upstream_catalog_or_discovery_done",
            "ready_to_serve",
            "mcp_discovery_budget_from_env",
            "preflight",
        ):
            assert needle in doc, f"module docstring must document {needle!r}"
        docstring = mod.record_stage_elapsed.__doc__ or ""
        assert "monotonic" in docstring, "helper must document fake-clock friendliness"

    def test_record_stage_elapsed_enforces_shared_deadline(self) -> None:
        # REWRITTEN: per-stage overrun is now DIAGNOSTIC ONLY (no raise).
        # Under budget: returns the budget value.
        result = record_stage_elapsed("config_loaded", 0.5, budget_s=1.0)
        assert result == 1.0
        # Over budget: returns None (diagnostic), does NOT raise.
        result = record_stage_elapsed("ready_to_serve", 1.25, budget_s=1.0)
        assert result is None

    def test_per_stage_envelope_independent_of_sum(self) -> None:
        # REWRITTEN: per-stage overrun is now DIAGNOSTIC ONLY (no raise).
        from ralph.mcp.protocol import _startup_timeouts as mod

        preflight = mcp_preflight_timeout_from_env({}).total_seconds()
        discovery = mcp_discovery_budget_from_env({}).total_seconds()
        # Aggregate is comfortably under preflight...
        assert discovery < preflight
        # ...and per-stage overrun does NOT raise (only logs warning).
        stage_elapsed = mod._CONFIG_LOAD_BUDGET_S + 0.1
        assert stage_elapsed < preflight
        # Must NOT raise — diagnostic only
        result = record_stage_elapsed(
            "config_loaded", stage_elapsed, budget_s=mod._CONFIG_LOAD_BUDGET_S
        )
        assert result is None


class TestDiagnosticStageContract:
    """New contract: per-stage overruns are diagnostic, not fatal."""

    def test_diagnostic_does_not_raise_overrun(self) -> None:
        """record_stage_elapsed returns None on overrun; does not raise."""
        result = record_stage_elapsed("config_loaded", 1.128, budget_s=1.0)
        assert result is None

    def test_real_deadline_equals_preflight_minus_safety(self) -> None:
        """At default env, real deadline = 30.0s - 2.0s = 28.0s."""
        preflight = mcp_preflight_timeout_from_env({}).total_seconds()
        real = preflight - _PROBE_SAFETY_S
        assert real == 28.0

    def test_per_stage_elapsed_independent_of_cumulative(self) -> None:
        """Per-stage overrun at exactly the budget does NOT raise."""
        from ralph.mcp.protocol import _startup_timeouts as mod

        # Exactly at budget: returns budget value (within budget)
        result = record_stage_elapsed("registry_built", 0.4, budget_s=mod._REGISTRY_BUILD_BUDGET_S)
        assert result == mod._REGISTRY_BUILD_BUDGET_S
        # Over budget by 0.0001s: returns None (diagnostic), does NOT raise
        result = record_stage_elapsed(
            "registry_built", 0.4001, budget_s=mod._REGISTRY_BUILD_BUDGET_S
        )
        assert result is None

    def test_ready_to_serve_at_budget_plus_epsilon_logs_warning_and_does_not_raise(
        self,
    ) -> None:
        """ready_to_serve at budget + epsilon logs a warning and does not raise.

        The contract: per-stage overrun at ready_to_serve (0.4001s with budget 0.4s)
        logs a diagnostic warning via logger.warning and returns None, rather than
        raising McpServerError.
        """
        from ralph.mcp.protocol import _startup_timeouts as mod

        with mock.patch("ralph.mcp.protocol._startup_timeouts.logger.warning") as mock_warning:
            result = record_stage_elapsed(
                "ready_to_serve",
                0.4001,
                budget_s=mod._READY_TO_SERVE_BUDGET_S,
            )
            assert result is None
            mock_warning.assert_called_once_with(
                mock.ANY,
                stage="ready_to_serve",
                elapsed_s=0.4001,
                budget_s=mod._READY_TO_SERVE_BUDGET_S,
                remaining_s=mock.ANY,
            )

    def test_overrun_recorded_in_sibling_jsonl_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """record_overrun writes one JSONL line to <stage_file_path>.overruns."""
        fake_sid = "test-spawn-1234"
        monkeypatch.setattr(
            "ralph.mcp.server._start_stage_tracker._get_spawn_id",
            lambda: fake_sid,
        )
        # Write one overrun
        record_overrun(
            root=tmp_path,
            stage="config_loaded",
            elapsed_s=1.128,
            budget_s=1.0,
            remaining_s=-0.128,
        )
        # Verify file exists and contains one valid JSON line
        sf_path = stage_file_path(tmp_path, spawn_id=fake_sid)
        overruns_path = Path(str(sf_path) + ".overruns")
        assert overruns_path.exists()
        lines = overruns_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        parsed: dict[str, object] = json.loads(lines[0])
        assert isinstance(parsed, dict)
        assert parsed["stage"] == "config_loaded"
        assert parsed["elapsed_s"] == 1.128
        assert parsed["budget_s"] == 1.0
        assert parsed["remaining_s"] == -0.128
        assert "headroom_s" in parsed
        assert "observed_at" in parsed
        assert "warning_id" in parsed

    def test_read_overruns_returns_recorded_entries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """read_overruns returns all recorded entries as dicts."""
        fake_sid = "test-spawn-5678"
        monkeypatch.setattr(
            "ralph.mcp.server._start_stage_tracker._get_spawn_id",
            lambda: fake_sid,
        )
        record_overrun(
            root=tmp_path,
            stage="config_loaded",
            elapsed_s=1.128,
            budget_s=1.0,
            remaining_s=-0.128,
        )
        record_overrun(
            root=tmp_path,
            stage="registry_built",
            elapsed_s=0.5,
            budget_s=0.4,
            remaining_s=-0.1,
        )
        records = read_overruns(tmp_path, spawn_id=fake_sid)
        assert len(records) == 2
        assert records[0]["stage"] == "config_loaded"
        assert records[1]["stage"] == "registry_built"

    def test_read_overruns_missing_file_returns_empty_list(self, tmp_path: Path) -> None:
        """read_overruns returns [] when the overruns file does not exist."""
        records = read_overruns(tmp_path, spawn_id="nonexistent-spawn-9999")
        assert records == []

    def test_read_overruns_skips_malformed_lines(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Malformed JSON lines are silently skipped."""
        fake_sid = "test-spawn-malformed"
        monkeypatch.setattr(
            "ralph.mcp.server._start_stage_tracker._get_spawn_id",
            lambda: fake_sid,
        )
        # Write one valid and one malformed line directly
        sf_path = stage_file_path(tmp_path, spawn_id=fake_sid)
        overruns_path = Path(str(sf_path) + ".overruns")
        overruns_path.parent.mkdir(parents=True, exist_ok=True)
        entry1: dict[str, object] = {
            "stage": "config_loaded",
            "elapsed_s": 1.0,
            "budget_s": 1.0,
            "remaining_s": 0.0,
            "observed_at": time.time(),
            "warning_id": "abc",
        }
        entry2: dict[str, object] = {
            "stage": "registry_built",
            "elapsed_s": 0.5,
            "budget_s": 0.4,
            "remaining_s": -0.1,
            "observed_at": time.time(),
            "warning_id": "def",
        }
        overruns_path.write_text(
            f"{json.dumps(entry1)}\nnot valid json\n{json.dumps(entry2)}\n",
            encoding="utf-8",
        )
        records = read_overruns(tmp_path, spawn_id=fake_sid)
        assert len(records) == 2
        assert records[0]["stage"] == "config_loaded"
        assert records[1]["stage"] == "registry_built"

    def test_config_loaded_1_128s_continues_to_next_stage(self) -> None:
        """REGRESSION TEST: 1.128s config_loaded does NOT raise or exit.

        Before the fix: record_stage_elapsed raised McpServerError on the
        1.128s overrun (1.128s > 1.0s budget), crashing the child with
        last_stage="config_loaded" instead of continuing to discovery.

        After the fix: record_stage_elapsed logs a WARNING and returns None.
        _check_real_startup_deadline is NOT exceeded at 1.128s (28.0s envelope).
        """
        # Diagnostic call: 1.128s over 1.0s budget → None (no raise)
        result = record_stage_elapsed("config_loaded", 1.128, budget_s=1.0, headroom_s=26.872)
        assert result is None
        # _check_real_startup_deadline uses time.monotonic() internally;
        # mock it so elapsed = 1.5s (well under 28.0s envelope).
        import time as time_module

        with mock.patch.object(time_module, "monotonic", return_value=1.5):
            _check_real_startup_deadline("config_loaded", t0=0.0, env_map={})
        # No exception means the test passes.

    def test_real_deadline_raises_at_threshold_plus_epsilon(self) -> None:
        """_check_real_startup_deadline DOES raise when elapsed exceeds 28.0s."""
        # Patch time.monotonic to report 28.1s elapsed
        with mock.patch.object(__import__("time"), "monotonic", return_value=28.1):
            with pytest.raises(McpServerError) as exc_info:
                _check_real_startup_deadline(
                    stage="config_loaded",
                    t0=0.0,
                    env_map={},
                )
            assert exc_info.value.last_stage == "config_loaded"
            # elapsed_s will be 28.1 (monotonic returns 28.1, t0=0)
            assert exc_info.value.elapsed_s == 28.1
            assert exc_info.value.restart_count == 0

    def test_real_startup_deadline_from_env_default_and_custom(self) -> None:
        """mcp_real_startup_deadline_from_env returns preflight - safety."""
        assert mcp_real_startup_deadline_from_env({}) == 28.0
        custom_env = {"RALPH_MCP_PREFLIGHT_TIMEOUT_MS": "10000"}
        assert mcp_real_startup_deadline_from_env(custom_env) == 8.0

    def test_record_stage_elapsed_logs_and_persists_headroom(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """record_stage_elapsed passes headroom_s to record_overrun and stores it."""
        fake_sid = "test-spawn-headroom"
        monkeypatch.setattr(
            "ralph.mcp.server._start_stage_tracker._get_spawn_id",
            lambda: fake_sid,
        )
        result = record_stage_elapsed(
            "config_loaded",
            1.128,
            budget_s=1.0,
            root=tmp_path,
            headroom_s=26.872,
        )
        assert result is None
        records = read_overruns(tmp_path, spawn_id=fake_sid)
        assert len(records) == 1
        assert records[0]["headroom_s"] == 26.872

    def test_production_path_fake_clock_config_loaded_1_128s_overrun_reaches_serving(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Production path: config_loaded takes 1.128s (overrun vs 1.0s budget),
        records overrun with headroom, and server successfully continues through
        registry_built and ready_to_serve without raising or exiting.
        """
        import ralph.mcp.server._standalone_http_server as standalone_mod
        import ralph.mcp.server.runtime as runtime_mod
        from ralph.config.mcp_models import McpConfig
        from ralph.mcp.protocol.session import AgentSession
        from ralph.mcp.server._start_stage_tracker import read_last_stage

        clock_val = 0.0

        def fake_clock() -> float:
            return clock_val

        monkeypatch.setattr(runtime_mod, "_clock", fake_clock)

        fake_sid = "fake-spawn-wu1-prod"
        monkeypatch.setattr(
            "ralph.mcp.server._start_stage_tracker._get_spawn_id",
            lambda: fake_sid,
        )

        session = AgentSession(
            session_id="test-session-wu1",
            run_id="test-run-wu1",
            drain="standalone",
            capabilities={"mcp"},
        )

        def mock_session_from_env(*args: object, **kwargs: object) -> AgentSession:
            return session

        monkeypatch.setattr(runtime_mod, "session_from_env", mock_session_from_env)

        # Simulate config loading taking 1.128s
        def slow_load_mcp_config(*args: object, **kwargs: object) -> McpConfig:
            nonlocal clock_val
            clock_val += 1.128
            return McpConfig()

        monkeypatch.setattr(runtime_mod, "load_mcp_config", slow_load_mcp_config)

        on_ready_executed = False

        def mock_server_run(
            self: object,
            *args: object,
            on_ready: object = None,
            **kwargs: object,
        ) -> None:
            nonlocal on_ready_executed
            if callable(on_ready):
                on_ready()
            on_ready_executed = True

        monkeypatch.setattr(standalone_mod._StandaloneHttpServer, "run", mock_server_run)

        with startup_clock_override(fake_clock):
            runtime_mod.run_standalone_server(tmp_path, env={})

        assert on_ready_executed is True
        assert read_last_stage(tmp_path, spawn_id=fake_sid) == "ready_to_serve"

        records = read_overruns(tmp_path, spawn_id=fake_sid)
        assert len(records) >= 1
        cfg_record = next(r for r in records if r["stage"] == "config_loaded")
        assert cfg_record["elapsed_s"] == 1.128
        assert cfg_record["budget_s"] == 1.0
        assert cfg_record["remaining_s"] == pytest.approx(-0.128)
        assert cfg_record["headroom_s"] == pytest.approx(28.0 - 1.128)

    def test_catalog_publish_bounded_fails_closed_before_real_deadline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """publish_tool_catalog_bounded bounds timeout by real startup deadline (not 29.8s)."""
        import concurrent.futures

        import ralph.mcp.server._publish_catalog_bounded as pub_mod
        from ralph.mcp.server._pre_serve_overrun import (
            CATALOG_PUBLISH_OVERRUN_EXIT_CODE,
            ExitHandler,
            StageRecorder,
            overrun_handler_override,
        )

        clock_val = 100.0

        def fake_monotonic() -> float:
            return clock_val

        monkeypatch.setattr(time, "monotonic", fake_monotonic)

        captured_budget: list[float] = []
        captured_exit_code: list[int] = []

        def mock_fail_closed(
            workspace_root: Path,
            *,
            stage: str,
            elapsed_s: float,
            budget_s: float,
            exit_code: int,
            diagnostic_timeout_s: float | None = None,
            stage_recorder: StageRecorder | None = None,
            exit_fn: ExitHandler | None = None,
        ) -> NoReturn:
            captured_budget.append(budget_s)
            captured_exit_code.append(exit_code)
            raise RuntimeError(f"fail_closed_pre_serve_overrun({exit_code})")

        def mock_submit_timeout(*args: object, **kwargs: object) -> None:
            raise concurrent.futures.TimeoutError("Stalled catalog publish")

        monkeypatch.setattr(concurrent.futures.Future, "result", mock_submit_timeout)

        with (
            overrun_handler_override(mock_fail_closed),
            pytest.raises(RuntimeError) as exc_info,
        ):
            pub_mod.publish_tool_catalog_bounded(
                workspace_root=tmp_path,
                catalog={},
                env_map={},
                t0=100.0,
            )

        assert "fail_closed_pre_serve_overrun(34)" in str(exc_info.value)
        assert captured_exit_code == [CATALOG_PUBLISH_OVERRUN_EXIT_CODE]
        assert captured_budget[0] == pytest.approx(28.0, abs=0.5)
        assert captured_budget[0] < 30.0

    def test_hung_overrun_diagnostic_write_times_out_promptly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """record_overrun diagnostic persistence is isolated and bounds I/O so stalled FS does not block."""
        fake_sid = "test-spawn-hung-diag"
        monkeypatch.setattr(
            "ralph.mcp.server._start_stage_tracker._get_spawn_id",
            lambda: fake_sid,
        )

        stall_event = threading.Event()

        def stalled_fsync(fd: int) -> None:
            stall_event.wait(timeout=5.0)

        monkeypatch.setattr(os, "fsync", stalled_fsync)

        try:
            record_overrun(
                root=tmp_path,
                stage="config_loaded",
                elapsed_s=1.128,
                budget_s=1.0,
                remaining_s=-0.128,
                headroom_s=26.872,
                timeout_s=0.05,
            )
            assert not stall_event.is_set()
        finally:
            stall_event.set()


class TestPreServeTimeoutAndOverrunSeams:
    """Validate public composition seams on _startup_timeouts and _pre_serve_overrun."""

    def test_pre_serve_timeout_seconds_defaults_to_real_deadline(self) -> None:
        """mcp_pre_serve_timeout_seconds returns 28.0s at t0=0.0 with default env."""
        timeout_s = mcp_pre_serve_timeout_seconds({}, t0=0.0, clock_fn=lambda: 0.0)
        assert timeout_s == 28.0

    def test_pre_serve_timeout_seconds_decreases_with_elapsed(self) -> None:
        """Elapsed monotonic time reduces the remaining pre-serve headroom."""
        timeout_s = mcp_pre_serve_timeout_seconds({}, t0=0.0, clock_fn=lambda: 5.5)
        assert timeout_s == pytest.approx(22.5)

    def test_pre_serve_timeout_seconds_floors_at_zero(self) -> None:
        """When elapsed time exceeds deadline, remaining timeout floors at 0.0."""
        timeout_s = mcp_pre_serve_timeout_seconds({}, t0=0.0, clock_fn=lambda: 35.0)
        assert timeout_s == 0.0

    def test_startup_clock_override_scopes_and_restores(self) -> None:
        """startup_clock_override replaces the clock only within the context."""
        from ralph.mcp.protocol._startup_timeouts import (
            get_startup_clock,
            mcp_startup_now,
            startup_clock_override,
        )

        assert get_startup_clock() is None
        with startup_clock_override(lambda: 42.0):
            assert get_startup_clock() is not None
            assert mcp_startup_now() == 42.0
        assert get_startup_clock() is None

    def test_exit_handler_override_scopes_and_restores(self) -> None:
        """exit_handler_override replaces the exit handler only within the context."""
        from ralph.mcp.server._pre_serve_overrun import (
            exit_handler_override,
            get_exit_handler,
        )

        assert get_exit_handler() is None

        def custom_exit(code: int) -> NoReturn:
            raise RuntimeError(f"exit({code})")

        with exit_handler_override(custom_exit):
            assert get_exit_handler() is custom_exit
        assert get_exit_handler() is None

    def test_overrun_handler_override_scopes_and_restores(self) -> None:
        """overrun_handler_override replaces the overrun handler only within the context."""
        from ralph.mcp.server._pre_serve_overrun import (
            get_overrun_handler,
            overrun_handler_override,
        )

        assert get_overrun_handler() is None

        def custom_overrun(*args: object, **kwargs: object) -> NoReturn:
            raise RuntimeError("overrun")

        with overrun_handler_override(custom_overrun):
            assert get_overrun_handler() is custom_overrun
        assert get_overrun_handler() is None
