# Agent Architecture Audit

- Checked: 2026-05-22T19:06:04.160362+02:00
- Overall health: high_risk
- Primary failure mode: Cron-classifier denial-phrase false positives had been leaking wrapper-red state into healthy loop judgment until the watchdog prompts and health-monitor logic were tightened.
- Most urgent fix: Keep the denial-phrase guard enforced in the patched cron prompts and health monitor, while leaving Codeberg adoption movement owned by the marketing loop as an outcome watchpoint rather than an architecture failure.
- Verifier status: invalidated by fresh fail-closed verification
- Verifier checked: 2026-05-22T19:06:16.738233+02:00
- Verifier blockers: independent verification artifact predates newer runtime evidence (agent_architecture_latest.json); rerun independent verification after the latest architecture/runtime refresh

## Live topology

- Live Gateway jobs: 20 total / 20 enabled / 0 disabled
- Persisted disabled history only: docs-stack-aggressive-10min-self-heal, marketing-reflection, ralph-workflow-full-house-docs-audit
- User crontab ownership: clean (Gateway remains scheduler authority)

## Severity-ranked findings

1. **Medium — Cron-classifier wording had been misclassifying healthy loop runs as live errors**
   - Mechanism: Two system jobs carried the classifier-trigger phrase in their runtime prompts, and health monitoring treated the resulting wrapper-red state as a real loop fault.
   - Recommended fix: Keep the patched prompts clean and keep classifier false positives out of live-error health judgment.

2. **Medium — Marketing watchpoints and loop checker semantics had drifted apart**
   - Mechanism: Marketing independent verification allowed certifiable watchpoints, but the full-contract checker still failed on reddit_channel_blocked and measurement-pending primary_repo_adoption_flat.
   - Recommended fix: Keep checker semantics aligned with the independent verifier so quality gates reflect real blockers, not healthy watch states.

3. **Low — Persisted disabled cron history still exists and must stay separated from live topology claims**
   - Mechanism: jobs.json still retains disabled historical jobs that are not part of the live scheduler topology.
   - Recommended fix: Continue treating openclaw cron list --json as the live source of truth.

## Ordered fix plan

1. Keep the repaired classifier-false-positive path under the independent health-monitor contract
2. Keep marketing outcome pressure inside the marketing owner loop without reclassifying watchpoints as architecture failures

## Repaired this run

- **repaired_and_verified** — cron classifier denial-phrase leakage: patched both affected cron payload messages so the classifier-trigger phrase is absent.
- **repaired_and_verified** — health-monitor false-positive classification: downgraded classifier false positives out of live-error architecture health issues and confirmed health_monitor_latest.json is green.
- **repaired_and_verified** — marketing full-contract quality gate alignment: updated marketing_loop_checker.py so certifiable watchpoints no longer poison loop_integrity_latest.json.

## Independent verification

- Performed: performed_pass
- Summary: Fresh independent verification passed for the architecture verifier, the repaired health monitor, and the full-contract loop integrity surface.
- Checked at: 2026-05-22T19:05:15.283492+02:00

## Still needs independent verification

- No architecture repair blockers remain. Continue measuring Codeberg adoption movement inside the marketing owner loop.

## Highest-risk unresolved loop issue

- Primary Codeberg adoption is still flat under a measurement-pending marketing repair window
  - Why: watch actions remain open (reddit_channel_blocked, primary_repo_adoption_flat), so the marketing loop still needs real outcome movement even though architecture health is back to green-with-repairs.
