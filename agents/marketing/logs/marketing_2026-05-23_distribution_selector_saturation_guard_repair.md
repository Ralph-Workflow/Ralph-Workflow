# Distribution selector saturation guard repair — 2026-05-23

- **Status:** executed
- **Type:** marketing runtime repair

## What changed
- Patched `agents/marketing/distribution_lane_selector.py` so a fresh reset no longer falls straight back into `curator_handoff_packet` when same-family curator review windows are already saturated.
- Updated `agents/marketing/tests/test_marketing_system.py` to lock the new behavior in.

## Why this mattered
The audit already said to hold more same-family curator follow-through while current reply/backlink windows mature. But the selector could still reward that churn anyway. That would keep the loop looking busy without improving the odds of real Codeberg movement.

## Verification
- `python3 -m unittest agents.marketing.tests.test_marketing_system.DistributionLaneSelectorFallbackTests agents.marketing.tests.test_marketing_system.DistributionLaneSelectorTests -v` ✅
- `python3 agents/marketing/distribution_lane_selector.py` ✅ → lane now resolves to `distribution_reset`

## Expected effect
Until the saturated curator window clears, the loop should favor fresh non-overlapping target discovery instead of refreshing another curator handoff packet.
