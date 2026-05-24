# Hold-window rerun-repair dedupe
Generated: 2026-05-25T00:05:43+02:00

## Why this was the highest-leverage action now
- The active short review window is still live until 2026-05-25T02:05:05.
- The execution board still says there is no truthful do-now packet in this review window.
- This hold window had already spent slots on `active_loop_prompt_repair` and `post_hold_reentry_contract_repair`.
- Without a stricter live cron rule, future hold-window runs could keep burning slots on near-duplicate rerun/prompt tweaks instead of a different concrete repair.

## Shared findings reused
- `drafts/marketing_execution_board_latest.md` → no truthful do-now packet exists right now.
- `agents/marketing/logs/distribution_lane_latest.json` → short review-window congestion clears at `2026-05-25T02:05:05`.
- `agents/marketing/logs/marketing_2026-05-24_234934_active_loop_prompt_repair.md` → prompt-level rerun-improvement repair already shipped in this hold window.
- `agents/marketing/logs/marketing_2026-05-24_235759_post_hold_reentry_contract_repair.json` → contract-level rerun-improvement repair already shipped in this hold window.

## Repair applied
- Patched the live `marketing-active-loop` cron payload to add a new fail-closed rule:
  - if the current hold window already contains both an `active_loop_prompt_repair` and a `post_hold_reentry_contract_repair`, do not spend another slot on more rerun/prompt tweaks
  - instead, reuse the existing hold-window truth or make a different concrete runtime/process repair with code/test changes

## Verification
- `openclaw cron show 5d2cc5b0-5c6c-4ff1-8865-a39dd24af854 --json` now includes the new hold-window dedupe rule in the live payload.

## Expected marketing effect
- Preserve remaining hold-window slots for genuinely different repairs.
- Reduce fake-progress prompt churn before the scheduled 2026-05-25T02:05:05 post-hold rerun.
- Increase the odds that the next change after this one is a distinct executable lane or a concrete architecture repair, not another near-duplicate prompt tweak.
