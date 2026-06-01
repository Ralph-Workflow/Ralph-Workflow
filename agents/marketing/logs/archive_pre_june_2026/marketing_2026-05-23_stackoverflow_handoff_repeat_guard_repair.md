# StackOverflow handoff repeat-guard repair — 2026-05-23

## Action
Patched the marketing selector so a StackOverflow answer packet that was already delivered for manual placement does **not** get selected again as if it were a fresh action.

## Why this was the highest-leverage move now
- Codeberg adoption is still flat.
- Reddit is fail-closed, Apollo is already inside its live measurement window, and same-day directory/curator lanes are already saturated.
- A fresh StackOverflow handoff packet had already been delivered manually today, so selecting that same handoff again would have been fake progress.
- The safest real improvement available right now was to harden the loop so the next slot gets spent on a new lane instead of another duplicate packet.

## What changed
- Added recent `stackoverflow_manual_delivery` detection in `agents/marketing/distribution_lane_selector.py`.
- Added a selector reason explaining that another handoff packet would be fake progress after manual delivery.
- Changed fallback behavior so the selector prefers `repo_conversion_proof_asset` or `distribution_reset` instead of reusing `stackoverflow_answer_handoff_packet`.
- Added a regression test for this exact case in `agents/marketing/tests/test_marketing_system.py`.

## Verification
- `python3 -m unittest agents.marketing.tests.test_marketing_system agents.marketing.tests.test_run_repair_mode` ✅

## Expected effect
Future runs should stop wasting a fresh marketing action on the same StackOverflow handoff packet and should move to a genuinely new conversion or distribution lane instead.
