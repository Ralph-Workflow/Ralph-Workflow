# PyPI Blocker Escalation
Generated: 2026-06-01T18:53:35.824944

⚠️ **3-DAY ESCALATION**

**Blocked action:** Publish v0.8.8 to PyPI (wheel + sdist built, twine-check PASSED)
**Days without token:** 4
**Monthly downloads affected:** ~1,299 (seeing old README without Codeberg CTA)
**What's needed:** Set `PYPI_TOKEN` environment variable to a valid PyPI API token
  with upload scope for the `ralph-workflow` package.

PyPI v0.8.8 has been built and ready for 3 days but remains unpublished due to missing PYPI_TOKEN. 1,299 downloads/month see the old README with no Codeberg → star/watch/fork CTA path. Each download is a missed conversion opportunity.

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
2026-06-01T18:53:35.824944

## Codeberg impact
- Primary repo: https://codeberg.org/RalphWorkflow/Ralph-Workflow
- PyPI page: https://pypi.org/project/ralph-workflow/
- Current version on PyPI: 0.8.7 (old README, no Codeberg forward-path CTA)
- Built awaiting publish: 0.8.8 (updated README with Codeberg primary CTA)
