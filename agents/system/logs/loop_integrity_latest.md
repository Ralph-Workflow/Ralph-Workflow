# Self-Improvement Loop Integrity Audit

Timestamp: 2026-05-25 14:59 UTC

## ralph-docs-watchdog
- Status: ok
- Checker: `DOCS_QUALITY_OK`

## autonomous-marketing-stack
- Status: error
- Notes:
  - Verifier artifact is red because the loop correctly failed closed on a live blocker; this is not, by itself, a loop-integrity contract failure.
- Repairs:
  - checker failed; executed runner for remediation
  - executed independent verifier after remediation
- Errors:
  - runner failed after checker failure
  - independent verifier failed after remediation
  - checker still failing after remediation/verifier pass
- Checker: `MARKETING_LOOP_FAIL: primary repo adoption remains flat inside a measurement-pending repair window; do not certify health yet`
- Checker after repair: `MARKETING_LOOP_FAIL: primary repo adoption remains flat inside a measurement-pending repair window; do not certify health yet`

## ralph-site-owner-loop
- Status: owner_only
- Notes:
  - Tracked owner loop for repo-boundary enforcement.
  - Independent verification is expected through Ralph-Site's own owner-loop outputs rather than this registry file.

## system-health-monitor
- Status: monitor_only
- Notes:
  - Runtime health monitor with bounded repair authority only.
  - Must not inspect content-loop outcome artifacts or trigger cross-owner remediation except explicit timeout reruns on the affected job.
  - Any material change to this monitor requires fresh independent verification.

## blocked-channel-recovery
- Status: owner_only
- Notes:
  - Generic blocked-channel ownership now lives under explicitly non-Reddit names to avoid marketing ownership leakage.
  - This loop was collapsed to one owner schedule after the old deep-review/follow-up split was found to share one runtime path and one result artifact.

## agent-architecture-watchdog
- Status: ok
- Checker: `AGENT_ARCHITECTURE_OK`

## research-findings-sync
- Status: owner_only
- Notes:
  - Owns pushing workspace research findings into the git repo with tests and sync proof.
  - Operational support loop; not a self-certifying quality authority.

## codeberg-github-mirror-sync
- Status: owner_only
- Notes:
  - Gateway-owned Codeberg-to-GitHub mirror sync. Codeberg remains source of truth.
  - Operational replication loop; ownership must stay explicit even though it is not a full verifier contract.

## user-crontab-ownership
- Status: ok
- Notes:
  - Gateway owns the active scheduler topology and no stray user-crontab entries were present.
