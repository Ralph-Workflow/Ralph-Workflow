# PyPI Publishing Fix — 2-Minute Setup

## What happened

The marketing loop discovered that PyPI (1,498 downloads/month — the single highest-volume 
conversion surface) is serving a **stale v0.8.7 package description with ZERO Codeberg or GitHub 
links**. The v0.8.8 README was already rewritten with a strong Codeberg-primary CTA. The tag was 
pushed but CI failed because PyPI trusted publishing isn't configured on the PyPI side.

## Fix applied (already shipped)

The CI workflow now supports both **Trusted Publishing** (preferred, automatic) and 
**Token-based auth** (fallback). When neither is configured, the workflow produces a clear 
step-by-step diagnostic instead of a cryptic failure.

## What you need to do (pick one — both take ~2 minutes)

### Option A: Trusted Publishing (preferred — zero-touch going forward)

1. Go to https://pypi.org/manage/project/ralph-workflow/settings/publishing/
2. Scroll to "Trusted Publishing" → "Add a new pending publisher"
3. Fill in:
   - **Owner:** `Ralph-Workflow`
   - **Repository name:** `Ralph-Workflow`
   - **Workflow name:** `publish-python-package.yml`
   - **Environment name:** `pypi`
4. Click "Add"
5. Done — the next tag push will publish automatically.

### Option B: API Token (simpler, but token expires)

1. Go to https://pypi.org/manage/account/token/
2. Click "Add API token"
3. Scope: limit to `ralph-workflow` project
4. Copy the generated token (starts with `pypi-`)
5. Go to https://github.com/Ralph-Workflow/Ralph-Workflow/settings/secrets/actions
6. Click "New repository secret"
7. Name: `PYPI_TOKEN`, Value: paste the token
8. Done — the next tag push will use token auth.

## After setup

To trigger a new publish, either:
- Push any commit with a tag matching `ralph-workflow-v*`
- Or manually trigger the workflow from GitHub Actions

The v0.8.8 README (with Codeberg-primary CTA) will then go live on PyPI, 
serving 1,498 downloads/month with a conversion path that currently doesn't exist.
