# Agent Architecture Audit

- Checked: 2026-05-27T17:56:40.662535+02:00
- Overall health: high_risk
- Primary failure mode: Whole-stack certification remains blocked by external owner-loop residue or a failed independent signoff.
- Most urgent fix: Do not certify green until the external owner loop clears its live residue and independent signoff stays current.
- Verifier status: performed
- Verifier verdict: pass

## Live topology

- Live Gateway jobs: 25 total / 25 enabled / 0 disabled
- Live running jobs now: agent-architecture-watchdog, apollo-channel-monitor, backlink-tracker, codeberg-github-mirror-sync, marketing-daily, marketing-research-daily, ralph-docs-supervisor-precheck, ralph-site-owner-loop, reddit-pipeline-watchdog, system-health-monitor
- Live last-error residue: none
- Persisted disabled history only: docs-stack-aggressive-10min-self-heal, marketing-measurement-hold-release, marketing-reflection, ralph-workflow-full-house-docs-audit, stackoverflow-post-cooldown-run-check
- User crontab ownership: ok

## Severity-ranked findings

1. **Medium — External owner-loop residue is still live**
   - Mechanism: Live health-monitor residue is now localized to the previous marketing-workflow-audit timeout; the timeout budget has been widened, but a fresh clean run has not cleared the residue yet.
   - Recommended fix: Let the widened marketing-workflow-audit budget produce one clean rerun, then rerun the owner verification and health monitor before treating the stack as green.

2. **Medium — Live Gateway topology matches the current runtime state**
   - Mechanism: Direct live cron inspection shows 25 enabled/total-visible jobs, 0 disabled jobs, 10 running jobs, and 0 live last-error jobs.
   - Recommended fix: Keep direct cron inspection as the source of truth on each watchdog run and avoid conflating persisted disabled history with live runtime topology.

3. **Medium — Architecture verifier path is green on freshness and ownership gates**
   - Mechanism: Loop integrity, health-monitor blocker localization, and shared market-intelligence consumption remain coherent after the refresh; remaining blocker classification is externalized correctly.
   - Recommended fix: Rerun independent verification after each material architecture artifact refresh.

4. **Low — Persisted disabled jobs remain history only, not live runtime blockers**
   - Mechanism: Disabled entries still exist in jobs.json history, but live Gateway topology currently exposes zero disabled jobs.
   - Recommended fix: Keep separating persisted disabled history from live runtime topology in every audit.

## Repaired this run

- **refreshed_live_topology** — Refreshed the audit against the current live view: 25 enabled jobs, 0 disabled jobs, 10 running jobs, and 0 live last-error jobs.
- **relocalized_runtime_drift** — Removed stale topology mismatch as an architecture-owned blocker so any remaining red stays localized to the external owner loop.
- **revalidated_shared_findings_consumption** — Reconfirmed that code-backed marketing consumers still expose machine-verifiable shared market-intelligence consumption.
- **widened_marketing_audit_timeout** — Raised marketing-workflow-audit timeout to 5400s after observing a 1148669ms last runtime; live residue remains until one clean rerun clears the old timeout error.

## Still red

- marketing-workflow-audit:timeout
- timeout budget widened to 5400s; waiting for one clean rerun to clear last-error residue

## Independent verification

- Performed: yes
- Verdict: pass
- Summary: Independent verification confirms the repaired architecture verifier now fails closed on stale signoff, the live loop topology/ownership checks remain green, and shared market-intelligence reuse stays machine-verifiable.

## Small gate passed

- `python3 agents/system/agent_architecture_audit.py`
