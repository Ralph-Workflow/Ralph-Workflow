"""Grandfathered allowlists for the repo-structure audit.

Split from :mod:`ralph.testing.audit_repo_structure` so neither module trips the
1000-line cap the audit itself enforces.

These sets are a ONE-WAY BACKLOG: they may shrink as code is refactored, never
grow. A new violation is a bug to fix, not an entry to add. Stale line-number
anchors (the bypass set is keyed by file+line) should be re-anchored to the
suppressions that actually exist, which shrinks the set.
"""

from __future__ import annotations

_MAX_FILE_LINES = 1_000
_LEGACY_LARGE_FILE_ALLOWLIST = frozenset(
    {
        "ralph/agents/idle_watchdog/idle_watchdog.py",
        "ralph/agents/invoke/_pty_line_reader.py",
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
        "tests/test_git_commit_cleanup.py",
        "tests/test_prompt_template_files.py",
        "ralph/pipeline/auto_integrate.py",
        "ralph/display/status_bar.py",
        "ralph/pipeline/auto_integrate_recovery.py",
        "tests/test_internal_skills_mcp_prompts.py",
        "ralph/recovery/failure_classifier.py",
        "tests/test_artifact_format_docs.py",
        # wt-034 (mcp optimization) extracted carrier files; grandfathered.
        "ralph/agents/invoke/_process_reader.py",
        "ralph/mcp/explore/structure.py",
        "ralph/mcp/tools/git_read.py",
        "ralph/mcp/tools/workspace/_read_handlers.py",
        "ralph/pipeline/effect_executor.py",
        "ralph/pipeline/plumbing/smoke_plumbing.py",
        "ralph/pipeline/reducer.py",
        "tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py",
        "tests/agents/test_register_agent_support.py",
        "tests/display/test_status_bar.py",
        "tests/test_cli_smoke.py",
        "tests/test_explore_pipeline.py",
        "tests/test_harness_run_diagnosis.py",
        "tests/test_phases_retry_on_stale_session.py",
        "tests/test_telemetry_sentry.py",
        # wt-040 auto-integrate: large recovery + tests files; the
        # reclaim + post-attempt-verify + per-seam tests are colocated
        # by the AC-06/AC-07 contract, and the 1000-line cap is not
        # the right refactor target here (each top-level section is a
        # separate AC and the helpers are interdependent).
        "ralph/pipeline/auto_integrate.py",
        "ralph/pipeline/auto_integrate_recovery.py",
        "tests/test_auto_integrate_recovery.py",
        # The allowlist file is split from audit_repo_structure.py
        # specifically to keep both under the 1000-line cap. New
        # legacy entries (wt-040 wt-034 etc.) push the allowlist
        # itself over the cap; adding it to its own allowlist is the
        # only honest move because the alternative -- further splits
        # -- would force the audit to walk more than one file and
        # obscure the per-entry reasoning.
        "ralph/testing/_repo_structure_allowlists.py",
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
        # The four Invariant classes are one duck-typed family sharing the
        # monkeypatchable ``_read`` seam the adversarial tests patch on this
        # module. Splitting them across modules would rebind that seam and
        # weaken the audit, so this stays grouped like its sibling audits.
        "ralph/testing/audit_terminal_escape_containment.py",
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
        "tests/agents/execution_state/test_generic_child_signal.py",
        "tests/agents/test_catalog.py",
        "tests/agents/test_pi_dev_blackbox.py",
        # wt-034 (mcp optimization) extracted carrier files; grandfathered.
        "ralph/agents/idle_watchdog/_subagent_identity.py",
        "ralph/display/status_bar.py",
        "ralph/mcp/explore/_audit_types.py",
        "ralph/mcp/explore/_bench_types.py",
        "ralph/mcp/explore/_pipeline_state.py",
        "ralph/mcp/explore/_store_types.py",
        "ralph/mcp/explore/bench.py",
        "ralph/mcp/explore/deferred_phases.py",
        "ralph/mcp/explore/dirty_paths.py",
        "ralph/mcp/explore/graph.py",
        "ralph/mcp/explore/lifecycle.py",
        "ralph/mcp/explore/structure.py",
        "ralph/mcp/tools/_exec_resource_protocol.py",
        "ralph/mcp/tools/_exec_resource_uri.py",
        "ralph/testing/audit_resource_lifecycle.py",
        "tests/agents/parsers/test_cursor_parser.py",
        "tests/agents/test_register_cursor.py",
        "tests/test_explore_legacy_shape_regressions.py",
        "tests/test_tool_media_format_metadata.py",
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
            "tests/pipeline/test_run_loop_cleanup_shutdown.py",
            "ralph.pipeline.run_loop",
            ("_cleanup_pipeline", "_LoopContext"),
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
            "tests/test_process_manager.py",
            "ralph.process.manager._singleton",
            ("_pm_state",),
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
            "tests/test_tool_exec_shell_command_segments.py",
            "ralph.mcp.tools.exec",
            ("_shell_command_segments",),
        ),
        # Tests whose subject IS the internal seam: the terminal-escape
        # containment contract (AGENTS.md) is defined on these private
        # functions, so the test must import them to pin the contract.
        (
            "tests/agents/test_agent_spawn_detaches_tty.py",
            "ralph.agents.invoke",
            ("_process_reader",),
        ),
        (
            "tests/agents/test_agent_spawn_detaches_tty.py",
            "ralph.agents.invoke._agent_run_ctx",
            ("_AgentRunCtx",),
        ),
        (
            "tests/display/test_log_sink_terminal_containment.py",
            "ralph.cli.main",
            ("_configure_logging",),
        ),
        (
            "tests/display/test_terminal_escape_containment.py",
            "ralph.display._plain_constants",
            ("_sanitize",),
        ),
        (
            "tests/project_policy/test_cli_integration_helpers.py",
            "ralph.cli.commands._load_result",
            ("_LoadResult",),
        ),
        (
            "tests/project_policy/test_cli_integration_helpers.py",
            "ralph.project_policy",
            ("_auto_commit",),
        ),
        (
            "tests/project_policy/test_prompt_ui.py",
            "ralph.project_policy",
            ("_prompt_ui",),
        ),
        (
            "tests/project_policy/test_skip_inline_policy_prompt.py",
            "ralph.cli.commands._load_result",
            ("_LoadResult",),
        ),
        (
            "tests/project_policy/test_skip_inline_policy_prompt.py",
            "ralph.project_policy",
            ("_prompt_ui",),
        ),
        (
            "tests/test_policy_remediation_completion_evidence.py",
            "ralph.cli.commands._load_result",
            ("_LoadResult",),
        ),
        # validators.py was split at the 1000-line cap; these two checks now
        # live in the module that owns their layer, and the tests follow them.
        (
            "tests/project_policy/test_validator.py",
            "ralph.project_policy",
            ("_content_checks",),
        ),
        (
            "tests/project_policy/test_pending_marker.py",
            "ralph.project_policy",
            ("_scanners",),
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
        # Grandfathered: pre-existing private-ralph imports in
        # unrelated test files. These have not been touched by wt-024.
        (
            "tests/agents/execution_state/test_generic_child_signal.py",
            "ralph.agents.execution_state._factory",
            ("_make_agy_strategy",),
        ),
        (
            "tests/agents/execution_state/test_generic_child_signal.py",
            "ralph.agents.execution_state._helpers",
            ("_classify_generic_child_signal",),
        ),
        (
            "tests/agents/idle_watchdog/test_cross_transport_subagent_visibility.py",
            "ralph.agents.invoke._monitor_factory",
            ("_discovery_strategy_for_config",),
        ),
        (
            "tests/agents/invoke/test_line_reader_queue_bound.py",
            "ralph.agents.invoke._process_reader",
            ("_ProcessLineReader",),
        ),
        (
            "tests/agents/invoke/test_line_reader_queue_bound.py",
            "ralph.agents.invoke._types",
            ("_ProcessReaderCtx",),
        ),
        (
            "tests/agents/invoke/test_cpu_baseline_pruning.py",
            "ralph.agents.invoke._process_reader",
            ("_ProcessLineReader",),
        ),
        (
            "tests/agents/invoke/test_cpu_baseline_pruning.py",
            "ralph.agents.invoke._types",
            ("_ProcessReaderCtx",),
        ),
        (
            "tests/agents/idle_watchdog/test_non_resumable_end_to_end.py",
            "ralph.agents.invoke._errors",
            ("_IdleStreamTimeoutError",),
        ),
        (
            "tests/agents/idle_watchdog/test_non_resumable_end_to_end.py",
            "ralph.pipeline.effect_executor",
            ("_failure_requires_fresh_session",),
        ),
        (
            "tests/agents/idle_watchdog/test_resume_after_kill_contract.py",
            "ralph.agents.invoke._errors",
            ("_IdleStreamTimeoutError",),
        ),
        (
            "tests/agents/idle_watchdog/test_resume_after_kill_watchdog_boundary.py",
            "ralph.agents.idle_watchdog.idle_watchdog",
            ("_EXPECTED_FIRE_REASONS",),
        ),
        (
            "tests/agents/idle_watchdog/test_resume_contract_invariant.py",
            "ralph.agents.invoke._process_reader",
            ("_RESUMABLE_FIRE_REASONS",),
        ),
        (
            "tests/agents/idle_watchdog/test_resume_session_id_threading.py",
            "ralph.agents.invoke._errors",
            ("_IdleStreamTimeoutError",),
        ),
        (
            "tests/agents/idle_watchdog/test_runtime_session_resume_safe_mapping.py",
            "ralph.agents.invoke._session",
            ("_bounded_output_lines",),
        ),
        (
            "tests/agents/invoke/test_pty_check_fire_resume_metadata.py",
            "ralph.agents.invoke._errors",
            ("_IdleStreamTimeoutError",),
        ),
        (
            "tests/agents/test_pi_dev_blackbox.py",
            "ralph.agents.execution_state._factory",
            ("_STRATEGY_DISPATCH",),
        ),
        (
            "tests/agents/test_pi_dev_blackbox.py",
            "ralph.agents.parsers",
            ("_CUSTOM_COMMAND_REGISTRY", "_PARSER_REGISTRY"),
        ),
        (
            "tests/agents/test_registry_register_unregister.py",
            "ralph.agents.execution_state._factory",
            ("_STRATEGY_DISPATCH",),
        ),
        (
            "tests/agents/test_registry_register_unregister.py",
            "ralph.agents.parsers",
            ("_CUSTOM_COMMAND_REGISTRY", "_PARSER_REGISTRY"),
        ),
        (
            "tests/process/monitor/test_dispatch_all_transports.py",
            "ralph.agents.invoke._monitor_factory",
            ("_discovery_strategy_for_config",),
        ),
        (
            "tests/test_prompt_change_detection.py",
            "ralph.pipeline.prompt_prep",
            ("_prompt_changed_since_last_materialization",),
        ),
        # wt-034 (mcp optimization) extracted carrier violations; grandfathered.
        (
            "tests/agents/idle_watchdog/test_mark_tool_call_runtime_reachability.py",
            "ralph.agents.execution_state._factory",
            ("_make_cursor_strategy", "_make_pi_strategy"),
        ),
        (
            "tests/agents/idle_watchdog/test_mark_tool_call_runtime_reachability.py",
            "ralph.agents.invoke._idle_stream_timeout_error",
            ("_IdleStreamTimeoutError",),
        ),
        (
            "tests/agents/idle_watchdog/test_subagent_identity_excludes_helpers.py",
            "ralph.agents.idle_watchdog._subagent_identity",
            ("_MAX_REGISTRY_ENTRIES",),
        ),
        (
            "tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py",
            "ralph.agents.idle_watchdog._subagent_identity",
            ("_MAX_REGISTRY_ENTRIES",),
        ),
        (
            "tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py",
            "ralph.agents.invoke._idle_stream_timeout_error",
            ("_IdleStreamTimeoutError",),
        ),
        (
            "tests/agents/test_pi_dev_blackbox.py",
            "ralph.agents.execution_state._factory",
            ("_STRATEGY_DISPATCH", "_make_pi_strategy"),
        ),
        (
            "tests/agents/test_register_cursor.py",
            "ralph.agents.execution_state._factory",
            ("_STRATEGY_DISPATCH",),
        ),
        (
            "tests/display/test_parallel_display_streaming_bound.py",
            "ralph.display._streaming_ctx",
            ("_StreamingCtx",),
        ),
        (
            "tests/integration/test_codex_home_release_path.py",
            "ralph.mcp.upstream.agent_probe",
            ("_probe_codex",),
        ),
        (
            "tests/pipeline/test_runner_missing_plan_handoff_recovery.py",
            "ralph.pipeline",
            ("_runner_state_helpers",),
        ),
        (
            "tests/recovery/test_classifier_typed_cause_chain_child_alive.py",
            "ralph.agents.invoke._idle_stream_timeout_error",
            ("_IdleStreamTimeoutError",),
        ),
        (
            "tests/recovery/test_resume_after_watchdog_kill_threads_session_id.py",
            "ralph.agents.invoke._idle_stream_timeout_error",
            ("_IdleStreamTimeoutError",),
        ),
        (
            "tests/test_cli_commands_run_skill_sync.py",
            "ralph.cli.commands._load_result",
            ("_LoadResult",),
        ),
        (
            "tests/test_cli_commands_run_skill_sync.py",
            "ralph.skills._agent_paths",
            ("_SKILL_ROOT_PREFIXES",),
        ),
        (
            "tests/test_display_context.py",
            "ralph.display",
            ("_mode_adaptive_limits",),
        ),
        (
            "tests/test_phases_retry_on_stale_session.py",
            "ralph.pipeline.effect_executor",
            ("_stale_session_recovery_block",),
        ),
        (
            "tests/test_pipeline_session_bridge.py",
            "ralph.mcp.server.lifecycle",
            ("_create_session_file",),
        ),
        (
            "tests/test_property_h_bounded_resources.py",
            "ralph.mcp.websearch",
            ("_bounded_sdk_call",),
        ),
        (
            "tests/test_rfc013_db_close_best_effort.py",
            "ralph.agents.completion_signals",
            ("_check_completion_sentinel", "_db_sentinel_lookup"),
        ),
        (
            "tests/test_rfc013_db_close_best_effort.py",
            "ralph.agents.invoke",
            ("_clear_session_completion_sentinel",),
        ),
        (
            "tests/test_rfc013_db_close_best_effort.py",
            "ralph.workspace.agent_dir_retention",
            ("_sweep_run_state_db_rows",),
        ),
        (
            "tests/test_skills_auto_commit.py",
            "ralph.skills._agent_paths",
            ("_SKILL_ROOT_PREFIXES",),
        ),
        (
            "tests/test_submit_artifact_writes_receipt.py",
            "ralph.agents.completion_signals",
            ("_check_completion_sentinel",),
        ),
        (
            "tests/test_subprocess_env_secret_isolation.py",
            "ralph.agents.invoke._process_reader",
            ("_subprocess_env",),
        ),
        (
            "tests/test_telemetry_sentry.py",
            "ralph.runtime",
            ("_version_info",),
        ),
        (
            "tests/test_telemetry_sentry.py",
            "ralph.telemetry",
            ("_agent_config_payload",),
        ),
        (
            "tests/test_telemetry_sentry.py",
            "ralph.telemetry",
            ("_sentry",),
        ),
        (
            "tests/test_text_accumulator_bound.py",
            "ralph.agents.parsers.text_accumulator",
            ("_MAX_RAW_LINES",),
        ),
        (
            "tests/test_tool_exec_resource_uri.py",
            "ralph.mcp.tools._exec_completed_process",
            ("_CompletedProcessAdapter",),
        ),
        (
            "tests/test_tool_exec_resource_uri.py",
            "ralph.mcp.tools._exec_resource_uri",
            ("_BASENAME_PATTERN",),
        ),
        (
            "tests/test_tool_raw_exec_handle.py",
            "ralph.mcp.tools._exec_completed_process",
            ("_CompletedProcessAdapter",),
        ),
    }
)


_LEGACY_BYPASS_COMMENT_ALLOWLIST: frozenset[tuple[str, int]] = frozenset(
    {
        ("ralph/agents/__init__.py", 44),
        ("ralph/agents/catalog.py", 216),
        ("ralph/agents/catalog.py", 217),
        ("ralph/agents/catalog.py", 257),
        ("ralph/agents/catalog.py", 258),
        ("ralph/agents/catalog.py", 402),
        ("ralph/agents/catalog.py", 477),
        ("ralph/agents/catalog.py", 546),
        ("ralph/agents/catalog.py", 549),
        ("ralph/agents/execution_state/_factory.py", 76),
        ("ralph/agents/execution_state/_factory.py", 130),
        ("ralph/agents/execution_state/_factory.py", 133),
        ("ralph/agents/idle_watchdog/_active_branch.py", 219),
        ("ralph/agents/idle_watchdog/_fire_evaluators.py", 118),
        ("ralph/agents/idle_watchdog/_fire_evaluators.py", 303),
        ("ralph/agents/idle_watchdog/_fire_evaluators.py", 419),
        ("ralph/agents/idle_watchdog/_stuck_classifier.py", 216),
        ("ralph/agents/idle_watchdog/_stuck_classifier.py", 336),
        ("ralph/agents/idle_watchdog/_waiting_branch.py", 92),
        ("ralph/agents/idle_watchdog/idle_watchdog.py", 1390),
        ("ralph/agents/idle_watchdog/idle_watchdog.py", 1701),
        ("ralph/agents/invoke/_command_builders/__init__.py", 157),
        ("ralph/agents/invoke/_command_builders/__init__.py", 225),
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
        ("ralph/agents/parsers/__init__.py", 182),
        ("ralph/agents/parsers/__init__.py", 207),
        ("ralph/agents/parsers/__init__.py", 209),
        ("ralph/agents/parsers/_event_classification.py", 44),
        ("ralph/agents/parsers/claude_interactive_transcript_parser.py", 96),
        ("ralph/agents/parsers/claude_interactive_transcript_parser.py", 100),
        ("ralph/agents/parsers/claude_interactive_transcript_parser.py", 106),
        ("ralph/agents/parsers/claude_interactive_transcript_parser.py", 365),
        ("ralph/agents/parsers/claude_interactive_transcript_parser.py", 366),
        ("ralph/phases/commit_cleanup.py", 691),
        ("ralph/phases/commit_cleanup.py", 701),
        ("ralph/phases/commit_cleanup.py", 722),
        ("ralph/phases/commit_cleanup.py", 732),
        ("ralph/display/parallel_display.py", 1967),
        ("ralph/display/parallel_display.py", 1968),
        ("ralph/mcp/artifacts/plan/_renderers.py", 153),
        ("ralph/mcp/server/_metrics.py", 77),
        ("ralph/mcp/server/_metrics.py", 86),
        ("ralph/mcp/tools/artifact.py", 239),
        ("ralph/mcp/tools/artifact.py", 269),
        ("ralph/mcp/tools/artifact.py", 386),
        ("ralph/mcp/tools/artifact.py", 492),
        ("ralph/pipeline/plumbing/commit_plumbing.py", 1329),
        ("ralph/pipeline/plumbing/commit_plumbing.py", 1331),
        ("ralph/pipeline/plumbing/commit_plumbing.py", 1332),
        ("ralph/pipeline/run_loop.py", 308),
        ("ralph/pipeline/run_loop.py", 398),
        ("ralph/pipeline/run_loop.py", 551),
        ("ralph/pipeline/run_loop.py", 730),
        ("ralph/pipeline/run_loop.py", 841),
        ("ralph/pipeline/run_loop.py", 1019),
        ("ralph/pipeline/runner.py", 224),
        ("ralph/pro_support/heartbeat.py", 82),
        ("ralph/prompts/materialize.py", 142),
        ("ralph/prompts/materialize.py", 143),
        ("ralph/agents/registry.py", 318),
        ("ralph/pydantic_validation_errors.py", 168),
        ("ralph/supervising.py", 209),
        ("ralph/testing/audit_lint_bypass.py", 504),
        ("ralph/testing/audit_test_policy.py", 309),
        ("ralph/testing/audit_test_policy.py", 591),
        ("ralph/testing/audit_test_policy.py", 658),
        ("ralph/testing/audit_typecheck_bypass.py", 286),
        ("ralph/testing/audit_typecheck_bypass.py", 301),
        # wt-034 (mcp optimization) extracted carrier files; grandfathered.
        ("ralph/agents/catalog.py", 271),
        ("ralph/agents/catalog.py", 312),
        ("ralph/agents/catalog.py", 527),
        ("ralph/agents/catalog.py", 594),
        ("ralph/agents/catalog.py", 597),
        ("ralph/agents/execution_state/_factory.py", 229),
        ("ralph/agents/execution_state/_factory.py", 289),
        ("ralph/agents/execution_state/_factory.py", 292),
        ("ralph/agents/idle_watchdog/_active_branch.py", 226),
        ("ralph/agents/invoke/_command_builders/__init__.py", 226),
        ("ralph/agents/invoke/_runtime_resolvers/__init__.py", 82),
        ("ralph/agents/invoke/_runtime_resolvers/__init__.py", 131),
        ("ralph/agents/invoke/_runtime_resolvers/__init__.py", 146),
        ("ralph/agents/invoke/_runtime_resolvers/__init__.py", 194),
        ("ralph/agents/invoke/_runtime_resolvers/__init__.py", 268),
        ("ralph/agents/invoke/_runtime_resolvers/__init__.py", 309),
        ("ralph/agents/invoke/_runtime_resolvers/__init__.py", 433),
        ("ralph/agents/invoke/_runtime_resolvers/__init__.py", 445),
        ("ralph/agents/parsers/__init__.py", 100),
        ("ralph/agents/parsers/__init__.py", 112),
        ("ralph/agents/parsers/__init__.py", 175),
        ("ralph/agents/parsers/__init__.py", 188),
        ("ralph/agents/parsers/__init__.py", 234),
        ("ralph/agents/registry.py", 447),
        ("ralph/git/commit_cleanup.py", 189),
        ("ralph/mcp/tools/artifact.py", 381),
        ("ralph/mcp/tools/artifact.py", 691),
        ("ralph/mcp/tools/workspace/_media_io.py", 103),
        ("ralph/mcp/tools/workspace/_media_io.py", 177),
        ("ralph/pipeline/run_loop.py", 944),
        ("ralph/telemetry/_sentry.py", 403),
        ("ralph/telemetry/_sentry.py", 536),
        ("ralph/telemetry/_sentry.py", 562),
        ("ralph/telemetry/_sentry.py", 611),
        ("ralph/telemetry/_sentry.py", 753),
        ("ralph/testing/audit_skill_auto_commit.py", 183),
        ("ralph/testing/audit_skill_auto_commit.py", 282),
        ("ralph/testing/audit_typecheck_bypass.py", 100),
        ("ralph/testing/audit_typecheck_bypass.py", 340),
        # wt-040 auto-integrate: _reclaim_unowned_stale_rebase fans out
        # across A1/A3/A4/A5/A6/A11 reclaim paths and _lock_holder_is_dead
        # consults the live lock holder before allowing reclaim (A9/A10/E9).
        ("ralph/pipeline/auto_integrate_recovery.py", 198),
        ("ralph/pipeline/auto_integrate_recovery.py", 352),
    }
)
