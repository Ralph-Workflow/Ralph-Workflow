# Agent Architecture Audit

- Checked: 2026-05-23T06:03:16.645471+02:00
- Overall health: healthy_with_repairs
- Primary failure mode: Architecture ownership, live topology checks, and fail-closed verification are healthy; end-to-end green remains blocked by a marketing-owned Codeberg adoption measurement window.
- Most urgent fix: Hold architecture at qualified pass while marketing waits out or replaces the current Codeberg-primary distribution tactic if the active review window stays flat.
- Verifier status: independently verified pass
- Verifier checked: 2026-05-23T06:03:28.172957+02:00
- Verifier blockers: none

## Live topology

- Live Gateway jobs: 20 total / 20 enabled / 0 disabled
- Persisted disabled history only: docs-stack-aggressive-10min-self-heal, marketing-reflection, ralph-workflow-full-house-docs-audit
- User crontab ownership: clean (Gateway remains scheduler authority)

## Severity-ranked findings

1. **High — Marketing adoption window remains the only live blocker to full green**
   - Mechanism: Marketing runner execution recovered, but marketing independent verification still fails closed while Codeberg adoption stays flat inside the active measurement window.
   - Recommended fix: Do not certify around the watchpoint; wait for measurable Codeberg movement or a marketing-owned tactic replacement at the review window.

2. **Medium — Repaired legacy marketing log-shape drift**
   - Mechanism: Legacy marketing action logs stored `channel` as a string, which broke selector/audit normalization and created a false local runner-bundle blocker.
   - Recommended fix: Keep legacy-log normalization tolerant of both dict and string channel shapes.

3. **Low — Persisted disabled cron history still exists but is not a live-topology problem**
   - Mechanism: jobs.json contains disabled historical entries while the live Gateway topology has zero disabled jobs.
   - Recommended fix: Keep separating persisted disabled history from live enabled runtime jobs in all architecture summaries.

## Ordered fix plan

1. Keep architecture signoff tied to the external marketing blocker and not to derivative runner/verifier redness inside that owner loop
2. Resolve the marketing measurement window with measurable primary-repo movement or tactic replacement

## Repaired this run

- **repaired_marketing_log_shape_handling** — Patched `distribution_lane_selector.py` and `marketing_workflow_audit.py` to accept legacy string-valued `channel` payloads.
- **restored_marketing_runner_operational_health** — Reran the marketing runner/audit bundle; operational health is green again and only outcome certification remains red.
- **reran_architecture_stack** — Refreshed `loop_integrity_audit.py`, `health_monitor.py`, and architecture verifier inputs against the live Gateway topology.

## Independent verification

- Performed: performed_qualified_pass
- Summary: Independent verification confirms architecture-owned checks are green and the remaining blocker is marketing-owned.
- Checked at: 2026-05-23T06:03:28.172957+02:00

## Still needs independent verification

- Fresh marketing independent pass after primary-repo adoption moves or the current tactic is replaced at the end of the active measurement window.

## Highest-risk unresolved loop issue

- Primary Codeberg adoption is still flat under the active marketing measurement window
  - Why: That marketing-owned outcome gap is still the only blocker preventing a fully green end-to-end certification.

## Small gate passed

- AGENT_ARCHITECTURE_OK
