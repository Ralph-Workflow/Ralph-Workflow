# Marketing Action Log

- Timestamp: 2026-05-27T11:57:53+02:00
- Action: install_page_conversion_cta_repair
- Status: executed

## Why this was the highest-leverage executable move
The current bottleneck is conversion to free use, the outbound/distribution lanes are still inside live review windows, and the comparison-page evaluator CTA is already hardened. The cleanest next same-run move was to tighten the install page so a high-intent visitor lands on the same Codeberg-first evaluator path instead of falling back to a generic install-only flow.

## What changed
- Added a new install-page conversion block that keeps the post-install path explicit:
  1. open the primary Codeberg repo
  2. choose a bounded first task
  3. copy the prompt shape and run tonight
- Carried the morning-after evaluation question directly onto the install page: “would I merge this?”
- Kept GitHub explicitly framed as the mirror while Codeberg stays primary.
- Added request-spec coverage so future copy changes do not silently remove the evaluator path from `/install`.
- Pushed the site commit: `ee8b9c7` (`Strengthen install evaluator path`).

## Shared findings reused
- ADOPTION_FUNNEL_NEXT.md
- marketing_workflow_audit_latest.json
- distribution_lane_latest.json
- market_intelligence_latest.json
- marketing_2026-05-27_114220_comparison_page_conversion_cta_repair.md
- marketing_2026-05-27_115319_comparison_page_conversion_guardrail.md

## Verification
- `RAILS_ENV=test PGHOST=/home/mistlight/.openclaw/workspace/Ralph-Site/tmp/pgsocket PGPORT=55432 ./bin/with-ruby bundle exec rspec spec/requests/pages_spec.rb` → 93 examples, 0 failures
- `./bin/with-ruby ruby -c spec/requests/pages_spec.rb` → Syntax OK
- `git push origin main` pushed commit `ee8b9c7`

## Expected outcome
Install-page visitors should now hit the same Codeberg-first free-use path as comparison-page visitors, which raises the odds that install-intent traffic becomes a real first run instead of stalling at command copy.

## Measurement window
Review install-page engagement and downstream Codeberg-linked movement within 7 days.
