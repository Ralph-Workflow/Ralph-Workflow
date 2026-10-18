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
