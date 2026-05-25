# NxCode runtime-sendable repair
Generated: 2026-05-25T07:17:24.570130+02:00

## Why this won
- The short review window is still active, so another live outbound action would blur measurement.
- The execution board still had NxCode trapped as a non-runtime-sendable blocker.
- Official NxCode docs expose support@nxcode.io, which is better than leaving the target as website-only/manual-only.

## Repair applied
- Patched `agents/marketing/primary_repo_flat_contact_discovery.py` to crawl `https://www.nxcode.io/docs/troubleshooting` for NxCode contact discovery.
- Updated regression coverage in `agents/marketing/tests/test_primary_repo_flat_contact_discovery.py`.
- Refreshed `primary_repo_flat_contact_discovery_latest.json`, `primary_repo_flat_contact_handoff_packet_latest.md`, and `marketing_execution_board_latest.md`.

## Verification
- `python3 -m unittest agents.marketing.tests.test_primary_repo_flat_contact_discovery agents.marketing.tests.test_distribution_lane_executor_contact_suggestion -q` → OK
- Refreshed discovery now includes `support@nxcode.io` for NxCode.
- Execution board now lists only `ctxt.dev / Signum` as still non-runtime-sendable.

## Expected effect
- The post-hold rerun now has another truthful email-backed publisher target for Codeberg-first outreach.
- If that rerun still cannot produce a real lane after the hold clears, it must escalate again instead of idling.
