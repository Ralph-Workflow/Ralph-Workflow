# Primary-repo-flat outreach state repair

- Timestamp: 2026-05-27T08:39:00+02:00
- Why: the execution board was still resurfacing SitePoint as a fresh publisher packet target even though live outreach had already been sent.
- Repair actions:
  - backfilled normalized publisher-outreach logs for Requesty and SOTAAZ
  - promoted the live Codersera send into the canonical publisher-outreach log family
  - patched `agents/marketing/distribution_lane_executor.py` so recent publisher targets match normalized name variants instead of exact strings only
  - added a regression test covering long-title publisher targets
- Shared findings reused:
  - `primary_repo_flat_contact_discovery_latest.json`
  - `market_intelligence_latest.json`
  - `adoption_metrics_latest.json`
  - `outreach-log.md`
- Verification:
  - `python3 -m unittest agents.marketing.tests.test_distribution_lane_executor_measurement_hold -k long_title`
  - `python3 -m unittest agents.marketing.tests.test_distribution_lane_executor_measurement_hold -k recipient_only_payload`
  - refreshed `drafts/marketing_execution_board_latest.md` now shows no truthful do-now packet in the current review window
