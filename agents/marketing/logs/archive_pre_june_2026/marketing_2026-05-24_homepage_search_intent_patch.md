# Homepage search-intent patch

- Timestamp: 2026-05-24T01:09:34+02:00
- Status: executed
- Goal: improve qualified search capture and Codeberg-first conversion on the live homepage

## Why this lane
- Codeberg adoption is flat, while same-family outreach lanes are already saturated.
- The daily SEO report still flagged on-page fixes and weak homepage keyword coverage.
- The adoption funnel still says conversion from interest to free use is the bottleneck.

## What changed
- Shortened the homepage title in `Ralph-Site/app/views/pages/home.html.erb`
- Rewrote the homepage meta description to fit the target length better
- Added a search-intent section that explicitly covers:
  - unattended coding agent
  - AI agent orchestration CLI
  - spec-driven AI agent
  - AI coding workflow automation
  - Claude Code automation
- Kept Codeberg as the primary source CTA in the new section

## Verification
- Ruby syntax checks passed for helper/controller files used by the page
- Rails render check returned `render_ok`
- Meta lengths after patch:
  - title source string: 30 chars
  - description source string: 152 chars

## Expected outcome
Visitors landing on the homepage from high-intent search queries should get a clearer match between their query, the product framing, and the Codeberg-first next step.

## Review window
- Review by: 2026-05-31
- Replace the tactic if the next window still shows no meaningful movement in qualified repo traffic or Codeberg adoption.
