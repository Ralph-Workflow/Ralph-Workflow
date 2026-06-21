"""Repository-structure policy checks that scan the maintained source tree."""

from __future__ import annotations

import functools
import io
import tokenize
from pathlib import Path

import pytest

pytestmark = [pytest.mark.timeout_seconds(5), pytest.mark.subprocess_e2e]

REPO_ROOT = Path(__file__).resolve().parents[2]
RALPH_DIR = REPO_ROOT / "ralph"
TESTS_DIR = REPO_ROOT / "tests"
_REPO_ROOT_STANDALONE_FILES: tuple[Path, ...] = (REPO_ROOT / "conftest.py",)
_SKIP_DIRS = frozenset({"__pycache__", ".venv", "tmp"})
_MAX_FILE_LINES = 1_000
_LEGACY_LARGE_FILE_ALLOWLIST = frozenset(
    {
        "ralph/agents/idle_watchdog/idle_watchdog.py",
        "ralph/cli/main.py",
        "ralph/display/parallel_display.py",
        "ralph/mcp/artifacts/plan/__init__.py",
        "ralph/mcp/tools/artifact.py",
        "ralph/pipeline/plumbing/commit_plumbing.py",
        "ralph/pipeline/runner.py",
        "ralph/pipeline/run_loop.py",
        "ralph/prompts/materialize.py",
        "ralph/process/manager/_process_manager.py",
        "ralph/recovery/controller.py",
        "tests/agents/test_idle_watchdog_1.py",
        "tests/recovery/test_agent_unavailable_tracker.py",
        "tests/test_agents_invoke_1.py",
        "tests/test_agents_invoke_2.py",
        "tests/test_agents_invoke_4.py",
        "tests/test_cli_commands_1.py",
        "tests/test_cli_commands_2.py",
        "tests/test_interrupt_dispatcher.py",
        "tests/test_mcp_server_file_backed_session_capability_profile.py",
        "tests/test_mcp_server_image_content_serialization.py",
        "tests/test_mcp_server_load_runtime_upstream_servers.py",
        "tests/test_no_anti_drift_regression.py",
        "tests/test_phases_commit_cleanup.py",
        "tests/test_pipeline_runner.py",
        "tests/test_pipeline_runner_execute_agent_effect_2_a.py",
        "tests/test_plan_artifact.py",
        "tests/test_process_hardening.py",
        "tests/test_process_manager.py",
        "tests/test_pipeline_runner_execute_commit_effect.py",
        "tests/agents/parsers/test_pi_parser.py",
    }
)

# Grandfathered structural violations that predate the current enforcement
# pass. New files must not be added to these sets; they are a one-way
# backlog that should shrink as code is refactored.
_LEGACY_MULTIPLE_CLASS_ALLOWLIST = frozenset(
    {
        "ralph/agents/idle_watchdog/_evidence_tier.py",
        "ralph/agents/idle_watchdog/_stuck_classifier.py",
        "ralph/agents/invoke/_command_builders/__init__.py",
        "ralph/agents/invoke/_runtime_resolvers/__init__.py",
        "ralph/install.py",
        "ralph/mcp/artifacts/plan/_acceptance_criteria.py",
        "ralph/mcp/artifacts/plan/_size_limits.py",
        "ralph/mcp/protocol/session.py",
        "ralph/pipeline/factory.py",
        "ralph/pipeline/plumbing/smoke_plumbing.py",
        "ralph/pipeline/session_bridge.py",
        "ralph/pro_support/state_query.py",
        "ralph/process/child_liveness.py",
        "ralph/process/monitor/_discovery_strategy.py",
        "ralph/process/monitor/_process_monitor.py",
        "ralph/process/monitor/_subagent_output_capture.py",
        "ralph/process/teardown.py",
        "ralph/recovery/agent_unavailability_tracker.py",
        "ralph/recovery/unavailability_reason.py",
        "ralph/testing/audit_mcp_timeout.py",
        "ralph/testing/audit_test_policy.py",
        "tests/agents/idle_watchdog/test_activity_aware.py",
        "tests/agents/invoke/test_invoke_dispatch_parity.py",
        "tests/agents/invoke/test_pi_command_builder_and_runtime_resolver.py",
        "tests/agents/parsers/test_flush_invariants_consolidated.py",
        "tests/agents/parsers/test_ndjson_base.py",
        "tests/agents/parsers/test_pi_parser.py",
        "tests/agents/parsers/test_template_base.py",
        "tests/agents/test_consolidated_add_invoke_remove.py",
        "tests/agents/test_idle_watchdog_no_output_at_start_lifecycle.py",
        "tests/agents/test_invoke_agent_uses_spec_pty.py",
        "tests/agents/test_register_agent_support.py",
        "tests/agents/test_register_my_agent.py",
        "tests/agents/test_registration_shim.py",
        "tests/agents/test_registry_register_unregister.py",
        "tests/process/monitor/test_monitor_consolidation.py",
        "tests/recovery/test_unavailability_reason.py",
        "tests/test_analysis_loop_counter.py",
        "tests/test_no_anti_drift_recovery_invariants.py",
        "tests/test_no_anti_drift_regression.py",
        "tests/test_pipeline_factory.py",
        "tests/test_pipeline_factory_default.py",
        "tests/test_pipeline_runner.py",
        "tests/test_pipeline_runner_opencode_resume_build_agent_recovery_plan_resumable_session.py",
        "tests/test_pipeline_session_bridge.py",
        "tests/test_planning_subagent_guidance.py",
        "tests/test_pydantic_validation_errors.py",
        "tests/test_tool_exec_handle_exec_command.py",
        "tests/test_tool_unsafe_exec_handle.py",
    }
)

_LEGACY_PRIVATE_IMPORT_ALLOWLIST: frozenset[tuple[str, str, tuple[str, ...]]] = frozenset(
    {
        (
            "tests/integration/test_process_zombie_cleanup.py",
            "ralph.process.manager",
            ("_process_manager",),
        ),
        (
            "tests/pipeline/test_run_loop_interrupt.py",
            "ralph.pipeline.run_loop",
            ("_handle_keyboard_interrupt", "_LoopContext"),
        ),
        (
            "tests/recovery/test_agent_unavailable_tracker.py",
            "ralph.pipeline.run_loop",
            ("_apply_connectivity_check",),
        ),
        (
            "tests/test_agent_registry_agy_model_alias.py",
            "ralph.agents.registry",
            ("_resolve_dynamic_agent",),
        ),
        (
            "tests/test_claude_interactive_timeout_reason.py",
            "ralph.agents.invoke._errors",
            ("_IdleStreamTimeoutError",),
        ),
        (
            "tests/test_cli_commands_run_mcp_preflight.py",
            "ralph.cli.commands._execute_pipeline_request",
            ("_ExecutePipelineRequest",),
        ),
        (
            "tests/test_completion_invocation_exit_logging.py",
            "ralph.agents.invoke._completion",
            ("_log_invocation_exit",),
        ),
        (
            "tests/test_in_memory_transport_round_trip.py",
            "ralph.mcp.server",
            ("_fallback_http_handler",),
        ),
        (
            "tests/test_list_directory_recursive_bounded.py",
            "ralph.mcp.tools.workspace",
            ("_list_ops",),
        ),
        (
            "tests/test_mcp_contract_single_source_of_truth.py",
            "ralph.mcp.protocol.startup",
            ("_visible_mcp_tool_names_owned",),
        ),
        (
            "tests/test_mcp_contract_single_source_of_truth.py",
            "ralph.mcp.server.lifecycle",
            ("_visible_mcp_tool_names_owned",),
        ),
        (
            "tests/test_mcp_server_exec_result_contract.py",
            "ralph.mcp.server._fallback_http_handler",
            ("_FallbackHttpHandler",),
        ),
        (
            "tests/test_mcp_server_exec_streaming_post_final_frame.py",
            "ralph.mcp.server._fallback_http_handler",
            ("_FallbackHttpHandler",),
        ),
        (
            "tests/test_mcp_server_exec_streaming_post_final_frame.py",
            "ralph.mcp.server._fallback_http_server",
            ("_FallbackHttpServer",),
        ),
        (
            "tests/test_mcp_server_fallback_handler_hardening.py",
            "ralph.mcp.server._fallback_http_handler",
            ("_FallbackHttpHandler",),
        ),
        (
            "tests/test_mcp_server_fallback_handler_hardening.py",
            "ralph.mcp.server._fallback_http_server",
            ("_FallbackHttpServer",),
        ),
        (
            "tests/test_mcp_server_http_serialization_safety.py",
            "ralph.mcp.server._fallback_http_handler",
            ("_FallbackHttpHandler",),
        ),
        (
            "tests/test_mcp_server_http_serialization_safety.py",
            "ralph.mcp.server._fallback_http_server",
            ("_FallbackHttpServer",),
        ),
        (
            "tests/test_mcp_server_lifecycle_server_output_persisted.py",
            "ralph.mcp.server.lifecycle",
            ("_spawn_process",),
        ),
        (
            "tests/test_mcp_transport.py",
            "ralph.mcp.upstream._stdio_upstream_client",
            ("_make_stdio_caller",),
        ),
        (
            "tests/test_opencode_session_execution_check_process_result_completion_seam.py",
            "ralph.agents.invoke",
            ("_completion",),
        ),
        (
            "tests/test_pipeline_runner_execute_agent_effect_2_a.py",
            "ralph.pipeline",
            ("_runner_session",),
        ),
        (
            "tests/test_plan_artifact.py",
            "ralph.mcp.artifacts.plan._plan_step",
            ("_STEP_TYPE_ALIASES",),
        ),
        (
            "tests/test_pro_support_prompt.py",
            "ralph.prompts.system_prompt",
            ("_sync_current_prompt_file",),
        ),
        (
            "tests/test_process_hardening.py",
            "ralph.process.manager._process_manager",
            ("_TERMINAL_STATUSES",),
        ),
        (
            "tests/test_prompt_helper_run.py",
            "ralph.cli.commands.prompt_helper",
            ("_run_single_invoke",),
        ),
        (
            "tests/test_property_a_one_transport_one_behavior.py",
            "ralph.mcp.server",
            ("_fallback_http_handler",),
        ),
        (
            "tests/test_property_c_liveness_contract.py",
            "ralph.mcp.server",
            ("_in_memory_transport",),
        ),
        (
            "tests/test_property_c_liveness_contract.py",
            "ralph.mcp.server._fallback_http_handler_probe",
            ("_ProbeResult",),
        ),
        (
            "tests/test_property_c_liveness_contract.py",
            "ralph.mcp.server._fallback_http_server",
            ("_FallbackHttpServer",),
        ),
        (
            "tests/test_property_d_failure_observability.py",
            "ralph.mcp.server",
            ("_in_memory_transport",),
        ),
        (
            "tests/test_property_d_failure_observability.py",
            "ralph.mcp.server._fallback_http_handler_probe",
            ("_ProbeResult",),
        ),
        (
            "tests/test_property_d_failure_observability.py",
            "ralph.mcp.server._fallback_http_server",
            ("_FallbackHttpServer",),
        ),
        (
            "tests/test_property_e_streaming_terminates.py",
            "ralph.mcp.server._fallback_http_handler",
            ("_FallbackHttpHandler",),
        ),
        (
            "tests/test_property_e_streaming_terminates.py",
            "ralph.mcp.server._fallback_http_server",
            ("_FallbackHttpServer",),
        ),
        ("tests/test_property_g_recovery_signal.py", "ralph.agents.execution_state", ("_helpers",)),
        (
            "tests/test_property_g_recovery_signal.py",
            "ralph.agents.execution_state._helpers",
            ("_registry_check_for_exit",),
        ),
        (
            "tests/test_property_g_recovery_signal.py",
            "ralph.mcp.server",
            ("_fallback_http_handler",),
        ),
        (
            "tests/test_property_h_bounded_resources.py",
            "ralph.mcp.server",
            ("_saturated_dispatch",),
        ),
        ("tests/test_property_i_timing_safety.py", "ralph.mcp.server", ("_timing_safety",)),
        (
            "tests/test_property_l_zero_progress_and_resume.py",
            "ralph.pipeline.effect_executor",
            ("_write_agent_retry_prompt",),
        ),
        (
            "tests/test_property_n_spill_inside_workspace.py",
            "ralph.mcp.tools",
            ("_exec_output_spill",),
        ),
        (
            "tests/test_pty_transcript_path_resolution.py",
            "ralph.agents.invoke",
            ("_pty_transcript",),
        ),
        (
            "tests/test_resume_action_aware_prompt.py",
            "ralph.pipeline.effect_executor",
            ("_write_agent_retry_prompt",),
        ),
        (
            "tests/test_session_wrapup.py",
            "ralph.mcp.server._fallback_http_handler",
            ("_FallbackHttpHandler",),
        ),
        (
            "tests/test_session_wrapup.py",
            "ralph.mcp.server._fallback_http_server",
            ("_FallbackHttpServer",),
        ),
        (
            "tests/test_session_wrapup.py",
            "ralph.mcp.server._fallback_standalone_server",
            ("_FallbackStandaloneServer",),
        ),
        ("tests/test_skills_agent_paths_research.py", "ralph.skills", ("_agent_paths",)),
        (
            "tests/test_telemetry_sentry.py",
            "ralph.telemetry._sentry",
            ("_scrub_event", "_scrub_obj"),
        ),
        (
            "tests/test_tool_exec_large_output_spill.py",
            "ralph.mcp.tools._exec_completed_process",
            ("_CompletedProcessAdapter",),
        ),
        ("tests/test_tool_spec_default_consistency.py", "ralph.mcp.tools", ("_exec_output_spill",)),
        (
            "tests/test_tool_spec_default_consistency.py",
            "ralph.mcp.tools.artifact",
            ("_section_mode",),
        ),
        (
            "tests/test_tool_spec_default_consistency.py",
            "ralph.mcp.tools.websearch",
            ("_DEFAULT_LIMIT",),
        ),
        (
            "tests/test_tool_spec_default_consistency.py",
            "ralph.mcp.tools.workspace._utils",
            ("_GREP_DEFAULT_LIMIT",),
        ),
        (
            "tests/test_tool_unsafe_exec_handle.py",
            "ralph.mcp.tools._exec_completed_process",
            ("_CompletedProcessAdapter",),
        ),
        (
            "tests/test_tool_workspace_handle_read_media.py",
            "ralph.mcp.tools.workspace",
            ("_media_io",),
        ),
        (
            "tests/agents/execution_state/test_strategy_factory_dispatch.py",
            "ralph.agents.execution_state._factory",
            ("_STRATEGY_DISPATCH",),
        ),
        (
            "tests/agents/invoke/test_dispatch_table_covers_every_transport.py",
            "ralph.agents.execution_state._factory",
            ("_STRATEGY_DISPATCH",),
        ),
        (
            "tests/agents/invoke/test_invoke_dispatch_recipe.py",
            "ralph.agents.invoke._types",
            ("_BuildCommandOptions",),
        ),
        (
            "tests/agents/parsers/test_codex_uses_ndjson_base.py",
            "ralph.agents.parsers.codex",
            ("_parse_codex_object",),
        ),
        (
            "tests/agents/test_builtin_spec_consolidation.py",
            "ralph.agents.execution_state._factory",
            ("_make_agy_strategy",),
        ),
        (
            "tests/agents/test_catalog.py",
            "ralph.agents.parsers",
            ("_CUSTOM_COMMAND_REGISTRY", "_PARSER_REGISTRY"),
        ),
        (
            "tests/agents/test_legacy_module_dicts_are_views.py",
            "ralph.agents.execution_state._factory",
            ("_STRATEGY_DISPATCH",),
        ),
        (
            "tests/agents/test_register_agent_support.py",
            "ralph.agents.execution_state._factory",
            ("_STRATEGY_DISPATCH",),
        ),
        (
            "tests/agents/test_register_my_agent.py",
            "ralph.agents.execution_state._factory",
            ("_make_agy_strategy",),
        ),
        (
            "tests/agents/test_registry_seeds_catalog.py",
            "ralph.agents.execution_state._factory",
            ("_STRATEGY_DISPATCH",),
        ),
        (
            "tests/pipeline/test_run_loop_unavailable_surface.py",
            "ralph.pipeline.run_loop",
            ("_LoopContext",),
        ),
        (
            "tests/pipeline/test_run_loop_waiting_state_logs.py",
            "ralph.pipeline.run_loop",
            ("_LoopContext", "_run_inner_loop"),
        ),
        (
            "tests/pipeline/test_run_loop_waiting_state_real_controller.py",
            "ralph.pipeline.run_loop",
            ("_LoopContext", "_run_inner_loop"),
        ),
        (
            "tests/recovery/test_unavailability_reason.py",
            "ralph.recovery.failure_classifier",
            ("_classify_unavailability_reason",),
        ),
        (
            "tests/test_agy_live_regression.py",
            "ralph.agents.invoke._agent_run_ctx",
            ("_AgentRunCtx",),
        ),
        (
            "tests/test_audit_artifact_submission_canonical_path.py",
            "ralph.mcp.tools.artifact",
            ("_KNOWN_ARTIFACT_TYPES",),
        ),
        (
            "tests/test_audit_artifact_submission_canonical_types_sync.py",
            "ralph.mcp.tools.artifact",
            ("_KNOWN_ARTIFACT_TYPES",),
        ),
        (
            "tests/test_canonical_artifact_submit.py",
            "ralph.agents.execution_state._helpers",
            ("_check_signals_terminal",),
        ),
        (
            "tests/test_claude_interactive_pty.py",
            "ralph.agents.registry",
            ("_seed_catalog_with_builtins",),
        ),
        (
            "tests/test_smoke_canonical_path.py",
            "ralph.pipeline.plumbing.smoke_plumbing",
            ("_run_smoke_agent",),
        ),
        (
            "tests/test_smoke_plumbing_uses_canonical_submit.py",
            "ralph.pipeline.plumbing.smoke_plumbing",
            ("_run_smoke_agent",),
        ),
    }
)

_LEGACY_BYPASS_COMMENT_ALLOWLIST: frozenset[tuple[str, int]] = frozenset(
    {
        ("ralph/agents/__init__.py", 44),
        ("ralph/agents/catalog.py", 217),
        ("ralph/agents/catalog.py", 258),
        ("ralph/agents/catalog.py", 402),
        ("ralph/agents/execution_state/_factory.py", 76),
        ("ralph/agents/execution_state/_factory.py", 130),
        ("ralph/agents/execution_state/_factory.py", 133),
        ("ralph/agents/idle_watchdog/_stuck_classifier.py", 216),
        ("ralph/agents/idle_watchdog/idle_watchdog.py", 1390),
        ("ralph/agents/idle_watchdog/idle_watchdog.py", 1701),
        ("ralph/agents/invoke/_command_builders/__init__.py", 157),
        ("ralph/agents/invoke/_runtime_resolvers/__init__.py", 78),
        ("ralph/agents/invoke/_runtime_resolvers/__init__.py", 127),
        ("ralph/agents/invoke/_runtime_resolvers/__init__.py", 142),
        ("ralph/agents/invoke/_runtime_resolvers/__init__.py", 190),
        ("ralph/agents/invoke/_runtime_resolvers/__init__.py", 238),
        ("ralph/agents/invoke/_runtime_resolvers/__init__.py", 279),
        ("ralph/agents/parsers/__init__.py", 94),
        ("ralph/agents/parsers/__init__.py", 106),
        ("ralph/agents/parsers/__init__.py", 169),
        ("ralph/agents/parsers/__init__.py", 181),
        ("ralph/agents/parsers/__init__.py", 207),
        ("ralph/agents/parsers/_event_classification.py", 44),
        ("ralph/agents/parsers/claude_interactive_transcript_parser.py", 96),
        ("ralph/agents/parsers/claude_interactive_transcript_parser.py", 100),
        ("ralph/agents/parsers/claude_interactive_transcript_parser.py", 365),
        ("ralph/agents/parsers/claude_interactive_transcript_parser.py", 366),
        ("ralph/phases/commit_cleanup.py", 691),
        ("ralph/phases/commit_cleanup.py", 701),
        ("ralph/display/parallel_display.py", 1967),
        ("ralph/display/parallel_display.py", 1968),
        ("ralph/mcp/artifacts/plan/_renderers.py", 153),
        ("ralph/mcp/server/_metrics.py", 77),
        ("ralph/mcp/server/_metrics.py", 86),
        ("ralph/mcp/tools/artifact.py", 239),
        ("ralph/mcp/tools/artifact.py", 386),
        ("ralph/pipeline/plumbing/commit_plumbing.py", 1329),
        ("ralph/pipeline/plumbing/commit_plumbing.py", 1331),
        ("ralph/pipeline/run_loop.py", 308),
        ("ralph/pipeline/run_loop.py", 391),
        ("ralph/pipeline/run_loop.py", 551),
        ("ralph/pipeline/run_loop.py", 704),
        ("ralph/pipeline/run_loop.py", 841),
        ("ralph/pipeline/run_loop.py", 992),
        ("ralph/pipeline/runner.py", 224),
        ("ralph/pro_support/heartbeat.py", 82),
        ("ralph/prompts/materialize.py", 142),
        ("ralph/pydantic_validation_errors.py", 168),
        ("ralph/supervising.py", 209),
        ("ralph/testing/audit_lint_bypass.py", 267),
        ("ralph/testing/audit_lint_bypass.py", 298),
        ("ralph/testing/audit_test_policy.py", 255),
        ("ralph/testing/audit_test_policy.py", 267),
        ("ralph/testing/audit_test_policy.py", 538),
        ("ralph/testing/audit_test_policy.py", 549),
        ("ralph/testing/audit_test_policy.py", 608),
        ("ralph/testing/audit_test_policy.py", 616),
        ("ralph/testing/audit_typecheck_bypass.py", 286),
        ("ralph/testing/audit_typecheck_bypass.py", 301),
    }
)
_TYPE_IGNORE_MARKER = "# type:" + " ignore"
_NOQA_MARKER = "# no" + "qa"
_EXTERNAL_LIBRARY_SUFFIX = (
    "# reason: external library has no type support, see "
    "docs/agents/type-ignore-policy.md#external-library"
)
_AUTOGENERATED_SUFFIX = (
    "# reason: autogenerated code has no type support, see "
    "docs/agents/type-ignore-policy.md#autogenerated-code"
)


@functools.cache
def _all_py_files(base: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for path in base.rglob("*.py")
            if not any(part in _SKIP_DIRS for part in path.parts)
        )
    )


@functools.cache
def _read_path(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _scan_private_imports(lines: tuple[str, ...]) -> tuple[tuple[str, list[str]], ...]:
    results: list[tuple[str, list[str]]] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped.startswith("from ralph") or " import " not in stripped:
            continue
        module_part, imported_part = stripped.split(" import ", 1)
        module = module_part[len("from ") :].strip()
        if not module.startswith("ralph"):
            continue
        private_names = []
        for item in imported_part.split(","):
            name = item.split("#", 1)[0].strip()
            if not name:
                continue
            if " as " in name:
                name = name.split(" as ", 1)[0].strip()
            if name.startswith("_"):
                private_names.append(name)
        if private_names:
            results.append((module, private_names))
    return tuple(results)


def _record_bypass_comment(
    *,
    tok_string: str,
    start_row: int,
    lines: tuple[str, ...],
    bypass_comments: list[tuple[int, str]],
) -> None:
    if _TYPE_IGNORE_MARKER not in tok_string and _NOQA_MARKER not in tok_string:
        return
    line = lines[start_row - 1].rstrip() if start_row - 1 < len(lines) else ""
    if line.endswith(_EXTERNAL_LIBRARY_SUFFIX) or line.endswith(_AUTOGENERATED_SUFFIX):
        return
    bypass_comments.append((start_row, line))


def _scan_structure(
    src: str, lines: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[tuple[int, str, str], ...], tuple[tuple[int, str], ...]]:
    top_level_classes: list[str] = []
    nested_classes: list[tuple[int, str, str]] = []
    bypass_comments: list[tuple[int, str]] = []
    indent_level = 0
    class_stack: list[tuple[str, int]] = []
    pending_class_name: str | None = None
    pending_top_level = False
    pending_nested_outer: str | None = None

    try:
        tokens = tokenize.generate_tokens(io.StringIO(src).readline)
        for tok_type, tok_string, (start_row, _), *_rest in tokens:
            if tok_type == tokenize.INDENT:
                indent_level += 1
                if pending_class_name is not None:
                    class_stack.append((pending_class_name, indent_level))
                    pending_class_name = None
                continue
            if tok_type == tokenize.DEDENT:
                indent_level = max(0, indent_level - 1)
                while class_stack and class_stack[-1][1] > indent_level:
                    class_stack.pop()
                continue
            if tok_type == tokenize.COMMENT:
                _record_bypass_comment(
                    tok_string=tok_string,
                    start_row=start_row,
                    lines=lines,
                    bypass_comments=bypass_comments,
                )
                continue
            if tok_type == tokenize.NAME and tok_string == "class":
                pending_class_name = None
                pending_top_level = indent_level == 0
                pending_nested_outer = class_stack[-1][0] if class_stack else None
                continue
            if (
                pending_top_level or pending_nested_outer is not None
            ) and tok_type == tokenize.NAME:
                if pending_top_level and not tok_string.startswith("_"):
                    top_level_classes.append(tok_string)
                if pending_nested_outer is not None and indent_level > 0:
                    nested_classes.append((start_row, pending_nested_outer, tok_string))
                pending_class_name = tok_string
                pending_top_level = False
                pending_nested_outer = None
                continue
            if (pending_top_level or pending_nested_outer is not None) and tok_type not in {
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.COMMENT,
            }:
                pending_top_level = False
                pending_nested_outer = None
    except tokenize.TokenError:
        return (), (), ()

    return tuple(top_level_classes), tuple(nested_classes), tuple(bypass_comments)


@functools.cache
def _analysis(path: Path) -> dict[str, object]:
    src = _read_path(path)
    lines = tuple(src.splitlines())
    top_level_classes, nested_classes, bypass_comments = _scan_structure(src, lines)
    return {
        "line_count": len(lines),
        "top_level_classes": top_level_classes,
        "nested_classes": nested_classes,
        "bypass_comments": bypass_comments,
        "private_ralph_imports": _scan_private_imports(lines),
    }


def _collect_source_violations() -> list[str]:
    violations: list[str] = []
    for base in (RALPH_DIR, TESTS_DIR):
        for path in _all_py_files(base):
            rel = str(path.relative_to(REPO_ROOT))
            data = _analysis(path)
            line_count = int(data["line_count"])
            if rel not in _LEGACY_LARGE_FILE_ALLOWLIST and line_count > _MAX_FILE_LINES:
                violations.append(f"file too large: {line_count} lines: {rel}")
            classes = list(data["top_level_classes"])
            if len(classes) > 1 and rel not in _LEGACY_MULTIPLE_CLASS_ALLOWLIST:
                violations.append(f"multiple top-level classes: {rel}: {classes[:5]}")
            for module, names in list(data["private_ralph_imports"]):
                if not path.is_relative_to(TESTS_DIR):
                    continue
                key = (rel, module, tuple(names))
                if key in _LEGACY_PRIVATE_IMPORT_ALLOWLIST:
                    continue
                violations.append(f"private ralph import: {rel}: from {module} import {names}")
            for lineno, line in list(data["bypass_comments"]):
                if (rel, lineno) in _LEGACY_BYPASS_COMMENT_ALLOWLIST:
                    continue
                violations.append(f"bypass comment: {rel}:{lineno}: {line}")
            if path.is_relative_to(RALPH_DIR):
                for lineno, outer, inner in list(data["nested_classes"]):
                    violations.append(f"nested class: {rel}:{lineno}: {outer}.{inner}")
    return violations


def _collect_repo_root_violations() -> list[str]:
    violations: list[str] = []
    for path in _REPO_ROOT_STANDALONE_FILES:
        if not path.exists():
            violations.append(f"expected file not found: {path.relative_to(REPO_ROOT)}")
            continue
        rel = str(path.relative_to(REPO_ROOT))
        data = _analysis(path)
        line_count = int(data["line_count"])
        if line_count > _MAX_FILE_LINES:
            violations.append(f"repo-root file too large: {line_count} lines: {rel}")
        classes = list(data["top_level_classes"])
        if len(classes) > 1:
            violations.append(f"repo-root multiple classes: {rel}: {classes}")
        for lineno, outer, inner in list(data["nested_classes"]):
            violations.append(f"repo-root nested class: {rel}:{lineno}: {outer}.{inner}")
        for lineno, line in list(data["bypass_comments"]):
            violations.append(f"repo-root bypass comment: {rel}:{lineno}: {line}")
    return violations


def test_repo_structure_policies_hold() -> None:
    source_violations = _collect_source_violations()
    repo_root_violations = _collect_repo_root_violations()

    assert not source_violations and not repo_root_violations, (
        "source violations:\n"
        + "\n".join(sorted(source_violations))
        + "\n\nrepo-root violations:\n"
        + "\n".join(sorted(repo_root_violations))
    )
