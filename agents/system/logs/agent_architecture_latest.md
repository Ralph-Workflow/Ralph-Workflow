# Agent Architecture Audit

- Checked: 2026-05-23T05:00:20.344256+02:00
- Overall health: healthy_with_repairs
- Primary failure mode: Architecture ownership, freshness checks, and verifier fail-closed behavior are healthy; full end-to-end green remains blocked only by the marketing primary-repo adoption watchpoint.
- Most urgent fix: Keep architecture on qualified-pass status until marketing clears the primary-repo adoption measurement window or replaces the tactic with one that moves Codeberg.
- Verifier status: independently verified pass
- Verifier checked: 2026-05-23T05:01:25.095637+02:00
- Verifier blockers: none

## Live topology

- Live Gateway jobs: 20 total / 20 enabled / 0 disabled
- Persisted disabled history only: docs-stack-aggressive-10min-self-heal, marketing-reflection, ralph-workflow-full-house-docs-audit
- User crontab ownership: clean (Gateway remains scheduler authority)

## Severity-ranked findings

1. **High — Marketing adoption watchpoint remains the only real live blocker**
   - Mechanism: The architecture path is locally healthy, while the marketing loop is still intentionally fail-closed on flat primary-repo adoption inside its active measurement window.
   - Recommended fix: Do not certify around the watchpoint; wait for measurable Codeberg movement or a marketing-owned tactic replacement at the review window.

2. **Medium — Architecture verifier correctly localizes the live blocker outside the architecture owner loop**
   - Mechanism: Fresh health-monitor follow-up items from the marketing loop no longer poison architecture signoff during the same refresh cycle.
   - Recommended fix: Preserve that owner-boundary classification.

3. **Low — Persisted disabled cron history still exists but is not a live-topology problem**
   - Mechanism: jobs.json contains disabled historical entries while the live Gateway topology has zero disabled jobs.
   - Recommended fix: Keep separating persisted disabled history from live enabled runtime jobs in all architecture summaries.

## Ordered fix plan

1. Keep architecture signoff tied to the external marketing blocker and not to derivative runner/verifier redness inside that owner loop.
2. Resolve the marketing measurement window with measurable primary-repo movement or tactic replacement.

## Repaired this run

- **reran_current_stack** — Reran loop_integrity_audit.py, then reran agent_architecture_independent_verify.py, agent_architecture_verifier.py, and agent_architecture_checker.py against the current live Gateway topology.
- **resynced_live_assertions** — Resynced the latest architecture audit with current live cron counts, current marketing measurement-window evidence, and fresh verifier timestamps after independent verification passed.

## Independent verification

- Performed: performed_qualified_pass
- Summary: Independent verification confirms the repaired architecture verifier now fails closed on stale signoff, the live loop topology/ownership checks remain green, and shared market-intelligence reuse stays machine-verifiable.
- Checked at: 2026-05-23T05:01:01.390263+02:00

## Still needs independent verification

- Fresh marketing independent pass after primary-repo adoption moves or the current tactic is replaced at the end of the active measurement window.

## Highest-risk unresolved loop issue

- Primary Codeberg adoption is still flat under the active marketing measurement window
  - Why: that marketing-owned outcome gap is still the only blocker preventing a fully green end-to-end certification.

## Small gate passed

- AGENT_ARCHITECTURE_OK
