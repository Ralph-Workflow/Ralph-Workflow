from __future__ import annotations

from ralph.mcp.server._mcp_server_error import McpServerError


class TestMcpServerErrorEnriched:
    def test_all_12_fields_populated(self) -> None:
        err = McpServerError(
            "probe overrun",
            restart_count=1,
            last_stage="registry_built",
            elapsed_s=2.0,
            restart_reason="probe_failed",
            handover_step="spawn",
            probe_duration_s=6.88,
            probe_timeout_s=5.0,
            child_pid=1234,
            psutil_status="disk-sleep",
            startup_diagnostic="child wrote no log output",
            failure_kind="budget_exhausted",
        )
        assert err.restart_count == 1
        assert err.last_stage == "registry_built"
        assert err.elapsed_s == 2.0
        assert err.restart_reason == "probe_failed"
        assert err.handover_step == "spawn"
        assert err.probe_duration_s == 6.88
        assert err.probe_timeout_s == 5.0
        assert err.child_pid == 1234
        assert err.psutil_status == "disk-sleep"
        assert err.startup_diagnostic == "child wrote no log output"
        assert err.failure_kind == "budget_exhausted"

    def test_str_contains_restart_reason_and_handover_step(self) -> None:
        err = McpServerError(
            "x",
            restart_count=1,
            restart_reason="probe_failed",
            handover_step="spawn",
        )
        s = str(err)
        assert "restart_reason=probe_failed" in s
        assert "handover_step=spawn" in s

    def test_str_contains_probe_durations(self) -> None:
        err = McpServerError(
            "x",
            restart_count=1,
            probe_duration_s=6.88,
            probe_timeout_s=5.0,
        )
        s = str(err)
        assert "probe_duration_s=6.88" in s
        assert "probe_timeout_s=5.0" in s

    def test_str_contains_psutil_status_and_failure_kind(self) -> None:
        err = McpServerError(
            "x",
            restart_count=1,
            psutil_status="disk-sleep",
            failure_kind="budget_exhausted",
        )
        s = str(err)
        assert "psutil_status=disk-sleep" in s
        assert "failure_kind=budget_exhausted" in s

    def test_existing_call_site_still_works(self) -> None:
        err = McpServerError("x", restart_count=1)
        assert err.restart_count == 1
        assert err.last_stage is None
        assert err.elapsed_s is None
        assert err.restart_reason is None
        assert err.handover_step is None
        assert err.probe_duration_s is None
        assert err.probe_timeout_s is None
        assert err.child_pid is None
        assert err.psutil_status is None
        assert err.startup_diagnostic == ""
        assert err.failure_kind == "transient"
        assert "x" in str(err)

    def test_str_renders_all_populated_fields_semicolon_separated(self) -> None:
        err = McpServerError(
            "child process died",
            restart_count=2,
            last_stage="registry_built",
            elapsed_s=3.5,
            restart_reason="preflight_timeout",
            handover_step="spawn",
            probe_duration_s=5.1,
            probe_timeout_s=5.0,
            child_pid=9999,
            psutil_status="zombie",
            startup_diagnostic="bad exit",
            failure_kind="budget_exhausted",
        )
        s = str(err)
        expected = (
            "child process died; "
            "restart_count=2; "
            "last_stage=registry_built; "
            "elapsed_s=3.5; "
            "restart_reason=preflight_timeout; "
            "handover_step=spawn; "
            "probe_duration_s=5.1; "
            "probe_timeout_s=5.0; "
            "child_pid=9999; "
            "psutil_status=zombie; "
            "startup_diagnostic=bad exit; "
            "failure_kind=budget_exhausted"
        )
        assert s == expected
