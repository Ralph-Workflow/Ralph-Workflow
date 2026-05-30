# Agent Architecture Audit

- Checked: 2026-05-30T21:00:31.768047+02:00
- Overall health: high_risk
- Primary failure mode: Architecture-owned health issues are live; do not certify green until they are diagnosed and repaired.
- Most urgent fix: Diagnose and repair the architecture-owned health issues before issuing a healthy certification artifact.
- Verifier status: performed
- Verifier verdict: fail

## Live topology

- Live Gateway jobs: 26 total / 26 enabled / 0 disabled
- Live running jobs now: agent-architecture-watchdog, apollo-channel-monitor, codeberg-github-mirror-sync, ralph-docs-supervisor-precheck, ralph-site-owner-loop, reddit-pipeline-watchdog, repo-adoption-tracker, system-health-monitor
- Live last-error residue: Push research findings to git repo (context overflow), blocked-channel-recovery (timeout)
- Persisted disabled history only: docs-stack-aggressive-10min-self-heal, marketing-measurement-hold-release (x9), marketing-reflection, ralph-workflow-full-house-docs-audit, stackoverflow-post-cooldown-run-check
- User crontab ownership: ok

## Severity-ranked findings

1. **High — Marketing remains externally red on outcome evidence**
   - Mechanism: Marketing independent verification still fails closed because primary-repo adoption is measurement-pending.
   - Recommended fix: Let the marketing owner loop produce fresh measurable outcome evidence, then rerun marketing independent verification before calling the whole stack green.

2. **Medium — Live Gateway topology matches the current runtime state**
   - Mechanism: Direct live cron inspection shows 26 enabled/total-visible jobs, 0 disabled jobs, 8 running jobs, and 2 live last-error jobs.
   - Recommended fix: Keep direct cron inspection as the source of truth on each watchdog run.

3. **Medium — Architecture verifier path is green on freshness and ownership gates**
   - Mechanism: Loop integrity, health-monitor blocker localization, and shared market-intelligence consumption remain coherent; remaining blocker classification is externalized correctly.
   - Recommended fix: Rerun independent verification after each material architecture artifact refresh.

4. **Low — Persisted disabled jobs remain history only, not live runtime blockers**
   - Mechanism: Live Gateway topology currently exposes zero disabled jobs.
   - Recommended fix: Keep separating persisted disabled history from live runtime topology in every audit.

## Repaired this run

- **refreshed_live_topology** — Refreshed the audit against the current live view: 26 enabled jobs, 0 disabled jobs, 8 running jobs, and 2 live last-error jobs.
- **relocalized_runtime_drift** — Removed stale topology mismatch as an architecture-owned blocker so any remaining red stays localized to the external owner loop.
- **revalidated_shared_findings_consumption** — Reconfirmed that code-backed marketing consumers still expose machine-verifiable shared market-intelligence consumption.

## Still red

- Marketing independent verification is not pass.
- Primary repo adoption remains measurement-pending after shipped repairs.
- Push research findings to git repo has live_error (context overflow).
- blocked-channel-recovery has live_error (timeout, 496 consecutive).
- Architecture independent verification returns fail.

## Independent verification

- Performed: yes
- Verdict: fail
- Summary: Independent verification found architecture blockers that prevent a healthy verdict. Architecture errors: health monitor non-architecture issues (Push research findings to git repo:live_error). External blockers: stale marketing independent verification, marketing independent verification not pass.

## Small gate passed

- `python3 agents/system/agent_architecture_audit.py`
