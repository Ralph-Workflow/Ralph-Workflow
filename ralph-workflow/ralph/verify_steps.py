from __future__ import annotations

from typing import Final

VerifyStep = tuple[str, str, tuple[str, ...], float | None]

_VERIFY_STEP_TIMEOUT_SECONDS: Final = 30.0

_TOTAL_TEST_HANG_CAP_SECONDS: Final = 180.0

_VERIFY_STEPS: tuple[VerifyStep, ...] = (
    (
        "ruff check ralph/ tests/",
        "uv",
        ("run", "ruff", "check", "ralph/", "tests/"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "python -m mypy ralph/",
        "uv",
        ("run", "python", "-m", "mypy", "ralph/"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    ("make test", "make", ("test",), _TOTAL_TEST_HANG_CAP_SECONDS),
    (
        "ruff format --check ralph/ tests/",
        "uv",
        ("run", "ruff", "format", "--check", "ralph/", "tests/"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "lint bypass audit (audit_lint_bypass)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_lint_bypass"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "typecheck bypass audit (audit_typecheck_bypass)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_typecheck_bypass"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "policy audit (audit_test_policy)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_test_policy"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "mcp timeout audit (audit_mcp_timeout)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_mcp_timeout"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "di seam audit (audit_di_seam)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_di_seam"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "activity-aware watchdog audit (audit_activity_aware_watchdog)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_activity_aware_watchdog"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "watchdog drift audit (audit_watchdog_drift)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_watchdog_drift"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "parallelization dormant audit (audit_parallelization_dormant)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_parallelization_dormant"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "artifact submission canonical-path audit (audit_artifact_submission_canonical_path)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_artifact_submission_canonical_path"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "agent registry sync audit (audit_agent_registry_sync)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_agent_registry_sync"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "agent module state audit (audit_agent_module_state)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_agent_module_state"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "agent internal paths audit (audit_agent_internal_paths)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_agent_internal_paths"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "social-proof gate (verify_social_proof)",
        "python3",
        ("../scripts/verify_social_proof.py",),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "resource lifecycle audit (audit_resource_lifecycle)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_resource_lifecycle"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "skill auto-commit audit (audit_skill_auto_commit)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_skill_auto_commit"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "artifact skill types audit (audit_artifact_skill_types)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_artifact_skill_types"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "terminal escape containment audit (audit_terminal_escape_containment)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_terminal_escape_containment"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "repo structure audit (audit_repo_structure)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_repo_structure"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "project policy pack readiness audit (audit_project_policy)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_project_policy"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "fsevents watch consolidation audit (audit_fsevents_watch_consolidation)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_fsevents_watch_consolidation"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "log sink buffering audit (audit_log_sink_buffering)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_log_sink_buffering"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "filesystem write consolidation audit (audit_filesystem_write_consolidation)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_filesystem_write_consolidation"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "filesystem read consolidation audit (audit_filesystem_read_consolidation)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_filesystem_read_consolidation"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "filesystem polling/invocation ownership audit (audit_filesystem_polling_invocation)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_filesystem_polling_invocation"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "regression-test elimination audit (audit_regression_test_elimination)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_regression_test_elimination"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "template render-integrity audit (audit_template_render_integrity)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_template_render_integrity"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "fenced artifact example audit (audit_fenced_artifact_examples)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_fenced_artifact_examples"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "prompt single-sourcing audit (audit_prompt_single_sourcing)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_prompt_single_sourcing"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "cast policy audit (audit_cast_policy)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_cast_policy"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "workspace resource inventory audit (audit_workspace_resource_inventory)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_workspace_resource_inventory"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "canonical session text audit (audit_canonical_session_text)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_canonical_session_text"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "kwargs forwarding audit (audit_kwargs_forwarding)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_kwargs_forwarding"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "opts key drift audit (audit_opts_key_drift)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_opts_key_drift"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "agent failure single owner audit (audit_agent_failure_single_owner)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_agent_failure_single_owner"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "import order audit (audit_import_order)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_import_order"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "instruction integrity audit (audit_instruction_integrity)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_instruction_integrity"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "dependency integrity audit (audit_dependency_integrity)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_dependency_integrity"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "secret scan audit (audit_secret_scan)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_secret_scan"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "sast baseline-diff audit (audit_sast)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_sast"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "absolute comment and docstring policy audit (audit_comments)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_comments"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "test integrity audit (audit_test_integrity)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_test_integrity"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "integration invariants audit (audit_integration_invariants)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_integration_invariants"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "integration swallow audit (audit_integration_swallow)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_integration_swallow"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "integration single-source audit (audit_integration_single_source)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_integration_single_source"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "integration no-legacy audit (audit_integration_no_legacy)",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_integration_no_legacy"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
    (
        "make test-verification-smoke",
        "make",
        ("test-verification-smoke",),
        _TOTAL_TEST_HANG_CAP_SECONDS,
    ),
    (
        "audit_mcp_agent_boundary",
        "uv",
        ("run", "python", "-m", "ralph.testing.audit_mcp_agent_boundary"),
        _VERIFY_STEP_TIMEOUT_SECONDS,
    ),
)
