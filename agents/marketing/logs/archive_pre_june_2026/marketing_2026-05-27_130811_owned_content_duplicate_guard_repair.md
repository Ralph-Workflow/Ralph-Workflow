# Owned-content duplicate guard repair

- Timestamp: 2026-05-27T13:08:11+02:00
- Status: executed
- Outcome-ready: yes
- Live external action: no

## Why this run happened
The current short-window hold still points at the owned-content lane until **2026-05-27T14:26:29**, but the execution board says there is **no truthful do-now packet** in the active review window.

That made the post-hold rerun quality the real leverage point. After `docs/first-task-guide.md` was re-added to owned-content priority, the publisher could have re-posted an already-shipped conversion asset under a new dated draft name if the body changed enough.

## Shared findings reused
- `agents/marketing/logs/distribution_lane_latest.json`
- `drafts/marketing_execution_board_latest.md`
- `agents/marketing/logs/marketing_2026-05-27_125429_owned_content_first_task_reentry_repair.md`
- `agents/marketing/logs/posted_urls.json`

## What changed
- Extended Telegraph duplicate detection to match **experiment_id** and **source_path**, not just body hash or generated draft name.
- Wired the owned-content executor to pass both fields into the duplicate guard.
- Added front matter to `docs/first-task-guide.md` so it maps back to the historical first-task guide publish record from **2026-05-22**.
- Added regression tests for helper-level dedupe and executor-level skip behavior.

## Files changed
- `agents/marketing/run_posting.py`
- `agents/marketing/distribution_lane_executor.py`
- `docs/first-task-guide.md`
- `agents/marketing/tests/test_marketing_system.py`

## Verification
```bash
python3 -m unittest \
  agents.marketing.tests.test_marketing_system.PostingTests.test_already_posted_successfully_matches_hash \
  agents.marketing.tests.test_marketing_system.PostingTests.test_already_posted_successfully_matches_experiment_id_or_source_path \
  agents.marketing.tests.test_marketing_system.PostingTests.test_execute_owned_content_skips_historic_first_task_guide_repost -v
```

Result: **passed**

## Expected post-hold effect
When the scheduled rerun fires after **2026-05-27T14:26:29**, it can no longer claim a recycled first-task-guide Telegraph repost as fresh owned-content progress. That forces the lane toward a truly unpublished source or another concrete repair instead of fake activity.
