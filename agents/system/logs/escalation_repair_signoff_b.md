# Escalation Repair Signoff B

## Verdict
The repaired system does **partially** behave like a real escalation framework, but it is **not yet signoff-ready** because it still leaves escalated incidents marked `open` after resolved/blocked outcomes and the live evidence still shows unresolved externally blocked marketing verification.

## What I inspected
- `agents/system/incidents.py`
- `agents/system/health_monitor.py`
- `agents/system/agent_architecture_independent_verify.py`
- `agents/system/logs/open_incidents_latest.json`
- `agents/system/logs/health_monitor_latest.json`
- `agents/system/logs/agent_architecture_independent_verification.json`
- `agents/marketing/logs/marketing_loop_independent_verification.json`

## Findings

### 1. Incident memory: mostly present
`incidents.py` persists incidents in `open_incidents_latest.json` with:
- stable incident keys
- `first_seen` / `last_seen`
- `repeat_count`
- owner domain classification
- escalation levels (`none` -> `owner` -> `critical`)
- owner action history
- blocked metadata

The log proves repeat-memory is working: multiple incidents are at `repeat_count: 6` and are being re-escalated based on history, not just current failure snapshots.

### 2. Owner-action tracking: present
`record_owner_action()` stores timestamped action records with:
- `action_type`
- `ok`
- `detail`
- `outcome`

The incident log shows concrete owner-loop actions for architecture and marketing escalations, including enqueue/already-running details. That is real owner-action tracking, not a fake status flip.

### 3. Blocked-external classification: implemented and evidenced
`health_monitor.py` computes blockers via `escalation_blockers()` and marks outcomes as `blocked_external` when the blocker is `marketing_independent_verification`.

This is reflected in live data:
- `marketing_independent_verification::loop_verification_fail` has `blocked_by: ["marketing_independent_verification"]`
- `agent_architecture_verifier_runtime::artifact_contract_fail` is also blocked by that external marketing condition
- the architecture independent verifier explicitly treats the marketing failure as an external watchpoint rather than an architecture-owned defect

That is a meaningful blocked-external model.

### 4. Outcome-aware verification: present
`agent_architecture_independent_verify.py` is outcome-aware:
- it does not blindly pass on activity
- it checks live artifacts, freshness, topology, docs stability, consumer proof, and marketing verification verdict
- it fails closed when the marketing independent verification remains `fail`

The current verification artifact correctly reports:
- architecture-related repair logic verified
- remaining blockers still prevent a healthy verdict

That is the right behavior for a real escalation framework.

## Critical flaw preventing signoff
`record_owner_action()` can set incident status to `resolved` or `blocked_external`, but the next `upsert_incidents()` call in `incidents.py` unconditionally resets any seen incident to:
- `status = 'open'`

This is visible in `open_incidents_latest.json`:
- `agent_architecture_verifier` has owner actions with `outcome: "resolved"` and even a `closed_at`, but current `status` is still `"open"`
- blocked incidents also show `status: "open"` despite `blocked_by` being populated and actions recording `blocked_external`

So the framework remembers outcomes, but it does **not preserve** them reliably for active repeated incidents. That breaks the state model expected from a true escalation system.

## Conclusion
The repair substantially improved the system and added the right primitives:
- incident memory
- owner-action tracking
- blocked-external classification
- outcome-aware verification

But the incident state machine is still wrong because repeated sightings overwrite `resolved` / `blocked_external` back to `open`. Live evidence also still contains unresolved marketing verification failure, so the system is not yet in a healthy end state.

SIGNOFF: FAIL
Reasons:
1. `incidents.py` overwrites previously recorded `resolved` and `blocked_external` outcomes by forcing seen incidents back to `status='open'` during `upsert_incidents()`.
2. `open_incidents_latest.json` shows contradictory state (`closed_at` or blocked evidence present while status remains `open`), proving the state model is not reliable yet.
3. `marketing_loop_independent_verification.json` still has `verdict: "fail"`, and that external blocker continues to keep the overall escalation chain from reaching a clean healthy outcome.
4. `agent_architecture_independent_verification.json` still returns `verdict: "fail"`, so end-to-end verification is correctly not yet passing.
