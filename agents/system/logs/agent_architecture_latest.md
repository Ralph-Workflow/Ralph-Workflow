# Agent Architecture Watchdog Report

**Checked:** 2026-06-04T14:52+02:00  
**Verdict:** `external_risk`  
**Independent verification:** qualified_pass

## Executive Summary

Architecture-owned gates are all green. The only remaining blocker for whole-stack green is external: marketing independent verification is ~47.7h stale with verdict=fail. Fresh primary-repo adoption evidence remains measurement-pending. Additionally, self-repair audit reports 2 HIGH findings requiring routing.

## Live Topology

- 22 total, 22 enabled, 0 disabled
- 3 running: ralph-docs-supervisor-precheck, marketing-measurement-hold-release, agent-architecture-watchdog (this run)
- 3 transient errors (all external domains, consecutiveErrors=1): backlink-tracker (gateway restart interrupt), blocked-channel-recovery, internal-linking-watchdog
- 18 persisted disabled history entries in jobs.json (12x duplicate at-job artifacts + 6 stale); none live

## Checker → Verifier → Independent Gate

| Gate | Result |
|------|--------|
| checker (agent_architecture_checker.py) | AGENT_ARCHITECTURE_OK |
| verifier (agent_architecture_verifier.py) | ok:true, errors:[] |
| independent verification (agent_architecture_independent_verify.py) | qualified_pass |

## Repairs This Run

None needed. Prior-run repairs (checker-schema gap, verifier allowed-set extension) hold. All pipeline gates pass fresh.

## Still Red

- Marketing independent verification: fail, ~47.7h stale (artifact: marketing_loop_independent_verification.json)
- Self-repair audit: 2 HIGH findings across 22 audited loops
- 3 transient external-domain errors: each consecutiveErrors=1, not escalating

## Independent Verification Status

Performed and passing (qualified_pass). All architecture-owned claims verified. External marketing blocker and self-repair HIGH findings classified as watchpoints.

## Next

1. Marketing owner loop produces fresh measurable outcome evidence (marketing-measurement-hold-release at-job currently running)
2. Rerun marketing independent verification
3. Route self-repair audit's 2 HIGH findings to appropriate owner loops
4. Architecture watchdog re-runs full cycle after marketing green
