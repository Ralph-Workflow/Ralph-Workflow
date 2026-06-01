# Owned-content Telegraph render repair

- Timestamp: 2026-05-27T13:13:06+02:00
- Status: executed
- Bottleneck: conversion_to_free_use

## Why this ran
The short review-window hold is still active until 2026-05-27T14:26:29, so another live outbound action would mostly blur measurement. The next truthful owned-content slot is likely the first-task guide, but the Telegraph renderer was leaving markdown links as raw text and would have degraded the conversion path on a high-priority free-use asset.

## Shared findings reused
- ADOPTION_FUNNEL_NEXT.md → first-task / start-here guide is the highest-priority conversion asset
- distribution_lane_latest.json → owned_content is the truthful hold-window lane
- marketing_execution_board_latest.md → no truthful do-now handoff packet exists in the current review window
- marketing_2026-05-27_125429_owned_content_first_task_reentry_repair.json → first-task guide was restored into the next owned-content slot
- docs/first-task-guide.md → contains relative markdown links and fenced code blocks that must survive Telegraph publication

## Changes made
- Added a Telegraph markdown-node builder in `agents/marketing/run_posting.py`
- Preserved headings, blockquotes, unordered lists, fenced code blocks, bold/italic text, and inline links
- Rewrote relative markdown links to canonical Codeberg source URLs
- Passed `source_path` through scheduled posting and owned-content lane execution
- Added regression coverage in `agents/marketing/tests/test_marketing_system.py`

## Verification
```bash
python3 -m unittest agents.marketing.tests.test_marketing_system.PostingTests.test_build_telegraph_nodes_rewrites_relative_links_to_codeberg agents.marketing.tests.test_marketing_system.PostingTests.test_execute_owned_content_skips_historic_first_task_guide_repost agents.marketing.tests.test_marketing_system.PostingTests.test_already_posted_successfully_matches_experiment_id_or_source_path -v
```

Result: passed

## Expected effect
When the hold clears at 2026-05-27T14:26:29, the next owned-content Telegraph publication should keep a working Codeberg-first path and readable proof formatting instead of leaking raw markdown on a conversion-critical asset.
