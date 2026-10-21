# MCP restart decisions: operator playbook

This page complements the technical reference at
:doc:`mcp-architecture` (sections *Restart-decision durability* and
*Startup serving bound and platform contract*). Where the technical
reference describes what the runtime writes, this page describes what
to do with the evidence during a runaway-restart incident.

The technical reference is the source of truth for the file shapes;
this page is the source of truth for the triage workflow.

## What the durable evidence actually proves

The supervisor records health events to a per-bridge JSONL sink at
`<workspace>/.agent/mcp-health-events-<bridge-id>.jsonl`. The
`bridge-id` is the 12-hex-char value assigned per-bridge in
`start_mcp_server` → `configure_bridge_sink`. The sink is auto-wired
by `lifecycle.py:configure_bridge_sink` once per bridge, so the
file from a prior bridge is never overwritten by a fresh one.

The stream records two distinct shapes:

1. **Restart decisions (five-field closed shape)**:
   Written via `record_restart_decision` whenever the supervisor
   triggers an actual restart of the child process. The record
   contains:
   - `reason`: `"process_exited"` or `"probe_failed"`.
   - `exit_status`: OS exit status from `process.poll()` (for `process_exited`), or `null`.
   - `probe_duration_s`: wall-clock probe duration in seconds (for `probe_failed`), or `null`.
   - `log_tail`: list of up to 10 captured child log lines, or `null` if the log was unavailable.
   - `stage`: always `"process_exited"` (when `reason` is `"process_exited"`) or `"probe_failed"` (when `reason` is `"probe_failed"`). The value records the restart trigger, **not** the child's startup-progress label. Startup-stage information for the child at decision time lives in the per-spawn stage file described in the next section. See `lifecycle.py:632-638`.

   The two restart reasons classify the restart trigger:
   - `PROCESS_EXITED` (`reason="process_exited"`) — the child process died
     unexpectedly; `exit_status` records the process exit code.
   - `PROBE_FAILED` (`reason="probe_failed"`) — the child process remained alive
     but missed `_PROBE_FAILURES_BEFORE_RESTART = 3` consecutive health
     probes; `probe_duration_s` records the duration of the failing probe.

2. **Contention deferrals (six-field shape)**:
   Written via `lifecycle.py:_record_contention_deferral` when a health probe
   fails while host contention is detected. The record contains:
   - `reason`: `"probe_deferred_contention"`.
   - `exit_status`: always `null`.
   - `probe_duration_s`: wall-clock probe duration in seconds, or `null`.
   - `deferral_count`: integer count of consecutive probe failures deferred under contention.
   - `log_tail`: always `null`.
   - `stage`: `"probe_deferred_contention"`.

   **Crucially, contention deferrals represent NO restart.** The bridge
   detects a host-contention signal (e.g. an inotify watch-exhaustion event)
   alongside a probe failure and *conservatively defers* the restart decision,
   incrementing `deferral_count` and backing off probe timeouts. The signal
   indicates the probe failed *in the presence of* contention, but does not
   prove the failure was caused by host starvation rather than a wedged
   server — the implementation cannot make that distinction.

The table below summarizes what each record type proves:

| Question | Look at | Interpretation |
|---|---|---|
| Did the child crash? | `reason == "process_exited"` | The child died; `exit_status` carries the OS exit code. |
| Did the child hang? | `reason == "probe_failed"` | The child is alive but unresponsive; `probe_duration_s` is the wall-clock duration of the failed probe. |
| Was a probe failure deferred under load? | `reason == "probe_deferred_contention"` | Host contention was detected; NO restart occurred. `deferral_count` tracks consecutive deferred misses. |
| Why was the restart triggered? | `stage` | For restart decisions, `stage` is always `"process_exited"` or `"probe_failed"` — it records the restart trigger, not the child's startup step. For contention deferrals, `stage` is `"probe_deferred_contention"`. What the child was actually doing at decision time is answered by the per-spawn stage file (see below), not by the JSONL `stage` field. |

## Triage procedure on the next incident

The JSONL record and the per-spawn stage file are the primary durable
artifacts the next incident is triaged from. Pair them manually —
the JSONL record does NOT carry `spawn_id`, `pid`, or `timestamp`,
so correlation is operator-driven based on retained evidence.

1. **Scan the entire per-bridge JSONL stream.** Read the file at
   `<workspace>/.agent/mcp-health-events-<bridge-id>.jsonl` completely;
   do NOT inspect only the last line.
   - A trailing run of `probe_deferred_contention` records means sustained
     host contention where restarts were intentionally **deferred**. No
     restart occurred during that run.
   - When a restart did occur, the restart trigger is the **latest record
     whose `reason` is `process_exited` or `probe_failed`**.
   - Inspect `exit_status` (for `process_exited`) or `probe_duration_s` (for
     `probe_failed`) to identify the quantitative trigger, and check
     `log_tail` for captured child output.

2. **Find the candidate child via per-spawn stage files.** The parent
   sets `MCP_STAGE_ID_ENV` to a fresh UUID4 before each spawn (`lifecycle.py`
   near the `setdefault(MCP_STAGE_ID_ENV, ...)` call) and the child writes
   its latest completed startup stage to
   `<workspace>/.agent/.ralph_mcp_start_stage.<spawn_id>` (see
   `ralph.mcp.server._start_stage_tracker:stage_file_path`). The file is
   keyed by spawn identity, ensuring multiple children on the same
   workspace never share a stage file.

3. **Inspect stage-file mtimes and observe attribution limits.**
   The JSONL carries no timestamp, so timing anchors come solely from
   **stage-file mtimes** and **process-manager log lines**.
   Keep the following attribution limits in mind:
   - A `pre_ready_to_serve` stage label (one of `env_loaded`, `session_ready`,
     `config_loading`, `config_loaded`, `upstream_catalog_or_discovery_done`,
     or `registry_built`) identifies the restarted child **only if it died
     mid-startup**.
   - A child that successfully completed startup and served healthily before
     failing displays `ready_to_serve`. In that scenario, both the restarted
     child and the surviving child show `ready_to_serve` and **cannot be
     distinguished from stage files alone**.

4. **Cross-reference the bounded process-manager log.** The
   `ralph.process.manager` module writes to
   `<workspace>/.agent/tmp/mcp-server.log` with a stable
   `ralph.process.manager._process_manager_runtime:loguru_event_listener:68`
   source line.
   - Cross-reference the process exit timestamps in that log against the
     stage-file mtimes to bracket child lifecycles.
   - **Attribution limit:** The process-manager exit log carries no
     `bridge-id` or spawn identity. If multiple process exit events appear
     within the incident bracket (e.g. two nonzero-exit events), attributing
     a specific PID to a specific bridge spawn is ambiguous.
   - A `PROBE_FAILED` record without a concurrent process-manager exit log
     indicates the child process was still alive when the restart decision
     was executed; `probe_duration_s` serves as the primary quantitative
     record.

## PR sentence: trigger and stalled-`git` evidence status

**Trigger (one sentence).** The run ended because a health probe overran its
own 5 s timeout by about 1.9 s (`probe_duration_s=6.887781133002136`) while
the host was stalled, and the break-before-make restart killed a working MCP
server before its replacement was ready; a SIGKILL survivor (pid 2156394,
still alive 2 s after the kill at 13:53) is the contemporaneous evidence that
the host, not the server, was unhealthy.

**Stalled-`git` identity: undetermined.** The retained evidence names the
survivor only by PID. Which `git` command timed out roughly once a minute, why
it timed out, and whether its timeout is correctly sized were not recoverable
from that run's artifacts, and the specification permits recording the
evidence rather than guessing the identity.

**Diagnostics added so the next incident can identify it.** A process that
survives the kill escalation now logs its psutil process state and command:
`_process_manager.py:1802-1810` emits
`Process {pid} still alive after kill (status=..., command=...)`, where
`status` is `psutil.Process(pid).status()` (`disk-sleep`, `zombie`, `stopped`,
...) and `command` comes from `cmdline()` falling back to `name()`
(`_process_manager.py:128-144`). On the MCP side, `McpServerError` carries the
same live-child state: `lifecycle.py:854-870` (`_child_process_diagnostics`)
attaches `psutil_status` and `handover_step`, and both are rendered by
`str(err)`. Triage on the next incident uses the *Triage procedure on the next
incident* section above.

## Edge-case coverage map (E1.1-E7.3)

Exactly one row per catalogue id, carrying the strongest proof: each row cites
the test that establishes the behaviour and quotes, at assertion level, what
that test actually proves. A test is credited only for what its body asserts,
never for what its name implies; where the two disagree, the body wins and the
disagreement is called out under *Tests whose name does not match their body*.
Secondary proofs for the same id are listed under *Supporting proofs* below, so
nothing is dropped by the one-row-per-id shape. Paths are relative to
`ralph-workflow/`; run any cited node id with
`uv run python -m pytest <file>::<test_name>`.

| Id | Test | What the assertions prove |
|---|---|---|
| E1.1 | `tests/mcp/test_bridge_e1_e2_edge_cases.py::test_e11_private_readiness_rejects_spawn_id_mismatch` | A candidate answering the private channel with `spawn_id="wrong-spawn-999"` is rejected: `stage2 == "spawn_id_mismatch"` and the wrong id is in the error, so the candidate is never judged by the shared endpoint. |
| E1.2 | `tests/mcp/test_single_acceptor_instrumented.py::test_e43_single_acceptor_graceful_handover` | Two instrumented real children never overlap: `cand_start_ts[0] >= old_dropped_ts[0]`, and on Linux the listening inode leaves the old child and appears in the candidate. |
| E1.3 | `tests/mcp/test_bridge_e1_e2_edge_cases.py::test_e13_hung_old_child_reaped_at_drain_deadline` | A real wedged child holding one accepted `tools/call` for 30 s is reaped within `_DRAIN_REAP_BOUND_S`, `elapsed < _WEDGED_CHILD_HOLD_S`, and the severed request surfaces exactly one `OSError`-family error. |
| E1.4 | `tests/mcp/test_handover_inflight_real_child.py::test_e14_duplicate_submission_across_swap_applied_once` | An artifact submission retried across the swap is applied exactly once rather than twice. |
| E1.5 | `tests/mcp/test_handover_inflight_real_child.py::test_e15_streaming_call_drain_fate_real_subprocess` | A long streaming call on the draining child ends in a typed network error, `proc.poll() is not None` after the drain, and the next replacement serves (`resp.status == 200`), so draining blocks nothing. |
| E1.6 | `tests/mcp/test_handover_inflight_real_child.py::test_e16_interleaved_ledger` | Interleaved appends from two real children still pass `verify_chain`, so the chain head is read from the file under the lock and never cached. |
| E1.7 | `tests/mcp/test_e17_state_continuity.py::test_e17_bridge_state_continuity_enumeration` | Per-category dispositions: `run_id`, `_session_id`, `_broker_fingerprint`, `_expected_tool_hash` and `restart_count` survive the swap; wrap-up flags, saturation counters, metrics, upstream sessions and `ralph://exec/` handles are proven reset-safe; the repetition tracker is proven carried. |
| E1.8 | `tests/mcp/test_e18_old_session_id_accepted.py::test_real_child_accepts_old_session_id_across_rotation` | A real child subprocess answers a request carrying an old `mcp-session-id` with `200` and `b'"candidate_ok": true'` across a simulated rotation. |
| E1.9 | `tests/mcp/test_lifecycle_tool_hash_and_classification.py::test_tool_set_drift_refused_when_old_child_alive` | A drifted candidate is refused while the old child is alive: the bridge keeps the old `_inner` and counts the failed attempt. |
| E2.1 | `tests/mcp/test_bridge_e3_e4_edge_cases.py::test_e46_candidate_death_on_probation_triggers_immediate_replacement` | After promotion a candidate that exits (poll 137) is replaced on the *next* check with no consecutive-probe requirement: `restarted is True`, `restart_count == 2`. |
| E2.2 | `tests/mcp/test_root_cause_real_child_bridge.py::test_c5_e22_candidate_stage_failures_parametrized` | For each of eight failure stages (spawn, import, session, config, catalog, registry, handshake-init, tool-set drift) a real failing subprocess gives `result is False`, `restart_count == 1`, `bridge.process.pid == old_proc.pid`, `old_proc.poll() is None`, every candidate reaped, and `descendants == set()`. |
| E2.3 | `tests/mcp/test_bridge_e5_e6_e7_edge_cases.py::test_e64_import_time_invariants_positive` | Both startup bounds are pinned: `0 < _CANDIDATE_STARTUP_DEADLINE_S < _PREFLIGHT_DEADLINE_S`, so the candidate deadline nests inside the existing bound. |
| E2.4 | `tests/mcp/test_e24_fresh_stage_id.py::test_spawn_mcp_process_assigns_fresh_stage_id_per_spawn` | Two consecutive spawns get different stage ids, and both differ from a pre-existing stale id (`stage_id_1 != stage_id_2`, `!= stale_stage_id`); pause files are stage-suffixed. |
| E2.5 | `tests/mcp/test_e25_candidate_survives_agent_tmp_deletion.py::test_candidate_survives_agent_tmp_and_workspace_cleanup` | After `.agent/` and `mcp.toml` are deleted mid-run, the candidate still reads the parent-owned snapshot and reports the same session id. |
| E2.6 | `tests/mcp/test_e26_broken_workspace_package.py::test_real_child_imports_package_root_despite_broken_ralph_in_workspace` | A real child importing from a workspace containing a `ralph/` that raises `SyntaxError` still exits `rc == 0`. |
| E2.7 | `tests/mcp/test_e42_two_supervisors_one_replacement.py::test_e27_single_candidate_during_startup_serialization` | A concurrent probe failure and registry reset never yield two candidates: `max_active_spawns == 1`. |
| E3.1 | `tests/mcp/test_e3x_transport_gap_typed_retry.py::test_e31_repeat_burst_during_handover_gap_with_zero_refusals` | Two consecutive burst cycles against the production endpoint complete or time out but never refuse, and the bridge is back to `SERVING` each cycle. Backlog size is cited at `ralph/mcp/server/_reserved_socket.py:54` (`sock.listen(128)`). |
| E3.2 | `tests/mcp/test_e3x_transport_gap_typed_retry.py::test_e32_production_bridge_retry_through_real_handover_gap` | Documents which branch holds: the dead-child replacement bound fits inside the client HTTP timeout, so the retry returns `200` with a well-formed JSON-RPC body. |
| E3.3 | `tests/mcp/test_e33_listening_fd_real_child.py::test_non_mcp_real_child_does_not_inherit_listening_fd` | A real non-MCP child holds no copy: `sock_ino not in child_inodes` and no fd link resolves to that inode. |
| E3.4 | `tests/mcp/test_e3x_transport_gap_typed_retry.py::test_e34_stale_keepalive_after_real_handover` | A request on a persistent connection pinned to the retired child is retried exactly once (`attempts == 1`) when safe; a non-idempotent call raises instead (`exc_info.value.tool_name == "ralph_submit_md_artifact"`) and is never replayed. |
| E4.1 | `tests/mcp/test_e3x_transport_gap_typed_retry.py::test_e41_bridge_state_transitions_cleanly_without_dropping_requests` | The state machine runs `SERVING -> REPLACING -> SERVING`: `bridge._bridge_generation == 1`, `new_pid != old_pid`, `tools/list` succeeds in both phases, and a concurrent `bridge.process` read succeeds within 0.5 s. |
| E4.2 | `tests/mcp/test_e42_two_supervisors_one_replacement.py::test_e42_two_supervisors_concurrent_replacement_single_winner` | Two supervisors on one bridge give `sorted(results) == [False, True]`, `restart_count == 1`, `total_spawns == 1`, `max_active_spawns == 1` — one replacement, no double-counting. |
| E4.3 | `tests/mcp/test_replacement_shutdown_signals_real_child.py::test_e43_supervisor_exit_mid_replacement_reaps_candidate_and_old` | `McpSupervisor.__exit__` during a real candidate verify leaves `cand_proc.poll() is not None` and `old_proc.poll() is not None`, and both the restart and exit threads finish. |
| E4.4 | `tests/mcp/test_replacement_shutdown_signals_real_child.py::test_e44_bridge_shutdown_mid_replacement_reaps_candidate_and_old` | Shutdown mid-replacement reaps both real children (`cand_proc.poll() is not None`, `old_proc.poll() is not None`). |
| E4.5 | `tests/mcp/test_replacement_shutdown_signals_real_child.py::test_e45_sigint_during_handover_no_orphans` | A real `KeyboardInterrupt` mid-handover fires and the leak audit finds no surviving pid. |
| E4.6 | `tests/mcp/test_e46_parent_sigkill_no_orphans.py::test_e46_parent_sigkill_reaps_every_child_of_a_make_before_break_overlap` | The overlap was real (`alive_at_sigkill` all `True`, `draining_count >= 1`), the watcher exits `-9`, `survivors == []`, and the port rebinds once the tree collapses. |
| E5.1 | `tests/mcp/test_bridge_e5_e6_e7_edge_cases.py::test_e51_pid_reuse_guard_anchored_to_bridge_generation` | With the OS reusing pid 1001, a probe carrying the *previous* generation is discarded: `restarted is False`, `restart_count == 0`. |
| E5.2 | `tests/mcp/test_bridge_e5_e6_e7_edge_cases.py::test_e52_unkillable_child_does_not_block_promotion` | An old child whose `kill()` does not end it still yields `promoted is True`, `bridge._inner is candidate`, `bridge.process.pid == 2002` — retirement failure never blocks promotion. |
| E5.3 | `tests/mcp/test_bridge_e5_e6_e7_edge_cases.py::test_e53_stopped_child_trips_deferral_ceiling_and_forces_replacement` | Deferrals accumulate to `_DEFERRAL_COUNT_CEILING` while the child stays alive, then the next failure forces `restarted is True` despite a fresh contention signal — a stopped server is still replaced. |
| E5.4 | `tests/mcp/test_e54_bridge_zombie.py::test_e54_zombie_child_is_treated_as_dead_and_replaced_by_bridge` | A zombie serving child (where `os.kill(pid, 0)` still succeeds) is classified dead: `restart_count == 1`, `decision.reason == RestartReason.PROCESS_EXITED`, and no `terminate`/`kill` is sent to it. |
| E6.1 | `tests/mcp/test_bridge_e5_e6_e7_edge_cases.py::test_e61_suspend_resume_clock_jump_trips_wall_time_ceiling` | With an injected clock, a jump past `_DEFERRAL_WALL_TIME_CEILING_S` forces `restarted is True` — a resumed host cannot burn the deferral budget silently. |
| E6.2 | `tests/mcp/test_probe_overrun_self_measure.py::test_e62_probe_overrun_is_contention_and_deferral_ceiling_is_the_backstop` | A probe past `timeout + slack` reports `overrun is True` with `infra_error is False` and never counts toward the restart threshold (`_restart_count == 0`), while the ceiling still forces the replacement — the backstop that stops a hung server hiding as contention forever. |
| E6.3 | `tests/mcp/test_bridge_e5_e6_e7_edge_cases.py::test_e63_flapping_server_resets_consecutive_failures` | Across a fail/pass alternation the counter is retained at 1 and 2, resetting only on the third consecutive success (`_consecutive_probe_successes == 3`), so a lucky probe cannot clear it. |
| E6.4 | `tests/mcp/test_e64_bound_dominance_proof.py::test_e64_bounds_match_independently_recomputed_numbers` | Both bounds are recomputed from independently pinned constants: `_WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S` from `20 x (2.0 + 30.0 + 12.0)` and `_CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S` from `5.0 + 1.0 + 1.0 + 0.5 + 12.0`. |
| E7.1 | `tests/mcp/test_e71_enospc_diagnostics_fail_open.py::test_spawn_process_succeeds_when_diagnostic_open_raises_enospc` | An `ENOSPC` on the diagnostic log open does not fail the spawn: a managed process is returned and stdout/stderr are redirected elsewhere. |
| E7.2 | `tests/mcp/test_e72_footprint_real_child.py::test_e72_real_child_make_before_break_footprint` | The doubled overlap footprint is measured against a real child tree and bounded (peak children, process-tree size, fd holders, aggregate RSS), and the whole tree is gone afterwards. |
| E7.3 | `tests/mcp/test_bridge_e5_e6_e7_edge_cases.py::test_e73_contention_signal_defers_restart` | With a fresh contention signal and a live old child no candidate is started: `restarted is False`, `_consecutive_probe_failures_deferred == 1`, `restart_count == 0`, `bridge.process.pid == 1001`. |

### Supporting proofs for the same ids

- **E1.1** `tests/mcp/test_replacement_public_paths.py::test_reset_refuses_candidate_with_wrong_spawn_identity` — on the production reset path the wrong-spawn-id candidate is refused (`bridge.process is old_proc`, `candidate_proc.terminate_calls >= 1`).
- **E1.2** `tests/mcp/test_single_acceptor_instrumented.py::test_e43_single_acceptor_sigstopped_old_child_kills_before_resume` — the SIGSTOPped-old-child case the catalogue names: `old_proc.poll() is not None` before the candidate's `STARTED_ACCEPT`. Also `tests/mcp/test_bridge_e1_e2_edge_cases.py::test_e12_pause_ack_timeout_triggers_escalation` (bounded ack wait, `elapsed >= 0.09` for a 0.1 s bound).
- **E1.4** `tests/mcp/test_handover_inflight_real_child.py::test_e14_outcome_unknown` — a severed non-idempotent in-flight call surfaces a typed `OSError`-family error, not a plain "failed". Also `tests/mcp/test_e14_duplicate_submission_once.py::test_duplicate_canonical_submission_runs_exactly_once` (`assert not history_dir.exists()` — no second history write).
- **E1.6** `tests/mcp/test_e16_store_interleave_real_children.py::test_e16_transport_sidecar_two_child_interleave_preserves_observations` (`carried_streak == observations_made`); `tests/mcp/test_lifecycle_e16_store_claims.py::test_e16_store_2_session_file_has_no_child_writer` (per-store claim enumeration); `tests/mcp/test_bridge_e1_e2_edge_cases.py::test_e16_two_child_ledger_interleave` (both forked writers `exitcode == 0`, `verify_chain(...) is True`).
- **E1.8** `tests/mcp/test_e18_old_session_id_accepted.py::test_session_id_persisted_before_first_child_serves_and_inherited` (id persisted before the first child serves) and `::test_standalone_server_accepts_completely_unknown_session_id` (an unknown id is answered `200`).
- **E1.9** `tests/mcp/test_lifecycle_tool_hash_and_classification.py::test_tool_set_drift_promoted_when_old_child_dead` (the one documented exception) and `tests/mcp/test_root_cause_real_child_bridge.py::test_c1_tool_hash_mismatch_rejected_real_child` (mismatch refused against a real serving child).
- **E2.1** `tests/mcp/test_bridge_e1_e2_edge_cases.py::test_e21_candidate_death_after_promotion_counted` — the post-promotion death is classified `process_exited` and replaced (`restart_count == 2`, `bridge.process.pid == 3003`).
- **E2.3** `tests/mcp/test_bridge_e1_e2_edge_cases.py::test_e23_startup_deadline_bounded` — cumulative stage budgets stay ordered and below `mcp_preflight_timeout_from_env()`.
- **E2.4** `tests/mcp/test_e24_fresh_stage_id.py::test_real_children_write_distinct_stage_files_without_collision` — two real children write distinct stage files with no collision.
- **E2.5** `tests/mcp/test_e25_candidate_survives_agent_tmp_deletion.py::test_missing_candidate_input_is_typed_counted_failure` — a genuinely missing input is a typed, counted failure inside 1 s (`err.last_stage == "snapshot_missing"`), not a 30 s timeout.
- **E2.6** `tests/mcp/test_e26_broken_workspace_package.py::test_candidate_spawn_uses_same_interpreter_as_parent` — `cmd[0] == sys.executable`, `cmd[1:3] == ["-m", "ralph.mcp.server"]`.
- **E2.7** `tests/mcp/test_replacement_public_paths.py::test_health_then_reset_produce_exactly_one_candidate` — a reset during an in-flight health replacement is refused (`spawns == [1]`, no budget consumed).
- **E3.1** `tests/mcp/test_e3x_transport_gap_typed_retry.py::test_e31_backlog_burst_during_production_bridge_handover_gap` — burst and restart threads both complete against the real production bridge with `bridge.restart_count >= 1`.
- **E3.2** `tests/mcp/test_e3x_transport_gap_typed_retry.py::test_e32_production_bridge_client_timeout_surfaces_typed_network_error` — the other branch: when the client timeout does fire, `isinstance(client_error, OSError)`, typed and retryable rather than a new failure class.
- **E3.3** `tests/mcp/test_e33_listening_fd_real_child.py::test_pass_fds_explicitly_passes_socket_to_child` — the positive control: the MCP child *does* hold the inode, so `pass_fds` is the only channel.
- **E4.1** `tests/mcp/test_replacement_public_paths.py::test_health_path_starts_candidate_without_holding_the_bridge_lock` — with private verification parked, an agent-path read completes and `bridge.process.pid == _OLD_PID`.
- **E4.2** `tests/mcp/test_e42_two_supervisors_one_replacement.py::test_e42_two_supervisors_terminal_budget_delivered_to_both` — on real exhaustion each supervisor catches one `BUDGET_EXHAUSTED` and each `_mcp_error is not None`.
- **E4.4** `tests/mcp/test_replacement_shutdown_signals_real_child.py::test_e44_bridge_shutdown_mid_drain_reaps_old_and_candidate` — shutdown mid-drain reaps the draining old child and the promoted candidate.
- **E4.5** `tests/mcp/test_replacement_shutdown_signals_real_child.py::test_e45_real_child_signal_order_sigterm_then_sigkill` — escalation order pinned against a real child (`SIGTERM_RECEIVED`, `record.status == ProcessStatus.KILLED`).
- **E4.6** `tests/mcp/test_e46_parent_sigkill_no_orphans.py::test_e46_upstream_grandchild_dies_with_its_candidate` — the candidate's upstream grandchild does not outlive the SIGKILLed owner, proving `PR_SET_PDEATHSIG` chains through the group.
- **E5.1** `tests/mcp/test_replacement_shutdown_signals_real_child.py::test_e51_pid_reuse_pin_no_bare_kill_in_replacement_path` — static pin: no bare-PID kill or liveness call exists in the replacement/handover path.
- **E5.2** `tests/mcp/test_host_contention_signal_producers.py::test_record_sigkill_survivor_emits_on_zombie` — the survivor is recorded as contention (`signals == ["sigkill_survivor:zombie"]`).
- **E5.3** `tests/mcp/test_root_cause_real_child_bridge.py::test_c3_e53_sigstopped_child_detected_and_reaped` — a real SIGSTOPped child shows zero CPU progress and is reaped on replacement.
- **E5.4** `tests/mcp/test_bridge_e5_e6_e7_edge_cases.py::test_e54_zombie_child_reaped_by_poll` — the liveness argument itself: the zombie is *observed*, `os.kill(pid, 0)` succeeds on it, `poll()` reaps it, then `os.kill` raises `ProcessLookupError`.
- **E6.1** `tests/mcp/test_e3x_transport_gap_typed_retry.py::test_e61_clock_enforced_single_attempt_per_backoff_interval` — at most one attempt per backoff interval, enforced by the clock (`1.5 in cooldown_called`).
- **E6.3** `tests/mcp/test_mcp_bridge_avoids_killing_under_contention.py::test_successful_recovery_preserves_server` — sustained success clears both counters and `_restart_count == 0`; sustained contention still escalates to `restart_count == 1`.
- **E6.4** `tests/mcp/test_e64_import_guard_execution.py::test_e64_import_guard_fires_under_both_pythons_with_same_marker` — the check is enforced at import time under normal and `-O` Python (non-zero exit plus guard marker in stderr), so it cannot be stripped.
- **E7.1** `tests/mcp/test_e71_enospc_diagnostics_fail_open.py::test_spawn_process_stage_stays_unknown_after_enospc` (stage reads as unknown instead of crashing) and `tests/mcp/test_bridge_e5_e6_e7_edge_cases.py::test_e71_enospc_fail_closed_for_ledger_only` (the integrity store fails *closed* with a typed `WireLedgerIOError`).
- **E7.2** `tests/mcp/test_bridge_e5_e6_e7_edge_cases.py::test_e72_candidate_limit_exhaustion_keeps_old_child` — an `EMFILE` at candidate spawn is a counted, non-terminal failure that keeps the old child (`restarted is False`, `old_inner.process.poll() is None`).
- **E7.3** `tests/mcp/test_lifecycle_tool_hash_and_classification.py::test_confirm_before_replace_defers_on_confirm_success` — a child that *is* making progress is not replaced (`mock_restart.call_count == 0`, the miss is reclassified as a deferral).

### Tests whose name does not match their body

Three cited tests carry an `eNN` prefix their bodies do not support. They are
mapped to the body, not the name:

- `tests/mcp/test_single_acceptor_instrumented.py::test_e43_single_acceptor_graceful_handover`
  and `::test_e43_single_acceptor_sigstopped_old_child_kills_before_resume`
  prove **E1.2** (ordered single-acceptor handover), not E4.3.
- `tests/mcp/test_bridge_e3_e4_edge_cases.py::test_e46_candidate_death_on_probation_triggers_immediate_replacement`
  proves **E2.1** (probation death counted as `process_exited`), not E4.6. The
  real E4.6 proof is `tests/mcp/test_e46_parent_sigkill_no_orphans.py`.

A further set of `eNN`-named tests in the shared edge-case modules assert only
their own constant or an isolated helper and are **not** credited here; every
id above is carried by the dedicated real-child or behavioural module instead:

- `tests/mcp/test_bridge_e1_e2_edge_cases.py::test_e24_stage_id_fresh_per_spawn` — compares two `uuid4()` strings, which says nothing about what the spawner assigns.
- `tests/mcp/test_bridge_e1_e2_edge_cases.py::test_e26_broken_workspace_ralph_does_not_affect_candidate` — asserts only that `_PACKAGE_ROOT` is first in `PYTHONPATH`.
- `tests/mcp/test_bridge_e1_e2_edge_cases.py::test_e19_tool_set_drift_detection` — a hash-determinism check, not a drift refusal.
- `tests/mcp/test_bridge_e1_e2_edge_cases.py::test_e25_snapshot_survives_tmp_cleanup` — the snapshot directory is untouched, but no candidate is spawned.
- `tests/mcp/test_bridge_e1_e2_edge_cases.py::test_e27_bridge_lock_serializes_restart` — only that the lock is acquireable.
- `tests/mcp/test_bridge_e5_e6_e7_edge_cases.py::test_e62_probe_slack_bounds_classification` — pins the slack constant; its own docstring defers the behavioural proof to the E6.2 test above.

## Documented 2026-10 incident: trigger undetermined

For the documented 2026-10 ECONNREFUSED incident the per-bridge
sink was not yet durable enough to retain the run's restart
decisions. The bounded process-manager log has 12 generic
nonzero-exit events in the incident bracket that cannot be
attributed to a specific child identity — the `bridge-id` is not
recorded in the log line, and the log does not carry the spawn
identity the parent assigned to the killed child. The R3 trigger
for that respawn (`RestartReason.PROCESS_EXITED` vs
`RestartReason.PROBE_FAILED`) is therefore **undetermined** from
retained evidence.

The per-bridge sink described above is the durable artifact the
next incident is triaged from. The triage procedure in the section
above resolves the trigger on the next run.

## Closed vocabulary for the JSONL record

The per-bridge JSONL restart-decision record carries exactly five fields:
`reason`, `exit_status`, `probe_duration_s`, `log_tail`, `stage`.
The set is closed: a future change that adds a `spawn_id`, `pid`,
or `timestamp` field is an explicit test edit
(`tests/mcp/test_mcp_health_event_recorded.py:test_durable_sink_record_shape_is_closed`)
and an explicit update to the manual correlation procedure in the
section above. Schema drift is detected by the regression test, not
left to a future operator to discover.

Contention deferral records emitted by `lifecycle.py:_record_contention_deferral`
carry six fields: `reason`, `exit_status`, `probe_duration_s`,
`deferral_count`, `log_tail`, `stage`. Like the restart-decision record,
contention deferrals contain neither `spawn_id`, `pid`, nor `timestamp`,
ensuring all health stream analysis respects the manual correlation
and attribution guidelines defined above.
