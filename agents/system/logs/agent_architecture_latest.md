# Agent Architecture Audit Report

**Checked:** 2026-06-05 19:03 CEST (17:03 UTC)
**Verdict:** `qualified_pass` — architecture-owned gates green; marketing external evidence red
**Schema:** ecc.agent-architecture-audit.report.v1

## Executive Summary

Architecture chain fully coherent: checker, verifier, independent verification, and loop integrity all pass fresh. Stable run — no new drift or repairs needed since prior pass. Sole remaining blocker is external marketing outcome evidence — ~75.8h stale, verdict `fail`, primary-repo adoption flat at 12 Codeberg stars.

## Runtime Topology (Live)

| Metric | Value |
|--------|-------|
| Total jobs | 19 |
| Enabled | 19 |
| Disabled | 0 |
| Running | 2 (system-health-monitor, codeberg-github-mirror-sync) |
| Errored | 0 |

All 19 jobs have `consecutiveErrors=0`.

## Chain Results

| Check | Status | Exit |
|-------|--------|------|
| agent_architecture_checker.py | `AGENT_ARCHITECTURE_OK` | 0 |
| agent_architecture_verifier.py | `ok=true, 0 errors` | 0 |
| agent_architecture_independent_verify.py | `ok=true, qualified_pass=true` | 0 |
| loop_integrity_audit.py | both loops ok | 0 |

## Findings

### 🔴 HIGH — Marketing independent verification red (~75.8h stale)
- Artifact: 2026-06-02, verdict: `fail`
- Primary repo flat: 12 Codeberg stars, 0 delta
- Bottleneck: `distribution_and_message_to_primary_repo_conversion`
- Fix: Marketing owner loop produces fresh measurable outcome evidence

### 🔴 HIGH — pypi-auto-unblocker lacks self-improvement mandate
- No self-improvement contract; cannot self-correct flat outcomes
- Fix: Add mandate or reclassify as monitor-only

### 🟡 MEDIUM — Live topology coherent
- 19/19/0/2/0, all clean, all consecutiveErrors=0

### 🟡 MEDIUM — Verifier chain fully green
- Fresh checker/verifier/independent/loop-integrity all pass

### 🟢 LOW — 17 loops unregistered in self_improvement_loops.json
- Only 2 of 19 loops have registry entries
- Fix: Onboard or document classification decisions

### 🟢 RESOLVED — internal-linking-watchdog
- Removed from live topology

## Repairs Applied This Run

None — architecture chain stable, no new repairs required.

## Independent Verification

- **Status:** performed
- **Verdict:** `qualified_pass`
- **External errors only:** marketing evidence ~75.8h stale, verdict `fail`

## What Still Needs Independent Verification

1. Fresh marketing independent pass with measurable primary-repo movement
2. pypi-auto-unblocker self-improvement mandate onboarding

## Notes

- Architecture green ≠ whole stack green
- 0 disabled jobs in live topology; persisted history separated
- PyPI 1,294 downloads/month is real usage not captured by repo star count
- 17 of 19 loops unregistered in self_improvement_loops.json
- Marketing workflow audit current but marketing IV is ~75.8h stale
- Stable run: chain unchanged from prior pass ~18 min ago
