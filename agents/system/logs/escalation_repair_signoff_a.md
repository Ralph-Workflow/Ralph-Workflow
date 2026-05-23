# Escalation repair signoff A

## Scope
Reviewed:
- `agents/system/incidents.py`
- `agents/system/health_monitor.py`
- `agents/system/agent_architecture_independent_verify.py`
- `agents/system/logs/open_incidents_latest.json`
- `agents/system/logs/health_monitor_latest.json`
- `agents/system/logs/agent_architecture_independent_verification.json`
- `agents/marketing/logs/marketing_loop_independent_verification.json`

## Findings
1. **Local owner failure vs external blocker is now explicitly distinguished.**
   - `record_owner_action()` stores `blocked_by` and flips incident status to `blocked_external` when an owner action succeeds but progress is blocked by another domain.
   - `health_monitor.py` computes blockers via `escalation_blockers()` and passes them into escalation outcomes.
   - Evidence: `open_incidents_latest.json` shows `marketing_independent_verification` and `agent_architecture_verifier_runtime` carrying `blocked_by=["marketing_independent_verification"]`, while `agent_architecture_verifier` remains an architecture-owned incident with no blocker.

2. **Escalation is now more than a label.**
   - `incident_escalations()` emits concrete escalation work items once repeat thresholds are hit.
   - `apply_safe_repairs()` routes those escalations to owner jobs using `owner_job_for_issue()`, records owner actions, and optionally performs direct verifier reruns.
   - Evidence: `health_monitor_latest.json` shows actual `repairs_attempted` entries for `owner_loop_escalation`, `rerun_independent_architecture_verification`, and `rerun_architecture_verifier`, not just tagged incidents.

3. **Architecture-side behavior improved, but signoff is still blocked.**
   - `agent_architecture_independent_verification.json` confirms the repaired architecture verifier now fails closed on stale evidence and isolates marketing as an external watchpoint rather than misclassifying it as an architecture-owned defect.
   - However the same artifact still returns `verdict: fail` with remaining blockers:
     - `architecture report overall health is not healthy: 'high_risk'`
     - `marketing independent verification is not pass: 'fail'`
   - `agent_architecture_latest.json` still says fresh marketing independent pass is required before healthy signoff.

## Assessment
The escalation architecture is **materially repaired**:
- it distinguishes owner-owned failures from external blockers,
- it performs real escalation actions,
- and it preserves the external marketing blocker as a blocker instead of laundering it into a fake architecture success.

But the overall system is **not yet signoff-ready** because the architecture verifier and architecture report are intentionally still fail-closed on unresolved marketing evidence. That is the correct behavior, but it still prevents final signoff.

## Exact reasons signoff is blocked
1. `agents/system/logs/agent_architecture_independent_verification.json` has `verdict: fail`.
2. Its recorded blockers are exactly:
   - `architecture report overall health is not healthy: 'high_risk'`
   - `marketing independent verification is not pass: 'fail'`
3. `agents/system/logs/health_monitor_latest.json` still reports live issues for:
   - `marketing_independent_verification::loop_verification_fail`
   - derived escalations tied to that blocker.

SIGNOFF: FAIL
