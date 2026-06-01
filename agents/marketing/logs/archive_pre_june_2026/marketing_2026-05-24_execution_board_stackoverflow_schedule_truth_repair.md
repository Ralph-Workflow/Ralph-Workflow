# StackOverflow schedule-truth repair

Generated: 2026-05-24T21:01:00+02:00

## Why this repair shipped
- The measurement-hold execution board was only recognizing legacy `stack_overflow_lane_repair` logs for scheduled StackOverflow retries.
- The live runtime currently writes `stack_overflow_demand_capture_cron` with `verification.scheduled_run_at`, so the board and hold follow-through could miss an already-scheduled retry and keep resurfacing the same handoff packet.
- That violates the current loop rule: if a post-cooldown run is already scheduled, do not spend the slot re-delivering the same StackOverflow packet.

## Repair applied
- Patched `agents/marketing/distribution_lane_executor.py` so `_current_stackoverflow_scheduled_run()` recognizes:
  - `stack_overflow_lane_repair`
  - `stackoverflow_post_cooldown_cron`
  - `stack_overflow_demand_capture_cron`
- The schedule probe now checks `review_window.scheduled_run_at`, `verification.scheduled_run_at`, and top-level `scheduled_run_at`.
- Measurement-hold follow-through now suppresses the “Best human-executable demand-capture asset still waiting” section when a post-cooldown StackOverflow run is already scheduled, and records the scheduled retry instead.
- If the post-cooldown slot is already exhausted, the hold artifact now explicitly keeps the StackOverflow packet retired for the current review window.

## Verification
- Ran: `python3 -m unittest agents.marketing.tests.test_distribution_lane_executor_measurement_hold`
- Result: `OK` (20 tests)

## Shared findings reused
- `agents/marketing/logs/distribution_lane_latest.json`
- `drafts/marketing_execution_board_latest.md`
- `agents/marketing/logs/marketing_2026-05-24_stackoverflow_post_cooldown_cron.json`
- `agents/marketing/logs/reddit_execution_status_latest.json`
- `agents/marketing/logs/adoption_metrics_latest.json`

## Outcome
- The marketing loop now tells the truth about scheduled StackOverflow follow-through during hold windows.
- That reduces fake-progress packet resurfacing and keeps the next slot available for a genuinely different Codeberg-first action.
