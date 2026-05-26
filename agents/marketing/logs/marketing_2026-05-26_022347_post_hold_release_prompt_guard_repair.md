# Post-hold release prompt guard repair

- Generated: 2026-05-26T02:23:47.811272
- Type: `post_hold_release_prompt_guard_repair`
- Scheduled run: `2026-05-26T03:05:18`
- Replacement cron job: `0274bd84-4928-4277-ab44-b735ef91b2db`
- Replaced cron job: `d45c668a-62a6-41cf-85ba-74db7acf1148`

## Why this repair shipped
- The current post-hold wake happened early while the execution board still showed no truthful do-now packet before `2026-05-26T03:05:18`.
- The release slot needed a durable prompt-level guard so an early wake self-corrects instead of pretending the post-hold slot is already open.

## What changed
- Updated `agents/marketing/distribution_lane_executor.py` so the scheduled release prompt now tells the marketer to verify that the short review window actually cleared before acting.
- If the window has not cleared, the prompt now instructs the run to treat the wake as an early-release scheduling failure, repair/reschedule it, and avoid fake progress.
- Rotated the scheduled `marketing-measurement-hold-release` cron job so the current 03:05:18 run carries the new guard text.

## Verification
- `python3 -m unittest agents.marketing.tests.test_distribution_lane_executor_measurement_hold.DistributionLaneExecutorMeasurementHoldTests.test_measurement_hold_scheduler_removes_stale_running_release_job_before_reschedule -q`
- Cron log: `/home/mistlight/.openclaw/workspace/agents/marketing/logs/marketing_2026-05-26_022316_measurement_hold_release_cron.json`
