# Reddit next-window opening dedupe repair

- Timestamp: 2026-05-26T07:50:08+02:00
- Status: executed
- Live external action: false

## Why this ran
- The freshest Reddit packet still reused the same finish-state opener across two `r/AI_Agents` threads.
- `reddit_post_analysis_latest.json` explicitly flagged repeated openings as an active failure mode.
- With the execution board still truthfully empty until the 2026-05-26 08:57 CEST release, improving the post-hold Reddit lane was the highest-leverage real action available.

## Shared findings reused
- `drafts/marketing_execution_board_latest.md`
- `agents/marketing/logs/adoption_metrics_latest.json`
- `agents/marketing/logs/reddit_post_analysis_latest.json`
- `seo-reports/reddit_monitor_latest.md`
- `seo-reports/competitor_analysis_2026-05-26.md`

## What changed
- Added opener-variant selection for generic production-failure, workflow, and parallel-agent drafts.
- Added packet-level dedupe so repeated generic openings inside the same next-window packet get rewritten before release.
- Kept Codeberg-first landing pages intact while preserving thread-specific angle lines.
- Regenerated the current next-window Reddit packet.

## Verification
- `python3 -m unittest agents.marketing.tests.test_reddit_next_window_packet && python3 agents/marketing/reddit_next_window_packet.py`
- Result: 9 tests passed; packet regenerated with distinct openings for the two production-failure AI_Agents threads.

## Artifacts
- `/home/mistlight/.openclaw/workspace/drafts/2026-05-26_reddit_next_window_packets.md`
- `/home/mistlight/.openclaw/workspace/drafts/reddit_next_window_packets_latest.md`
