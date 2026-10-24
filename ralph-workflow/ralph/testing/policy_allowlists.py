"""The test-policy allowlists of the ``audit_test_policy`` audit.

Each allowlist skips exactly ONE policy category, and only for the files it
names:

``IO_ALLOWLIST``
    skips the real-I/O category,
``SOURCE_READ_ALLOWLIST``
    skips the shipped-source READ findings only (every other category is
    still checked for the same file),
``WALL_CLOCK_ALLOWLIST``
    skips the wall-clock category.

Every entry carries the reason it exists: an allowlist entry is a reviewed
exception, and an unexplained exception is indistinguishable from a hole in
the policy. The three white-box detectors are NEVER allowlisted — a file
marked ``subprocess_e2e`` is still white-box checked.
"""

from __future__ import annotations

#: Files that may perform real filesystem I/O in a test.
IO_ALLOWLIST: set[str] = {
    # Memory regression test (test_multimodal_session_memory_regression) is
    # intrinsically coupled to FsWorkspace for valid tracemalloc measurements.
    # The test measures the production code path through handle_read_media
    # and collect_media_entries_for_phase, which in production use FsWorkspace.
    # Switching to MemoryWorkspace would:
    #   (a) change the call graph (different Workspace subclass),
    #   (b) alter object allocations (in-memory dict vs filesystem ops),
    #   (c) invalidate the calibrated regression thresholds.
    # The tracemalloc snapshot MUST match the production code path exactly —
    # a fake workspace would measure a DIFFERENT code path and produce
    # meaningless regression assertions. This is a legitimate allowlist entry.
    "test_multimodal_session_memory_regression",
    # Upstream-media memory regression test. Drives
    # normalize_upstream_content_blocks() repeatedly with N embedded-data
    # blocks AND a workspace argument; the cache_path/byte_loader seam
    # requires real FsWorkspace for the same reasons as the existing
    # multimodal regression test (memory accounting through the real
    # _write_durable_media_cache call path).
    "test_upstream_media_memory_regression",
    # FileBackedSession caching test. Writes a real session JSON file
    # to tmp_path and triggers the (mtime_ns, size)-keyed cache; the
    # caching is the unit under test and the production loader calls
    # path.read_text() which the audit flags as real I/O. Switching
    # to a fake Path would defeat the regression assertion (which
    # specifically tests the on-disk change-detection contract).
    "test_mcp_file_backed_session_caching",
    # Template rendering and source-invariant tests read Jinja2 template files
    # from the repo. The template body is the subject under test, so mocking
    # it would test nothing meaningful.
    "test_prompt_best_practices",
    "test_analysis_context_partial_analysis_context_path_behavior",
    "test_analysis_context_partial_analysis_context_path_only_behavior",
    "test_analysis_context_partial_analysis_context_rendering",
    "test_analysis_context_partial_analysis_context_suppression",
    "test_analysis_prompt_payload_contract_analysis_template_payload_contract",
    "test_analysis_prompt_payload_contract_retry_hint_guard_in_templates",
    # Failure-ownership prompt guard reads the shared partial and five source
    # templates because their literal contents are the regression contract;
    # fixture text would not detect prompt-source drift.
    "test_prompts_no_exemption_for_failures",
    # Source-inspection guard that reads production Python source files
    # (prompts/materialize.py, mcp/tools/artifact.py, display/parallel_display.py,
    # display/completion_summary.py, cli/commands/run.py, pipeline/handoffs.py,
    # and the plain_renderer package) to enforce no hardcoded phase names.
    # The seven previous single-source guards were consolidated into this
    # single module. Production source is the observable subject of these
    # structural tests, so replacing with mocked content would defeat the
    # purpose.
    "test_no_hardcoded_phase_names",
    # Static analysis tests that read Python source or documentation files
    # from the repo to enforce structural invariants.
    "test_doc_adding_a_new_agent",
    "test_parallel_no_worktree_imports",
    "test_repo_root_operational_docs_sync",
    # Documentation-source and CLI callback source-sync invariants inspect
    # repository documentation and production modules. Their read targets are
    # the subjects under test, so fixtures or a tmp_path copy would make the
    # regression guards meaningless; the callback guidance test also pins the
    # explicit-only local-config boundary.
    "test_documentation_command_sync",
    # AC-08 silent-skip audit that reads auto_integrate.py source to
    # enforce that the AC-01 disabled-path and the
    # phase-transition-pre-check surface both remain byte-identical
    # (the production source IS the subject under test; a tmp_path
    # copy would defeat the structural invariant the test pins).
    "test_auto_integrate_no_silent_skip",
    # Agent spawn site guard that drives the (sync) invoke reader and
    # the (async) SubprocessAgentExecutor.run() paths RUNTIME via
    # recording sync/async fake factories on a real ``ProcessManager``.
    # The factories use ``FakePopen`` /
    # ``FakeControllableAsyncProcess`` from ``ralph.testing.fake_process``
    # so no real OS process is created and no ``subprocess.run`` /
    # ``Popen`` / ``create_subprocess_exec`` is invoked. Listed here
    # so the audit does not flag the ``ProcessManager`` /
    # ``FakePopen`` / ``FakeControllableAsyncProcess`` imports as
    # test policy violations; the runtime seam itself does no real I/O.
    "test_agent_spawn_detaches_tty",
    # Spawn-entry containment test reads the package's script declarations and
    # guarded production modules. Those files are the subject under test: a
    # mocked filesystem could not detect a newly added entry point that skips
    # inherited malloc-debug cleanup.
    "test_spawn_env_containment",
    # Artifact-submission prompt audits that read the packaged Jinja
    # templates (production source) to enforce that every single-shot
    # template embeds the shared ``_artifact_submission.j2`` macro with
    # the canonical artifact type. The template body is the subject under
    # test — a mock would defeat the audit's purpose.
    "test_audit_artifact_submission_canonical_types",
    "test_audit_artifact_submission_dumb_agent_proof",
    "test_audit_artifact_submission_standardization",
    # Spawns python -O to verify import-time invariants survive -O.
    # This cannot be tested from the same process because -O is a
    # per-process flag; a subprocess is the only way to test this.
    "test_audit_artifact_submission_canonical_path",
    # Spawns python -O to verify timing-safety import-time invariants survive -O.
    # This cannot be tested from the same process because -O is a
    # per-process flag; a subprocess is the only way to test this.
    "test_timing_safety_transports",
    # Black-box CLI entry-point verification for the wt-013 skill-mirror
    # freshness audit. The test spawns ``python -m
    # ralph.testing.audit_skill_mirror_freshness`` to prove the audit's
    # ``main()`` is reachable as a module CLI (the path the verify
    # step uses), not just as an imported function. MockProcessExecutor
    # cannot replace this: the verification step runs the audit as a
    # subprocess, and the test's job is to mirror that path so a
    # packaging regression that breaks ``python -m ...`` invocation
    # cannot slip past the unit test.
    "test_audit_skill_mirror_freshness",
    # Git integration tests using the tmp_git_repo fixture (which wraps
    # tmp_path). The write_text calls go to the fixture's temp directory.
    "test_git_rebase_preconditions",
    "test_git_wrapper",
    # Legacy-restart acceptance test (scenario 8) seeds raw legacy checkpoint
    # + record bytes into the tmp_git_repo fixture dir so the production
    # resume read must parse and migrate them; same tmp_git_repo-writes
    # pattern as the two entries above.
    "test_legacy_restart_acceptance",
    # Docs<->pin-list parity guard: reads docs/agents/watchdog-spec.md
    # to enforce that every RALPH_PIN_TEST_PATHS entry is cited in the
    # traceability doc. The assertion is the contract under test;
    # mocking the file read would test nothing.
    "test_watchdog_spec_docs_pin_parity",
    # Subprocess-launched AGY mock agent stub
    # (tests/_support/mock_agy_v1_1_13.py, the v1.1.13 emitter split out of
    # mock_agy.py). The stub performs REAL loopback JSON-RPC round trips
    # (urllib.request.urlopen) against the harness's MCP fallback server at
    # RALPH_MCP_ENDPOINT -- the real HTTP transport is the system under test
    # (wire-ledger evidence is only produced by an actual tools/call), so
    # mocking the boundary would defeat the purpose. Mirrors
    # mock_multimodal_agent.py's real-wire dispatch.
    "mock_agy_v1_1_13",
    # Startup-diagnostics tests: the LifecycleDeps contract requires a
    # real listening socket (reserve_listening_socket returns
    # (host, port, socket.socket)); a kernel-assigned loopback bind on
    # port 0 is the production dependency shape under test. Mocking the
    # boundary would break the tuple type the production code asserts.
    "test_mcp_server_startup_diagnostics",
}

#: Files that may read only shipped repository source files. This exemption
#: suppresses Path.read_text/read_bytes findings only; every other policy
#: category remains checked for the same file.
SOURCE_READ_ALLOWLIST: set[str] = {
    # Starter-policy and ADR alignment guard. Reads the shipped
    # ``ralph/project_policy/starters/*.md`` files and ADR-0002 because their
    # literal contents are the regression contract: the test detects drift
    # between the four starter policies and the recorded architecture
    # decision. A tmp_path copy would assert against the fixture, not the
    # shipped file, so the guard would stop detecting drift entirely. Reading
    # those files is the ONLY thing the test needs exempted.
    "test_visual_verdict_policy_alignment",
    # Parallel-execution guidance regression guard. Reads the shipped
    # ``ralph/prompts/templates/shared/_parallel_execution.jinja`` partial
    # because its literal contents are the regression contract: the test
    # pins the wave/ownership/protected-path prose, a fixture copy would
    # assert the fixture's contents, not the shipped file's, and a
    # ``tmp_path`` rewrite would defeat the drift guard. Reading that one
    # template is the ONLY thing the test needs exempted.
    "test_developer_prompt_subagent_guidance",
    # per-file-ignores inventory drift guard. Reads the shipped
    # ``pyproject.toml`` and ``docs/ralph-workflow-policy/linting-policy.md``
    # because the policy document claims to be "the complete inventory of
    # keys" for ``[tool.ruff.lint.per-file-ignores]``; a fixture copy would
    # assert the fixture's completeness, not the shipped files'. Reading
    # those two files is the ONLY thing the test needs exempted.
    "test_linting_policy_inventory",
    # Single-owner regression guards for the pipeline state/record
    # authorities. Each scans the shipped ``ralph/pipeline/*.py`` sources
    # with ``ast`` because the shipped source IS the regression contract:
    # the test detects any new direct writer of an authority-owned field
    # outside its owning module. A tmp_path copy would scan the fixture, not
    # the production tree, so the guard would stop detecting drift entirely.
    # Reading those files is the ONLY thing these tests need exempted; any
    # write/subprocess/sleep in them is still reported.
    "test_waiting_state_owner",
    "test_recovery_record_retained_owner",
    "test_run_loop_setters_only",
    "test_run_loop_decision_delegation",
    "test_diagnostic_transition_helpers",
    "test_commit_completion_authority",
}

#: Files that may measure real wall-clock time in a test.
WALL_CLOCK_ALLOWLIST: set[str] = {
    # Single timestamp measurement for fake process creation — not an
    # elapsed-time loop. Aliased as _time.monotonic().
    "test_process_manager",
    # Comparing FakeClock timing against real wall-clock time.
    # Core purpose of these tests: verify FakeClock.sleep() and
    # FakeClock.advance() don't cause real wall-clock delays.
    "test_timeout_clock",
    # Parallel execution timing assertions.
    # Measures fan-out/verify timing to confirm parallelism works
    # correctly, not to accumulate passage-of-time for control flow.
    "test_parallel_serialized_verification",
    # Measures actual process kill duration for hard-kill correctness testing.
    # Wall-clock measurement IS the point of this test.
    # Renamed from ``test_*_helper_*`` to ``_*_helper_*`` in commit
    # e1247b55d; the audit's allowlist keys on the file stem, so this
    # entry must match the renamed stem to remain effective.
    "_hard_kill_helper_sleeperexecutor",
    # Performance regression test: measures real subscriber dispatch latency.
    # Wall-clock measurement IS the correctness assertion.
    "test_subscriber_performance",
    # Wall-clock budget pin for the new anti-drift test classes (per
    # PA-004). The TestRegressionBudget::test_combined_wall_clock_under_8s
    # test and the test_wedged_run_exits_cleanly test (PA-006) both
    # measure real wall-clock to assert budget compliance. The wall-clock
    # measurement IS the correctness assertion in both cases.
    "test_no_anti_drift_regression",
    "test_no_anti_drift_recovery_invariants",
    # Wall-clock budget pin for the F1-F20 fault matrix. Each
    # per-mode case asserts the call returned inside its 1.0s
    # budget via real wall-clock because the production handler
    # does not expose a clock injection seam yet; the measurement
    # IS the correctness assertion (criterion 1c).
    "test_explore_fault_matrix_full",
    "test_explore_fault_matrix_full_late",
    "test_explore_fault_matrix",
    # Wall-clock budget pin for the runtime FD-leak / watch-handle
    # resource tests. The proofs use real wall-clock to measure the
    # post-operation FD count and inspect the child process
    # /proc/<pid>/fdinfo via a real subprocess because the explore
    # substrate owns the SQLite connection (no injection seam).
    "test_explore_resource_lifecycle",
    # Default-gate endpoint sweep wall-clock budget pin (per AC-05,
    # AC-06). The sweep must complete well inside the immutable
    # 60-second combined test budget; the per-tool and per-suite
    # 5s/10s budgets are the correctness assertion, so the test
    # uses ``time.monotonic()`` to measure real elapsed time.
    # Production code paths still inject FakeClock for any
    # timeout-driven flow; the wall-clock measurement here is a
    # single-point budget pin that cannot be expressed with a
    # fake clock. The ``test_mcp_endpoint_functional_sweep`` file
    # is the surviving single sweep after the zero-dead-code
    # consolidation (the legacy
    # ``tests/integration/test_mcp_endpoint_sweep.py`` was deleted
    # and its unique wall-clock assertions ported here).
    "test_mcp_endpoint_functional_sweep",
    # Realistic-codebase fixture wall-clock budget pin (per AC-02,
    # AC-06). ``test_indexed_search_completes_under_5s`` measures
    # the index + grep cycle over a ~50-file synthetic workspace
    # so a regression that triples the indexed search wall time
    # cannot slip past the default gate. The wall-clock
    # measurement IS the assertion; production paths inside the
    # cycle still use FakeClock for any timeout contract.
    "test_explore_real_codebase",
    # Memory-plateau characterization harness (wt-024): prints
    # early/late elapsed means via time.perf_counter() for drift
    # inspection. Retained-byte plateau remains the hard assertion;
    # elapsed timing is characterization evidence, not a brittle
    # wall-clock oracle. Kept in the default suite (no
    # subprocess_e2e) because the harness uses no real child
    # processes; only the single-point timing measurement needs
    # this allowlist.
    "test_recovery_memory_regression",
    # S-12 scenario baseline characterization harness: the
    # responsiveness dimension measures real reindex + search
    # elapsed time across 6 filesystem scenarios. The wall-clock
    # measurement IS the characterization data being recorded
    # (not a control-flow oracle); production code paths inside
    # the cycle still use FakeClock for any timeout contract.
    # Kept in the default suite because the harness uses no real
    # child processes; only the single-point timing measurement
    # needs this allowlist.
    "test_filesystem_scenario_baseline",
    # Multi-cycle pipeline memory regression harness. The wall-clock
    # measurement IS the correctness assertion, so the real elapsed
    # time read is allowed.
    "test_pipeline_multi_cycle_memory_regression",
}
