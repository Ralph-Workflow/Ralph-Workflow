.PHONY: help install dev install-dev test test-unit test-integration test-subprocess-e2e lint typecheck build build-wheel build-sdist publish test-pypi test-pypi-upload twine-upload twine-upload-testpypi clean dist clean-dist fmt format-check ruff-fix mypy-fix pre-commit dist-homebrew verify coverage dist-binary dist-pypi dist-all formula-check dead-code docs docs-linkcheck serve-docs packaging-smoke

PYTEST_WORKERS ?= 8
PYTEST_WORKERS_C_AH ?= 4
PYTEST_WORKERS_C_IL ?= 8
PYTEST_WORKERS_C_MO ?= 4
PYTEST_WORKERS_C_PZ ?= 4
PYTEST_WORKERS_D ?= 4
PYTEST_WORKERS_D_REST ?= 4
PYTEST_WORKERS_G_H ?= 4
PYTEST_WORKERS_MCP ?= 4
PYTEST_WORKERS_A_HN_MISC ?= 4
PYTEST_WORKERS_UNIT_DISPLAY ?= 4
PYTEST_WORKERS_PM_PZ ?= 8
PYTEST_WORKERS_A_OZ ?= 12
PYTEST_WORKERS_AF_G ?= 8
PYTEST_WORKERS_INTEGRATION ?= 8
PYTEST_SUITE_TIMEOUT_SECONDS ?= 30
PYTEST_CORE_AGENT_PATHS = tests/agents/test_idle_watchdog_1.py tests/agents/test_idle_watchdog_2.py tests/agents/test_post_exit_watchdog.py tests/agents/test_timeout_clock.py
PYTEST_CORE_AGENT_PATHS_2 = tests/agents/test_invoke_mcp_merge.py tests/agents/test_invoke_timeout_integration.py
PYTEST_CORE_CONFIG_PATHS = tests/config
PYTEST_CORE_DISPLAY_CONTEXT_PATHS = tests/display/test_di_invariants.py
PYTEST_CORE_DISPLAY_MODE_PATHS = tests/display/test_mode_adaptiveness.py
PYTEST_CORE_DISPLAY_PHASE_PATHS = tests/display/test_phase_banner_modes.py tests/display/test_plain_renderer_waiting.py
PYTEST_CORE_DISPLAY_REST_PATHS = tests/display/test_subscriber.py tests/display/test_supervising_subscriber.py
PYTEST_CORE_FIXTURES_PATHS = tests/fixtures
PYTEST_CORE_UNIT_AGENT_PATHS = tests/unit/agents
PYTEST_CORE_UNIT_DISPLAY_PATHS = tests/unit/display
PYTEST_CORE_UNIT_PIPELINE_PATHS = tests/unit/pipeline
PYTEST_RUNTIME_MCP_PATHS = tests/mcp
PYTEST_RUNTIME_PIPELINE_PATHS = tests/pipeline
PYTEST_RUNTIME_RECOVERY_PATHS = tests/recovery
PYTEST_ROOT_PATHS_A_AC = tests/test_a[a-cA-C]*.py
PYTEST_ROOT_PATHS_AD_E = tests/test_a[d-eD-E]*.py
PYTEST_ROOT_PATHS_AF_G = tests/test_a[f-gF-G]*.py
PYTEST_ROOT_PATHS_A_HN_CONTEXT = tests/test_analysis_context_*.py tests/test_analysis_drain_mcp_tooling.py
PYTEST_ROOT_PATHS_A_HN_ITERATION = tests/test_analysis_iteration_new_analysis_decision_event_completed.py tests/test_analysis_iteration_new_analysis_decision_event_request_changes.py tests/test_analysis_iteration_new_analysis_success_resets_counters.py tests/test_analysis_iteration_new_dev_analysis_loopback_at_max_routes_to_development.py tests/test_analysis_iteration_new_dev_analysis_loopback_increments_continuation_counter.py tests/test_analysis_iteration_new_dev_commit_success_increments_iteration.py
PYTEST_ROOT_PATHS_A_HN_ITERATION_2 = tests/test_analysis_iteration_new_dev_commit_success_resets_dev_analysis_iteration.py tests/test_analysis_iteration_new_review_analysis_loopback_at_max_routes_to_fix.py
PYTEST_ROOT_PATHS_A_HN_ITERATION_3 = tests/test_analysis_iteration_new_review_analysis_loopback_increments_continuation_counter.py tests/test_analysis_iteration_new_review_commit_success_increments_reviewer_pass.py tests/test_analysis_iteration_new_review_commit_success_resets_review_analysis_iteration.py
PYTEST_ROOT_PATHS_A_HN_MISC = tests/test_analysis_loop_policy_single_source.py tests/test_analysis_prompt_payload_*.py tests/test_analysis_template_paths_*.py
PYTEST_ROOT_PATHS_A_OZ = tests/test_a[o-zO-Z]*.py
PYTEST_ROOT_PATHS_B = tests/test_[bB]*.py
PYTEST_ROOT_PATHS_C_AH_1 = tests/recovery/test_chain_exhaustion.py tests/recovery/test_checkpoint_recovery_fields.py tests/test_capability_mapping_drain_class_for_session_with_policy.py tests/test_capability_mapping_new_capabilities.py tests/test_check_mcp_cli_agy.py tests/test_checkpoint.py tests/test_checkpoint_async.py tests/test_checkpoint_builder.py
PYTEST_ROOT_PATHS_C_AH_2 = tests/test_checkpoint_cleanup.py tests/test_checkpoint_execution_history.py tests/test_checkpoint_parallel.py tests/test_checkpoint_parallel_compatibility.py tests/test_checkpoint_run_context.py tests/test_checkpoint_size_monitor.py tests/test_child_liveness.py
PYTEST_ROOT_PATHS_C_IL = tests/test_classifier_missing_artifact.py tests/test_classifier_stale_session.py tests/test_claude_interactive_menu.py tests/test_claude_interactive_parser.py tests/test_claude_interactive_pty.py tests/test_claude_interactive_pty_helper__fakeptyhandle.py tests/test_claude_interactive_pty_helper__fakeptymanager.py tests/test_claude_interactive_session_resume.py tests/test_claude_parser_noise_suppression.py tests/test_claude_thinking_parser.py tests/test_cli_additional_shortcut_aliases.py tests/test_cli_cleanup.py tests/test_cli_commands_1.py tests/test_cli_commands_2.py tests/test_cli_commands_3.py tests/test_cli_commands_run_inline_prompt_persistence.py tests/test_cli_commands_run_mcp_preflight.py tests/test_cli_commands_run_validate_counter_overrides.py
PYTEST_ROOT_PATHS_C_IL_2 = tests/test_cli_commit_command.py tests/test_cli_diagnose_path.py tests/test_cli_dry_run_uses_policy_entry_phase.py tests/test_cli_first_run.py tests/test_cli_inject_quick_prompt.py tests/test_cli_iteration_counter_flags.py tests/test_cli_parse_counter_overrides.py tests/test_cli_parse_counter_overrides_cli_result.py tests/test_cli_parse_counter_overrides_cli_runner.py tests/test_cli_prepare_init_args.py tests/test_cli_prompt_helper.py tests/test_cli_quick_mode_semantics.py tests/test_cli_removed_review_flags.py tests/test_cli_smoke.py tests/test_cli_thorough_mode_semantics.py tests/test_cli_verbose_default.py
PYTEST_ROOT_PATHS_C_MO = tests/test_c[m-oM-O]*.py
PYTEST_ROOT_PATHS_C_PZ = tests/test_c[p-zP-Z]*.py
PYTEST_ROOT_PATHS_D_CORE = tests/test_development_result*.py tests/test_diagnose_mcp.py tests/test_diagnostics*.py
PYTEST_ROOT_PATHS_D_DISPLAY = tests/test_display*.py tests/test_docs*.py tests/test_documentation_command_sync.py
PYTEST_ROOT_PATHS_E_F = tests/test_[e-fE-F]*.py
PYTEST_ROOT_PATHS_G_H = tests/test_[g-hG-H]*.py
PYTEST_ROOT_PATHS_I_J = tests/test_[i-jI-J]*.py
PYTEST_ROOT_PATHS_K_L = tests/test_[k-lK-L]*.py
PYTEST_ROOT_PATHS_M_A = tests/test_makefile_verification_workflow.py tests/test_managed_agent_sessions.py
PYTEST_ROOT_PATHS_MCP_ARTIFACTS = tests/test_mcp_artifacts*.py
PYTEST_ROOT_PATHS_MCP_BRIDGE = tests/test_mcp_bridge*.py
PYTEST_ROOT_PATHS_MCP_CAPABILITY = tests/test_mcp_capability_mapping*.py
PYTEST_ROOT_PATHS_MCP_MISC = tests/test_mcp_env.py tests/test_mcp_factory_impl*.py tests/test_mcp_lifecycle.py tests/test_mcp_policy_outcomes.py
PYTEST_ROOT_PATHS_MCP_SERVER = tests/test_mcp_server*.py tests/test_mcp_startup.py tests/test_mcp_supervisor.py tests/test_mcp_tool*.py
PYTEST_ROOT_PATHS_MULTIMODAL = tests/test_multimodal_capabilities.py
PYTEST_ROOT_PATHS_N = tests/test_[nN]*.py
PYTEST_ROOT_PATHS_O = tests/test_[oO]*.py
PYTEST_ROOT_PATHS_PA_PC = tests/test_package_markdown_docs_sync_package_mcp_docs.py tests/test_package_markdown_docs_sync_package_root_docs.py tests/test_package_release_classifier.py tests/test_parallel_coordinator.py tests/test_parallel_coordinator_helper_recordingdisplay.py tests/test_parallel_display.py tests/test_parallel_display_completion_panel.py tests/test_parallel_display_content_routing.py tests/test_parallel_display_output.py tests/test_parallel_display_subscriber_forwarding.py tests/test_parallel_display_transcript_regression.py tests/test_parallel_docs_no_worktree_language.py tests/test_parallel_mode_docs_banned_phrases_across_all_docs.py
PYTEST_ROOT_PATHS_PA_PC_2 = tests/test_parallel_mode_docs_namespaced_payload_docs.py tests/test_parallel_multimodal_worker_collision.py tests/test_parallel_no_worktree_imports.py tests/test_parallel_partial_failure_handoff_analysis_handoff_wiring.py tests/test_parallel_partial_failure_handoff_combined_summary_honesty.py tests/test_parallel_public_api.py tests/test_parallel_stress.py tests/test_parallel_worker_runtime.py tests/test_parsers_1.py tests/test_parsers_2.py tests/test_payload_refs_surrogates.py tests/test_payload_section_macros_render_payload_path.py tests/test_payload_section_macros_render_payload_section.py
PYTEST_ROOT_PATHS_PD_PF = tests/test_p[d-fD-F]*.py
PYTEST_ROOT_PATHS_PG_PH = tests/display/test_phase_banner_modes.py tests/test_phase_*.py tests/test_phases_*.py tests/integration/test_pipeline_iterations.py
PYTEST_ROOT_PATHS_PG_PI = tests/integration/test_pipeline_*.py tests/test_pipeline_*.py
PYTEST_ROOT_PATHS_PJ_PL = tests/test_p[j-lJ-L]*.py
PYTEST_ROOT_PATHS_PM_POLICY = tests/test_policy_*.py
PYTEST_ROOT_PATHS_PM_PZ = tests/test_process_*.py tests/test_product_*.py tests/test_progress_*.py tests/test_prompt_*.py tests/test_prompts_*.py tests/test_post_*.py
PYTEST_ROOT_PATHS_Q_S = tests/test_ralph_prompt_entry.py tests/test_raw_overflow.py tests/test_readme_long_content_summary_doc.py tests/test_recovery_first_invariant.py tests/test_reducer_analysis_budget_bypass.py tests/test_reducer_analysis_decision_dispatch.py tests/test_reducer_phase_failure_event.py tests/test_repo_root_agents_docs_sync_current_agents_guides.py tests/test_repo_root_agents_docs_sync_redirect_stubs.py tests/test_repo_root_docs_sync.py tests/test_repo_root_operational_docs_sync.py tests/test_repo_root_performance_docs_sync.py tests/test_repo_root_tooling_and_code_style_scope_archival_tooling.py tests/test_repo_root_tooling_and_code_style_scope_code_style_index.py tests/test_repo_root_tooling_and_code_style_scope_code_style_leaves.py tests/test_repository_urls.py tests/test_review_handler_uses_effect_phase_name.py tests/test_review_template_schema_alignment.py tests/test_ring_buffer.py tests/test_root_makefile_wrapper.py
PYTEST_ROOT_PATHS_Q_S_2 = tests/test_runner_final_summary_counters.py tests/test_runner_mcp_validation.py tests/test_runner_output_routing.py tests/test_runner_parallel_mode_selection.py tests/test_runner_phase_transition_banner.py tests/test_runner_phase_transition_banner_helper__stubdisplay.py tests/test_runner_phase_transition_banner_helper__stubphasecounters.py tests/test_runner_phase_transition_banner_helper__stubsubscriber.py tests/test_runner_policy_driven_render.py tests/test_runner_quiet_mode.py tests/test_runtime_environment.py tests/test_same_workspace_parallel_artifacts_only_success.py tests/test_same_workspace_parallel_concurrent_worker_artifact_isolation.py tests/test_same_workspace_parallel_edit_area_enforcement.py tests/test_same_workspace_parallel_mcp_tool_boundary_enforcement.py tests/test_same_workspace_parallel_no_git_status_fallback.py tests/test_same_workspace_parallel_no_git_status_fallback_helper__fakemcpserverfactory.py tests/test_same_workspace_parallel_no_merge_step_contract.py tests/test_same_workspace_parallel_parallel_execution_mode.py tests/test_same_workspace_parallel_prepare_executor_same_workspace.py
PYTEST_ROOT_PATHS_Q_S_3 = tests/test_same_workspace_parallel_runner_no_merge_step.py tests/test_same_workspace_parallel_same_workspace_context.py tests/test_same_workspace_parallel_validate_for_same_workspace.py tests/test_same_workspace_parallel_worker_artifact_isolation.py tests/test_scheduler.py tests/test_session_mcp_plan_commit_drain_is_strictly_read_only.py tests/test_session_mcp_plan_media_read_explicit_opt_out.py tests/test_session_mcp_plan_media_read_granted_to_all_drains_by_default.py tests/test_session_mcp_plan_model_flag_resolution_in_build_session_mcp_plan.py tests/test_session_mcp_plan_session_mcp_plan_capability_profile.py tests/test_session_mcp_plan_workspace_edit_and_delete_granted_to_dev_and_fix_drains.py tests/test_session_mcp_plan_workspace_metadata_read_granted_to_all_drains.py tests/test_sphinx_documentation_setup.py tests/test_sphinx_member_documentation.py tests/test_sphinx_modules_coverage.py tests/test_sphinx_theme_auto_mode.py tests/test_subprocess_agent_executor.py tests/test_subscriber_silent_drops.py tests/test_supervising.py tests/test_system_prompt.py
PYTEST_ROOT_PATHS_T_U = tests/test_tables_themed.py tests/test_theme_log_styles.py tests/test_tool_args.py tests/test_tool_artifact_1.py tests/test_tool_artifact_1_helper_failingartifactbackend.py tests/test_tool_artifact_1_helper_memorybackend.py tests/test_tool_artifact_1_helper_mocksession.py tests/test_tool_artifact_1_helper_mockworkspace.py tests/test_tool_artifact_2.py tests/test_tool_artifact_2_helper_failingartifactbackend.py tests/test_tool_artifact_2_helper_memorybackend.py tests/test_tool_artifact_2_helper_mocksession.py tests/test_tool_artifact_2_helper_mockworkspace.py tests/test_tool_coordination.py tests/test_tool_exec_apply_exec_policy.py tests/test_tool_exec_check_command_integration.py tests/test_tool_exec_check_container_escape.py tests/test_tool_exec_check_destructive_system.py tests/test_tool_exec_check_multi_file_operation.py tests/test_tool_exec_check_network_exfiltration.py tests/test_tool_exec_check_package_manager.py tests/test_tool_exec_check_privilege_escalation.py tests/test_tool_exec_check_version_control.py tests/test_tool_exec_format_exec_result.py
PYTEST_ROOT_PATHS_T_U_2 = tests/test_tool_exec_handle_exec_command.py tests/test_tool_exec_parse_exec_params.py tests/test_tool_exec_run_command.py tests/test_tool_exec_workspace_with_root_protocol.py tests/test_tool_git_read_handle_git_diff.py tests/test_tool_git_read_handle_git_log.py tests/test_tool_git_read_handle_git_show.py tests/test_tool_git_read_handle_git_status.py tests/test_tool_git_read_parse_git_diff_params.py tests/test_tool_git_read_parse_git_log_params.py tests/test_tool_git_read_parse_git_show_params.py tests/test_tool_git_read_run_git_command.py tests/test_tool_git_read_run_git_command_lenient.py tests/test_tool_git_read_workspace_with_root_protocol.py tests/test_tool_workspace_check_edit_area_restriction.py tests/test_tool_workspace_handle_append_file.py tests/test_tool_workspace_handle_copy_file.py tests/test_tool_workspace_handle_create_directory.py tests/test_tool_workspace_handle_delete_path.py tests/test_tool_workspace_handle_directory_tree.py tests/test_tool_workspace_handle_edit_file.py tests/test_tool_workspace_handle_grep_files.py tests/test_tool_workspace_handle_list_allowed_roots.py tests/test_tool_workspace_handle_list_directory.py
PYTEST_ROOT_PATHS_T_U_3 = tests/test_tool_workspace_handle_list_directory_recursive.py tests/test_tool_workspace_handle_move_file.py tests/test_tool_workspace_handle_read_file.py tests/test_tool_workspace_handle_read_file_full_read_truncation.py tests/test_tool_workspace_handle_read_file_non_utf8.py tests/test_tool_workspace_handle_read_file_partial.py tests/test_tool_workspace_handle_read_image.py tests/test_tool_workspace_handle_read_media.py tests/test_tool_workspace_handle_read_multiple_files.py tests/test_tool_workspace_handle_search_files.py tests/test_tool_workspace_handle_stat.py tests/test_tool_workspace_handle_write_file.py tests/test_tool_workspace_infer_image_mime_type.py tests/test_tool_workspace_is_parallel_worker.py tests/test_tool_workspace_is_path_git_tracked.py tests/test_tool_workspace_is_policy_approved.py tests/test_tool_workspace_join_path.py tests/test_tool_workspace_list_dir_entries.py tests/test_tool_workspace_list_dir_flat.py tests/test_tool_workspace_match_glob.py tests/test_tool_workspace_normalize_relative_path.py tests/test_tool_workspace_required_string_param.py tests/test_type_ignore_policy.py tests/test_unified_config_prompt_helper.py
PYTEST_ROOT_PATHS_V_W = tests/test_verify.py tests/test_verify_timeout.py tests/test_work_units.py tests/test_work_units_validators.py tests/test_worker_session_factory.py tests/test_worker_state.py tests/test_workspace_fs_fs_workspace_absolute_path.py tests/test_workspace_fs_fs_workspace_allowed_roots.py tests/test_workspace_fs_fs_workspace_append.py tests/test_workspace_fs_fs_workspace_copy.py tests/test_workspace_fs_fs_workspace_delete.py tests/test_workspace_fs_fs_workspace_exists.py tests/test_workspace_fs_fs_workspace_init.py tests/test_workspace_fs_fs_workspace_is_dir.py tests/test_workspace_fs_fs_workspace_is_file.py tests/test_workspace_fs_fs_workspace_iter_files.py tests/test_workspace_fs_fs_workspace_list_dir.py tests/test_workspace_fs_fs_workspace_mkdirs.py tests/test_workspace_fs_fs_workspace_move.py tests/test_workspace_fs_fs_workspace_read.py tests/test_workspace_fs_fs_workspace_read_bytes.py
PYTEST_ROOT_PATHS_V_W_2 = tests/test_workspace_fs_fs_workspace_read_lines.py tests/test_workspace_fs_fs_workspace_remove.py tests/test_workspace_fs_fs_workspace_root.py tests/test_workspace_fs_fs_workspace_stat.py tests/test_workspace_fs_fs_workspace_write.py tests/test_workspace_memory_memory_workspace_absolute_path.py tests/test_workspace_memory_memory_workspace_append.py tests/test_workspace_memory_memory_workspace_clear.py tests/test_workspace_memory_memory_workspace_create_dir.py tests/test_workspace_memory_memory_workspace_exists.py tests/test_workspace_memory_memory_workspace_init.py tests/test_workspace_memory_memory_workspace_is_dir.py tests/test_workspace_memory_memory_workspace_is_file.py tests/test_workspace_memory_memory_workspace_list_dir.py tests/test_workspace_memory_memory_workspace_normalize.py tests/test_workspace_memory_memory_workspace_read.py tests/test_workspace_memory_memory_workspace_read_bytes.py tests/test_workspace_memory_memory_workspace_remove.py tests/test_workspace_memory_memory_workspace_write.py tests/test_workspace_scope.py

help:
	@echo "Ralph Workflow - Makefile commands"
	@echo ""
	@echo "  install         Install package and refresh pipx executable"
	@echo "  dev             Install package in editable mode with dev dependencies"
	@echo "  install-dev     Alias for dev"
	@echo "  test           Run the full pytest suite without coverage (excludes subprocess_e2e)"
	@echo "  test-unit      Run unit/root tests without tests/integration/"
	@echo "  test-integration Run tests/integration/ only (excludes subprocess_e2e)"
	@echo "  test-subprocess-e2e Run tests that require real subprocesses or network sockets (60s limit)"
	@echo "  lint           Run ruff linting"
	@echo "  typecheck      Run mypy type checking"
	@echo "  fmt            Format code with ruff"
	@echo "  format-check   Check formatting without applying changes"
	@echo "  pre-commit     Run pre-commit hooks"
	@echo "  build          Build wheel and sdist"
	@echo "  build-wheel    Build wheel only"
	@echo "  build-sdist    Build sdist only"
	@echo "  twine-upload   Upload dist/* to PyPI via Twine"
	@echo "  twine-upload-testpypi Upload dist/* to Test PyPI via Twine"
	@echo "  publish        Alias for twine-upload"
	@echo "  test-pypi      Alias for twine-upload-testpypi"
	@echo "  test-pypi-upload Alias for twine-upload-testpypi"
	@echo "  clean          Clean build artifacts"
	@echo "  clean-dist     Clean dist folder"
	@echo "  dist-homebrew  Build Homebrew distribution"
	@echo "  verify         Run full verification (ruff, mypy, pytest; excludes docs build and coverage)"
	@echo "  dist-binary    Build PyInstaller single-file binary"
	@echo "  dist-pypi      Build and publish to PyPI via hatch"
	@echo "  dist-all       Build both binary and PyPI distributions"
	@echo "  formula-check  Validate Homebrew formula syntax"
	@echo "  dead-code      Run Vulture dead-code audit (fails while backlog exists)"
	@echo "  docs           Build Sphinx HTML documentation"
	@echo "  docs-linkcheck Check all hyperlinks in docs (opt-in, not in verify)"
	@echo "  serve-docs     Build and serve docs at http://localhost:8080"
	@echo ""
	@echo "  run            Run ralph CLI directly"
	@echo "  check          Run all checks (lint, typecheck, test)"

# Installation
install:
	python -m ralph.install

dev:
	python -m pip install -e ".[dev]"

install-dev: dev

# Testing
test:
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_AGENT_PATHS) -q -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_AGENT_PATHS_2) -q -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_CONFIG_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_DISPLAY_CONTEXT_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_DISPLAY_MODE_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_DISPLAY_PHASE_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_DISPLAY_REST_PATHS) -q -n $(PYTEST_WORKERS_D_REST) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_FIXTURES_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_UNIT_AGENT_PATHS) -q -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_UNIT_DISPLAY_PATHS) -q -n $(PYTEST_WORKERS_UNIT_DISPLAY) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_UNIT_PIPELINE_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_RUNTIME_MCP_PATHS) -q -n $(PYTEST_WORKERS_MCP) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_RUNTIME_PIPELINE_PATHS) -q -n $(PYTEST_WORKERS_MCP) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_RUNTIME_RECOVERY_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_AC) -q -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_AF_G) -q -n $(PYTEST_WORKERS_AF_G) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_HN_CONTEXT) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_HN_ITERATION) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_HN_ITERATION_2) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_HN_ITERATION_3) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_HN_MISC) -q -n $(PYTEST_WORKERS_A_HN_MISC) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_OZ) -q -n $(PYTEST_WORKERS_A_OZ) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_B) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_AH_1) -q -n $(PYTEST_WORKERS_C_AH) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_AH_2) -q -n $(PYTEST_WORKERS_C_AH) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_IL) -q -n $(PYTEST_WORKERS_C_IL) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_IL_2) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_MO) -q -n $(PYTEST_WORKERS_C_MO) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_PZ) -q -n $(PYTEST_WORKERS_C_PZ) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_D_CORE) -q -n $(PYTEST_WORKERS_D) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_D_DISPLAY) -q -n $(PYTEST_WORKERS_D) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_E_F) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_G_H) -q -n $(PYTEST_WORKERS_G_H) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_I_J) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_K_L) -q -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M_A) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_MCP_ARTIFACTS) -q -n $(PYTEST_WORKERS_MCP) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_MCP_BRIDGE) -q -n $(PYTEST_WORKERS_MCP) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_MCP_CAPABILITY) -q -n $(PYTEST_WORKERS_MCP) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_MCP_MISC) -q -n $(PYTEST_WORKERS_MCP) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_MCP_SERVER) -q -n $(PYTEST_WORKERS_MCP) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_MULTIMODAL) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_N) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_O) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PA_PC) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PA_PC_2) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PD_PF) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PJ_PL) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PM_POLICY) -q -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PM_PZ) -q -n $(PYTEST_WORKERS_PM_PZ) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_Q_S) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_Q_S_2) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_Q_S_3) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_T_U) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_T_U_2) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_T_U_3) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_V_W) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_V_W_2) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest tests/integration/ -q -n $(PYTEST_WORKERS_INTEGRATION) --dist worksteal -m "not subprocess_e2e"

test-unit:
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_AGENT_PATHS) -q -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_AGENT_PATHS_2) -q -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_CONFIG_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_DISPLAY_CONTEXT_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_DISPLAY_MODE_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_DISPLAY_PHASE_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_DISPLAY_REST_PATHS) -q -n $(PYTEST_WORKERS_D_REST) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_FIXTURES_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_UNIT_AGENT_PATHS) -q -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_UNIT_DISPLAY_PATHS) -q -n $(PYTEST_WORKERS_UNIT_DISPLAY) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_UNIT_PIPELINE_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_RUNTIME_MCP_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_RUNTIME_PIPELINE_PATHS) -q -n $(PYTEST_WORKERS_MCP) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_RUNTIME_RECOVERY_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_AC) -q -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_AF_G) -q -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_HN_CONTEXT) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_HN_ITERATION) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_HN_ITERATION_2) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_HN_ITERATION_3) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_HN_MISC) -q -n $(PYTEST_WORKERS_A_HN_MISC) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_OZ) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_B) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_AH_1) -q -n $(PYTEST_WORKERS_C_AH) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_AH_2) -q -n $(PYTEST_WORKERS_C_AH) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_IL) -q -n $(PYTEST_WORKERS_C_IL) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_IL_2) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_MO) -q -n $(PYTEST_WORKERS_C_MO) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_PZ) -q -n $(PYTEST_WORKERS_C_PZ) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_D_CORE) -q -n $(PYTEST_WORKERS_D) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_D_DISPLAY) -q -n $(PYTEST_WORKERS_D) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_E_F) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_G_H) -q -n $(PYTEST_WORKERS_G_H) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_I_J) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_K_L) -q -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M_A) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_MCP_ARTIFACTS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_MCP_BRIDGE) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_MCP_CAPABILITY) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_MCP_MISC) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_MCP_SERVER) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_MULTIMODAL) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_N) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_O) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PA_PC) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PA_PC_2) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PD_PF) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PG_PH) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PG_PI) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PJ_PL) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PM_PZ) -q -n $(PYTEST_WORKERS_PM_PZ) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_Q_S) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_Q_S_2) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_Q_S_3) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_T_U) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_T_U_2) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_T_U_3) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_V_W) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_V_W_2) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"

test-integration:
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest tests/integration/ -q -n $(PYTEST_WORKERS_INTEGRATION) --dist worksteal -m "not subprocess_e2e"

coverage: test-cov

test-subprocess-e2e:
	.venv/bin/python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest tests/ -q -n 1 -m subprocess_e2e

test-cov:
	rm -f .coverage.pytest .coverage.pytest.*
	uv run python -m pytest tests/ -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e" --cov=ralph --cov-report=
	uv run coverage report --data-file=.coverage.pytest --fail-under=80

# Linting
lint:
	uv run ruff check ralph/ tests/

typecheck:
	uv run python -m mypy ralph/

fmt:
	uv run ruff format ralph/ tests/

format-check:
	uv run ruff format --check ralph/ tests/

ruff-fix:
	uv run ruff check --fix ralph/ tests/

mypy-fix:
	uv run python -m mypy ralph/

# Pre-commit
pre-commit:
	pre-commit run --all-files

# Building
build: clean-dist
	uv run --with hatchling hatch build

build-wheel: clean-dist
	uv run --with hatchling hatch build --wheel

build-sdist: clean-dist
	uv run --with hatchling hatch build --sdist

# Publishing
twine-upload: build
	uv run --with twine python -m twine upload dist/*

twine-upload-testpypi: build
	uv run --with twine python -m twine upload --repository testpypi dist/*

publish: build
	uv run --with twine python -m twine upload dist/*

test-pypi: build
	uv run --with twine python -m twine upload --repository testpypi dist/*

test-pypi-upload: build
	uv run --with twine python -m twine upload --repository testpypi dist/*

# Cleaning
clean:
	rm -rf build/
	rm -rf .pytest_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf .mypy_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

clean-dist:
	rm -rf dist/
	rm -rf build/
	rm -rf *.egg-info/

# Homebrew
dist-homebrew: build-wheel
	@mkdir -p Formula
	@echo "Building Homebrew bottle..."
	@python -m installer --print-blob --no-scrollbar ralph_python*.whl 2>/dev/null || \
		echo "Note: Homebrew bottle creation requires macOS and Homebrew environment"

# Distribution targets
verify: packaging-smoke policy-loader-smoke
	@uv run python -m ralph.verify

policy-loader-smoke:
	@python -c "import sys; sys.stderr = open('/dev/null', 'w'); from pathlib import Path; from ralph.policy.loader import load_policy; load_policy(Path('ralph/policy/defaults'))"

packaging-smoke:
	@mkdir -p $(shell realpath .)/../tmp
	@python -c "import shutil; from pathlib import Path; shutil.rmtree(Path('$(shell realpath .)/../tmp/packaging-smoke'), ignore_errors=True)"
	@python -m venv --clear $(shell realpath .)/../tmp/packaging-smoke
	@PYTHONPATH="" uv pip install --quiet --python $(shell realpath .)/../tmp/packaging-smoke/bin/python $(shell realpath .)/../ralph-workflow/
	@ls -la $(shell realpath .)/../tmp/packaging-smoke/bin/ralph-prompt
	@PYTHONPATH="" $(shell realpath .)/../tmp/packaging-smoke/bin/python -c "from ralph.cli._prompt_helper_entry import main; print('entrypoint importable')"
	@uv run pytest -q tests/test_ralph_prompt_entry.py
	@python -c "import shutil; from pathlib import Path; shutil.rmtree(Path('$(shell realpath .)/../tmp/packaging-smoke'), ignore_errors=True)"

dist-binary:
	@echo "Building PyInstaller binary..."
	pyinstaller ralph-workflow.spec
	@echo "Binary built at dist/ralph-workflow"

dist-pypi:
	@echo "Building and publishing to PyPI..."
	hatch build
	hatch publish

dist-all: dist-binary dist-pypi
	@echo "All distributions built successfully"

formula-check:
	@echo "Validating Homebrew formula..."
	@if command -v brew >/dev/null 2>&1; then \
		brew audit --strict Formula/ralph-workflow.rb; \
	else \
		echo "Homebrew not installed, skipping formula check"; \
	fi

dead-code:
	uv run --with vulture python -m vulture --config pyproject.toml

docs:
	uv run --extra docs sphinx-build -b html docs/sphinx docs/sphinx/_build/html -W --keep-going

docs-linkcheck:
	uv run --extra docs sphinx-build -b linkcheck docs/sphinx docs/sphinx/_build/linkcheck

serve-docs: docs
	@echo "Serving docs at http://localhost:8080"
	cd docs/sphinx/_build/html && python -m http.server 8080

# Development helpers
run:
	python -m ralph

check:
	@echo "Running all checks..."
	@$(MAKE) lint
	@$(MAKE) typecheck
	@$(MAKE) test

.DEFAULT_GOAL := help
