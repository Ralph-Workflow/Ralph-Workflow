# Marketing Runtime Repair — distribution selector manual-contact gate

- Timestamp: 2026-05-23T18:38:00+02:00
- Status: completed
- Type: marketing_runtime_repair

## What changed
- Patched `agents/marketing/distribution_lane_selector.py` so `curator_contact_handoff_packet` only wins when current non-GitHub contact discovery is actually available.
- Added coverage in `agents/marketing/tests/test_marketing_system.py` for the stale-contact-discovery fallback case.
- Moved fresh reset-target promotion lower in the selector order so it no longer masks stronger higher-intent branches.

## Why this repair mattered
The loop had enough evidence to know that some manual-contact targets were stale or incomplete, but it could still drift into the handoff path or let reset-target presence dominate earlier than intended. That wastes cycles and makes the active lane look more executable than it really is.

## Verification
Passed:
`python3 -m unittest agents.marketing.tests.test_marketing_system.DistributionLaneSelectorTests agents.marketing.tests.test_marketing_system.DistributionLaneSelectorFallbackTests agents.marketing.tests.test_marketing_system.DistributionLaneSelectorManualContactFreshnessTests`

## Expected effect
- stale manual-contact targets without current discovery -> `distribution_reset`
- current manual-contact discovery -> `curator_contact_handoff_packet`
- fresh reset targets no longer override stronger branches too early
