# Agent Architecture Audit

- Checked: 2026-06-04T14:03:18.960615+02:00
- Overall health: high_risk
- Primary failure mode: Whole-stack certification blocked by external owner-loop residue (marketing independent verification fails closed).
- Architecture-owned gates: green
- Verifier verdict: qualified_pass

## Live topology

- Live Gateway jobs: 22 total / 22 enabled / 0 disabled
- Running: agent-architecture-watchdog, codeberg-github-mirror-sync, system-health-monitor
- Last-error residue: backlink-tracker, blocked-channel-recovery, internal-linking-watchdog

## Repaired this run

- **refreshed_live_topology** — Refreshed audit against current live view: 22 enabled, 0 disabled, 3 running, 3 last-error.
- **relocalized_runtime_drift** — Removed stale topology mismatch; remaining red stays localized to external owner loop.
- **revalidated_shared_findings_consumption** — Code-backed marketing consumers still expose machine-verifiable shared market-intelligence consumption.

## Still red

- Marketing independent verification → fail (Codeberg-primary adoption evidence still measurement-pending).
- Two loops lack self-improvement mandate: pypi-auto-unblocker, internal-linking-watchdog.

## Independent verification

- Performed: yes
- Verdict: qualified_pass
- Architecture-owned errors: 0
- External blockers: marketing_independent_verification:stale_artifact, verdict=fail

## Small gate passed

- `python3 agents/system/agent_architecture_audit.py` → ok
- `python3 agents/system/agent_architecture_independent_verify.py` → qualified_pass
- `python3 agents/system/agent_architecture_verifier.py` → ok
