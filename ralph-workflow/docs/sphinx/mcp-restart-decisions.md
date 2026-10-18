# MCP restart decisions: operator playbook

This page complements the technical reference at
:doc:`mcp-architecture` (sections *Restart-decision durability* and
*Startup serving bound and platform contract*). Where the technical
reference describes what the runtime writes, this page describes what
to do with the evidence during a runaway-restart incident.

The technical reference is the source of truth for the file shapes;
this page is the source of truth for the triage workflow.

## What the durable evidence actually proves

The `RalphRestartDecision` (lives at
`ralph.mcp.server._mcp_health_event:RestartDecision`) is written to
a per-bridge JSONL sink at
`<workspace>/.agent/mcp-health-events-<bridge-id>.jsonl`. The
`bridge-id` is the 12-hex-char value assigned per-bridge in
`start_mcp_server` → `configure_bridge_sink`. The sink is auto-wired
by `lifecycle.py:configure_bridge_sink` once per bridge, so the
file from a prior bridge is never overwritten by a fresh one.

The two `RestartReason` values classify the trigger:

- `PROCESS_EXITED` (`reason="process_exited"`) — the child process
  died unexpectedly; the record carries `exit_status` from
  `process.poll()` and the last 10 child log lines.
- `PROBE_FAILED` (`reason="probe_failed"`) — the child process is
  still alive but `_PROBE_FAILURES_BEFORE_RESTART = 3` consecutive
  probes missed; the record carries `probe_duration_s`.

The two values answer different questions:

| Question | Look at | Interpretation |
|---|---|---|
| Did the child crash? | `reason == "process_exited"` | The child died; `exit_status` carries the OS exit code. |
| Did the child hang? | `reason == "probe_failed"` | The child is alive but unresponsive; `probe_duration_s` is the wall-clock duration of the failed probe. |
| What was the child doing when the decision was made? | `stage` | The label is `"process_exited"` or `"probe_failed"`; the per-spawn stage file (see below) is the source of truth for the child's startup stage. |

## Triage procedure on the next incident

The JSONL record and the per-spawn stage file are the only durable
artifacts the next incident is triaged from. Pair them manually —
the JSONL record does NOT carry `spawn_id`, `pid`, or `timestamp`
today, so the correlation is operator-driven, not automatic.

1. **Classify the trigger from the per-bridge JSONL.** Read the
   file at `<workspace>/.agent/mcp-health-events-<bridge-id>.jsonl`.
   The last line is the most recent restart decision. `reason`
   distinguishes `PROCESS_EXITED` from `PROBE_FAILED`; `exit_status`
   (for `process_exited`) and `probe_duration_s` (for `probe_failed`)
   carry the per-decision quantitative evidence; `log_tail` carries
   the last 10 child log lines (or `null` when the log was
   unavailable at decision time).

2. **Find the matching child via the per-spawn stage file.** The
   parent sets `MCP_STAGE_ID_ENV` to a fresh UUID4 before each
   spawn (`lifecycle.py` near the `setdefault(MCP_STAGE_ID_ENV, ...)`
   call) and the child writes the latest completed startup stage to
   `<workspace>/.agent/.ralph_mcp_start_stage.<spawn_id>` (see
   `ralph.mcp.server._start_stage_tracker:stage_file_path`). The
   file is keyed by the parent's spawn identity, so two children
   on the same workspace root never share a stage file.

3. **List the stage files and inspect mtimes.** The child that was
   restarted is the one whose stage file mtime matches the
   JSONL-record decision time AND whose content is a
   `pre_ready_to_serve` label (one of `env_loaded`,
   `session_ready`, `config_loading`, `config_loaded`,
   `upstream_catalog_or_discovery_done`, `registry_built`). The
   surviving child's stage file shows `ready_to_serve` and an
   mtime from the latest successful startup.

4. **Cross-reference the bounded process-manager log.** The
   `ralph.process.manager` module writes to
   `<workspace>/.agent/tmp/mcp-server.log` with a stable
   `ralph.process.manager._process_manager_runtime:loguru_event_listener:68`
   source line. A child's exit timestamp in that log paired with a
   restart-decision time in the JSONL sink identifies the PID that
   was killed; a `PROBE_FAILED` JSONL record without a matching log
   line means the child was still alive at decision time (the
   `probe_duration_s` value is the only timing anchor).

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

The per-bridge JSONL record carries exactly five fields:
`reason`, `exit_status`, `probe_duration_s`, `log_tail`, `stage`.
The set is closed: a future change that adds a `spawn_id`, `pid`,
or `timestamp` field is an explicit test edit
(`tests/mcp/test_mcp_health_event_recorded.py:test_durable_sink_record_shape_is_closed`)
and an explicit update to the manual correlation procedure in the
section above. Schema drift is detected by the regression test, not
left to a future operator to discover.
