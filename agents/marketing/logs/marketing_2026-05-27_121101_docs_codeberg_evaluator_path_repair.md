# Marketing Action Log

- Timestamp: 2026-05-27T12:11:01+02:00
- Action: docs_codeberg_evaluator_path_repair
- Status: executed

## Why this was the highest-leverage executable move
The active bottleneck is still conversion to free use, the execution board/distribution lane were still in guard-pause truth, and recent owned-surface repairs had already tightened the comparison, install, and blog evaluator exits. The remaining high-intent owned surface was the docs entry path, where getting-started and first-task pages still needed the Codeberg-primary / GitHub-mirror framing pushed higher and made regression-proof.

## What changed
- Added above-the-fold callouts to `docs/sphinx_overrides/getting-started.md` and `docs/sphinx_overrides/first-task-guide.md`.
- Made the evaluator path explicit on both pages:
  1. Codeberg is the primary repo
  2. GitHub is only the mirror
  3. choose one bounded first task
  4. ask tomorrow: would I merge this?
- Added request/system regression coverage so the docs path keeps this framing.
- Rebuilt the docs surface and pushed the site commit: `89f4933` (`Strengthen docs Codeberg evaluator path`).

## Shared findings reused
- `ADOPTION_FUNNEL_NEXT.md`
- `market_intelligence_latest.json`
- `marketing_workflow_audit_latest.json`
- `distribution_lane_latest.json`
- `outcome_execution_board_latest.md`
- `marketing_2026-05-27_114220_comparison_page_conversion_cta_repair.md`
- `marketing_2026-05-27_115753_install_page_conversion_cta_repair.md`
- `marketing_2026-05-27_120322_blog_conversion_cta_repair.md`

## Verification
- `./bin/build-docs` → rebuilt 652 HTML files successfully
- `grep -n "Codeberg is the primary repo\|GitHub is only the mirror\|choose one bounded first task" public/docs/getting-started.html public/docs/first-task-guide.html` → phrases present in both live built docs pages
- `./bin/with-ruby bundle exec rspec spec/requests/docs_spec.rb -e 'serves the getting-started page with full chrome' -e 'keeps the Codeberg-first evaluator framing on the first-task guide' spec/system/docs_spec.rb -e 'shows the docs navbar and footer with expected links on /docs/getting-started'` → 3 examples, 0 failures
- `./bin/with-ruby ruby -c spec/requests/docs_spec.rb && ./bin/with-ruby ruby -c spec/system/docs_spec.rb` → Syntax OK
- `git push origin main` → pushed commit `89f4933`

## Expected outcome
Docs visitors with the highest setup intent now hit the Codeberg-first evaluator path immediately instead of discovering it later in the page, which should improve qualified repo visits and first-run evaluation starts.

## Measurement window
Check docs-entry traffic behavior and downstream Codeberg/GitHub movement within 7 days.
