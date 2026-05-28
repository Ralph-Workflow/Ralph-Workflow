# PyPI Release Gate: ralph-workflow 0.8.8

**Created:** 2026-05-28T17:18+02:00
**Status:** READY_TO_UPLOAD (wheel built, verified, waiting for credentials)

## Why this matters

PyPI is the single largest untapped conversion funnel: **1,498 downloads/month**
with **zero Codeberg repo conversion**. The current live PyPI page (v0.8.7) has
a 2,840-character README with ZERO mentions of Codeberg, stars, or watching.

The built v0.8.8 wheel contains a rewritten README with:
- `⭐ Star on Codeberg` CTA in the second visual line
- Codeberg-primary messaging throughout
- The full `project_urls` already pointing to Codeberg

## What's in the wheel

- **Version:** 0.8.8
- **Build:** `ralph_workflow-0.8.8-py3-none-any.whl` (wheel) + `ralph_workflow-0.8.8.tar.gz` (sdist)
- **Location:** `repos/Ralph-Workflow/github-mirror/ralph-workflow/dist/`
- **README verified:** contains `⭐ Star on Codeberg: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>` as second line
- **PKG-INFO verified:** contains Codeberg links

## How to release

```bash
cd /home/mistlight/.openclaw/workspace/repos/Ralph-Workflow/github-mirror/ralph-workflow
pipx run twine upload dist/ralph_workflow-0.8.8-py3-none-any.whl dist/ralph_workflow-0.8.8.tar.gz
```

Or via the Makefile:
```bash
cd /home/mistlight/.openclaw/workspace/repos/Ralph-Workflow/github-mirror
make publish
```

## Expected outcome

After release, the PyPI description (which 1,498 monthly visitors see) will
contain an explicit Codeberg star/watch/fork CTA. This is the highest-leverage
single change available, converting an existing audience to repo adoption.

## Measurement

- Check PyPI page within 10 minutes of upload: https://pypi.org/project/ralph-workflow/
- Monitor Codeberg star movement for 7 days post-release
- Expected: at least 1-2 new stars from the 1498/month download audience
