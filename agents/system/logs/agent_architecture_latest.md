# Agent Architecture Watchdog — Latest Report

**Checked at:** 2026-05-30T11:37+02:00  
**Overall health:** external_risk

## Verdict

Architecture-owned path is **green**. Whole-stack certification remains blocked by external marketing outcome evidence.

## Live Topology Snapshot

| Metric | Value |
|--------|-------|
| Live jobs | 24 |
| Enabled | 24 |
| Disabled (live) | 0 |
| Running | 7 |
| Last-error jobs | 2 (both external) |

**Last-error details:**
- `marketing-workflow-audit`: Context overflow (external)
- `blocked-channel-recovery`: Execution timed out (external/unblocker)

## Architecture Verifier

- Verifier status: performed
- Verifier verdict: qualified_pass
- Architecture errors: 0
- Independent verification: performed, qualified_pass

## Repairs Applied This Run

1. **Refreshed live topology** — Direct `openclaw cron list --json` re-inspection: 24 enabled, 0 disabled, 7 running, 2 external-only last-error jobs.
2. **Revalidated architecture verifier chain** — `agent_architecture_verifier.py` reports 0 architecture errors; `agent_architecture_independent_verify.py` confirms qualified_pass.
3. **Relocalized all blockers as external** — Confirmed every live error, timeout, and stale-artifact issue is external-domain (marketing, unblocker). No architecture-owned blockers.

## Independent Verification

- Performed: yes
- Verdict: qualified_pass
- Summary: Architecture verifier reports 0 errors; independent verification confirms qualified_pass. All remaining blockers are external.

## What's Still Red

- **Marketing independent verification** — Still fail. Primary-repo outcome evidence missing.
- **blocked-channel-recovery** — Timeout (366 repeats, critical escalation).
- **marketing-workflow-audit** — Context overflow (102 repeats, critical escalation).

## Highest-Risk Unresolved Issue

Marketing remains red on Codeberg-primary outcome evidence. Architecture-owned runtime checks are coherent, but marketing independent verification still fails closed because primary-repo movement is measurement-pending.

## Small Gate

✅ Architecture-owned path: green (0 architecture errors, independent verification passed)  
⚠️ Whole-stack: externally blocked (marketing outcome evidence pending)
