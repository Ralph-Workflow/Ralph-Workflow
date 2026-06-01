# Owned-content first-task reentry repair

- Timestamp: 2026-05-27T12:54:29+02:00
- Action: `owned_content_first_task_reentry_repair`
- Status: `executed`

## Why this was the highest-leverage move
- The short review-window hold is still active until **2026-05-27T14:26:29**.
- Another live outbound action right now would mostly blur measurement.
- The current bottleneck is still **conversion to free use**.
- The next strongest move was to make the next truthful owned-content slot publish the **first-task guide** instead of stalling once the already-posted Telegraph guides are exhausted.

## What changed
- Added `docs/first-task-guide.md` to the owned-content source candidates.
- Put it directly after `good_unattended_task.md` in priority order.
- Added regression coverage so the owned-content lane keeps that order.

## Shared findings reused
- `ADOPTION_FUNNEL_NEXT.md`
- `marketing_workflow_audit_latest.json`
- `distribution_lane_latest.json`
- `drafts/marketing_execution_board_latest.md`

## Verification
- `python3 -m unittest agents.marketing.tests.test_owned_content_priority -q` → passed
- `docs/first-task-guide.md` parses as a Telegraph-ready source with body length **3404**

## Expected outcome
When the next truthful owned-content slot opens, the lane can publish a Codeberg-first first-task guide instead of nooping after the recent proof posts.
