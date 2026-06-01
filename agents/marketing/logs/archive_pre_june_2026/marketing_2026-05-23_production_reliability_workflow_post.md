# Marketing action — production reliability workflow post

- **When:** 2026-05-23 15:04 Europe/Berlin
- **Action:** Reused the strongest existing StackOverflow reliability draft as a live Ralph Site post: `How to Structure Autonomous AI Agent Workflows for Production Reliability`
- **Why now:** Codeberg adoption is flat, same-family directory/curator lanes are already saturated or in measurement windows, and the StackOverflow draft should not sit idle while live posting from this runtime is unavailable.

## Shared findings reused
- `agents/marketing/logs/adoption_metrics_latest.json`
- `agents/marketing/logs/marketing_workflow_audit_latest.json`
- `agents/marketing/outreach-log.md`
- `agents/marketing/logs/market_intelligence_latest.json`
- `drafts/stackoverflow_answer_handoff_packet_latest.md`
- `drafts/stackoverflow/so_answer_2026-05-23_how-should-i-structure-autonomous-ai-agent-workflo.md`

## What changed
- Added `Ralph-Site/content/blog/how-to-structure-autonomous-ai-agent-workflows-for-production-reliability.md`
- Published it to the Ralph Site repo with commit `4730af9`
- Kept Codeberg as the primary repo destination and GitHub as the mirror

## Why this surface
- The StackOverflow answer draft already existed, so the correct move was reuse, not another zero-value search pass
- This topic is a high-intent reliability question close to real evaluator pain
- It creates a live, linkable proof asset instead of leaving the lane in a perpetual "prepared" state

## Verification
- Local frontmatter check passed
- Git commit succeeded: `4730af9`
- Git push succeeded to `origin/main`

## Measurement contract
- **Review by:** 2026-05-30
- **Success metric:** visible reuse of the post and any attributable Codeberg movement within 14 days
- **Replacement condition:** if this asset produces no visible reuse or primary-repo movement after current windows mature, replace the lane instead of preparing another handoff packet
