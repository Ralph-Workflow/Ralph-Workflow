# StackOverflow overdue follow-through guard repair

- Timestamp: **2026-05-24T11:33:26+02:00**
- Action: **Patched the marketing lane selector so an overdue StackOverflow post-cooldown one-shot stops masking follow-through after a 3-minute grace window.**

## Why this was the highest-leverage move now
- The scheduled StackOverflow demand-capture job (`7a71bb58-75ac-4862-b316-ed3bdff44b0c`) was still **idle** after its scheduled fire time of **2026-05-24 11:30 CEST**.
- The current selector logic still treated that scheduled run as “current,” which could keep the loop stuck in measurement hold and hide a missed high-intent demand-capture slot.
- Another StackOverflow packet refresh would have been fake progress because the handoff packet was already current and already delivered in this review window.

## What changed
- Added `STACKOVERFLOW_POST_COOLDOWN_GRACE = 3 minutes` in `agents/marketing/distribution_lane_selector.py`.
- Changed `_stack_overflow_post_cooldown_run_current()` so a scheduled one-shot only blocks follow-through until a short grace period after its scheduled time.
- Added regression coverage in `agents/marketing/tests/test_marketing_system.py` proving that an overdue scheduled StackOverflow run no longer forces `measurement_hold`.

## Proof
- Cron state checked after the due time: job remained `idle` with scheduled time `2026-05-24T11:30:00+02:00`.
- Tests passed:
  - `python3 -m unittest agents.marketing.tests.test_marketing_system -k stackoverflow`

## Expected outcome
The next marketing loop run will no longer hide behind a missed StackOverflow one-shot; it can switch immediately to real follow-through or a different executable lane instead of burning another slot on stale scheduled-state optimism.
