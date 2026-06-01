# Primary-repo-flat contact truthfulness repair
Generated: 2026-05-27T01:43:00+02:00

## Why this repair ran
- Codeberg adoption is still flat in the active window.
- The execution board said there was no truthful do-now packet.
- Primary-repo-flat discovery was still capable of making a GitHub-issue-only target look packet-executable, which risked more fake packet churn.

## Repair applied
- Stopped treating `github_issue`-only publisher targets as packet-executable in `agents/marketing/distribution_lane_selector.py`.
- Kept the executor aligned so primary-repo-flat handoff packets only include targets with truthful manual/runtime-sendable channels in `agents/marketing/distribution_lane_executor.py`.
- Updated regression coverage so:
  - GitHub-issue-only targets are classified as non-executable.
  - The execution board does not surface a primary-repo-flat packet for TLDL.
  - Packet refresh selection prefers still-truthful targets like ToolChase instead of invalid GitHub-issue-only fallbacks.

## Verification
- Ran: `python3 -m unittest agents.marketing.tests.test_distribution_lane_selector_repair_pause agents.marketing.tests.test_distribution_lane_executor_measurement_hold agents.marketing.tests.test_primary_repo_flat_contact_discovery`
- Result: 171 tests passed.

## Shared findings reused
- `marketing_workflow_audit_latest.json` / `.md`: primary bottleneck remains `distribution_and_message_to_primary_repo_conversion`.
- `marketing_execution_board_latest.md`: no truthful do-now packet exists right now.
- `primary_repo_flat_contact_discovery_latest.json`: TLDL only had about/faq/X plus a GitHub issue path that is not trustworthy enough to count as executable.
- `adoption_metrics_latest.json`: Codeberg remains flat, so packet churn is failing.
