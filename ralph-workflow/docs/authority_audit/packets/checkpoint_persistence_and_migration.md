# checkpoint_persistence_and_migration

## Authoritative owner
`ralph.pipeline.checkpoint.Checkpoint` (and the underlying
`ralph.checkpoint.checkpoint_payload.CheckpointPayload`) is the single
authority for durable checkpoint persistence and the migration of legacy
checkpoint data (`pipeline/checkpoint.py:56`,
`checkpoint/checkpoint_payload.py:13`).

## Writers
- `ralph.pipeline.checkpoint.Checkpoint.save` writes the durable
  `CheckpointPayload` via `CheckpointBuilder`
  (`pipeline/checkpoint.py:77`, `checkpoint/builder.py:83`).
- `ralph.pipeline.auto_integrate_record._migrate_payload_evidence`
  migrates legacy on-disk evidence keys to the new key space and
  routes the legacy global-evidence case through the closed
  `RAISE_DIAGNOSTIC_INTERVENTION` transition
  (`auto_integrate_record.py:609`).
- `ralph.pipeline._coerce_payload_failure_cause` migrates legacy
  `last_failure_cause` to the typed `ResolutionFailureCause` enum
  (`auto_integrate_record.py:598`).

## Readers
- `ralph.pipeline.run_loop` reads the latest `CheckpointPayload` on
  restart to rebuild `PipelineState` from the authority.
- `ralph.pipeline._integration_diagnostic` reads the integration
  record's `diagnostic_evidence` tuple to drive the diagnostic
  path.

## Derived projections
- `CheckpointPayload.to_dict()` is the one-way serialized
  projection; on restart, `from_dict()` rebuilds the authority
  from the serialized form
  (`checkpoint/checkpoint_payload.py:26,37`).

## Valid transitions
- Checkpoint persistence is a single-actor mutation; the
  integration record's transitions are the closed set
  (`auto_integrate_transition.py:261`).

## Restart behaviour
On restart, the run loop reads the latest checkpoint, re-reads the
durable integration record via the typed `read_record_outcome`, and
rebuilds `PipelineState` from those two sources. Newest-timestamp
merging of partial histories is forbidden: the durable record is
the authority, the checkpoint is a snapshot.

## UNKNOWN representation
- `read_record_outcome` returns `UNREADABLE` for corrupt bytes;
  `parse_record_payload` returns `None` for any unsupported schema
  or phase value
  (`auto_integrate_record.py:648`).
- `RebaseState` legacy validation migrates unknown legacy
  `last_action` verbs to `RebaseOutcomeKind.UNKNOWN` and refuses
  contradictory legacy data (`rebase_state.py:94-181`).

## What blocks dispatch / terminal success
- A corrupt or unreadable integration record blocks dispatch via
  the `UNREADABLE` branch of the recovery boundary
  (`auto_integrate_recovery.py:608-627`).
- `parse_record_payload` returns `None` for unknown phase or
  missing required fields; the recovery boundary treats that as
  `UNREADABLE` and surfaces `DIAGNOSTIC_INTERVENTION_REQUIRED`.
