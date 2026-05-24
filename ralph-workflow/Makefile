.PHONY: help install dev install-dev test test-unit test-integration test-subprocess-e2e lint typecheck build build-wheel build-sdist publish test-pypi test-pypi-upload twine-upload twine-upload-testpypi clean dist clean-dist fmt format-check ruff-fix mypy-fix pre-commit dist-homebrew verify coverage dist-binary dist-pypi dist-all formula-check dead-code docs docs-linkcheck serve-docs packaging-smoke

PYTEST_WORKERS ?= 4
export PYTHONPATH := $(shell realpath .)
PYTEST_SUITE_TIMEOUT_SECONDS ?= 30
PYTEST_CORE_PATHS = tests/agents tests/config tests/display tests/fixtures tests/unit
PYTEST_RUNTIME_PATHS_MCP = tests/mcp
PYTEST_RUNTIME_PATHS_PIPELINE = tests/pipeline
PYTEST_RUNTIME_PATHS_RECOVERY = tests/recovery
PYTEST_ROOT_PATHS_A_ACTIVITY = tests/test_activity*.py
PYTEST_ROOT_PATHS_A_AGENT = tests/test_agent_*.py
PYTEST_ROOT_PATHS_A_AGENTS = tests/test_agents*.py
PYTEST_ROOT_PATHS_A_AGY = tests/test_agy*.py
PYTEST_ROOT_PATHS_A_ANALYSIS = tests/test_analysis*.py
PYTEST_ROOT_PATHS_A_API = tests/test_api*.py
PYTEST_ROOT_PATHS_A_ARTIFACT = tests/test_artifact*.py
PYTEST_ROOT_PATHS_A_ASYNCIO = tests/test_asyncio*.py
PYTEST_ROOT_PATHS_A_AUDIT = tests/test_audit*.py
PYTEST_ROOT_PATHS_B = tests/test_[bB]*.py
PYTEST_ROOT_PATHS_C_CAPABILITY = tests/test_capability_mapping*.py
PYTEST_ROOT_PATHS_C_CHECKPOINT = tests/test_checkpoint*.py
PYTEST_ROOT_PATHS_C_CHILD_CLASSIFIER = tests/test_child*.py tests/test_classifier*.py
PYTEST_ROOT_PATHS_C_CLAUDE = tests/test_claude*.py
PYTEST_ROOT_PATHS_C_CLI_1 = tests/test_cli_additional_shortcut_aliases.py tests/test_cli_cleanup.py tests/test_cli_commands_1.py tests/test_cli_commands_2.py tests/test_cli_commands_3.py tests/test_cli_commands_init.py tests/test_cli_commands_run_capability_warning.py tests/test_cli_commands_run_inline_prompt_persistence.py tests/test_cli_commands_run_mcp_preflight.py tests/test_cli_commands_run_process_view_fallback.py tests/test_cli_commands_run_validate_counter_overrides.py tests/test_cli_commit_command.py tests/test_cli_diagnose_path.py tests/test_cli_dry_run_uses_policy_entry_phase.py tests/test_cli_first_run.py tests/test_cli_inject_quick_prompt.py
PYTEST_ROOT_PATHS_C_CLI_2 = tests/test_cli_iteration_counter_flags.py tests/test_cli_parse_counter_overrides.py tests/test_cli_parse_counter_overrides_cli_result.py tests/test_cli_parse_counter_overrides_cli_runner.py tests/test_cli_prepare_init_args.py tests/test_cli_prompt_helper.py tests/test_cli_quick_mode_semantics.py tests/test_cli_removed_review_flags.py tests/test_cli_smoke.py tests/test_cli_thorough_mode_semantics.py tests/test_cli_verbose_default.py
PYTEST_ROOT_PATHS_C_CODEX_COMMIT = tests/test_codex_parser.py tests/test_commit_cleanup_artifact.py tests/test_commit_cleanup_diff.py tests/test_commit_command_surrogates.py tests/test_commit_message.py
PYTEST_ROOT_PATHS_C_COMPLETION = tests/test_completion_summary.py tests/test_completion_summary_badges_themed.py tests/test_completion_summary_grouping.py tests/test_completion_summary_role_only_render_completion_summary_role_only.py tests/test_completion_summary_role_only_style_for_role.py tests/test_completion_summary_role_only_style_for_terminal_failure.py
PYTEST_ROOT_PATHS_C_CONFIG = tests/test_config_bootstrap.py tests/test_config_loader.py tests/test_config_propagation.py tests/test_config_welcome.py tests/test_config_welcome_helper__fakeregistry.py tests/test_content_condenser.py
PYTEST_ROOT_PATHS_C_CUSTOM_POLICY = tests/test_custom_policy_workflow_custom_named_pipeline_handler_dispatch.py tests/test_custom_policy_workflow_custom_policy_explain_renders_custom_names.py tests/test_custom_policy_workflow_custom_policy_loads_and_validates.py tests/test_custom_policy_workflow_custom_policy_loop_counter_dict.py tests/test_custom_policy_workflow_custom_policy_resolve_post_commit.py tests/test_custom_policy_workflow_run_with_fully_renamed_phases.py
PYTEST_ROOT_PATHS_C_CYCLE = tests/test_cycle_baseline_diff_cycle_baseline_diff.py tests/test_cycle_baseline_diff_runner_cycle_start_uses_force_true.py tests/test_cycle_baseline_diff_write_cycle_baseline_force_param.py
PYTEST_ROOT_PATHS_D = tests/test_[dD]*.py
PYTEST_ROOT_PATHS_E = tests/test_[eE]*.py
PYTEST_ROOT_PATHS_F = tests/test_[fF]*.py
PYTEST_ROOT_PATHS_G = tests/test_[gG]*.py
PYTEST_ROOT_PATHS_H = tests/test_[hH]*.py
PYTEST_ROOT_PATHS_I = tests/test_[iI]*.py
PYTEST_ROOT_PATHS_J = tests/test_[jJ]*.py
PYTEST_ROOT_PATHS_K = tests/test_[kK]*.py
PYTEST_ROOT_PATHS_L = tests/test_[lL]*.py
PYTEST_ROOT_PATHS_M_CONFIG = tests/config/test_mcp_*.py tests/integration/test_mcp_*.py tests/test_makefile_verification_workflow.py tests/test_managed_agent_sessions.py
PYTEST_ROOT_PATHS_M_ARTIFACTS = tests/test_mcp_artifacts_*.py
PYTEST_ROOT_PATHS_M_BRIDGE = tests/test_mcp_bridge_*.py
PYTEST_ROOT_PATHS_M_CAPABILITY = tests/test_mcp_capability_mapping_*.py
PYTEST_ROOT_PATHS_M_CORE = tests/test_mcp_env.py tests/test_mcp_factory_impl*.py tests/test_mcp_policy_outcomes.py
PYTEST_ROOT_PATHS_M_RUNTIME = tests/test_mcp_lifecycle.py tests/test_mcp_startup.py tests/test_mcp_supervisor.py tests/test_mcp_transport.py tests/test_mcp_transport_helper__fakethread.py
PYTEST_ROOT_PATHS_M_SERVER = tests/test_mcp_server_*.py
PYTEST_ROOT_PATHS_M_TOOL = tests/test_mcp_tool_*.py
PYTEST_ROOT_PATHS_N = tests/test_[nN]*.py
PYTEST_ROOT_PATHS_O = tests/test_[oO]*.py
PYTEST_ROOT_PATHS_PA_PC = tests/test_p[a-cA-C]*.py
PYTEST_ROOT_PATHS_PD_PF = tests/test_p[d-fD-F]*.py
PYTEST_ROOT_PATHS_PG_PI = tests/test_p[g-iG-I]*.py
PYTEST_ROOT_PATHS_PJ_PL = tests/test_p[j-lJ-L]*.py
PYTEST_ROOT_PATHS_PM_PZ = tests/test_p[m-zM-Z]*.py
PYTEST_ROOT_PATHS_Q_S = tests/test_[q-sQ-S]*.py
PYTEST_ROOT_PATHS_T_Z = tests/test_[t-zT-Z]*.py

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
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_RUNTIME_PATHS_MCP) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_RUNTIME_PATHS_PIPELINE) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_RUNTIME_PATHS_RECOVERY) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_ACTIVITY) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_AGENT) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_AGENTS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_AGY) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_ANALYSIS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_API) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_ARTIFACT) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_ASYNCIO) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_AUDIT) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_B) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CAPABILITY) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CHECKPOINT) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CHILD_CLASSIFIER) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CLAUDE) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CLI_1) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CLI_2) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CODEX_COMMIT) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_COMPLETION) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CONFIG) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CUSTOM_POLICY) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CYCLE) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_D) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_E) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_F) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_G) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_H) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_I) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_J) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_K) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_L) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M_CONFIG) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M_ARTIFACTS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M_BRIDGE) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M_CAPABILITY) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M_CORE) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M_RUNTIME) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M_SERVER) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M_TOOL) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_N) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_O) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PA_PC) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PD_PF) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PG_PI) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PJ_PL) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PM_PZ) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_Q_S) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_T_Z) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"

test-integration:
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest tests/integration/ -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"

test-unit:
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_RUNTIME_PATHS_MCP) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_RUNTIME_PATHS_PIPELINE) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_RUNTIME_PATHS_RECOVERY) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_ACTIVITY) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_AGENT) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_AGENTS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_AGY) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_ANALYSIS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_API) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_ARTIFACT) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_ASYNCIO) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A_AUDIT) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_B) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CAPABILITY) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CHECKPOINT) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CHILD_CLASSIFIER) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CLAUDE) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CLI_1) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CLI_2) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CODEX_COMMIT) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_COMPLETION) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CONFIG) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CUSTOM_POLICY) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_CYCLE) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_D) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_E) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_F) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_G) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_H) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_I) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_J) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_K) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_L) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M_CONFIG) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M_ARTIFACTS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M_BRIDGE) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M_CAPABILITY) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M_CORE) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M_RUNTIME) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M_SERVER) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M_TOOL) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_N) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_O) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PA_PC) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PD_PF) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PG_PI) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PJ_PL) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PM_PZ) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_Q_S) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_T_Z) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"

test-subprocess-e2e:
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest tests/ -q -n 1 -m subprocess_e2e

coverage: test-cov

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
verify:
	@uv run python -m ralph.verify

policy-loader-smoke:
	@python -c "import sys; sys.stderr = open('/dev/null', 'w'); from pathlib import Path; from ralph.policy.loader import load_policy; load_policy(Path('ralph/policy/defaults'))"

packaging-smoke:
	-@rm -rf $(shell realpath .)/../tmp/packaging-smoke
	@mkdir -p $(shell realpath .)/../tmp
	@uv venv $(shell realpath .)/../tmp/packaging-smoke
	@PYTHONPATH="" uv pip install --quiet --python $(shell realpath .)/../tmp/packaging-smoke/bin/python $(shell realpath .)/../ralph-workflow/
	@ls -la $(shell realpath .)/../tmp/packaging-smoke/bin/ralph-prompt
	@PYTHONPATH="" $(shell realpath .)/../tmp/packaging-smoke/bin/python -c "from ralph.cli._prompt_helper_entry import main; print('entrypoint importable')"
	@uv run pytest -q tests/test_ralph_prompt_entry.py
	-@rm -rf $(shell realpath .)/../tmp/packaging-smoke

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
