# Primary-repo-flat review-window truth repair

- Timestamp: 2026-05-27T14:18:00
- Goal: improve the next truthful post-hold rerun instead of recycling exhausted/manual-delivered publisher packets.

## What changed
1. Patched `agents/marketing/primary_repo_flat_contact_discovery.py` so recent live publisher contact suppression now includes:
   - `publisher_email_outreach`
   - `publisher_contact_form_submission`
   - `publisher_feedback_form_submission`
2. Added regression coverage in `agents/marketing/tests/test_primary_repo_flat_contact_discovery.py` for email, contact-form, and feedback-form live actions.
3. Re-ran publisher discovery. Newly omitted recent-review targets now include:
   - AI Saying
   - Codersera
   - ToolChase
   - Toolradar
   - Codivox
   - Morph
   - Requesty
   - SOTAAZ
   - NxCode
   - Beam
   - WyeWorks
   - Bollwerk / Werkstatt
   - AXME Code
4. Rewrote the post-hold re-entry contract to the truthful live short-window release time: `2026-05-27T14:43:06`.
5. Rescheduled the measurement-hold release cron to the same truthful release time and removed the stale earlier job that would have fired at `2026-05-27T12:26:29.000Z`.

## Why this matters
- ToolChase and other already-contacted publisher targets were still reappearing in discovery because contact-form/feedback-form live actions were not counted as recent outreach.
- The stale post-hold contract would have triggered too early, before the actual short-window congestion cleared, which would have created more empty-board churn.

## Verification
- `python3 -m unittest agents.marketing.tests.test_primary_repo_flat_contact_discovery -v` → PASS (16 tests)
- `agents/marketing/logs/primary_repo_flat_contact_discovery_latest.json` now omits recent live-contact targets truthfully.
- `drafts/post_hold_distribution_reentry_latest.md` now shows hold release at `2026-05-27T14:43:06`.
- `agents/marketing/logs/marketing_2026-05-27_141800_measurement_hold_release_cron.json` records stale-job cleanup plus the new cron id `3fc85cc5-e6ae-4c78-ace6-8dece93a767d`.

## Result
The highest-leverage executable action for this slot was a runtime/process truthfulness repair: remove already-contacted publisher targets from discovery and move the post-hold rerun to the real release boundary so the next run can choose a truthful lane instead of repeating fake progress.
