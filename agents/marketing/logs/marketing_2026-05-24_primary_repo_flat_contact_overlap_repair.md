# Marketing Active Loop — Primary Repo Flat Contact Overlap Repair

- Timestamp: 2026-05-24T07:55:00+02:00
- Chosen action: **primary_repo_flat_contact_overlap_repair**
- Status: **executed**

## Why this was the highest-leverage action
The audit still says the bottleneck is `distribution_and_message_to_primary_repo_conversion`, but the freshest artifacts also show same-family outreach overlap and fresh publisher emails already sent to discovered targets. That made another publisher packet refresh more likely to blur measurement than move Codeberg adoption.

## What changed
- Filter recently-contacted publisher targets out of the selector's primary-repo-flat contact lane.
- Keep executor follow-through mode when every discovered publisher target is already inside its review window.
- Hide already-contacted publisher targets from the marketing execution board.
- Add regression coverage for selector + executor behavior.

## Shared findings reused
- `market_intelligence_latest.json`
- `marketing_workflow_audit_latest.json`
- `marketing_workflow_audit_latest.md`
- `reddit_monitor_latest.md`
- `reddit_post_analysis.json`
- `adoption_metrics_latest.md`
- `distribution_lane_latest.json`
- `primary_repo_flat_contact_discovery_latest.json`

## Verification
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_selector_repair_pause agents.marketing.tests.test_distribution_lane_executor_measurement_hold`
- `python3 -m py_compile agents/marketing/distribution_lane_selector.py agents/marketing/distribution_lane_executor.py`
- Runtime check: `choose_distribution_lane(2026-05-24T05:55:00) -> measurement_hold`

## Expected outcome
Cleaner measurement around already-sent publisher outreach and fewer fake-progress packet regenerations while waiting for Codeberg-primary conversion signals.
