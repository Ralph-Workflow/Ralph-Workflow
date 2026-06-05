# PyPI Blocker Escalation
Generated: 2026-06-05T01:58:56.163120

🚨 **URGENT 7-DAY ESCALATION**

**Blocked action:** Publish v0.8.8 to PyPI (wheel + sdist built, twine-check PASSED)
**Days without token:** 7
**Monthly downloads affected:** ~1,299 (seeing old README without Codeberg CTA)
**What's needed:** Set `PYPI_TOKEN` environment variable to a valid PyPI API token
  with upload scope for the `ralph-workflow` package.

PyPI v0.8.8 has been built and ready for 7+ days. The missing PYPI_TOKEN is now the #1 structural blocker between the marketing system and real Codeberg adoption. 1,299 monthly downloads are landing on an outdated README without a primary-repo CTA. This should be the next human action, before any other manual lane work.

## How to fix (human action required)
1. Go to https://pypi.org/manage/account/token/
2. Create a token with scope: Upload for `ralph-workflow`
3. Set in this environment:
   ```
   export PYPI_TOKEN=pypi-xxxxxxxxxxxxxxxxxxxx
   ```
4. The auto-unblocker will detect the token within 6 hours and publish automatically.
   Or run manually: `python3 /home/mistlight/.openclaw/workspace/agents/marketing/pypi_auto_unblocker.py`

## Token was last checked
2026-06-05T01:58:56.163120

## Codeberg impact
- Primary repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow
- PyPI page: https://pypi.org/project/ralph-workflow/
- Current version on PyPI: 0.8.7 (old README, no Codeberg forward-path CTA)
- Built awaiting publish: 0.8.8 (updated README with Codeberg primary CTA)
