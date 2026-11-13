# MCP restart evidence: operator playbook

The runtime contracts are implemented in
`ralph-workflow/ralph/mcp/server/_mcp_health_event.py`,
`ralph-workflow/ralph/mcp/server/lifecycle.py`, and
`ralph-workflow/ralph/mcp/server/_start_stage_tracker.py`.

## Durable health-event stream

Each bridge writes health events to
`<workspace>/.agent/mcp-health-events-<bridge-id>.jsonl`. The bridge identifier
is a 12-character UUID4 hex prefix assigned when `lifecycle.py` calls
`configure_bridge_sink`. A new bridge receives a new sink path.

Restart records written by `record_restart_decision` contain five fields:
`reason`, `exit_status`, `probe_duration_s`, `log_tail`, and `stage`.

- `reason="process_exited"` means the child process exited. `exit_status`
  carries the process exit code, and `log_tail` contains up to ten child log
  lines when available.
- `reason="probe_failed"` means the child remained alive but missed
  `_PROBE_FAILURES_BEFORE_RESTART = 3` consecutive probes.
  `probe_duration_s` carries the failed probe duration.

Contention deferrals written by `lifecycle.py:_record_contention_deferral`
contain six fields: `reason`, `exit_status`, `probe_duration_s`,
`deferral_count`, `log_tail`, and `stage`. Their `reason` and `stage` are
`"probe_deferred_contention"`. A contention deferral does not restart the
child.

## Triage procedure

1. Read the complete per-bridge JSONL stream. A trailing run of
   `probe_deferred_contention` records represents deferred probe failures, not
   restarts. The latest record whose `reason` is `process_exited` or
   `probe_failed` identifies the latest restart trigger.
2. Inspect `exit_status` for `process_exited`, or `probe_duration_s` for
   `probe_failed`. Inspect `log_tail` for captured child output.
3. Inspect the per-spawn stage files at
   `<workspace>/.agent/.ralph_mcp_start_stage.<spawn_id>`. The parent assigns a
   fresh UUID4 through `MCP_STAGE_ID_ENV` before each spawn, and
   `stage_file_path` keeps child startup progress separate by spawn.
4. Compare stage-file mtimes with process-manager log entries in
   `<workspace>/.agent/tmp/mcp-server.log`. The JSONL records carry no
   `spawn_id`, `pid`, or timestamp, so attribution uses the retained stage-file
   and log evidence.

The startup labels in `_start_stage_tracker.RUNTIME_STAGES` are `env_loaded`,
`session_ready`, `config_loading`, `config_loaded`,
`upstream_catalog_or_discovery_done`, `registry_built`, and `ready_to_serve`.
A pre-ready label can identify a child that failed during startup. Children
that reached `ready_to_serve` cannot be distinguished from stage files alone.

## Attribution limits

The process-manager exit log carries no bridge identifier or spawn identity.
When multiple exits occur in the same interval, a specific PID cannot be
attributed to a bridge spawn from that log alone. A `probe_failed` record
without a concurrent process-exit log means the child was alive when the
restart path began.
