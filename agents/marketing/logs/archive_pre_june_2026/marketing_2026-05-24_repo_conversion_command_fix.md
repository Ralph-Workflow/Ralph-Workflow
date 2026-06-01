# Marketing execution log — repo conversion command fix

- Timestamp: 2026-05-24 04:04 Europe/Berlin
- Action: repair stale owned-content setup instructions and enforce Codeberg-primary conversion surfaces

## Why this was the highest-leverage move now
- Codeberg adoption is flat in the active window.
- The active audit says public-facing content must keep Codeberg primary and stop wasting evaluators with weak conversion paths.
- A live owned-content draft still contained deprecated install/run commands that could turn qualified interest into failed first runs.

## What changed
- Updated `content/posts/2026-05-11_devto-spec-driven-development-unattended-claude.md` to use the current `pipx install ralph-workflow` + `ralph --init` + `ralph --diagnose` + `ralph` flow.
- Tightened the CTA in `content/posts/2026-05-10_devto-ai-cli-orchestration-problem.md` so it points to Codeberg first.
- Updated `drafts/2026-05-20_ai-engineering-pipeline_telegraph.md` to remove stale `ralph run --spec` guidance and use the current first-run contract.
- Added `agents/marketing/tests/test_public_conversion_surfaces.py` so this regression fails closed next time.

## Verification
- `python3 -m unittest agents.marketing.tests.test_public_conversion_surfaces -v`
- deprecated-command grep over the repaired public surfaces returned no matches

## Measurement contract
- Expected outcome: fewer false starts from owned-content traffic and cleaner Codeberg-first evaluator routing
- Review command-hygiene impact by: 2026-05-31 04:04 Europe/Berlin
- Review adoption movement by: 2026-06-07 04:04 Europe/Berlin
- Replace this lane if Codeberg is still flat at review time
